"""The evaluation harness (issue #19): a reader on every document of the kit.

    cd backend && uv run python -m app.docintel.evaluate --reader fixture
    cd backend && uv run --env-file ../.env python -m app.docintel.evaluate \
        --reader omni --runs 3

For each document of `data/mock/` (the scenarios, then the hard versions) it calls
`classify` and `extract` through the `Reader` contract, compares each field with the
answer key (`expected.json`, rules in `app/docintel/compare.py`) and times the two
calls with a monotonic clock. Each run gets a new reader, so no cache of one run
helps the next. The report goes to `report_dir()/latest.json` (and a copy with the
date): the page "Metrics" reads it through `GET /evaluation/latest`.

It touches no claim, no store, no upload and no gateway: it measures the reader only.
What counts as right, the pass rate and the line items: `docs/EVALUATION.md`.
"""

import argparse
import json
import logging
import os
import re
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic_core import to_jsonable_python
from pypdf import PdfReader

from app import demo
from app.docintel.compare import (
    BadAnswerKey,
    Kind,
    Verdict,
    check_expected,
    compare_field,
    compare_lines,
    field_kind,
)
from app.docintel.reader import Reader
from app.models import Document, DocumentType, Extraction
from app.store import CLAIM_TYPE

SCHEMA_VERSION = 1
KINDS = ("clean", "scan", "photo")  # the order of the tables
TEXT_CHARS = 300  # an error text or a read value is cut there
KNOWN_TYPES = {t.value for t in DocumentType}
Clock = Callable[[], float]


def report_dir() -> Path:
    """Where the reports go. Relative to `backend/`, like the other files of a run
    (`.dev-data/` is not in git): the CLI and the API read the same folder."""
    return Path(os.environ.get("CLAIMS_EVALUATION", ".dev-data/evaluation"))


# ---------------------------------------------------------------- the kit


@dataclass(frozen=True)
class KitDocument:
    id: str  # "folder/file", as the reader bench and the demo kit name it
    path: Path
    kind: str  # clean, scan or photo
    workflow: str  # the claim type of the scenario: what `extract_document` gives
    key: dict[str, Any] | None  # the answer key; None when it cannot be used
    key_error: str | None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _check_key(key: Any) -> None:
    """Raises BadAnswerKey when the answer key cannot be used to score a reader."""
    if not isinstance(key, dict):
        raise BadAnswerKey("the answer key is not a JSON object")
    doc_type = key.get("document_type")
    if doc_type not in KNOWN_TYPES:
        raise BadAnswerKey(f"unknown document_type {doc_type!r}")
    fields = key.get("fields")
    if not isinstance(fields, list):
        raise BadAnswerKey("`fields` is not a list")
    seen: set[str] = set()
    for f in fields:
        name = f.get("name") if isinstance(f, dict) else None
        if not isinstance(name, str):
            raise BadAnswerKey("a field has no name")
        if name in seen:
            raise BadAnswerKey(f"the field {name} is there twice")
        seen.add(name)
        kind = field_kind(doc_type, name)
        if kind is None:
            raise BadAnswerKey(f"the field {name} is not in the schema of {doc_type}")
        page = f.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise BadAnswerKey(f"the field {name} has no page of 1 or more")
        if "value" not in f:
            raise BadAnswerKey(f"the field {name} has no value")
        check_expected(kind, f["value"])
    lines = key.get("line_items", [])
    if not isinstance(lines, list) or not all(isinstance(x, dict) for x in lines):
        raise BadAnswerKey("`line_items` is not a list of objects")
    compare_lines(lines, lines)  # a bad value in a line raises BadAnswerKey


def _answer_key(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    expected = path.with_suffix(".expected.json")
    if not expected.is_file():
        return None, "no expected.json next to the document"
    try:
        key = json.loads(expected.read_text(encoding="utf-8"))
        _check_key(key)
    except (OSError, ValueError, TypeError) as e:  # BadAnswerKey is a ValueError
        return None, f"{type(e).__name__}: {e}"[:TEXT_CHARS]
    return key, None


def kit() -> list[KitDocument]:
    """Every document of the kit, in the order of the demo kit (`demo._kit`): the
    reader bench, the contract test and this harness see the same documents."""
    mock = demo._data() / "mock"
    index = demo._index()
    policies = {
        p.get("number"): p
        for p in _read_json(mock / "policies.json").get("policies", [])
    }
    scenarios = {s["folder"]: s for s in index.get("scenarios", [])}
    hard = {h["file"]: h for h in index.get("hard", [])}
    out = []
    for (folder, name), path in demo._kit().items():
        doc_id = f"{folder}/{name}"
        variant = hard.get(doc_id)
        key, error = _answer_key(path)
        kind = (variant or {}).get("variant") or (key or {}).get("variant") or "clean"
        source = variant["source_document"].split("/")[0] if variant else folder
        scenario = scenarios.get(source)
        policy = policies.get(scenario["policy"]) if scenario else None
        workflow = CLAIM_TYPE.get(policy["line_of_business"]) if policy else None
        out.append(
            KitDocument(doc_id, path, str(kind), workflow or "unknown", key, error)
        )
    return out


# ---------------------------------------------------------------- one document


def _jsonable(value: Any) -> Any:
    """A value of a reader as JSON: dates as ISO text, anything else as text."""
    try:
        out = to_jsonable_python(value, fallback=str)
        json.dumps(out, allow_nan=False)
    except (TypeError, ValueError):
        out = str(value)
    if isinstance(out, str) and len(out) > TEXT_CHARS:
        out = out[:TEXT_CHARS] + "..."
    return out


def _timed(call: Callable[[], Any], clock: Clock) -> tuple[Any, float, str | None]:
    """The result, the seconds, and the error text when the reader raises: one
    document that fails must not stop the run."""
    start = clock()
    try:
        out, error = call(), None
    except Exception as e:
        out, error = None, f"{type(e).__name__}: {e}"[:TEXT_CHARS]
    return out, round(clock() - start, 3), error


def _pages(path: Path, data: bytes) -> int:
    if path.suffix != ".pdf":
        return 1
    try:
        return max(1, len(PdfReader(BytesIO(data)).pages))
    except Exception:  # a broken PDF is still read by the reader: one page
        return 1


def _source(field: Any, document: Document) -> tuple[bool, int | None]:
    """Is the source valid (non-negotiable 3: this document, a page that exists, a
    snippet or a box), and its page."""
    source = getattr(field, "source", None)
    page = getattr(source, "page", None)
    if isinstance(page, bool) or not isinstance(page, int):
        page = None
    valid = (
        source is not None
        and getattr(source, "document_id", None) == document.id
        and page is not None
        and 1 <= page <= document.pages
        and bool(getattr(source, "snippet", None) or getattr(source, "box", None))
    )
    return valid, page


def _score_field(
    kind: Kind,
    expected: dict[str, Any],
    got: list[Any],
    document: Document,
    failed: bool,
) -> dict[str, Any]:
    value, expected_page = expected["value"], expected["page"]
    row: dict[str, Any] = {
        "name": expected["name"],
        "kind": kind,
        "expected": _jsonable(value),
        "read": None,
        "expected_page": expected_page,
        "page": None,
        "page_right": None,
        "source": None,
    }
    if not got:
        verdict = compare_field(kind, value, None)
        if verdict.status == "missing":
            verdict = Verdict("missing", "the reader failed" if failed else "absent")
        return row | {"status": verdict.status, "reason": verdict.reason}
    first = got[0]
    verdict = compare_field(kind, value, getattr(first, "value", None))
    if verdict.status == "right" and any(
        compare_field(kind, value, getattr(g, "value", None)).status != "right"
        for g in got[1:]
    ):
        verdict = Verdict("wrong", "given twice with different values")
    row |= {"status": verdict.status, "reason": verdict.reason}
    if verdict.status == "missing":  # null with a source: still nothing read
        return row
    valid, page = _source(first, document)
    right_page = valid and page == expected_page
    return row | {
        "read": _jsonable(getattr(first, "value", None)),
        "page": page,
        "page_right": right_page,
        "source": "invalid" if not valid else "right" if right_page else "wrong_page",
    }


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _field_counts(rows: list[dict[str, Any]], extra: int = 0) -> dict[str, Any]:
    right = sum(r["status"] == "right" for r in rows)
    wrong = sum(r["status"] == "wrong" for r in rows)
    page_right = sum(r["page_right"] is True for r in rows)
    # right and on the expected page; a field expected empty has no page to check
    verified = sum(
        r["status"] == "right" and (r["page_right"] is True or r["expected"] is None)
        for r in rows
    )
    return _with_shares(
        {
            "expected": len(rows),
            "right": right,
            "wrong": wrong,
            "missing": sum(r["status"] == "missing" for r in rows),
            "extra": extra,
            # values read: they have a source to check (page_right is not None)
            "read": sum(r["page_right"] is not None for r in rows),
            "page_right": page_right,
            "verified": verified,
        }
    )


def _with_shares(c: dict[str, Any]) -> dict[str, Any]:
    return c | {
        "accuracy": _share(c["right"], c["expected"]),
        "verified_accuracy": _share(c["verified"], c["expected"]),
        "page_accuracy": _share(c["page_right"], c["read"]),
    }


COUNTS: tuple[str, ...] = ("expected", "right", "wrong", "missing", "extra", "read")
COUNTS = ("expected", "right", "wrong", "missing", "extra", "read")
COUNTS += ("page_right", "verified")


def _sum_counts(many: Iterable[dict[str, Any]]) -> dict[str, Any]:
    total = dict.fromkeys(COUNTS, 0)
    for c in many:
        for k in COUNTS:
            total[k] += c[k]
    return _with_shares(total)


def _score(
    reader: Reader, doc: KitDocument, number: int, clock: Clock
) -> dict[str, Any]:
    key = doc.key
    assert key is not None  # documents with no usable key are not scored
    data = doc.path.read_bytes()
    document = Document(
        id=f"D-eval-{number}", file=doc.path.name, pages=_pages(doc.path, data)
    )
    classified, t_classify, classify_error = _timed(
        lambda: reader.classify(document, data), clock
    )
    out, t_extract, extract_error = _timed(
        lambda: reader.extract(document, data, doc.workflow), clock
    )
    if classify_error is None and not isinstance(classified, str):
        classify_error, classified = f"returned {type(classified).__name__}", None
    if extract_error is None and not isinstance(out, Extraction):
        extract_error, out = f"returned {type(out).__name__}", None
    error = "; ".join(
        f"{step}: {e}"
        for step, e in (("classify", classify_error), ("extract", extract_error))
        if e
    )

    by_name: dict[str, list[Any]] = {}
    for f in out.fields if out else []:
        by_name.setdefault(str(getattr(f, "name", "")), []).append(f)
    duplicates = [name for name, found in by_name.items() if len(found) > 1]
    rows = []
    for expected in key["fields"]:
        kind = field_kind(key["document_type"], expected["name"])
        assert kind is not None  # checked with the key
        got = by_name.pop(expected["name"], [])
        rows.append(_score_field(kind, expected, got, document, failed=out is None))
    extra = [
        {
            "name": name,
            "value": _jsonable(getattr(f, "value", None)),
            "page": _source(f, document)[1],
        }
        for name, found in by_name.items()
        for f in found
    ]
    counts = _field_counts(rows, len(extra))
    lines = compare_lines(key.get("line_items", []), out.line_items if out else [])
    return {
        "id": doc.id,
        "folder": doc.path.parent.name,
        "file": doc.path.name,
        "document_type": key["document_type"],
        "kind": doc.kind,
        "workflow": doc.workflow,
        "pages": document.pages,
        "classified": classified,
        "classified_right": classified == key["document_type"],
        "extracted_type": out.document_type.value if out else None,
        "seconds": {
            "classify": t_classify,
            "extract": t_extract,
            "total": round(t_classify + t_extract, 3),
        },
        "error": error or None,
        # every expected field right and on its page, and the reader did not raise
        "passed": not error and counts["verified"] == counts["expected"],
        "fields": rows,
        "counts": counts,
        "extra": extra,
        "duplicates": duplicates,
        "line_items": lines._asdict(),
    }


# ---------------------------------------------------------------- totals


def _timing(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "total": 0.0} | dict.fromkeys(
            ("mean", "median", "min", "max")
        )
    return {
        "count": len(values),
        "total": round(sum(values), 3),
        "mean": round(statistics.fmean(values), 3),
        "median": round(statistics.median(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def _aggregate(docs: list[dict[str, Any]]) -> dict[str, Any]:
    with_lines = [
        d["line_items"]
        for d in docs
        if d["line_items"]["expected"] or d["line_items"]["read"]
    ]
    passed = sum(d["passed"] for d in docs)
    return {
        "documents": {
            "count": len(docs),
            "passed": passed,
            "pass_rate": _share(passed, len(docs)),
            "errors": sum(d["error"] is not None for d in docs),
            "classified_right": sum(d["classified_right"] for d in docs),
        },
        "fields": _sum_counts(d["counts"] for d in docs),
        "line_items": {
            "documents": len(with_lines),
            "documents_right": sum(li["all_right"] for li in with_lines),
            "expected": sum(li["expected"] for li in with_lines),
            "read": sum(li["read"] for li in with_lines),
            "right": sum(li["right"] for li in with_lines),
        },
        "seconds": _timing([d["seconds"]["total"] for d in docs]),
    }


def _by_field(docs: list[dict[str, Any]]) -> dict[str, Any]:
    rows: dict[str, list[dict[str, Any]]] = {}
    extra: dict[str, int] = {}
    for d in docs:
        for r in d["fields"]:
            rows.setdefault(r["name"], []).append(r)
        for x in d["extra"]:
            extra[x["name"]] = extra.get(x["name"], 0) + 1
    names = list(rows) + [n for n in extra if n not in rows]
    return {n: _field_counts(rows.get(n, []), extra.get(n, 0)) for n in names}


def _group(docs: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for d in docs:
        groups.setdefault(d[key], []).append(d)
    return groups


def _documents(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line per document of the kit, over the runs."""
    out = []
    for doc_id, runs in _group(docs, "id").items():
        errors = [d["error"] for d in runs if d["error"]]
        out.append(
            {
                "id": doc_id,
                "document_type": runs[0]["document_type"],
                "kind": runs[0]["kind"],
                "runs": len(runs),
                "passed_runs": sum(d["passed"] for d in runs),
                "classified_right_runs": sum(d["classified_right"] for d in runs),
                "fields": _sum_counts(d["counts"] for d in runs),
                "seconds": _timing([d["seconds"]["total"] for d in runs]),
                "errors": list(dict.fromkeys(errors)),
            }
        )
    return out


def _stat(values: list[float | None]) -> dict[str, float | None]:
    found = [v for v in values if v is not None]
    if not found:
        return dict.fromkeys(("mean", "min", "max", "spread"))
    return {
        "mean": round(statistics.fmean(found), 4),
        "min": min(found),
        "max": max(found),
        "spread": round(max(found) - min(found), 4),
    }


def _summary(overalls: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean, lowest and highest over the runs (docs/NIKOLA.md, section 7)."""
    pick: dict[str, Callable[[dict[str, Any]], float | None]] = {
        "accuracy": lambda o: o["fields"]["accuracy"],
        "verified_accuracy": lambda o: o["fields"]["verified_accuracy"],
        "page_accuracy": lambda o: o["fields"]["page_accuracy"],
        "pass_rate": lambda o: o["documents"]["pass_rate"],
        "seconds_per_document": lambda o: o["seconds"]["mean"],
        "wrong": lambda o: o["fields"]["wrong"],
        "missing": lambda o: o["fields"]["missing"],
        "errors": lambda o: o["documents"]["errors"],
    }
    return {name: _stat([get(o) for o in overalls]) for name, get in pick.items()}


# ---------------------------------------------------------------- HTTP


class HttpMeter:
    """Counts the HTTP calls of a reader, by status, and notes the model settings
    of each request body. It keeps no header (the key is in a header), no image and
    no answer. Status codes are what the server answered: a 429 retried by the
    reader is counted too."""

    BODY_KEYS = ("model", "temperature", "top_p", "max_tokens")

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._clients: list[httpx.Client] = []
        self.observed: dict[str, set[Any]] = {}
        self.reset()

    def reset(self) -> None:
        self.requests = 0
        self.by_status: dict[str, int] = {}

    def client(self, timeout: float) -> httpx.Client:
        c = httpx.Client(
            timeout=timeout,
            transport=self._transport,
            event_hooks={"request": [self._request], "response": [self._response]},
        )
        self._clients.append(c)
        return c

    def _request(self, request: httpx.Request) -> None:
        self.requests += 1
        try:
            body = json.loads(request.content)
        except ValueError:
            return
        if not isinstance(body, dict):
            return
        seen = {k: body[k] for k in self.BODY_KEYS if k in body}
        template = body.get("chat_template_kwargs")
        if isinstance(template, dict) and "enable_thinking" in template:
            seen["enable_thinking"] = template["enable_thinking"]
        form = body.get("response_format")
        if isinstance(form, dict) and "type" in form:
            seen["response_format"] = form["type"]
        for k, v in seen.items():
            if isinstance(v, str | int | float | bool):
                self.observed.setdefault(k, set()).add(v)

    def _response(self, response: httpx.Response) -> None:
        code = str(response.status_code)
        self.by_status[code] = self.by_status.get(code, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        answered = sum(self.by_status.values())
        return {
            "requests": self.requests,
            "by_status": dict(sorted(self.by_status.items())),
            "transport_errors": self.requests - answered,  # timeouts, refused, ...
            "http_429": self.by_status.get("429", 0),
        }

    def settings(self) -> dict[str, list[Any]]:
        return {k: sorted(v, key=str) for k, v in sorted(self.observed.items())}

    def close(self) -> None:
        for c in self._clients:
            c.close()


def _add_http(many: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    found = [h for h in many if h is not None]
    if not found:
        return None
    by_status: dict[str, int] = {}
    for h in found:
        for code, n in h["by_status"].items():
            by_status[code] = by_status.get(code, 0) + n
    return {
        "requests": sum(h["requests"] for h in found),
        "by_status": dict(sorted(by_status.items())),
        "transport_errors": sum(h["transport_errors"] for h in found),
        "http_429": sum(h["http_429"] for h in found),
    }


# ---------------------------------------------------------------- the readers

ReaderSetup = tuple[Callable[[], Reader], dict[str, Any], HttpMeter | None]


def _fixture() -> ReaderSetup:
    from app.docintel.fixture import FixtureReader

    return FixtureReader, {"reader": "fixture"}, None


def _endpoint(url: str | None) -> str | None:
    """The model endpoint with no user, password or query: only where it goes."""
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]  # the port stays, a user does not
    return f"{parts.scheme}://{host}{parts.path}"


def _omni(
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ReaderSetup:
    from app.docintel import omni

    meter = HttpMeter(transport)

    def make() -> Reader:
        return omni.OmniReader(client=meter.client(omni.TIMEOUT), sleep=sleep)

    settings = {
        "reader": "omni",
        "model": os.environ.get("VLM_MODEL"),
        "endpoint": _endpoint(os.environ.get("VLM_BASE_URL")),
        "dpi": omni.DPI,
        "max_tokens": omni.MAX_TOKENS,
        "max_pages": omni.MAX_PAGES,
        "max_pixels": omni.MAX_PIXELS,
        "timeout_seconds": omni.TIMEOUT,
        "retries": omni.RETRIES,
    }
    return make, settings, meter


READERS: dict[str, Callable[[], ReaderSetup]] = {"fixture": _fixture, "omni": _omni}


# ---------------------------------------------------------------- the report


class ReaderNotStarted(RuntimeError):
    """The reader could not be built (no key, a broken answer key for the fixture)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def evaluate(
    make_reader: Callable[[], Reader],
    runs: int = 1,
    *,
    settings: dict[str, Any] | None = None,
    meter: HttpMeter | None = None,
    clock: Clock = time.perf_counter,
    command: str = "",
) -> dict[str, Any]:
    """Runs a new reader `runs` times on the kit and returns the report (schema
    version 1, `docs/EVALUATION.md`). A document with no usable answer key is not
    scored: it is listed in `answer_key_errors` and the report is not complete."""
    if runs < 1:
        raise ValueError("runs must be 1 or more")
    documents = kit()
    scored = [d for d in documents if d.key is not None]
    details: list[dict[str, Any]] = []
    name = ""
    try:
        for run in range(1, runs + 1):
            if meter is not None:
                meter.reset()
            try:
                reader = make_reader()
            except Exception as e:  # no reader, no measure: no report at all
                raise ReaderNotStarted(f"{type(e).__name__}: {e}"[:TEXT_CHARS]) from e
            name = reader.name
            started, start = _now(), clock()
            results = [_score(reader, d, i, clock) for i, d in enumerate(scored, 1)]
            details.append(
                {
                    "run": run,
                    "started_at": started,
                    "seconds": round(clock() - start, 3),
                    "overall": _aggregate(results),
                    "http": meter.snapshot() if meter is not None else None,
                    "documents": results,
                }
            )
    finally:
        if meter is not None:
            meter.close()
    every = [d for r in details for d in r["documents"]]
    by_type = {
        t: _aggregate(docs) | {"by_field": _by_field(docs)}
        for t, docs in _group(every, "document_type").items()
    }
    kinds = _group(every, "kind")
    order = [k for k in KINDS if k in kinds] + [k for k in kinds if k not in KINDS]
    kit_kinds: dict[str, int] = {}
    for d in documents:
        kit_kinds[d.kind] = kit_kinds.get(d.kind, 0) + 1
    # what the configuration says, and apart, what the request bodies really held
    sent = {"sent": meter.settings()} if meter is not None else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _now(),
        "reader": name,
        "command": command,
        "settings": (settings or {"reader": name}) | sent,
        "runs": runs,
        "complete": len(scored) == len(documents),
        "kit": {
            "documents": len(documents),
            "by_kind": {k: kit_kinds[k] for k in sorted(kit_kinds, key=_kind_order)},
            "expected_fields": sum(len(d.key["fields"]) for d in scored if d.key),
            "expected_line_items": sum(
                len(d.key.get("line_items", [])) for d in scored if d.key
            ),
        },
        "answer_key_errors": [
            {"document": d.id, "error": d.key_error} for d in documents if d.key is None
        ],
        "summary": _summary([r["overall"] for r in details]),
        "pooled": _aggregate(every)
        | {
            "by_type": by_type,
            "by_kind": {k: _aggregate(kinds[k]) for k in order},
            "by_field": _by_field(every),
            "per_document": _documents(every),
        },
        "http": _add_http([r["http"] for r in details]),
        "run_details": details,
    }


def _kind_order(kind: str) -> tuple[int, str]:
    return (KINDS.index(kind) if kind in KINDS else len(KINDS), kind)


def _write_atomic(path: Path, text: str) -> None:
    """A reader of the file sees the old report or the new one, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dump(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def save_report(report: dict[str, Any], folder: Path | None = None) -> Path:
    """Writes `latest.json` and a copy named with the reader and the time. Returns
    the path of the copy."""
    folder = folder or report_dir()
    stamp = re.sub(r"[^0-9TZ]", "", report["created_at"])
    reader = re.sub(r"[^a-z0-9_-]", "-", str(report["reader"]).lower()) or "reader"
    text = dump(report)
    copy = folder / f"evaluation-{reader}-{stamp}.json"
    _write_atomic(copy, text)
    _write_atomic(folder / "latest.json", text)
    return copy


def load_latest(folder: Path | None = None) -> dict[str, Any]:
    """The last report. FileNotFoundError when there is none; ValueError when the
    file is not a report of this schema version."""
    path = (folder or report_dir()) / "latest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("not an evaluation report of schema version 1")
    return data


# ---------------------------------------------------------------- the command


def _percent(share: float | None) -> str:
    return "-" if share is None else f"{share * 100:.1f}%"


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _row(label: str, a: dict[str, Any]) -> str:
    f, d = a["fields"], a["documents"]
    cells = [
        label,
        d["count"],
        f["right"],
        f["wrong"],
        f["missing"],
        _percent(f["accuracy"]),
        _percent(f["page_accuracy"]),
        f"{d['passed']} ({_percent(d['pass_rate'])})",
        _seconds(a["seconds"]["mean"]),
    ]
    return "| " + " | ".join(str(c) for c in cells) + " |"


HEAD = ["", "Documents", "Right", "Wrong", "Missing", "Accuracy", "Page right"]
HEAD += ["All right", "s/doc"]


def _spread(stat: dict[str, Any]) -> str:
    return (
        f"{_percent(stat['mean'])} "
        f"(runs {_percent(stat['min'])} to {_percent(stat['max'])})"
    )


def summary_text(report: dict[str, Any]) -> str:
    """A markdown summary for the terminal and the pull request. The counts are
    added over the runs."""
    pooled, s = report["pooled"], report["summary"]
    kit_line = ", ".join(f"{k} {n}" for k, n in report["kit"]["by_kind"].items())
    lines = [
        f"Reader {report['reader']}; {report['runs']} run(s); "
        f"{report['kit']['documents']} documents ({kit_line}); "
        f"{report['kit']['expected_fields']} expected fields per run",
        f"Accuracy {_spread(s['accuracy'])}; pass rate {_spread(s['pass_rate'])}; "
        f"seconds per document {_seconds(s['seconds_per_document']['mean'])}",
        "",
        "| " + " | ".join(HEAD) + " |",
        "|" + "---|" * len(HEAD),
        _row("**All**", pooled),
        *(_row(f"kind: {k}", a) for k, a in pooled["by_kind"].items()),
        *(_row(t, a) for t, a in pooled["by_type"].items()),
    ]
    lines.append(
        f"\nLine items (apart): {pooled['line_items']['right']} of "
        f"{pooled['line_items']['expected']} lines right."
    )
    if report["http"] is not None:
        h = report["http"]
        lines.append(
            f"HTTP: {h['requests']} requests, by status {h['by_status']}, "
            f"{h['transport_errors']} transport errors, {h['http_429']} answers 429."
        )
    for e in report["answer_key_errors"]:
        lines.append(f"Answer key not used: {e['document']}: {e['error']}")
    return "\n".join(lines)


def _positive(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.docintel.evaluate",
        description="Run a reader on every document of the kit and write a report.",
    )
    ap.add_argument("--reader", required=True, choices=sorted(READERS))
    ap.add_argument("--runs", type=_positive, default=1, help="new reader per run")
    ap.add_argument("--output", type=Path, help="also write the report to this file")
    args = ap.parse_args(argv)
    # pypdf only counts the pages here; its warnings on a PDF it repairs (the
    # line ends of a Windows checkout) are not a result of the reader
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    command = "uv run python -m app.docintel.evaluate " + " ".join(
        sys.argv[1:] if argv is None else argv
    )
    try:
        make, settings, meter = READERS[args.reader]()
        report = evaluate(
            make, args.runs, settings=settings, meter=meter, command=command
        )
    # RuntimeError: e.g. OmniReader with no NVIDIA_KEY; OSError, ValueError: a kit
    # folder or an index.json that cannot be read. No report then.
    except (RuntimeError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    copy = save_report(report)
    if args.output:
        try:
            _write_atomic(args.output, dump(report))
        except OSError as e:
            print(
                f"error: the copy {args.output} was not written: {e}", file=sys.stderr
            )
            return 2
    print(summary_text(report))
    print(f"\nReport: {report_dir() / 'latest.json'} (copy: {copy})")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

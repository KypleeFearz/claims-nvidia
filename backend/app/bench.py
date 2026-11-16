"""The reader bench (issue #82): read one document of the mock kit with the active
reader and compare the result with its expected.json, field by field.

It is a tool to see one error fast, for the person who builds a reader. It calls the
reader through the `Reader` contract and nothing else: no claim, no agent, no
gateway, nothing stored. The numbers on the whole kit are the evaluation harness
(issue #19), not this file.
"""

import json
import time
from collections.abc import Callable
from io import BytesIO
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from pypdf import PdfReader

from app import demo
from app.docintel.reader import Reader
from app.models import Document, DocumentType, Extraction
from app.store import CLAIM_TYPE, Store

router = APIRouter(prefix="/bench")


class Pick(BaseModel):
    folder: str
    file: str


def compare(expected: Any, read: Any) -> bool:
    """Is the value read the expected one? Numbers to the cent; text with no
    difference of case or spaces. The harness of issue #19 can use or replace it."""
    if isinstance(expected, bool) or isinstance(read, bool):
        return expected is read
    try:
        return abs(float(expected) - float(read)) < 0.005
    except (TypeError, ValueError):
        pass
    if expected is None or read is None:
        return expected is read
    return " ".join(str(expected).split()).casefold() == (
        " ".join(str(read).split()).casefold()
    )


def _timed(call: Callable[[], Any]) -> tuple[Any, float, str | None]:
    """The result, the seconds, and the error text when the reader raises: a reader
    in progress fails often, and the bench must show it."""
    start = time.perf_counter()
    try:
        out, error = call(), None
    except Exception as e:
        out, error = None, f"{type(e).__name__}: {e}"[:300]
    return out, round(time.perf_counter() - start, 3), error


def _workflow(store: Store, row: dict[str, Any]) -> str:
    """The claim type of the scenario of the document: what `extract_document` gives."""
    for policy in store.get_customer_policies(str(row.get("customer_id"))):
        if policy.number == row.get("policy"):
            return CLAIM_TYPE[policy.line_of_business]
    return "unknown"


def _fields(expected: list[dict[str, Any]], out: Extraction | None) -> list[Any]:
    read = {f.name: f for f in (out.fields if out else [])}
    rows = []
    for e in expected:
        f = read.pop(e["name"], None)
        rows.append(
            {
                "name": e["name"],
                "status": "missing"
                if f is None
                else "right"
                if compare(e["value"], f.value)
                else "wrong",
                "expected": e["value"],
                "read": f.value if f else None,
                "expected_page": e["page"],
                "page": f.source.page if f else None,
                "page_right": f is not None and f.source.page == e["page"],
                "snippet": f.source.snippet if f else None,
                "confidence": f.confidence if f else None,
            }
        )
    for f in read.values():  # the reader gave a field that nobody expects
        rows.append(
            {
                "name": f.name,
                "status": "extra",
                "expected": None,
                "read": f.value,
                "expected_page": None,
                "page": f.source.page,
                "page_right": False,
                "snippet": f.source.snippet,
                "confidence": f.confidence,
            }
        )
    return rows


@router.post("/read")
def read_document(pick: Pick, request: Request) -> dict[str, Any]:
    # the path comes from the listing of the kit, never from the request
    path = demo._kit().get((pick.folder, pick.file))
    expected_file = path.with_suffix(".expected.json") if path else None
    if path is None or expected_file is None or not expected_file.is_file():
        raise HTTPException(404, "unknown document")
    expected: dict[str, Any] = json.loads(expected_file.read_text())
    row = next(
        r for r in demo.kit() if (r["folder"], r["file"]) == (pick.folder, pick.file)
    )
    data = path.read_bytes()
    pages = len(PdfReader(BytesIO(data)).pages) if path.suffix == ".pdf" else 1
    document = Document(id="D-bench", file=path.name, pages=pages)
    workflow = _workflow(request.app.state.store, row)
    reader: Reader = request.app.state.reader

    kind, t_classify, error = _timed(lambda: reader.classify(document, data))
    out: Extraction | None
    out, t_extract, extract_error = _timed(
        lambda: reader.extract(document, data, workflow)
    )
    fields = _fields(expected["fields"], out)
    want_items = expected.get("line_items", [])
    got_items = out.line_items if out else []
    return {
        "reader": reader.name,
        "workflow": workflow,
        "pages": pages,
        "variant": row["variant"],
        "error": error or extract_error,
        "seconds": {"classify": t_classify, "extract": t_extract},
        "type": {
            "expected": expected["document_type"],
            "read": kind or DocumentType.unknown.value,
            "right": kind == expected["document_type"],
        },
        "fields": fields,
        "counts": {
            s: sum(1 for f in fields if f["status"] == s)
            for s in ("right", "wrong", "missing", "extra")
        },
        "line_items": {
            "expected": len(want_items),
            "read": len(got_items),
            "right": len(want_items) == len(got_items)
            and all(
                compare(w.get(k), g.get(k))
                for w, g in zip(want_items, got_items, strict=True)
                for k in w
            ),
        },
    }

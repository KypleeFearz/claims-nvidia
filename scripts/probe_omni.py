"""Probe the hosted NVIDIA endpoint of Nemotron 3 Nano Omni (for docs/DECISIONS.md).

    cd backend && uv run python ../scripts/probe_omni.py format
    cd backend && uv run python ../scripts/probe_omni.py rate --per-minute 60 --max 80

`format`: one page of a mock document, six ways to ask for the same JSON, then two
repeats of the first way for the latency. `rate`: tiny text requests at a fixed pace
until the first 429. Settings: NVIDIA_KEY, VLM_BASE_URL, VLM_MODEL, from the
environment or from the `.env` at the repo root. The key, the headers and the request
body (base64) are never printed. No retries. Needs the network. Mock data only.
"""

import argparse
import base64
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx2 as httpx  # noqa: E402
import pypdfium2 as pdfium  # type: ignore[import-untyped]  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from app.docintel.schemas import SCHEMAS  # noqa: E402
from app.models import DocumentType  # noqa: E402

PDF = ROOT / "data/mock/claim-1-travel/booking-confirmation.pdf"
PHOTO = ROOT / "data/mock/hard/kit-anna-luggage-receipt-jacket-shoes-photo.jpg"
NAMES = ("NVIDIA_KEY", "VLM_BASE_URL", "VLM_MODEL")


def settings() -> dict[str, str]:
    file_env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                file_env[k.strip()] = v.strip().strip("'\"")
    values = {n: os.environ.get(n) or file_env.get(n, "") for n in NAMES}
    for name, value in values.items():
        if not value:
            sys.exit(f"Missing setting: {name} (environment or .env)")
    return values


def render_page(data: bytes, dpi: int = 150) -> tuple[bytes, tuple[int, int]]:
    """Page 1 of a PDF as PNG bytes, and its pixel size."""
    image = pdfium.PdfDocument(data)[0].render(scale=dpi / 72).to_pil()
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue(), image.size


def answer_schema(doc_type: DocumentType) -> dict[str, Any]:
    values = SCHEMAS[doc_type].model_json_schema()
    defs = values.pop("$defs", {})  # refs are relative to the root: move them up
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "values": values,
            "snippets": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["values", "snippets"],
        "additionalProperties": False,
    }
    if defs:
        schema["$defs"] = defs
    return schema


def prompt_text(schema: dict[str, Any]) -> str:
    return (
        "Read the whole page. Fill `values` following the schema. "
        "A value that is not on the page is null. Never guess. "
        "Dates are YYYY-MM-DD. Amounts are numbers. "
        "For every non-null value, give in `snippets` the exact text of the page "
        "it was read from, under the name of the field. Answer with JSON only.\n\n"
        f"Schema:\n{json.dumps(schema)}"
    )


def post(
    cfg: dict[str, str], body: dict[str, Any]
) -> tuple[httpx.Response | None, float]:
    start = time.perf_counter()
    try:
        response = httpx.post(
            f"{cfg['VLM_BASE_URL']}/chat/completions",
            json=body,
            headers={
                "Authorization": f"Bearer {cfg['NVIDIA_KEY']}",
                "Accept": "application/json",
            },
            timeout=120,
        )
    except httpx.HTTPError as exc:
        print(f"  network error: {type(exc).__name__}")
        return None, time.perf_counter() - start
    return response, time.perf_counter() - start


def norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def parse_json(content: str) -> tuple[Any, str]:
    try:
        return json.loads(content), "plain"
    except ValueError:
        pass
    stripped = content.strip().removeprefix("```json").removesuffix("```").strip()
    try:
        return json.loads(stripped), "after fence removal"
    except ValueError:
        return None, "no"


def report(
    name: str,
    response: httpx.Response,
    seconds: float,
    doc_type: DocumentType,
    page: str | None,
) -> None:
    print(f"[{name}] HTTP {response.status_code}, {seconds:.1f} s")
    if response.status_code != 200:
        print(f"  {response.text[:300]}")
        return
    payload = response.json()
    choice = payload["choices"][0]
    message = choice["message"]
    content = message.get("content") or ""
    usage = payload.get("usage") or {}
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    reasoning = usage.get("reasoning_tokens", reasoning)
    keys = [k for k in ("reasoning_content", "reasoning") if message.get(k)]
    lengths = {k: len(message[k]) for k in keys}
    print(
        f"  finish={choice.get('finish_reason')} tokens={usage.get('prompt_tokens')}"
        f"/{usage.get('completion_tokens')} reasoning_tokens={reasoning}"
    )
    print(
        f"  content_empty={not content.strip()} reasoning_field_chars={lengths or None}"
        f" fence_start={content.lstrip().startswith('```')}"
    )
    answer, how = parse_json(content)
    print(f"  json_parses={how}")
    if not isinstance(answer, dict):
        return
    values = answer.get("values")
    try:
        SCHEMAS[doc_type].model_validate_json(json.dumps(values))
        print("  values_valid=True")
    except ValueError as exc:
        errors: list[Any] = getattr(exc, "errors", lambda: [])()
        summary = [(".".join(map(str, e["loc"])), e["type"]) for e in errors]
        print(f"  values_valid=False, {len(errors)} errors: {summary}")
    print(f"  values: {json.dumps(values)[:600]}")
    snippets = answer.get("snippets")
    if isinstance(snippets, dict) and page is not None:
        missing = [k for k, v in snippets.items() if norm(str(v)) not in norm(page)]
        print(f"  snippets on page: {len(snippets) - len(missing)}/{len(snippets)}")
        print(f"  snippet keys not on page: {missing}")
    elif isinstance(snippets, dict):
        print(f"  snippets: {len(snippets)} (photo: no text layer to check)")


def run_format() -> None:
    cfg = settings()
    pdf_bytes = PDF.read_bytes()
    png, size = render_page(pdf_bytes)
    page_text = PdfReader(BytesIO(pdf_bytes)).pages[0].extract_text()
    photo = PHOTO.read_bytes()
    print(
        f"model={cfg['VLM_MODEL']} page render {size[0]}x{size[1]} px, {len(png)} bytes"
    )

    def data_url(mime: str, raw: bytes) -> str:
        return f"data:{mime};base64,{base64.b64encode(raw).decode()}"

    pdf_url, photo_url = data_url("image/png", png), data_url("image/jpeg", photo)
    booking, receipt = DocumentType.booking_confirmation, DocumentType.receipt
    schemas = {booking: answer_schema(booking), receipt: answer_schema(receipt)}
    json_schema = {"name": "answer", "schema": schemas[booking], "strict": True}
    off, on = {"enable_thinking": False}, {"enable_thinking": True}
    variants: list[tuple[str, DocumentType, str, dict[str, Any]]] = [
        ("1 prompt", booking, pdf_url, {"chat_template_kwargs": off}),
        (
            "2 json_schema",
            booking,
            pdf_url,
            {
                "response_format": {"type": "json_schema", "json_schema": json_schema},
                "chat_template_kwargs": off,
            },
        ),
        (
            "3 json_object",
            booking,
            pdf_url,
            {"response_format": {"type": "json_object"}, "chat_template_kwargs": off},
        ),
        (
            "4 guided_json",
            booking,
            pdf_url,
            {"nvext": {"guided_json": schemas[booking]}, "chat_template_kwargs": off},
        ),
        ("5 thinking_on", booking, pdf_url, {"chat_template_kwargs": on}),
        ("6 photo", receipt, photo_url, {"chat_template_kwargs": off}),
        ("1 prompt (repeat a)", booking, pdf_url, {"chat_template_kwargs": off}),
        ("1 prompt (repeat b)", booking, pdf_url, {"chat_template_kwargs": off}),
    ]
    for name, doc_type, url, extra in variants:
        thinking = extra["chat_template_kwargs"]["enable_thinking"]
        body = {
            "model": cfg["VLM_MODEL"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text(schemas[doc_type])},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }
            ],
            "temperature": 0.2,
            "max_tokens": 4096 if thinking else 2048,
            **extra,
        }
        response, seconds = post(cfg, body)
        if response is None:
            continue
        report(
            name,
            response,
            seconds,
            doc_type,
            None if doc_type == receipt else page_text,
        )
        if response.status_code == 429:
            print("  429: stop")
            return


def run_rate(per_minute: int, max_requests: int) -> None:
    cfg = settings()
    body = {
        "model": cfg["VLM_MODEL"],
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 4,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    gap = 60 / per_minute
    sent: list[float] = []
    first = time.perf_counter()
    for _ in range(max_requests):
        started = time.perf_counter()
        response, _ = post(cfg, body)
        sent.append(started)
        if response is None:
            return
        if response.status_code != 200:
            last_minute = sum(1 for t in sent if started - t <= 60)
            print(
                f"HTTP {response.status_code} after {len(sent) - 1} x 200, "
                f"{started - first:.1f} s since the first request, "
                f"{last_minute} sent in the last 60 s, "
                f"Retry-After={response.headers.get('retry-after')}"
            )
            print(f"  {response.text[:200]}")
            return
        time.sleep(max(0.0, gap - (time.perf_counter() - started)))
    elapsed = time.perf_counter() - first
    print(f"No 429: {len(sent)} x 200 in {elapsed:.1f} s ({per_minute}/min)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("format", help="six ways to ask for JSON, plus latency repeats")
    rate = sub.add_parser("rate", help="requests before the first 429")
    rate.add_argument("--per-minute", type=int, default=60)
    rate.add_argument("--max", type=int, default=80)
    args = parser.parse_args()
    if args.mode == "format":
        run_format()
    else:
        run_rate(args.per_minute, args.max)


if __name__ == "__main__":
    main()

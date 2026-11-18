"""OmniReader (issue #18): reads a document with Nemotron 3 Nano Omni.

Each full page goes to the model as an image. The answer follows the schema of
the type (issue #17) and gives, for each value, the text it was read from.
"""

import base64
import hashlib
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable
from datetime import date
from io import BytesIO
from itertools import islice
from typing import Any, NamedTuple

import httpx2 as httpx
import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader

from app.docintel.schemas import SCHEMAS
from app.models import Document, DocumentType, ExtractedField, Extraction, Source

log = logging.getLogger(__name__)  # status codes only: never a value or a snippet

DPI = 150
# Bounds (D80): a document may be long or a page huge, the work stays bounded. A
# document with more pages is read in part, and a person checks it.
MAX_PAGES = 10  # pages rendered and sent per document: at most 1 + 10 model calls
MAX_PIXELS = 16_000_000  # per image sent; A4 at 150 DPI is 2.2 MP
TIMEOUT = 90.0  # seconds, per model call
MAX_TOKENS = 2048
RETRIES = 2  # after a timeout, a 429 or a 5xx
WAITS = (2.0, 8.0)  # seconds before each retry, when no Retry-After
MAX_WAIT = 30.0
MAGIC = {b"%PDF-": "application/pdf", b"\x89PNG\r\n\x1a\n": "image/png"}
MAGIC[b"\xff\xd8\xff"] = "image/jpeg"
KNOWN = {t.value for t in DocumentType}
# Provisional, not calibrated (D80): they say on which side of the routing threshold
# (0.8) a field falls. Checked evidence passes; the model alone needs a person.
VERIFIED = 0.9  # the snippet is on the text layer of the page and holds the value
UNVERIFIED = 0.5  # no text layer: only the model says where the value is
MAX_SNIPPET = 120  # characters; a longer "snippet" (a paragraph) proves nothing
# unknown has no schema; the description of a damage photo is issue #25
NOTHING_TO_READ = {DocumentType.unknown, DocumentType.damage_photo}
EXTRACT = """Read the whole image. It is page {page} of {pages} of a {kind}.
Answer with JSON only, in this form: {{"values": {{...}}, "snippets": {{...}}}}.
`values` follows the JSON schema below. Every key is required. Use null for a \
value that is not on this page: never guess.
Dates are YYYY-MM-DD. Amounts are plain numbers: no currency, no thousands separator.
`line_items` lists every line of the table on this page, or [] when there is none.
For each value that is not null, `snippets` maps its key to the exact text of the \
page that shows it, copied character by character, as short as possible.
JSON schema of `values`:
{schema}"""
CLASSIFY = (
    "What kind of document is this page? Answer with JSON only: "
    '{"document_type": "<type>"}. The type is one of: '
    + ", ".join(t.value for t in DocumentType)
    + '. Answer "unknown" when it is none of them.'
)
CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"document_type": {"type": "string", "enum": sorted(KNOWN)}},
    "required": ["document_type"],
    "additionalProperties": False,
}


def answer_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema of a page answer: the values of the type (#17) and a
    snippet per value. `$defs` move to the root, where `#/$defs/...` resolves."""
    values = schema.model_json_schema()
    defs = values.pop("$defs", None)
    answer: dict[str, Any] = {
        "type": "object",
        "properties": {
            "values": values,
            "snippets": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["values", "snippets"],
        "additionalProperties": False,
    }
    if defs:
        answer["$defs"] = defs
    return answer


class Page(NamedTuple):
    image: bytes
    mime: str
    size: tuple[int, int]  # pixels of the image sent
    text: str  # the text layer of the page; "" for a scan or a photo


def _fit(width: float, height: float) -> float:
    """The factor (at most 1) that keeps an image within MAX_PIXELS, even when
    each side is rounded up: (width * f + 1) * (height * f + 1) <= MAX_PIXELS."""
    area, sides = max(width * height, 1.0), width + height
    root = math.sqrt(sides * sides + 4 * area * (MAX_PIXELS - 1))
    return min(1.0, (root - sides) / (2 * area))


def pages(data: bytes, first: int | None = None) -> list[Page]:
    """The first pages (MAX_PAGES by default) as whole images of at most MAX_PIXELS,
    or [] when the bytes are not a readable PDF or image."""
    first = MAX_PAGES if first is None else first
    mime = next((m for head, m in MAGIC.items() if data.startswith(head)), None)
    try:
        if mime == "application/pdf":
            pdf_pages = islice(PdfReader(BytesIO(data)).pages, first)
            texts = [p.extract_text() or "" for p in pdf_pages]
            pdf = pdfium.PdfDocument(data)
            try:
                found = []
                for i in range(min(len(pdf), first)):
                    width, height = pdf[i].get_size()  # points
                    scale = DPI / 72 * _fit(width * DPI / 72, height * DPI / 72)
                    image = pdf[i].render(scale=scale).to_pil()
                    png = BytesIO()
                    image.save(png, "PNG")
                    text = texts[i] if i < len(texts) else ""
                    found.append(Page(png.getvalue(), "image/png", image.size, text))
                return found
            finally:
                pdf.close()
        if mime is not None:
            with Image.open(BytesIO(data)) as image:
                size = image.size
                image.verify()
            factor = _fit(*size)
            if factor == 1.0:
                return [Page(data, mime, size, "")]
            # Pillow refuses to open a decompression bomb (above about 179 MP)
            with Image.open(BytesIO(data)) as image:
                image.thumbnail((int(size[0] * factor), int(size[1] * factor)))
                small = BytesIO()
                image.convert("RGB").save(small, "JPEG", quality=90)
                return [Page(small.getvalue(), "image/jpeg", image.size, "")]
    except Exception:  # pdfium, pypdf and Pillow raise many kinds of errors
        return []
    return []


def _length(data: bytes) -> int:
    """The number of pages of a document that `pages` could read."""
    if not data.startswith(b"%PDF-"):
        return 1
    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


def _json(content: str | None) -> Any:
    """The JSON of an answer, or None. One ```json fence around the whole answer
    is removed; nothing else is repaired."""
    if content is None:
        return None
    text = content.strip()
    if text.startswith("```"):
        first, _, rest = text.partition("\n")
        if first not in ("```", "```json") or not rest.rstrip().endswith("```"):
            return None
        text = rest.rstrip().removesuffix("```")
    try:
        return json.loads(text)
    except ValueError:
        return None


def _read(
    schema: type[BaseModel], answer: Any
) -> tuple[BaseModel, dict[str, Any], bool] | None:
    """(values, snippets, line items refused) of a page answer, or None when the
    answer as a whole cannot be trusted: not JSON, not the asked form, or no valid
    values even after the failing keys are dropped."""
    if not isinstance(answer, dict):
        return None
    values, snippets = answer.get("values"), answer.get("snippets")
    if not isinstance(values, dict) or not isinstance(snippets, dict):
        return None
    try:
        return schema.model_validate_json(json.dumps(values)), snippets, False
    except ValidationError as e:
        bad = {str(err["loc"][0]) for err in e.errors() if err["loc"]}
    # A key that fails is dropped, never converted: "1840" stays unread. An
    # unknown key is not stored and is never mapped to a known one.
    kept = {
        k: v for k, v in values.items() if k in schema.model_fields and k not in bad
    }
    for name in schema.model_fields:
        kept.setdefault(name, [] if name == "line_items" else None)
    try:
        return (
            schema.model_validate_json(json.dumps(kept)),
            snippets,
            "line_items" in bad,
        )
    except ValidationError:
        return None


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


# A number: "1,840.00", "1'840.00", "-1,500.00". A separator stands between two
# digits; a minus is a sign only where no letter or digit comes before it, so
# "WPR-482913" and "2026-08-04" hold no negative number.
NUMBER = re.compile(r"(?:(?<!\w)[-−])?\d(?:[',]?\d)*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    """The numbers of a text."""
    found = NUMBER.findall(text)
    return [float(n.replace(",", "").replace("'", "").replace("−", "-")) for n in found]


def _holds(text: str, value: Any) -> bool:
    """Does this text show the value? A number to the cent, a date as YYYY-MM-DD or
    DD.MM.YYYY, a text with no difference of case or spaces."""
    if isinstance(value, bool):
        return False
    if isinstance(value, date):
        return value.isoformat() in text or value.strftime("%d.%m.%Y") in text
    if isinstance(value, int | float):
        return any(abs(n - value) < 0.005 for n in _numbers(text))
    return _norm(str(value)) in _norm(text)


def _shown(snippet: str, text: str) -> bool:
    """Is the snippet on the page, with every number of the page that it touches
    whole? "250.00" is not shown by a page that only has "1,250.00", nor
    "1,500.00" by "-1,500.00"."""
    needle, page = _norm(snippet), _norm(text)
    if not needle:
        return False
    spans = [m.span() for m in NUMBER.finditer(page)]
    start = page.find(needle)
    while start != -1:
        end = start + len(needle)
        if all(start <= a and b <= end for a, b in spans if a < end and b > start):
            return True
        start = page.find(needle, start + 1)
    return False


def _on_page(line: dict[str, Any], text: str) -> bool:
    """Is every value of a line item on the text layer of the page? A page with no
    text layer confirms nothing."""
    return bool(text.strip()) and all(
        _holds(text, v) for v in line.values() if v is not None
    )


def _field(
    document_id: str,
    number: int,
    page: Page,
    name: str,
    value: Any,
    snippets: dict[str, Any],
) -> ExtractedField | None:
    """The field with its source, or None when its source cannot be given.

    With a text layer, the snippet must be on the page and hold the value: it is
    stored as checked evidence. With no text layer (a scan, a photo), nothing can
    check the snippet: the source is the whole page (a box) and a person checks it.
    """
    snippet = snippets.get(name)
    if not isinstance(snippet, str) or not 0 < len(snippet.strip()) <= MAX_SNIPPET:
        return None
    if not _holds(snippet, value):  # the answer contradicts itself
        return None
    if page.text.strip():
        if not _shown(snippet, page.text):  # an invented source
            return None
        source = Source(document_id=document_id, page=number, snippet=snippet.strip())
        confidence = VERIFIED
    else:
        width, height = page.size
        box = (0.0, 0.0, float(width), float(height))
        source = Source(document_id=document_id, page=number, box=box)
        confidence = UNVERIFIED
    stored = value.isoformat() if isinstance(value, date) else value
    return ExtractedField(name=name, value=stored, confidence=confidence, source=source)


def _retry_after(header: str | None, default: float) -> float:
    try:
        return min(max(float(header or ""), 0.0), MAX_WAIT)
    except ValueError:
        return default


class OmniReader:
    name = "omni"

    def __init__(
        self,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        env = os.environ
        for var in ("NVIDIA_KEY", "VLM_BASE_URL", "VLM_MODEL"):
            if not env.get(var):
                raise RuntimeError(f"CLAIMS_READER=omni needs {var} (see .env.example)")
        self.model = env["VLM_MODEL"]
        self._url = env["VLM_BASE_URL"].rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {env['NVIDIA_KEY']}"}
        self._client = client or httpx.Client(timeout=TIMEOUT)
        self._sleep = sleep
        self._types: dict[str, str] = {}  # SHA-256 of the bytes -> type

    def classify(self, document: Document, data: bytes) -> str:
        """From page 1. Only a type the model gave is kept, so a failure is retried
        on the next call; `extract` reuses the type, so both agree."""
        digest = hashlib.sha256(data).hexdigest()
        if digest in self._types:
            return self._types[digest]
        found = pages(data, first=1)
        if not found:
            return DocumentType.unknown.value
        answer = _json(self._post(CLASSIFY, found[0], CLASSIFY_SCHEMA))
        kind = answer.get("document_type") if isinstance(answer, dict) else None
        if kind not in KNOWN:
            return DocumentType.unknown.value
        self._types[digest] = str(kind)
        return str(kind)

    def _post(self, prompt: str, page: Page, schema: dict[str, Any]) -> str | None:
        """The content of the answer, or None. The answer must follow `schema`
        (`response_format`, D80). Retries a timeout, a 429 and a 5xx a few times
        with a bounded wait; any other error is final."""
        url = f"data:{page.mime};base64,{base64.b64encode(page.image).decode()}"
        form = {"name": "answer", "schema": schema, "strict": True}
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }
            ],
            "temperature": 0.2,
            "max_tokens": MAX_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": form},
        }
        status, wait = "none", 0.0
        for attempt in range(RETRIES + 1):
            if attempt:
                self._sleep(wait)
            wait = WAITS[min(attempt, len(WAITS) - 1)]
            try:
                r = self._client.post(self._url, json=body, headers=self._headers)
            except httpx.TransportError as e:  # a timeout too
                status = type(e).__name__
                continue
            status = str(r.status_code)
            if r.status_code == 200:
                try:
                    content = r.json()["choices"][0]["message"]["content"]
                except (ValueError, LookupError, TypeError):
                    content = None
                if isinstance(content, str) and content.strip():
                    return content
                status = "200 with no content"  # e.g. reasoning used every token
                break
            if r.status_code != 429 and r.status_code < 500:
                break
            wait = _retry_after(r.headers.get("retry-after"), wait)
        log.warning("omni call failed: %s (model %s)", status, self.model)
        return None

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        """The fields that have a source, and the line items when every line is
        confirmed. Never raises: a page that fails gives no field."""
        found = pages(data)
        kind = DocumentType(self.classify(document, data) if found else "unknown")
        schema = SCHEMAS.get(kind)
        if schema is None or kind in NOTHING_TO_READ:
            return Extraction(document_type=kind)
        has_lines = "line_items" in schema.model_fields
        fields: dict[str, ExtractedField] = {}
        lines: list[dict[str, Any]] = []
        lines_complete = True
        length = _length(data)
        all_read = len(found) == length  # False above MAX_PAGES
        first_seen: dict[str, Any] = {}
        disagree: set[str] = set()  # two pages give two values
        form = answer_schema(schema)
        for number, page in enumerate(found, 1):
            prompt = EXTRACT.format(
                page=number,
                pages=length,
                kind=kind.value,
                schema=json.dumps(schema.model_json_schema()),
            )
            answer = _read(schema, _json(self._post(prompt, page, form)))
            if answer is None:  # this page may hold values and lines nobody read
                all_read = False
                continue
            values, snippets, lines_refused = answer
            for name in schema.model_fields:
                value = getattr(values, name)
                if name == "line_items" or value is None:
                    continue
                same = _norm(value) if isinstance(value, str) else value
                if first_seen.setdefault(name, same) != same:
                    disagree.add(name)
                if name in fields:
                    continue
                field = _field(document.id, number, page, name, value, snippets)
                if field is not None:
                    fields[name] = field
            if has_lines:
                read = [
                    line.model_dump()
                    for line in values.line_items  # type: ignore[attr-defined]
                    if any(v is not None for v in line.model_dump().values())
                ]
                if lines_refused or not all(_on_page(i, page.text) for i in read):
                    lines_complete = False
                lines += read
        # Not final, so a person checks it (D80): every field when a page was not
        # read (it may hold a value that changes the claim); a field that two pages
        # give differently; the total when the list of lines is not complete.
        doubt = set(fields) if not all_read else disagree
        if has_lines and not (lines_complete and all_read):
            # An incomplete list would look complete to T003 and to the settlement:
            # no list at all.
            lines = []
            doubt = doubt | {"total"}
        for name in doubt & set(fields):
            field = fields[name]
            fields[name] = field.model_copy(
                update={"confidence": min(field.confidence, UNVERIFIED)}
            )
        return Extraction(
            document_type=kind, fields=list(fields.values()), line_items=lines
        )

"""The N1 safety set (issue #125), read by the reader from the saved answers of the
model: no network, no key.

On 2026-11-19 the reader read the nine documents of `data/mock/n1/` with the real
model: two prompts (`v1`, `schema-only`), three runs, one call to classify and one
call for the page per document. The raw answers are in `fixtures/n1/answers.json`
(the content of each answer, nothing else). Here each case is read again, with a
transport that gives the saved answers in order and fails on any other request. The
reader of today is run on the answers of the model, so what is checked is the code of
the reader (the checks of the snippets, the confidence), not the model.

Before #125 the reader stores the values that the model makes up for a field that the
page does not hold. The safety test says so and is marked `xfail`: remove the marker
when the safeguards are built.
"""

import functools
import json
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from app.docintel import omni
from app.docintel.compare import compare_field, field_kind
from app.docintel.omni import OmniReader, OmniSettings
from app.fraud.routing import LOW_CONFIDENCE
from app.models import Document, Extraction
from tests.reader_contract import MOCK

N1 = MOCK / "n1"
FIXTURES = Path(__file__).parent / "fixtures" / "n1"
SAVED: dict[str, Any] = json.loads((FIXTURES / "answers.json").read_text("utf-8"))
ANSWERS: dict[str, dict[str, dict[str, dict[str, str]]]] = SAVED["answers"]
BEFORE: dict[str, Any] = json.loads(
    (FIXTURES / "stored-before.json").read_text("utf-8")
)
PROMPTS = ["v1", "schema-only"]
RUNS = ["1", "2", "3"]
FILES = sorted(ANSWERS["v1"]["1"])
CASES = [(p, r, f) for p in PROMPTS for r in RUNS for f in FILES]
IDS = [f"{p}-run{r}-{f.split('-')[0]}" for p, r, f in CASES]
PLACEHOLDERS = {"not answerable", "null", "n/a"}


def key_values(file: str) -> dict[str, Any]:
    """The answer key of a document: field -> expected value (None: absent)."""
    key = json.loads((N1 / file).with_suffix(".expected.json").read_text("utf-8"))
    return {f["name"]: f["value"] for f in key["fields"]}


@functools.cache
def replay(prompt: str, run: str, file: str) -> Extraction:
    """The reader of today on one saved case: first the answer to classify, then the
    answer for the page. Any other request fails the case, and so does an answer that
    is not used."""
    saved = ANSWERS[prompt][run][file]
    queue = [saved["classify"], saved["page"]]
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if not queue or str(request.url) != "http://replay.invalid/v1/chat/completions":
            raise AssertionError(f"unexpected request {len(seen)}: {request.url}")
        text = json.loads(request.content)["messages"][0]["content"][0]["text"]
        if (text == omni.CLASSIFY) != (len(seen) == 1):  # classify first, then the page
            raise AssertionError(f"request {len(seen)} is not the saved call")
        content = queue.pop(0)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handle))
    with pytest.MonkeyPatch.context() as env:
        env.setenv("NVIDIA_KEY", "test-not-a-key")
        env.setenv("VLM_BASE_URL", "http://replay.invalid/v1")
        env.setenv("VLM_MODEL", SAVED["source"]["model"])
        reader = OmniReader(client=client, settings=OmniSettings(prompt=prompt))
    out = reader.extract(
        Document(id=file, file=file), (N1 / file).read_bytes(), "travel"
    )
    assert queue == [], "a saved answer was not used"
    assert len(seen) == 2, "the reader made another request"
    return out


def test_the_saved_answers_are_the_nine_documents_two_prompts_three_runs() -> None:
    files = sorted(p.name for p in N1.iterdir() if p.suffix in {".pdf", ".jpg"})
    assert len(files) == 9
    assert sorted(ANSWERS) == sorted(PROMPTS)
    for prompt in PROMPTS:
        assert sorted(ANSWERS[prompt]) == RUNS
        for run in RUNS:
            assert sorted(ANSWERS[prompt][run]) == files
            for pair in ANSWERS[prompt][run].values():
                assert sorted(pair) == ["classify", "page"]
    assert SAVED["source"]["calls"] == 108 == len(PROMPTS) * len(RUNS) * 9 * 2


@pytest.mark.parametrize(("prompt", "run", "file"), CASES, ids=IDS)
def test_the_replay_gives_the_fields_of_the_live_run(
    prompt: str, run: str, file: str
) -> None:
    """CHARACTERISATION, BEFORE #125: this is what the reader stored on the live run
    of 2026-11-19 (`fixtures/n1/stored-before.json`, with the made-up values in it).
    It will change when the safeguards are built: then replace the expected file by
    the new one, and say why in the pull request."""
    saved = BEFORE[prompt][run][file]
    out = replay(prompt, run, file)
    assert out.document_type == saved["document_type"]
    assert [[f.name, f.value, f.confidence] for f in out.fields] == saved["fields"]


def _incorrect(prompt: str) -> list[tuple[str, str, str, Any, float, bool]]:
    """Each incorrect stored value: (run, file, field, value, confidence, absent).
    Absent: the page does not hold the field (the key says null), so any value is
    incorrect. Present: the value is not the value of the key."""
    found = []
    for run in RUNS:
        for file in FILES:
            out = replay(prompt, run, file)
            expected = key_values(file)
            for f in out.fields:
                absent = expected.get(f.name) is None
                kind = field_kind(out.document_type, f.name)
                right = (
                    not absent
                    and kind is not None
                    and compare_field(kind, expected[f.name], f.value).status == "right"
                )
                if not right:
                    found.append((run, file, f.name, f.value, f.confidence, absent))
    return found


def _lines(found: list[tuple[str, str, str, Any, float, bool]]) -> str:
    return "\n".join(
        f"run {run}: {file}: {name} = {value!r}, confidence {confidence} "
        f"({'absent' if absent else 'present'} field)"
        for run, file, name, value, confidence, absent in found
    )


@pytest.mark.xfail(
    strict=True, reason="issue #125: the reader safeguards are not built yet"
)
@pytest.mark.parametrize("prompt", PROMPTS)
def test_no_incorrect_value_is_trusted(prompt: str) -> None:
    """THE SAFETY TEST of #125 (its acceptance criterion). No incorrect value, in a
    field that the page does not hold or in a field that it holds, may be stored at
    LOW_CONFIDENCE or more: above it nobody looks at the value again. An incorrect
    value below it is allowed: a handler checks it (a scan or a photo has no text
    layer, so nothing can check its label). All incorrect values are counted apart,
    in the evaluation."""
    trusted = [i for i in _incorrect(prompt) if i[4] >= LOW_CONFIDENCE]
    head = f"prompt {prompt}: {len(trusted)} incorrect values trusted:\n"
    assert not trusted, head + _lines(trusted)


@pytest.mark.xfail(
    strict=True, reason="issue #125: the reader safeguards are not built yet"
)
@pytest.mark.parametrize("prompt", PROMPTS)
def test_no_placeholder_is_stored(prompt: str) -> None:
    """Issue #125: a text such as "Not answerable", "null" or "n/a" is no value. The
    field is not stored, at any confidence."""
    stored = [
        i
        for i in _incorrect(prompt)
        if isinstance(i[3], str) and i[3].strip().casefold() in PLACEHOLDERS
    ]
    assert not stored, f"prompt {prompt}: {len(stored)} placeholders stored:\n" + (
        _lines(stored)
    )


@pytest.mark.parametrize(("prompt", "run", "file"), CASES, ids=IDS)
def test_right_present_fields_stay_right_and_verified(
    prompt: str, run: str, file: str
) -> None:
    """A safeguard must not cost a good value: every present field that the live run
    stored right, at LOW_CONFIDENCE or more, is still stored right at that level."""
    expected = key_values(file)
    document_type = BEFORE[prompt][run][file]["document_type"]
    good = []
    for name, value, confidence in BEFORE[prompt][run][file]["fields"]:
        kind = field_kind(document_type, name)
        if (
            expected.get(name) is not None
            and kind is not None
            and compare_field(kind, expected[name], value).status == "right"
            and confidence >= LOW_CONFIDENCE
        ):
            good.append(name)
    now = {f.name: f for f in replay(prompt, run, file).fields}
    for name in good:
        kind = field_kind(document_type, name)
        assert kind is not None and name in now, f"{file}: {name} is lost"
        assert compare_field(kind, expected[name], now[name].value).status == "right"
        assert now[name].confidence >= LOW_CONFIDENCE, f"{file}: {name} is not verified"


def test_the_replay_uses_the_prompts_of_the_live_run() -> None:
    """The config ids of the live run are the ones of these settings: the replay reads
    the answers of the same requests (the text of the prompts and the form of the
    answers have not changed since)."""
    ids = SAVED["source"]["config_ids"]
    assert {p: omni.config_id(OmniSettings(prompt=p)) for p in PROMPTS} == ids

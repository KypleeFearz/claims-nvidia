"""The report of the evaluation harness (issue #19): where it goes, the command, and
the HTTP numbers of the Omni reader. No network: the Omni reader gets a stub."""

import json
import os
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from app.docintel import evaluate as ev
from app.docintel.fixture import FixtureReader
from tests.test_evaluate import KEY, StubReader, small_kit


def test_the_report_goes_to_latest_json_and_a_dated_copy(tmp_path: Path) -> None:
    report = ev.evaluate(FixtureReader)
    copy = ev.save_report(report, tmp_path)

    assert copy.parent == tmp_path and copy.name.startswith("evaluation-fixture-")
    latest = tmp_path / "latest.json"
    assert json.loads(latest.read_text(encoding="utf-8")) == report
    assert copy.read_text(encoding="utf-8") == latest.read_text(encoding="utf-8")
    assert ev.load_latest(tmp_path) == report
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [copy.name, "latest.json"]
    )


def test_a_failed_write_keeps_the_old_report_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = ev.evaluate(FixtureReader)
    ev.save_report(old, tmp_path)

    def broken(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        ev.save_report(ev.evaluate(FixtureReader) | {"reader": "other"}, tmp_path)
    assert ev.load_latest(tmp_path) == old
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (None, FileNotFoundError),
        ("{half a rep", ValueError),
        ("[1, 2]", ValueError),
        ('{"schema_version": 2}', ValueError),
    ],
)
def test_a_missing_or_broken_latest_report_is_refused(
    tmp_path: Path, text: str | None, error: type[Exception]
) -> None:
    if text is not None:
        (tmp_path / "latest.json").write_text(text)
    with pytest.raises(error):
        ev.load_latest(tmp_path)


def test_the_report_folder_comes_from_claims_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAIMS_EVALUATION", str(tmp_path / "x"))
    assert ev.report_dir() == tmp_path / "x"
    monkeypatch.delenv("CLAIMS_EVALUATION")
    assert ev.report_dir() == Path(".dev-data/evaluation")


def test_tests_never_write_the_real_report_folder() -> None:
    """conftest.py points CLAIMS_EVALUATION to a temporary folder."""
    assert ".dev-data" not in str(ev.report_dir())


def test_the_command_writes_the_report_and_prints_a_summary(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "copy.json"
    assert ev.main(["--reader", "fixture", "--output", str(out)]) == 0

    printed = capsys.readouterr().out
    assert "Reader fixture; 1 run(s)" in printed
    assert "| **All** |" in printed and "| kind: scan |" in printed
    report = ev.load_latest()
    assert report["command"].endswith("--reader fixture --output " + str(out))
    assert report["settings"] == {"reader": "fixture"} and report["http"] is None
    assert json.loads(out.read_text(encoding="utf-8")) == report


def test_the_command_says_when_an_answer_key_is_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY, "b.pdf": KEY | {"fields": 1}})
    stub: Any = lambda: (lambda: StubReader({}), {"reader": "stub"}, None)  # noqa: E731
    monkeypatch.setitem(ev.READERS, "fixture", stub)
    assert ev.main(["--reader", "fixture"]) == 1  # the report is written, not complete
    assert ev.load_latest()["complete"] is False


def test_a_reader_that_cannot_start_writes_no_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY, "b.pdf": "{"})  # the fixture
    assert ev.main(["--reader", "fixture"]) == 2  # reader reads every key at start
    assert "JSONDecodeError" in capsys.readouterr().err
    assert not (ev.report_dir() / "latest.json").exists()


@pytest.mark.parametrize(
    "argv", [["--reader", "nope"], ["--reader", "fixture", "--runs", "0"], []]
)
def test_the_command_refuses_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        ev.main(argv)
    assert stop.value.code == 2


def test_omni_with_no_key_writes_no_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NVIDIA_KEY", raising=False)
    assert ev.main(["--reader", "omni"]) == 2
    assert "needs NVIDIA_KEY" in capsys.readouterr().err
    assert not (ev.report_dir() / "latest.json").exists()


# ---------------------------------------------------------------- Omni, with a stub

SECRET = "test-key-of-the-harness"


class Endpoint:
    """A fake chat completions endpoint: a 429 first, then a type for each classify
    call and null values for each page."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, text="{}")
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        if "document_type" in schema["properties"]:
            content: Any = {"document_type": "booking_confirmation"}
        else:
            values = dict.fromkeys(KEY_NAMES) | {"line_items": []}
            content = {"values": values, "snippets": {}}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
        )


KEY_NAMES = ["booking_reference", "traveller_name", "departure_date", "trip_price"]


def test_omni_counts_its_requests_and_429s_and_keeps_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY})  # one PDF of two blank pages
    monkeypatch.setenv("NVIDIA_KEY", SECRET)
    monkeypatch.setenv("VLM_BASE_URL", "https://user:pw@vlm.test/v1?token=x")
    monkeypatch.setenv("VLM_MODEL", "omni-test")
    endpoint, waits = Endpoint(), list[float]()
    make, settings, meter = ev._omni(httpx.MockTransport(endpoint), waits.append)

    report = ev.evaluate(make, runs=2, settings=settings, meter=meter)

    # run 1: the 429, its retry (classify), page 1, page 2; run 2: a new reader
    # (no type in its cache): classify, page 1, page 2
    first, second = (r["http"] for r in report["run_details"])
    assert first == {
        "requests": 4,
        "by_status": {"200": 3, "429": 1},
        "transport_errors": 0,
        "http_429": 1,
    }
    assert second["requests"] == 3 and second["http_429"] == 0
    assert report["http"]["requests"] == 7 and report["http"]["http_429"] == 1
    assert waits == [0.0]  # Retry-After: 0 was followed
    s = report["settings"]
    assert s["reader"] == "omni" and s["model"] == "omni-test"
    assert s["endpoint"] == "https://vlm.test/v1"  # no user, password or query
    assert s["dpi"] == 150 and s["max_tokens"] == 2048
    # what the request bodies really held, apart from the configuration
    assert s["sent"] == {
        "enable_thinking": [False],
        "max_tokens": [2048],
        "model": ["omni-test"],
        "response_format": ["json_schema"],
        "temperature": [0.2],
    }
    # every field is missing: the model gave null for each
    assert report["pooled"]["fields"]["missing"] == 8
    assert report["pooled"]["documents"]["classified_right"] == 2
    text = ev.dump(report)
    for secret in (SECRET, "Bearer", "Authorization", "base64", "pw@"):
        assert secret not in text


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("https://integrate.api.nvidia.com/v1", "https://integrate.api.nvidia.com/v1"),
        ("https://u:p@host.test:8443/v1?key=x#f", "https://host.test:8443/v1"),
        (None, None),
    ],
)
def test_the_endpoint_keeps_host_port_and_path_only(url: str | None, kept: str) -> None:
    assert ev._endpoint(url) == kept


def test_a_broken_index_of_the_kit_is_an_error_not_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY})
    (tmp_path / "mock" / "index.json").write_text("{")
    assert ev.main(["--reader", "fixture"]) == 2
    assert "error:" in capsys.readouterr().err
    assert not (ev.report_dir() / "latest.json").exists()


def test_a_copy_that_cannot_be_written_is_an_error_after_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "a-file"
    folder.write_text("not a folder")
    assert ev.main(["--reader", "fixture", "--output", str(folder / "x.json")]) == 2
    assert "error:" in capsys.readouterr().err
    assert ev.load_latest()["reader"] == "fixture"  # the report itself is written

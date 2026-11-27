"""The report of the evaluation harness (issue #19): where it goes, the command, and
the HTTP numbers of the Omni reader. No network: the Omni reader gets a stub."""

import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from app.docintel import evaluate as ev
from app.docintel import omni
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
        # issue #20: the stub answers give no usage and no finish reason
        "tokens": {
            "prompt": 0,
            "completion": 0,
            "reasoning": 0,
            "no_usage": 3,
            "no_reasoning": 3,
        },
        "finish_reasons": {"none": 3},
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


# ---------------------------------------------------------------- settings (#20)


def omni_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: Any = None,
    options: Any = None,
) -> dict[str, Any]:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY})
    monkeypatch.setenv("NVIDIA_KEY", SECRET)
    monkeypatch.setenv("VLM_BASE_URL", "https://vlm.test/v1")
    monkeypatch.setenv("VLM_MODEL", "omni-test")
    transport = httpx.MockTransport(endpoint or Endpoint())
    make, settings, meter = ev._omni(transport, lambda s: None, options)
    return ev.evaluate(make, settings=settings, meter=meter)


def test_the_report_records_the_settings_of_issue_18_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = omni_report(tmp_path, monkeypatch)["settings"]
    assert s["dpi"] == 150 and s["max_tokens"] == 2048 and s["timeout_seconds"] == 90
    assert s["temperature"] == 0.2 and s["thinking"] is False
    assert s["reasoning_budget"] is None and s["prompt"] == "v1"
    assert s["config_id"] == omni.config_id(omni.ISSUE_18)
    assert s["prompt_sha256"] == omni.prompt_sha256("v1")


def test_the_report_records_other_settings_and_what_was_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = omni.OmniSettings(
        dpi=72, temperature=0.0, thinking=True, reasoning_budget=512, max_tokens=3584
    )
    s = omni_report(tmp_path, monkeypatch, options=options)["settings"]
    assert (s["dpi"], s["temperature"], s["thinking"]) == (72, 0.0, True)
    assert s["reasoning_budget"] == 512 and s["max_tokens"] == 3584
    assert s["config_id"] == omni.config_id(options) != omni.config_id(omni.ISSUE_18)
    assert s["sent"]["temperature"] == [0.0]
    assert s["sent"]["enable_thinking"] == [True]
    assert s["sent"]["reasoning_budget"] == [512]


def test_each_setting_and_each_prompt_gives_its_own_config_id() -> None:
    ids = {omni.config_id(omni.OmniSettings(prompt=p)) for p in omni.PROMPTS}
    ids |= {omni.config_id(omni.OmniSettings(dpi=d)) for d in (72, 100, 200)}
    ids.add(omni.config_id(omni.OmniSettings(temperature=0.0)))
    assert len(ids) == len(omni.PROMPTS) + 4
    assert len({omni.prompt_sha256(p) for p in omni.PROMPTS}) == len(omni.PROMPTS)
    # the same setting written two ways is one configuration
    zero = omni.OmniSettings(temperature=0)
    assert omni.config_id(zero) == omni.config_id(omni.OmniSettings(temperature=0.0))


def test_two_configs_are_never_combined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = omni_report(tmp_path / "a", monkeypatch)
    other = omni_report(tmp_path / "b", monkeypatch, options=omni.OmniSettings(dpi=72))
    paths = []
    for name, report in (("first.json", first), ("other.json", other)):
        (tmp_path / name).write_text(ev.dump(report), encoding="utf-8")
        paths.append(tmp_path / name)
    with pytest.raises(ev.BadCombine, match="other.json: the settings differ"):
        ev.combine(paths)


def test_a_report_written_before_issue_20_still_combines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report of #19 has no config, no tokens and no finish reasons."""
    report = omni_report(tmp_path, monkeypatch)
    old = json.loads(ev.dump(report))
    for key in ("temperature", "thinking", "reasoning_budget", "prompt"):
        del old["settings"][key]
    del old["settings"]["prompt_sha256"], old["settings"]["config_id"]
    del old["reader_code_sha256"]
    for run in old["run_details"]:
        del run["http"]["tokens"], run["http"]["finish_reasons"]
    paths = []
    for i in (1, 2):
        old["run_details"][0]["run_id"] = f"old-run-{i}"
        (tmp_path / f"old-{i}.json").write_text(json.dumps(old), encoding="utf-8")
        paths.append(tmp_path / f"old-{i}.json")
    combined = ev.combine(paths)
    assert combined["runs"] == 2 and combined["settings"] == old["settings"]
    assert combined["http"]["requests"] == 2 * old["http"]["requests"]
    assert "tokens" not in combined["http"]


class Counted(Endpoint):
    """The fake endpoint, with the usage and the finish reason of a real answer."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = super().__call__(request)
        if response.status_code != 200:
            return response
        body = response.json()
        body["choices"][0]["finish_reason"] = "length" if self.calls == 3 else "stop"
        body["usage"] = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "completion_tokens_details": {"reasoning_tokens": 20},
        }
        return httpx.Response(200, json=body)


def test_the_meter_counts_the_tokens_and_the_finish_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = omni_report(tmp_path, monkeypatch, Counted())
    http = report["run_details"][0]["http"]
    # the 429, then classify, page 1, page 2: three answers with a usage
    assert http["tokens"] == {
        "prompt": 3000,
        "completion": 150,
        "reasoning": 60,
        "no_usage": 0,
        "no_reasoning": 0,
    }
    assert http["finish_reasons"] == {"length": 1, "stop": 2}
    assert report["http"]["tokens"] == http["tokens"]
    for secret in (SECRET, "base64", "snippets"):
        assert secret not in ev.dump(report)


GOOD = {"prompt_tokens": 1000, "completion_tokens": 50}


@pytest.mark.parametrize(
    ("body", "tokens", "reason"),
    [
        # a reasoning count of 0 is a count: it is not "not reported"
        (
            {"usage": GOOD | {"completion_tokens_details": {"reasoning_tokens": 0}}},
            (1000, 50, 0, 0, 0),
            "none",
        ),
        # the endpoint does not always give the details of the completion
        ({"usage": GOOD}, (1000, 50, 0, 0, 1), "none"),
        (
            {"usage": GOOD | {"completion_tokens_details": None}},
            (1000, 50, 0, 0, 1),
            "none",
        ),
        (
            {"usage": GOOD | {"completion_tokens_details": {"reasoning_tokens": True}}},
            (1000, 50, 0, 0, 1),
            "none",
        ),
        (
            {"usage": GOOD | {"completion_tokens_details": {"reasoning_tokens": "9"}}},
            (1000, 50, 0, 0, 1),
            "none",
        ),
        # a usage that cannot be used adds nothing and is counted apart
        ({"usage": ["1000"]}, (0, 0, 0, 1, 1), "none"),
        ({"usage": None}, (0, 0, 0, 1, 1), "none"),
        ({"usage": {"prompt_tokens": 1000}}, (0, 0, 0, 1, 1), "none"),
        ({"usage": GOOD | {"completion_tokens": True}}, (0, 0, 0, 1, 1), "none"),
        ({"usage": GOOD | {"prompt_tokens": -1}}, (0, 0, 0, 1, 1), "none"),
        ("not json {", (0, 0, 0, 1, 1), "none"),
        ([1, 2], (0, 0, 0, 1, 1), "none"),
        # the finish reason
        ({"choices": [{"finish_reason": "stop"}]}, (0, 0, 0, 1, 1), "stop"),
        ({"choices": [{"finish_reason": None}]}, (0, 0, 0, 1, 1), "none"),
        ({"choices": [{"finish_reason": ""}]}, (0, 0, 0, 1, 1), "none"),
        ({"choices": []}, (0, 0, 0, 1, 1), "none"),
        ({"choices": ["stop"]}, (0, 0, 0, 1, 1), "none"),
    ],
)
def test_the_meter_counts_what_an_answer_reports_and_no_more(
    body: Any, tokens: tuple[int, ...], reason: str
) -> None:
    meter = ev.HttpMeter()
    content = body if isinstance(body, str) else json.dumps(body)
    meter._response(httpx.Response(200, content=content.encode()))
    names = ("prompt", "completion", "reasoning", "no_usage", "no_reasoning")
    assert meter.tokens == dict(zip(names, tokens, strict=True))
    assert meter.finish_reasons == {reason: 1}


def test_the_meter_reads_no_answer_that_is_not_a_200() -> None:
    meter = ev.HttpMeter()
    meter._response(httpx.Response(429, json={"usage": GOOD}))
    assert meter.by_status == {"429": 1} and meter.finish_reasons == {}
    assert set(meter.tokens.values()) == {0}


@pytest.mark.parametrize(
    ("pairs", "settings"),
    [
        ([], omni.ISSUE_18),
        (["dpi=72", "temperature=0"], omni.OmniSettings(dpi=72, temperature=0.0)),
        (
            ["thinking=on", "reasoning_budget=512", "max_tokens=3584", "timeout=240"],
            omni.OmniSettings(
                thinking=True, reasoning_budget=512, max_tokens=3584, timeout=240.0
            ),
        ),
        (["prompt=example", "thinking=off"], omni.OmniSettings(prompt="example")),
    ],
)
def test_omni_options_give_the_settings(pairs: list[str], settings: Any) -> None:
    assert ev.omni_options(pairs) == settings


@pytest.mark.parametrize(
    "pairs",
    [
        ["nope=1"],
        ["dpi"],
        ["dpi=abc"],
        ["dpi=72", "dpi=100"],
        ["thinking=maybe"],
        ["dpi=10"],
        ["reasoning_budget=512"],  # with no thinking
        ["timeout=nan"],
        ["timeout=inf"],
        ["temperature=nan"],
        ["max_tokens=65537"],
    ],
)
def test_bad_omni_options_are_refused(pairs: list[str]) -> None:
    with pytest.raises(ValueError):
        ev.omni_options(pairs)


def test_every_setting_has_an_omni_option() -> None:
    assert set(ev.OPTIONS) == {f.name for f in fields(omni.OmniSettings)}


def test_the_command_refuses_omni_options_for_another_reader() -> None:
    with pytest.raises(SystemExit) as stop:
        ev.main(["--reader", "fixture", "--omni", "dpi=72"])
    assert stop.value.code == 2


def test_the_command_refuses_a_bad_omni_option_and_writes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert ev.main(["--reader", "omni", "--omni", "dpi=10"]) == 2
    assert "dpi must be 50 to 300" in capsys.readouterr().err
    assert not (ev.report_dir() / "latest.json").exists()


def test_the_command_runs_omni_with_the_options_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole path of the command: --omni, the reader, the requests, the report.
    Only the transport is a stub."""
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY})
    monkeypatch.setenv("NVIDIA_KEY", SECRET)
    monkeypatch.setenv("VLM_BASE_URL", "https://vlm.test/v1")
    monkeypatch.setenv("VLM_MODEL", "omni-test")
    endpoint, real = Counted(), ev._omni

    def stubbed(**kwargs: Any) -> Any:
        return real(httpx.MockTransport(endpoint), lambda s: None, **kwargs)

    monkeypatch.setattr(ev, "_omni", stubbed)
    argv = ["--reader", "omni", "--omni", "dpi=72", "--omni", "prompt=described"]
    argv += ["--omni", "temperature=0"]
    assert ev.main(argv) == 0

    report = ev.load_latest()
    expected = omni.OmniSettings(dpi=72, prompt="described", temperature=0.0)
    s = report["settings"]
    assert (s["dpi"], s["prompt"], s["temperature"]) == (72, "described", 0.0)
    assert s["config_id"] == omni.config_id(expected)
    assert s["prompt_sha256"] == omni.prompt_sha256("described")
    assert s["sent"]["temperature"] == [0.0] and s["sent"]["max_tokens"] == [2048]
    assert report["command"].endswith(" ".join(argv))
    assert endpoint.calls == 4  # the 429, classify, page 1, page 2
    assert report["http"]["tokens"]["prompt"] == 3000


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

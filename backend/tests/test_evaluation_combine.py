"""`--combine` (issue #19): the runs of saved reports, added into one report.

No reader runs: the counts are made again from the results of each document of each
run, so three runs saved in two reports give the same numbers as one report of
three runs. Reports that were not made in the same conditions are refused, and
then nothing is written."""

import copy
import hashlib
import itertools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.docintel import evaluate as ev
from app.docintel.fixture import FixtureReader
from tests.test_evaluate import KEY, RIGHT, StubReader, answer, small_kit

Json = dict[str, Any]


def readers() -> Callable[[], StubReader]:
    """One new reader per call; the second one reads a wrong price on a.pdf."""
    made: list[StubReader] = []

    def make() -> StubReader:
        wrong = answer(RIGHT | {"trip_price": (1.0, 2)})
        made.append(StubReader({"a.pdf": wrong} if len(made) == 1 else {}))
        return made[-1]

    return make


def run(make: Callable[[], StubReader], runs: int) -> Json:
    tick = itertools.count()  # each read of the clock is one second later
    return ev.evaluate(make, runs, clock=lambda: float(next(tick)))


def save(folder: Path, name: str, report: Json) -> Path:
    path = folder / name
    path.write_text(ev.dump(report), encoding="utf-8")
    return path


@pytest.fixture
def kit_of_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    small_kit(tmp_path / "data", monkeypatch, {"a.pdf": KEY, "b.pdf": KEY})
    out = tmp_path / "reports"
    out.mkdir()
    return out


def test_one_run_and_two_runs_give_the_numbers_of_three_runs(kit_of_two: Path) -> None:
    make = readers()
    first, second = run(make, 1), run(make, 2)  # the wrong price is in run 2 of 3
    three = run(readers(), 3)  # the same readers, in one report

    combined = ev.combine(
        [save(kit_of_two, "r1.json", first), save(kit_of_two, "r2.json", second)]
    )

    assert combined["runs"] == 3
    assert [r["run"] for r in combined["run_details"]] == [1, 2, 3]
    assert [r["source"] for r in combined["run_details"]] == [
        {"report": "r1.json", "run": 1},
        {"report": "r2.json", "run": 1},
        {"report": "r2.json", "run": 2},
    ]
    assert combined["pooled"] == three["pooled"]
    assert combined["summary"] == three["summary"]
    s = combined["summary"]
    assert s["accuracy"] == {"mean": 0.9583, "min": 0.875, "max": 1.0, "spread": 0.125}
    assert s["pass_rate"] == {"mean": 0.8333, "min": 0.5, "max": 1.0, "spread": 0.5}
    f = combined["pooled"]["fields"]
    assert (f["expected"], f["right"], f["wrong"]) == (24, 23, 1)  # counts, added
    assert f["accuracy"] == round(23 / 24, 4)
    assert [x["file"] for x in combined["combined_from"]] == ["r1.json", "r2.json"]
    assert combined["reader"] == "stub" and combined["settings"] == first["settings"]


def test_the_runs_are_counted_again_from_their_documents(kit_of_two: Path) -> None:
    """A stored total is never trusted: it is made again, and it must agree."""
    make = readers()
    first, second = run(make, 1), run(make, 1)
    for r in (first, second):  # as an old report: no new share in the totals
        for detail in r["run_details"]:
            del detail["overall"]["documents"]["classification_accuracy"]
    combined = ev.combine(
        [save(kit_of_two, "a.json", first), save(kit_of_two, "b.json", second)]
    )
    overall = combined["run_details"][1]["overall"]
    assert overall["fields"]["wrong"] == 1
    assert overall["documents"]["classification_accuracy"] == 1.0


def test_a_report_of_an_older_form_is_counted_from_its_field_rows(
    kit_of_two: Path,
) -> None:
    """A report written before the count `read` existed: its counts are made again
    from the rows of its fields, so the combined numbers have every count."""
    make = readers()
    old, new = run(make, 1), run(make, 1)
    for detail in old["run_details"]:
        for part in ("fields",):
            del detail["overall"][part]["read"]
        for d in detail["documents"]:
            del d["counts"]["read"]
    combined = ev.combine(
        [save(kit_of_two, "old.json", old), save(kit_of_two, "new.json", new)]
    )
    f = combined["pooled"]["fields"]
    assert (f["read"], f["page_right"], f["wrong"]) == (16, 16, 1)
    assert combined["run_details"][0]["documents"][0]["counts"]["read"] == 4


def test_the_report_keeps_four_measures_apart(kit_of_two: Path) -> None:
    make = readers()
    combined = ev.combine(
        [
            save(kit_of_two, "a.json", run(make, 1)),
            save(kit_of_two, "b.json", run(make, 1)),
        ]
    )
    s = combined["summary"]
    assert s["accuracy"]["min"] == 0.875  # fields: right values / expected
    assert s["pass_rate"]["min"] == 0.5  # documents with every field right and on page
    assert s["classification_accuracy"]["mean"] == 1.0  # type right / documents
    assert s["line_item_accuracy"]["mean"] == 1.0  # lines right / lines expected
    assert s["page_accuracy"]["mean"] == 1.0  # values on their page / values read
    text = ev.summary_text(combined)
    for label in (
        "Field accuracy",
        "Strict document pass rate",
        "Source page right",
        "Classification",
        "Line items",
    ):
        assert label in text


def test_the_input_files_are_never_changed(
    kit_of_two: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make = readers()
    paths = [save(kit_of_two, n, run(make, 1)) for n in ("one.json", "two.json")]
    before = [p.read_bytes() for p in paths]
    monkeypatch.setenv("CLAIMS_EVALUATION", str(kit_of_two))
    assert ev.main(["--combine", *map(str, paths)]) == 0
    assert [p.read_bytes() for p in paths] == before
    assert ev.load_latest()["runs"] == 2


def test_combine_runs_no_reader(
    kit_of_two: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make = readers()
    paths = [save(kit_of_two, n, run(make, 1)) for n in ("one.json", "two.json")]

    def no(*_: object, **__: object) -> Any:
        raise AssertionError("combine must not run a reader")

    monkeypatch.setattr(ev, "evaluate", no)
    monkeypatch.setattr(ev, "_score", no)
    monkeypatch.setitem(ev.READERS, "fixture", no)
    monkeypatch.setitem(ev.READERS, "omni", no)
    assert ev.combine(paths)["runs"] == 2


def test_the_code_of_the_reader_is_in_the_report(kit_of_two: Path) -> None:
    report = run(readers(), 1)
    source = Path(__file__).with_name("test_evaluate.py").read_bytes()
    digest = hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()
    assert report["reader_code_sha256"] == digest  # StubReader lives there


def test_a_report_with_no_code_fingerprint_is_combined_and_said(
    kit_of_two: Path,
) -> None:
    make = readers()
    old, new = run(make, 1), run(make, 1)
    del old["reader_code_sha256"]  # a report written before the fingerprint
    combined = ev.combine(
        [save(kit_of_two, "old.json", old), save(kit_of_two, "new.json", new)]
    )
    assert combined["reader_code_sha256"] is None
    assert combined["combine_notes"] == [
        "old.json has no fingerprint of the reader code (it was written before the "
        "harness recorded it): its prompt and code are checked by its settings only."
    ]


# ---------------------------------------------------------------- refused


def _pair(folder: Path, change: Callable[[Json], None]) -> list[Path]:
    make = readers()
    good, other = run(make, 1), run(make, 1)
    change(other)
    return [save(folder, "good.json", good), save(folder, "other.json", other)]


def _set(path: str, value: Any) -> Callable[[Json], None]:
    def change(report: Json) -> None:
        node = report
        *keys, last = path.split(".")
        for k in keys:
            node = node[int(k)] if isinstance(node, list) else node[k]
        node[last] = value

    return change


def _first_doc(change: Callable[[Json], None]) -> Callable[[Json], None]:
    return lambda r: change(r["run_details"][0]["documents"][0])


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_set("reader", "omni"), "other.json: the reader is omni, not stub"),
        (_set("settings", {"reader": "stub", "dpi": 100}), "other.json: the settings"),
        (_set("reader_code_sha256", "0" * 64), "other.json: the code of the reader"),
        (_set("schema_version", 2), "other.json: not an evaluation report of schema"),
        (_set("kit.documents", 3), "other.json: the kit"),
        (_set("complete", False), "other.json: the kit"),
        (_set("runs", 2), "other.json: `runs` is 2 but it holds 1 run"),
        (_set("run_details", []), "other.json: it holds no run"),
        (_set("run_details.0.run", 7), "other.json: the runs are not numbered 1 to 1"),
        (
            _first_doc(lambda d: d["fields"][2].update(expected=1500.0)),
            "other.json, run 1: the documents or their answer keys",
        ),
        (
            _first_doc(lambda d: d.update(id="trip/c.pdf")),
            "other.json, run 1: the documents or their answer keys",
        ),
        (
            _first_doc(lambda d: d["counts"].update(right=4, wrong=0)),
            "other.json, run 1: trip/a.pdf: the counts do not match its fields",
        ),
        (
            _first_doc(lambda d: d.update(passed=True)),  # it has a wrong price
            "other.json, run 1: trip/a.pdf: the counts do not match its fields",
        ),
        (
            _set("run_details.0.overall.fields.right", 99),
            "other.json, run 1: the totals do not match its documents",
        ),
        (_first_doc(lambda d: d.pop("fields")), "other.json, run 1: a document"),
    ],
)
def test_a_report_made_in_other_conditions_is_refused(
    kit_of_two: Path, change: Callable[[Json], None], message: str
) -> None:
    with pytest.raises(ev.BadCombine) as refused:
        ev.combine(_pair(kit_of_two, change))
    assert message in str(refused.value)


def test_a_file_given_twice_or_two_copies_of_one_report_are_refused(
    kit_of_two: Path,
) -> None:
    report = run(readers(), 1)
    a = save(kit_of_two, "a.json", report)
    with pytest.raises(ev.BadCombine, match="a.json is given twice"):
        ev.combine([a, kit_of_two / "." / "a.json"])
    b = save(kit_of_two, "b.json", report)  # latest.json and its dated copy, say
    with pytest.raises(ev.BadCombine, match="a.json and b.json hold the same report"):
        ev.combine([a, b])


def test_the_same_run_in_two_reports_is_refused(kit_of_two: Path) -> None:
    make = readers()
    one, two = run(make, 1), run(make, 1)
    two = copy.deepcopy(two)
    two["run_details"].append(copy.deepcopy(one["run_details"][0]) | {"run": 2})
    two["runs"] = 2
    with pytest.raises(ev.BadCombine, match="run 1 of a.json is also run 2 of b.json"):
        ev.combine([save(kit_of_two, "a.json", one), save(kit_of_two, "b.json", two)])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{not json", "x.json: not JSON"),
        ("[1, 2]", "x.json: not an evaluation report of schema version 1"),
        ('{"schema_version": 1}', "x.json: `reader` is missing"),
    ],
)
def test_a_broken_file_is_refused(kit_of_two: Path, text: str, message: str) -> None:
    good = save(kit_of_two, "good.json", run(readers(), 1))
    bad = kit_of_two / "x.json"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ev.BadCombine, match=message.replace("`", ".")):
        ev.combine([good, bad])


def test_one_report_or_a_missing_file_is_refused(kit_of_two: Path) -> None:
    good = save(kit_of_two, "good.json", run(readers(), 1))
    with pytest.raises(ev.BadCombine, match="two reports or more"):
        ev.combine([good])
    with pytest.raises(ev.BadCombine, match="nope.json: cannot be read"):
        ev.combine([good, kit_of_two / "nope.json"])


def test_a_fixture_report_and_a_stub_report_are_refused(kit_of_two: Path) -> None:
    stub = save(kit_of_two, "stub.json", run(readers(), 1))
    fixture = save(kit_of_two, "fixture.json", ev.evaluate(FixtureReader))
    with pytest.raises(ev.BadCombine, match="the reader is fixture, not stub"):
        ev.combine([stub, fixture])


# ---------------------------------------------------------------- the command


def _two(folder: Path) -> list[str]:
    make = readers()
    return [str(save(folder, n, run(make, 1))) for n in ("one.json", "two.json")]


def test_the_command_writes_the_combined_report_and_its_copy(
    kit_of_two: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = kit_of_two / "three.json"
    assert ev.main(["--combine", *_two(kit_of_two), "--output", str(out)]) == 0
    report = ev.load_latest()
    assert report["runs"] == 2 and report["command"].startswith(
        "uv run python -m app.docintel.evaluate --combine "
    )
    assert json.loads(out.read_text(encoding="utf-8")) == report
    printed = capsys.readouterr().out
    assert (
        "Reader stub; 2 run(s)" in printed
        and "Combined from one.json, two.json" in printed
    )


def test_a_refused_combination_writes_nothing(
    kit_of_two: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ev.save_report(run(readers(), 1))
    latest = ev.report_dir() / "latest.json"
    before = latest.read_bytes()
    files = sorted(ev.report_dir().iterdir())
    one, two = _two(kit_of_two)
    report = json.loads(Path(two).read_text(encoding="utf-8"))
    report["reader"] = "omni"
    Path(two).write_text(ev.dump(report), encoding="utf-8")

    assert ev.main(["--combine", one, two]) == 2
    assert "error: two.json: the reader is omni, not stub" in capsys.readouterr().err
    assert latest.read_bytes() == before
    assert sorted(ev.report_dir().iterdir()) == files


def test_the_output_must_not_be_an_input(
    kit_of_two: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one, two = _two(kit_of_two)
    before = Path(one).read_bytes()
    assert ev.main(["--combine", one, two, "--output", one]) == 2
    assert "is an input" in capsys.readouterr().err
    assert Path(one).read_bytes() == before
    assert not (ev.report_dir() / "latest.json").exists()


@pytest.mark.parametrize(
    "extra", [["--runs", "2"], ["--reader", "fixture"]], ids=["runs", "reader"]
)
def test_combine_takes_no_reader_and_no_runs(
    kit_of_two: Path, extra: list[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        ev.main(["--combine", *_two(kit_of_two), *extra])
    assert stop.value.code == 2

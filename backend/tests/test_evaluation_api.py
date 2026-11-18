"""`GET /evaluation/latest` (issue #19): the last report of the harness, for the
page "Metrics". It reads one file in the report folder. It never runs a reader."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.docintel import evaluate as ev
from app.docintel.fixture import FixtureReader
from app.main import create_app
from app.models import Document, Extraction


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def test_no_report_yet_is_a_404(client: TestClient) -> None:
    res = client.get("/evaluation/latest")
    assert res.status_code == 404
    assert res.json() == {"detail": "no evaluation report yet"}


def test_the_last_report_is_served_as_it_is(client: TestClient) -> None:
    report = ev.evaluate(FixtureReader)
    ev.save_report(report)
    res = client.get("/evaluation/latest")
    assert res.status_code == 200 and res.json() == report
    ev.save_report(report | {"reader": "second"})  # a newer run replaces it
    assert client.get("/evaluation/latest").json()["reader"] == "second"


@pytest.mark.parametrize("text", ["{half", '{"schema_version": 7}', "[]"])
def test_a_broken_report_is_a_500_with_no_detail_of_the_file(
    client: TestClient, text: str
) -> None:
    folder = ev.report_dir()
    folder.mkdir(parents=True)
    (folder / "latest.json").write_text(text)
    res = client.get("/evaluation/latest")
    assert res.status_code == 500
    assert res.json() == {"detail": "the latest evaluation report cannot be read"}


def test_the_route_never_calls_the_reader(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Forbidden(FixtureReader):
        def classify(self, document: Document, data: bytes) -> str:
            raise AssertionError("the metrics page must not read a document")

        def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
            raise AssertionError("the metrics page must not read a document")

    client.app.state.reader = Forbidden()  # type: ignore[attr-defined]
    monkeypatch.setattr(ev, "evaluate", lambda *a, **k: pytest.fail("no run"))
    assert client.get("/evaluation/latest").status_code == 404


def test_the_route_takes_no_path_from_the_request(
    client: TestClient, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.json"
    secret.write_text('{"schema_version": 1, "reader": "secret"}')
    for url in (
        f"/evaluation/latest?path={secret}",
        "/evaluation/latest?folder=..",
        "/evaluation/..%2Fsecret.json",
        "/evaluation/secret.json",
    ):
        res = client.get(url)
        assert res.status_code == 404, url
        assert "secret" not in res.text
    assert client.post("/evaluation/latest").status_code == 405  # read-only

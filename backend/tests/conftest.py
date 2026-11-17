from pathlib import Path

import pytest

from tests.rig import make as make  # the fake-openshell fixture, shared


@pytest.fixture(autouse=True)
def _mode_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A CLAIMS_MODE of the shell must not change the tests. A test that wants the
    LangGraph agent sets it (`tests/fake_model.py::use_fake_model`). The checkpoints
    go to a temporary file, never to `.dev-data/`. So do the export files of the
    paid claims (issue #94)."""
    monkeypatch.delenv("CLAIMS_MODE", raising=False)
    monkeypatch.setenv("CLAIMS_CHECKPOINTS", str(tmp_path / "checkpoints.sqlite"))
    monkeypatch.setenv("CLAIMS_EXPORTS", str(tmp_path / "exports"))
    # no real wait after a 429 of the fake model (issue #117)
    monkeypatch.setattr("app.agent.graph.WAITS_429", (0.0, 0.0, 0.0))

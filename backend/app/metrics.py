"""The page "Metrics" (issue #19): the last report of the evaluation harness.

`GET /evaluation/latest` reads one fixed file, `latest.json` in the report folder
(`app/docintel/evaluate.py::report_dir`). The request names no path. It never runs
a reader and never calls a model: the command `python -m app.docintel.evaluate`
writes the report, in another process.
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from app.docintel.evaluate import load_latest

router = APIRouter(prefix="/evaluation")


@router.get("/latest")
def latest() -> dict[str, Any]:
    try:
        return load_latest()
    except FileNotFoundError:
        raise HTTPException(404, "no evaluation report yet") from None
    except (OSError, ValueError):  # a broken file: say so, show nothing of it
        raise HTTPException(
            500, "the latest evaluation report cannot be read"
        ) from None

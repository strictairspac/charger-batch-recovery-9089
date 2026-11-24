from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select

from src.constants.job_state import JobState
from src.domain import scenario
from src.domain.transaction import progress as batch_progress
from src.models.cases import cases
from src.models.runs import runs
from . import audit, cases as case_service


def _legacy_progress(result):
    """Derive progress for batches finished before per-station rows existed."""
    rows = result["rows"]
    passed = sum(1 for row in rows if row.get("决定") == "通过")
    return {
        "total": len(rows), "decided": len(rows), "passed": passed, "held": len(rows) - passed,
        "ports": sum(int(row.get("送检枪口") or 0) for row in rows if row.get("决定") == "通过"),
        "pending": 0,
    }


def _progress(db, run):
    total = run["station_total"]
    result = run["result"]
    if not total and result and result.get("rows"):
        total = len(result["rows"])
    if not total and run["status"] in (JobState.QUEUED, JobState.RUNNING):
        # Before the worker claims the batch, derive the size from the case so the
        # progress panel can already show "0 of N".
        payload = db.scalar(select(cases.c.payload).where(cases.c.id == run["case_id"]))
        stations = (payload or {}).get("stations")
        total = len(stations) if isinstance(stations, list) else 0
    current = batch_progress(db, run["id"], total)
    if not current["decided"] and result and result.get("rows"):
        return _legacy_progress(result)
    return current


def query():
    return select(runs, cases.c.title.label("case_title")).join(cases, cases.c.id == runs.c.case_id)


def _entry(db, row):
    run = dict(row)
    run["progress"] = _progress(db, run)
    return run


def get(db, run_id):
    run = db.execute(query().where(runs.c.id == run_id)).mappings().first()
    if run is None:
        raise HTTPException(404, "执行批次不存在")
    return _entry(db, run)


def list_entries(db):
    rows = db.execute(query().order_by(runs.c.id.desc()).limit(100)).mappings()
    return [_entry(db, row) for row in rows]


def enqueue(db, case_id, parameter, actor):
    if parameter > scenario.CONFIG["max_parameter"]:
        raise HTTPException(422, f"参数不能超过 {scenario.CONFIG['max_parameter']}")
    case = case_service.get(db, case_id)
    try:
        stations = scenario.check(case["payload"], parameter)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    change = db.execute(insert(runs).values(
        case_id=case_id, status=JobState.QUEUED, parameter=parameter, actor=actor,
        created_at=int(time()), station_total=len(stations),
    ))
    run_id = change.inserted_primary_key[0]
    audit.record(db, "enqueue", actor, "run", run_id)
    return get(db, run_id)

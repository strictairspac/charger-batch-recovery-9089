import logging
from time import time

from sqlalchemy import and_, or_, select, update

from src.config.resources import engine
from src.constants.job_state import JobState
from src.domain import scenario
from src.domain.transaction import decided_positions, evidence_and_metrics, settle_station
from src.models.cases import cases
from src.models.runs import runs
from src.services.audit import record
from src.utils.serial import content_digest

logger = logging.getLogger(__name__)

# A claim holds a short lease that is renewed after every station. An interrupted
# worker stops renewing, so the batch is picked up again once the lease lapses.
LEASE_SECONDS = 10
# Safety bound for a fault that repeats without making progress. Normal service
# restarts resume from persisted progress and stay well below this.
MAX_ATTEMPTS = 50

TERMINAL = (ValueError, TypeError, KeyError, OverflowError)


class JobFailure(Exception):
    def __init__(self, run_id, attempt, cause):
        super().__init__(str(cause))
        self.run_id = run_id
        self.attempt = attempt
        self.cause = cause

    @property
    def permanent(self):
        return isinstance(self.cause, TERMINAL)


def _claimable(now):
    return or_(
        runs.c.status == JobState.QUEUED,
        and_(runs.c.status == JobState.RUNNING, runs.c.lease_until < now),
    )


def _claim(db, now):
    candidate = db.execute(
        select(runs.c.id, runs.c.case_id, runs.c.parameter)
        .where(_claimable(now)).order_by(runs.c.id)
        .limit(1).with_for_update(skip_locked=True)
    ).mappings().first()
    if candidate is None:
        return None
    # Bumping the attempt fences later writes: a worker still holding the previous
    # attempt can no longer commit stations or the final result.
    claimed = db.execute(
        update(runs)
        .where(runs.c.id == candidate["id"], _claimable(now))
        .values(status=JobState.RUNNING, attempt=runs.c.attempt + 1, lease_until=now + LEASE_SECONDS)
        .returning(runs.c.id, runs.c.case_id, runs.c.parameter, runs.c.attempt, runs.c.station_total)
    ).mappings().first()
    return dict(claimed) if claimed else None


def _still_owned(db, run_id, attempt):
    """Lock the run row and confirm this worker still owns the current attempt."""
    current = db.execute(
        select(runs.c.status, runs.c.attempt).where(runs.c.id == run_id).with_for_update()
    ).one_or_none()
    return current is not None and current.status == JobState.RUNNING and current.attempt == attempt


def _renew(db, run_id, attempt):
    db.execute(
        update(runs).where(runs.c.id == run_id, runs.c.attempt == attempt)
        .values(lease_until=int(time()) + LEASE_SECONDS)
    )


def service_one():
    now = int(time())
    failure = None
    with engine.begin() as db:
        claim = _claim(db, now)
        if claim is None:
            return False
        try:
            stations = scenario.check(
                db.scalar(select(cases.c.payload).where(cases.c.id == claim["case_id"])),
                claim["parameter"],
            )
        except TERMINAL as exc:
            failure = JobFailure(claim["id"], claim["attempt"], exc)
        else:
            db.execute(update(runs).where(runs.c.id == claim["id"]).values(station_total=len(stations)))
    if failure is not None:
        fail(failure)
        return True
    try:
        return evaluate(claim, stations)
    except JobFailure:
        raise
    except Exception as exc:
        raise JobFailure(claim["id"], claim["attempt"], exc) from exc


def evaluate(claim, stations):
    """Resume the batch from the first undecided station and commit each one."""
    run_id, attempt, parameter = claim["id"], claim["attempt"], claim["parameter"]
    with engine.begin() as db:
        # Positions decided by an earlier, interrupted attempt are carried over
        # unchanged: decision, reason and port ownership all stay put.
        if not _still_owned(db, run_id, attempt):
            return None
        settled = decided_positions(db, run_id)
        db.execute(update(runs).where(runs.c.id == run_id).values(station_total=len(stations)))
        if settled:
            record(db, "resume", "worker", "run", run_id)
    for position, station in enumerate(stations, 1):
        with engine.begin() as db:
            if not _still_owned(db, run_id, attempt):
                return None
            if position in settled:
                _renew(db, run_id, attempt)
                continue
            if settle_station(db, run_id, position, station, parameter) is None:
                # A concurrent attempt settled this position first; stop here and
                # leave its newer progress untouched.
                return None
            _renew(db, run_id, attempt)
    return _finish(run_id, attempt, stations)


def _finish(run_id, attempt, stations):
    with engine.begin() as db:
        if not _still_owned(db, run_id, attempt):
            return None
        missing = len(stations) - len(decided_positions(db, run_id))
        if missing:
            raise JobFailure(run_id, attempt, RuntimeError(f"仍有 {missing} 个站点未裁决"))
        evidence, _accepted, _held, _ports = evidence_and_metrics(db, run_id)
        result = scenario.summarize(stations, evidence)
        output = {"status": JobState.DONE, "result": result, "digest": content_digest(result)}
        finish(db, run_id, output)
        return True


def fail(job_failure):
    with engine.begin() as db:
        current = db.execute(
            select(runs.c.status, runs.c.attempt).where(runs.c.id == job_failure.run_id).with_for_update()
        ).one_or_none()
        if current is None or current.attempt != job_failure.attempt:
            return "stale"
        if current.status not in (JobState.QUEUED, JobState.RUNNING):
            return "stale"
        cause = job_failure.cause
        permanent = job_failure.permanent or job_failure.attempt >= MAX_ATTEMPTS
        if permanent:
            reason = (str(cause) if job_failure.permanent else "").strip() or "处理失败，请检查 worker 日志"
            finish(db, job_failure.run_id, {"status": JobState.FAILED, "failure": reason[:320]})
            return "failed"
        # Transient fault: release the claim so the batch resumes from its
        # persisted progress once the worker reconnects.
        db.execute(
            update(runs).where(runs.c.id == job_failure.run_id, runs.c.attempt == job_failure.attempt)
            .values(status=JobState.QUEUED, lease_until=None)
        )
        return "retry"


def finish(db, run_id, output):
    db.execute(update(runs).where(runs.c.id == run_id).values(**output, finished_at=int(time())))
    record(db, str(output["status"]), "worker", "run", run_id)

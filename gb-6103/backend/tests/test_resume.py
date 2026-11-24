"""Integration tests for interrupted-batch resumption.

Requires DATABASE_URL and REDIS_URL (the same configuration the app uses). The
tests create their own users/cases/fixtures and clean up their runs afterwards.
"""
import time

import pytest
from sqlalchemy import delete, insert, select, text, update

from src.config.resources import engine
from src.constants.job_state import JobState
from src.domain import scenario
from src.domain.transaction import decided_positions, settle_station
from src.models.batches import run_claims, run_ports, run_stations
from src.models.cases import cases
from src.models.reviews import reviews
from src.models.runs import runs
from src.services import execution, runs as run_service


def make_case(db, payload):
    change = db.execute(insert(cases).values(
        code=f"TEST-{time.time_ns()}", title="测试批次", description="",
        payload=payload, created_by="test",
    ))
    return change.inserted_primary_key[0]


def enqueue(payload, parameter=1000):
    with engine.begin() as db:
        case_id = make_case(db, payload)
        return run_service.enqueue(db, case_id, parameter, "operator")


def cleanup(run_id):
    with engine.begin() as db:
        case_id = db.scalar(select(runs.c.case_id).where(runs.c.id == run_id))
        db.execute(delete(reviews).where(reviews.c.run_id == run_id))
        for table in (run_ports, run_claims, run_stations):
            db.execute(delete(table).where(table.c.run_id == run_id))
        db.execute(delete(runs).where(runs.c.id == run_id))
        if case_id is not None:
            db.execute(delete(cases).where(cases.c.id == case_id))


def drain():
    """Process every queued batch so each test inspects only its own run."""
    while execution.service_one():
        pass


@pytest.fixture(autouse=True)
def no_leftover_test_runs():
    with engine.begin() as db:
        stale = db.execute(select(runs.c.id).join(cases, cases.c.id == runs.c.case_id)
                           .where(cases.c.code.like("TEST-%"))).scalars().all()
    for run_id in stale:
        cleanup(run_id)
    drain()
    yield


def claim(run_id, now=1):
    with engine.begin() as db:
        return execution._claim(db, now)


def expire(run_id):
    with engine.begin() as db:
        db.execute(update(runs).where(runs.c.id == run_id).values(lease_until=0))


def detail(run_id):
    with engine.connect() as db:
        return run_service.get(db, run_id)


@pytest.fixture()
def batch():
    run = enqueue(scenario.FIXTURES[0]["payload"])
    yield run["id"]
    cleanup(run["id"])


def test_full_batch_matches_fixture(batch):
    assert execution.service_one() is True
    run = detail(batch)
    assert run["status"] == JobState.DONE
    assert run["progress"] == {"total": 4, "decided": 4, "passed": 2, "held": 2, "ports": 3, "pending": 0}
    assert run["result"]["summary"] == "4 次分站送检，2 站通过、2 次搁置，登记 3 个枪口。"
    assert [(row["顺序"], row["站号"], row["决定"]) for row in run["result"]["rows"]] == [
        (1, "TY-S11", "通过"), (2, "TY-S12", "搁置"), (3, "TY-S13", "搁置"), (4, "TY-S14", "通过"),
    ]


def test_crash_resumes_only_undecided_stations(batch):
    stations = scenario.check(scenario.FIXTURES[0]["payload"], 1000)
    assert claim(batch)["attempt"] == 1
    with engine.begin() as db:
        settle_station(db, batch, 1, stations[0], 1000)
        settle_station(db, batch, 2, stations[1], 1000)
        db.execute(update(runs).where(runs.c.id == batch).values(station_total=4))
    # Crash: the worker never finished and its lease lapses.
    expire(batch)
    assert execution.service_one() is True
    run = detail(batch)
    assert run["status"] == JobState.DONE
    assert run["progress"]["decided"] == 4
    # The first two rows were carried over untouched (same reasons as the run).
    assert [row["原因"] for row in run["result"]["rows"][:2]] == [
        "站点与全部枪口已登记", "枪口序列号重复，枪口与所属站记录已经回滚",
    ]
    with engine.connect() as db:
        actions = db.execute(text(
            "SELECT action FROM audit_events WHERE entity='run' AND entity_id=:r ORDER BY id"
        ), {"r": batch}).scalars().all()
    assert "resume" in actions


def test_stale_attempt_cannot_overwrite_newer_progress(batch):
    stations = scenario.check(scenario.FIXTURES[0]["payload"], 1000)
    first = claim(batch)
    with engine.begin() as db:
        settle_station(db, batch, 1, stations[0], 1000)
        db.execute(update(runs).where(runs.c.id == batch).values(station_total=4))
    expire(batch)
    assert execution.service_one() is True  # a second worker takes over and finishes
    finished = detail(batch)
    # The old attempt now wakes up and tries to write a station and the result.
    with engine.begin() as db:
        assert execution._still_owned(db, batch, first["attempt"]) is False
        # Position 2 is already settled by the new attempt: the late write is
        # refused instead of overwriting it.
        assert settle_station(db, batch, 2, stations[1], 1000) is None
    with engine.begin() as db:
        assert execution._finish(batch, first["attempt"], stations) is None
    after = detail(batch)
    assert after["result"] == finished["result"]
    assert after["status"] == JobState.DONE


def test_held_station_releases_code_and_serials(batch):
    run = enqueue(scenario.FIXTURES[1]["payload"])
    try:
        drain()
        row_decisions = [(row["站号"], row["决定"]) for row in detail(run["id"])["result"]["rows"]]
        assert row_decisions == [("TY-S21", "搁置"), ("TY-S21", "通过")]
    finally:
        cleanup(run["id"])


def test_progress_visible_before_claim(batch):
    run = detail(batch)
    assert run["status"] == JobState.QUEUED
    assert run["progress"] == {"total": 4, "decided": 0, "passed": 0, "held": 0, "ports": 0, "pending": 4}


def test_review_blocked_until_whole_batch_decided(batch):
    from fastapi import HTTPException
    from src.services import reviews
    stations = scenario.check(scenario.FIXTURES[0]["payload"], 1000)
    claim(batch)
    with engine.begin() as db:
        settle_station(db, batch, 1, stations[0], 1000)
        db.execute(update(runs).where(runs.c.id == batch).values(station_total=4))
    with engine.begin() as db:
        with pytest.raises(HTTPException) as exc:
            reviews.sign(db, batch, type("Data", (), {"note": "提前签核"})(), "reviewer")
        assert exc.value.status_code == 409
    assert execution.service_one() is True
    with engine.begin() as db:
        signed = reviews.sign(db, batch, type("Data", (), {"note": "整批裁决完成"})(), "reviewer")
    assert signed["actor"] == "reviewer"
    assert decided_positions(engine.connect(), batch) == {1, 2, 3, 4}


def test_concurrent_claims_yield_one_worker(batch):
    import threading
    barrier = threading.Barrier(4)
    winners = []

    def grab(index):
        with engine.begin() as db:
            barrier.wait()
            claimed = execution._claim(db, 1)
            if claimed:
                winners.append((index, claimed["attempt"]))

    threads = [threading.Thread(target=grab, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(winners) == 1
    with engine.connect() as db:
        assert db.scalar(select(runs.c.attempt).where(runs.c.id == batch)) == 1


def test_resumed_batch_keeps_submission_order_and_parameter(batch):
    stations = scenario.check(scenario.FIXTURES[0]["payload"], 1000)
    first = claim(batch)
    with engine.begin() as db:
        settle_station(db, batch, 1, stations[0], 1000)
        settle_station(db, batch, 3, stations[2], 1000)   # position 2 left undecided
        db.execute(update(runs).where(runs.c.id == batch).values(station_total=4))
    expire(batch)
    resumed = None
    with engine.begin() as db:
        resumed = execution._claim(db, 2)
    assert resumed["attempt"] == first["attempt"] + 1
    assert resumed["parameter"] == 1000  # the stored lower bound, not a new one
    assert execution.service_one() is True
    run = detail(batch)
    assert [row["顺序"] for row in run["result"]["rows"]] == [1, 2, 3, 4]
    # Position 2 was recomputed; positions 1 and 3 keep their earlier verdicts.
    assert run["result"]["rows"][2]["原因"] == "TY-P03 绝缘 620 kΩ 低于 1000 kΩ"

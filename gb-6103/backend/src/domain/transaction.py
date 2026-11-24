from sqlalchemy import case, func, insert, select
from sqlalchemy.exc import IntegrityError

from src.models.batches import run_claims, run_ports, run_stations

from .acceptance_rules import StationHold, ensure_energizable

PASSED = "通过"
HELD = "搁置"


def _held(db, run_id, position, station, detail):
    # A held station still needs its own row, but if a concurrent attempt already
    # settled this position the later write must not clobber it.
    try:
        with db.begin_nested():
            db.execute(insert(run_stations).values(
                run_id=run_id,
                position=position,
                station=station["station"],
                district=station["district"],
                decision=HELD,
                reason=detail[:320],
                port_count=len(station["ports"]),
            ))
    except IntegrityError as exc:
        if _integrity_note(exc) is None:
            return None
        raise
    return HELD, detail, 0


def _integrity_note(exc):
    native = exc.orig
    if getattr(native, "sqlstate", getattr(native, "pgcode", None)) != "23505":
        raise exc
    diag = getattr(native, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    if constraint == "run_claims_pkey":
        return "站号已被本批次的通过记录占用"
    if constraint == "run_ports_pkey":
        return "枪口序列号重复，枪口与所属站记录已经回滚"
    if constraint == "run_stations_pkey":
        # A concurrent attempt settled this position first; leave its row alone.
        return None
    raise exc


def settle_station(db, run_id, position, station, minimum):
    """Adjudicate one station inside a caller-owned SAVEPOINT.

    The station row, its code claim and every port commit together. A held
    station rolls the savepoint back first, so its code and serials stay free for
    later stations in the same batch.
    """
    ports = station["ports"]
    try:
        with db.begin_nested():
            db.execute(insert(run_stations).values(
                run_id=run_id,
                position=position,
                station=station["station"],
                district=station["district"],
                decision=PASSED,
                reason="站点与全部枪口已登记",
                port_count=len(ports),
            ))
            db.execute(insert(run_claims).values(
                run_id=run_id, station=station["station"], position=position,
            ))
            db.execute(insert(run_ports), [
                {
                    "run_id": run_id,
                    "serial": port["serial"],
                    "station": station["station"],
                    "insulation": port["insulation"],
                    "interlock": port["interlock"],
                }
                for port in ports
            ])
            ensure_energizable(ports, minimum)
    except StationHold as exc:
        return _held(db, run_id, position, station, str(exc))
    except IntegrityError as exc:
        note = _integrity_note(exc)
        if note is None:
            return None
        return _held(db, run_id, position, station, note)
    return PASSED, "站点与全部枪口已登记", len(ports)


def decided_positions(db, run_id):
    return set(db.execute(
        select(run_stations.c.position).where(run_stations.c.run_id == run_id)
    ).scalars())


def progress(db, run_id, total):
    # Portable conditional aggregation (works on PostgreSQL and MySQL alike).
    decided, passed, held = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((run_stations.c.decision == PASSED, 1), else_=0)), 0),
            func.coalesce(func.sum(case((run_stations.c.decision == HELD, 1), else_=0)), 0),
        ).where(run_stations.c.run_id == run_id)
    ).one()
    ports = db.scalar(select(func.count()).select_from(run_ports).where(run_ports.c.run_id == run_id))
    return {
        "total": total,
        "decided": decided,
        "passed": passed,
        "held": held,
        "ports": ports,
        "pending": max(total - decided, 0),
    }


def evidence_and_metrics(db, run_id):
    rows = db.execute(
        select(run_stations).where(run_stations.c.run_id == run_id).order_by(run_stations.c.position)
    ).mappings()
    evidence = [{
        "顺序": row["position"],
        "站号": row["station"],
        "片区": row["district"],
        "送检枪口": row["port_count"],
        "决定": row["decision"],
        "原因": row["reason"],
    } for row in rows]
    accepted = sum(1 for row in evidence if row["决定"] == PASSED)
    ports = db.scalar(select(func.count()).select_from(run_ports).where(run_ports.c.run_id == run_id))
    return evidence, accepted, len(evidence) - accepted, ports

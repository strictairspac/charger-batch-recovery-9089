from sqlalchemy import inspect, text

from src.models import audit, batches, cases, reviews, runs, users  # Register the tables.
from src.models.base import metadata

# Columns added after version 1 that must be patched onto an existing runs table.
_ADDED_RUN_COLUMNS = {
    "attempt": "INTEGER NOT NULL DEFAULT 0",
    "lease_until": "BIGINT",
    "station_total": "INTEGER NOT NULL DEFAULT 0",
}


def _upgrade_runs(connection):
    existing = inspect(connection).get_table_names()
    if "runs" not in existing:
        return
    columns = {column["name"] for column in inspect(connection).get_columns("runs")}
    for name, definition in _ADDED_RUN_COLUMNS.items():
        if name in columns:
            continue
        connection.execute(text(f"ALTER TABLE runs ADD COLUMN {name} {definition}"))


def upgrade(connection):
    # Version 1 is additive and fully declarative. batches (run_stations,
    # run_claims, run_ports) persists each station's verdict so an interrupted
    # batch resumes from the first undecided station.
    metadata.create_all(connection)
    _upgrade_runs(connection)

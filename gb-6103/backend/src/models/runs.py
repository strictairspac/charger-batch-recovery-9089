from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Integer, String, Table, text
from .base import metadata


runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", ForeignKey("cases.id"), nullable=False, index=True),
    Column("status", String(16), nullable=False, index=True),
    Column("parameter", Integer, nullable=False),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("finished_at", BigInteger, nullable=True),
    Column("result", JSON(none_as_null=True), nullable=True),
    Column("failure", String(320), nullable=True),
    Column("digest", String(64), nullable=True),
    # Every claim bumps the attempt and takes a short lease. Station writes and
    # the final result only apply while the caller still owns the current attempt,
    # so a late worker cannot overwrite newer progress.
    Column("attempt", Integer, nullable=False, server_default=text("0")),
    Column("lease_until", BigInteger, nullable=True),
    Column("station_total", Integer, nullable=False, server_default=text("0")),
)

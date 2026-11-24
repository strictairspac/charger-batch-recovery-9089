from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table

from .base import metadata


# One row per station that has been adjudicated, in the order it was sent for
# inspection. Each row commits together with its claims and ports, so an
# interrupted batch can resume without redoing settled stations. The composite
# primary key also fences a late worker off a position that is already settled.
run_stations = Table(
    "run_stations", metadata,
    Column("run_id", ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("station", String(24), nullable=False),
    Column("district", String(40), nullable=False),
    Column("decision", String(8), nullable=False),
    Column("reason", String(320), nullable=False),
    Column("port_count", Integer, nullable=False),
)

# Only a passed station holds a claim on its station code. A held station rolls
# its claim back, leaving the code free for a later station in the same batch.
run_claims = Table(
    "run_claims", metadata,
    Column("run_id", ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    Column("station", String(24), primary_key=True),
    Column("position", Integer, nullable=False),
)

# Port ownership belongs to the passed station that registered the serial. A held
# station keeps no rows here, so its serials stay available to later stations.
run_ports = Table(
    "run_ports", metadata,
    Column("run_id", ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    Column("serial", String(24), primary_key=True),
    Column("station", String(24), nullable=False),
    Column("insulation", Integer, nullable=False),
    Column("interlock", Boolean, nullable=False),
)

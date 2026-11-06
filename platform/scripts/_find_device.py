import json
from sqlalchemy import text
from app.db import get_engine, set_tenant

TENANT = "e406274e-dcbd-408f-abc1-4953fc3f22b3"
ADMIN = "04405b1b-591c-4469-8a01-f303ec2598d1"
engine = get_engine()
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    row = conn.execute(
        text("SELECT id FROM devices WHERE tenant_id=CAST(:t AS uuid) AND status='active' LIMIT 1"),
        {"t": TENANT},
    ).mappings().first()
    print("device_row=", dict(row) if row else None)

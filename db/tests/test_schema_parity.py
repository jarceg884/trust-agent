"""AC-DB-040 — Paridad schema.sql ↔ migración Alembic.

Aplica schema.sql a una DB y `alembic upgrade head` a otra, y compara los
pg_dump --schema-only (normalizando el token aleatorio \\restrict de pg_dump 17
y las líneas vacías). El diff debe ser vacío: la migración ejecuta el mismo DDL.
"""
import os
import re
import subprocess

import pytest

from conftest import DB_HOST, DB_PORT, ADMIN_PASSWORD, ADMIN_DSN, DB_DIR, ALEMBIC, SCHEMA_SQL, _admin

DB_SCHEMA = "trust_parity_schema"
DB_MIG = "trust_parity_mig"
# Container name for the pg_dump/psql side of the parity check. The
# canonical docker-compose.yml names it `trust-agent-db`; db/README.md's
# manual setup names it `trust-db`. Parameterized like DB_HOST/DB_PORT so
# the suite runs against whichever stack is up.
DB_CONTAINER = os.environ.get("TRUST_DB_CONTAINER", "trust-db")


def _dump(dbname: str) -> str:
    out = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "pg_dump", "-U", "postgres",
         "--schema-only", "--exclude-table=public.alembic_version", dbname],
        capture_output=True, text=True, check=True,
    ).stdout
    lines = [
        ln for ln in out.splitlines()
        if ln.strip() and not re.match(r"^\\(un)?restrict ", ln)
    ]
    return "\n".join(lines)


@pytest.fixture(scope="module")
def parity_dbs():
    with _admin() as c:
        for db in (DB_SCHEMA, DB_MIG):
            c.execute(f"DROP DATABASE IF EXISTS {db}")
            c.execute(f"CREATE DATABASE {db}")
    # vía 1: schema.sql canónico
    with open(SCHEMA_SQL, "rb") as f:
        subprocess.run(
            ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", "postgres",
             "-v", "ON_ERROR_STOP=1", "-d", DB_SCHEMA],
            stdin=f, capture_output=True, check=True,
        )
    # vía 2: alembic upgrade head
    env = dict(
        os.environ,
        DATABASE_URL=f"postgresql+psycopg://postgres:{ADMIN_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_MIG}",
    )
    proc = subprocess.run(
        [ALEMBIC, "upgrade", "head"], cwd=DB_DIR, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, f"alembic upgrade head falló:\n{proc.stderr}"
    yield
    with _admin() as c:
        for db in (DB_SCHEMA, DB_MIG):
            c.execute(f"DROP DATABASE IF EXISTS {db}")


def test_schema_sql_and_migration_produce_identical_schema(parity_dbs):
    dump_schema = _dump(DB_SCHEMA)
    dump_mig = _dump(DB_MIG)
    assert dump_schema == dump_mig, (
        "Divergencia entre schema.sql y la migración Alembic:\n"
        + _first_diff(dump_schema, dump_mig)
    )


def test_alembic_version_tracked(parity_dbs):
    """La vía de migración registra su revisión (gobernanza de versiones).

    El head esperado se calcula con `alembic heads` (única cabeza) en vez de
    hardcodear la revisión: la línea canónica de migraciones evoluciona con
    cada spec (0002_login_throttle, 0003_devices_enrollment_token, ...).
    """
    env = dict(
        os.environ,
        DATABASE_URL=f"postgresql+psycopg://postgres:{ADMIN_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_MIG}",
    )
    proc = subprocess.run(
        [ALEMBIC, "heads"], cwd=DB_DIR, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, f"alembic heads falló:\n{proc.stderr}"
    heads = [ln.strip().split(" ")[0] for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(heads) == 1, f"múltiples cabezas de migración: {heads}"
    with _admin(DB_MIG) as c:
        version = c.execute("SELECT version_num FROM public.alembic_version").fetchone()
    assert version is not None and version[0] == heads[0]


def _first_diff(a: str, b: str) -> str:
    al, bl = a.splitlines(), b.splitlines()
    for i, (x, y) in enumerate(zip(al, bl)):
        if x != y:
            return f"línea {i + 1}:\n  schema.sql: {x}\n  migración:  {y}"
    if len(al) != len(bl):
        return f"longitudes distintas: {len(al)} vs {len(bl)}"
    return "(sin detalle)"

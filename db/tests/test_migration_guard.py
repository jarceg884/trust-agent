"""Tests del guardián de serialización de migraciones (ADR 0001).

No tocan la DB: ejercitan el parseo del grafo (ast), el rechazo de
bifurcaciones y la creación apilada bajo lock contra directorios temporales.
Correr: `.venv/bin/python -m pytest tests/test_migration_guard.py -q`
"""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

DB_DIR = Path(__file__).resolve().parent.parent
GUARD_PATH = DB_DIR / "scripts" / "migration_guard.py"

spec = importlib.util.spec_from_file_location("migration_guard", GUARD_PATH)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
sys.modules["migration_guard"] = guard
spec.loader.exec_module(guard)


def _mig(rev: str, down: str | None, name: str | None = None) -> str:
    down_lit = "None" if down is None else f'"{down}"'
    return (
        f'"""test {rev}"""\n'
        "from alembic import op\n\n"
        f'revision = "{rev}"\n'
        f"down_revision = {down_lit}\n"
        "branch_labels = None\n"
        "depends_on = None\n\n"
        "def upgrade() -> None:\n    pass\n\n"
        "def downgrade() -> None:\n    pass\n"
    )


@pytest.fixture()
def versions(tmp_path: Path) -> Path:
    """Cadena lineal válida de 3 revisiones con nombres convencionales."""
    d = tmp_path / "versions"
    d.mkdir()
    (d / "20260101_0001_initial.py").write_text(_mig("0001_initial", None))
    (d / "20260102_0002_second.py").write_text(_mig("0002_second", "0001_initial"))
    (d / "20260103_0003_third.py").write_text(_mig("0003_third", "0002_second"))
    return d


# --- check: grafo -----------------------------------------------------------


def test_check_cadena_lineal_ok(versions: Path):
    assert guard.cmd_check(versions) == 0


def test_check_rechaza_dos_cabezas(versions: Path):
    """El incidente real: dos workers apilan al MISMO padre en paralelo."""
    (versions / "20260104_0004_lane_a.py").write_text(_mig("0004_lane_a", "0003_third"))
    (versions / "20260104_0004_lane_b.py").write_text(_mig("0004_lane_b", "0003_third"))
    assert guard.cmd_check(versions) == 1


def test_check_acepta_fork_mergeado(versions: Path):
    """Un fork unido por revisión merge deja UNA cabeza: es válido (caso 0009)."""
    (versions / "20260104_0004_lane_a.py").write_text(_mig("0004_lane_a", "0003_third"))
    (versions / "20260104_0004_lane_b.py").write_text(_mig("0004_lane_b", "0003_third"))
    merge = (
        '"""merge"""\nfrom alembic import op\n\n'
        'revision = "0005_merge"\n'
        'down_revision = ("0004_lane_a", "0004_lane_b")\n'
        "branch_labels = None\n"
        "depends_on = None\n"
    )
    (versions / "20260105_0005_merge.py").write_text(merge)
    assert guard.cmd_check(versions) == 0


def test_check_rechaza_padre_inexistente(versions: Path):
    (versions / "20260104_0004_huerfana.py").write_text(_mig("0004_huerfana", "0099_ghost"))
    assert guard.cmd_check(versions) == 1


def test_check_rechaza_id_duplicado(versions: Path):
    (versions / "20260104_0004_dup.py").write_text(_mig("0003_third", "0002_second"))
    with pytest.raises(guard.MigrationError, match="duplicado"):
        guard.parse_versions(versions)


def test_check_rechaza_revision_larga(versions: Path):
    largo = "0004_" + "x" * 40
    (versions / "20260104_0004_largo.py").write_text(_mig(largo, "0003_third"))
    assert guard.cmd_check(versions) == 1


def test_check_rechaza_archivo_roto(versions: Path):
    (versions / "20260104_0004_roto.py").write_text("def upgrade(: pass\n")
    with pytest.raises(guard.MigrationError, match="sintaxis"):
        guard.parse_versions(versions)


def test_check_rechaza_directorio_vacio(tmp_path: Path):
    d = tmp_path / "versions"
    d.mkdir()
    with pytest.raises(guard.MigrationError, match="no hay migraciones"):
        guard.parse_versions(d)


# --- new: creación apilada bajo lock ----------------------------------------


def test_new_apila_sobre_head(versions: Path):
    assert guard.cmd_new(versions, "mi_cambio", lock_timeout=5.0) == 0
    hoy = datetime.now(timezone.utc).strftime("%Y%m%d")
    creado = versions / f"{hoy}_0004_mi_cambio.py"
    assert creado.exists()
    texto = creado.read_text()
    assert 'revision = "0004_mi_cambio"' in texto
    assert 'down_revision = "0003_third"' in texto
    # la cadena sigue teniendo una única cabeza
    assert guard.cmd_check(versions) == 0


def test_new_secuencial_apila_en_cadena(versions: Path):
    """Dos creaciones consecutivas (lo que el lock garantiza en paralelo)."""
    assert guard.cmd_new(versions, "lane_b", lock_timeout=5.0) == 0
    assert guard.cmd_new(versions, "lane_a", lock_timeout=5.0) == 0
    revs = guard.parse_versions(versions)
    heads = [r for r in revs if not any(
        r in (d if isinstance(d, tuple) else (d,)) for d in
        (i["down"] for i in revs.values())
    )]
    assert len(heads) == 1
    assert heads[0] == "0005_lane_a"
    assert revs["0005_lane_a"]["down"] == "0004_lane_b"


def test_new_rechaza_cadena_rota(versions: Path):
    (versions / "20260104_0004_lane_a.py").write_text(_mig("0004_lane_a", "0003_third"))
    (versions / "20260104_0004_lane_b.py").write_text(_mig("0004_lane_b", "0003_third"))
    assert guard.cmd_new(versions, "otra", lock_timeout=5.0) == 1
    assert not (versions / "20260104_0004_otra.py").exists()


def test_new_slug_invalido(versions: Path):
    with pytest.raises(guard.MigrationError, match="slug"):
        guard.cmd_new(versions, "---", lock_timeout=5.0)


def test_new_trunca_revision_a_32(versions: Path):
    slug = "cambio_con_nombre_muy_largo_que_excede_el_limite"
    assert guard.cmd_new(versions, slug, lock_timeout=5.0) == 0
    revs = guard.parse_versions(versions)
    new_heads = [r for r in revs if r.startswith("0004_")]
    assert len(new_heads) == 1
    assert len(new_heads[0]) <= 32

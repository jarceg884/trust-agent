#!/usr/bin/env python3
"""Guardián de serialización de migraciones Alembic — Trust Agent.

Mecanismo obligatorio (ADR docs/adr-0001-serializacion-migraciones-alembic.md)
para evitar ramas paralelas en ``db/migrations/versions/`` creadas por workers
dev simultáneos (incidente 0009 triple, resuelto por la merge 12c08d4fb858).

Dos subcomandos:

- ``check``: valida el grafo de revisiones SIN tocar la DB. Rechaza múltiples
  cabezas, ids duplicados, padres inexistentes, ciclos y ids > 32 chars
  (``alembic_version.version_num`` es VARCHAR(32)). Es la puerta de entrada
  del servicio ``migrate`` de compose: un árbol bifurcado falla aquí con un
  mensaje accionable en vez del críptico ``exit 255`` de Alembic.

- ``new --slug <slug>``: la ÚNICA vía bendecida para crear una migración.
  Toma un lock exclusivo (``flock``) sobre ``migrations/.versions.lock``,
  lee el head REAL bajo el lock, calcula el siguiente NNNN y escribe el
  archivo ya apilado. Dos workers en paralelo quedan serializados: el
  segundo espera y se apila sobre el primero.

Solo stdlib: corre igual en el host (db/.venv o python3) y dentro de la
imagen ``trust-agent/platform:local`` (python:3.12-slim).

Uso::

    python db/scripts/migration_guard.py check
    python db/scripts/migration_guard.py new --slug mi_cambio
"""
from __future__ import annotations

import argparse
import ast
import fcntl
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_VERSIONS_DIR = Path(__file__).resolve().parent.parent / "migrations" / "versions"
LOCK_FILE = Path(__file__).resolve().parent.parent / "migrations" / ".versions.lock"
FILE_RE = re.compile(r"^(\d{8})_(\d{4})_([a-z0-9_]+)\.py$")
REV_MAX_LEN = 32  # alembic_version.version_num es VARCHAR(32)


class MigrationError(Exception):
    """Error accionable del guardián."""


def _norm_down(node: ast.expr) -> str | tuple[str, ...] | None:
    """Normaliza el valor de down_revision: None, str o tupla (merge)."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str) or node.value is None:
            return node.value
        raise MigrationError(f"down_revision con valor no string: {node.value!r}")
    if isinstance(node, ast.Tuple):
        vals = []
        for elt in node.elts:
            if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
                raise MigrationError(f"down_revision con valor no string: {ast.dump(elt)}")
            vals.append(elt.value)
        return tuple(vals)
    raise MigrationError(f"down_revision con sintaxis no soportada: {ast.dump(node)}")


def parse_versions(versions_dir: Path) -> dict[str, dict]:
    """Parsea versions/*.py con ast (sin importar nada ni tocar la DB).

    Devuelve {revision_id: {"down": str|tuple|None, "file": Path}}.
    """
    if not versions_dir.is_dir():
        raise MigrationError(f"no existe el directorio de versiones: {versions_dir}")
    files = sorted(p for p in versions_dir.glob("*.py") if p.is_file())
    if not files:
        raise MigrationError(f"no hay migraciones en {versions_dir}")
    revisions: dict[str, dict] = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise MigrationError(f"{path.name}: sintaxis inválida ({exc}) — ¿archivo a medio escribir?") from exc
        rev, down = None, None
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if target.id == "revision":
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    rev = node.value.value
                else:
                    raise MigrationError(f"{path.name}: 'revision' no es un string literal")
            elif target.id == "down_revision":
                down = _norm_down(node.value)
        if rev is None:
            raise MigrationError(f"{path.name}: no define 'revision = <id>'")
        if rev in revisions:
            raise MigrationError(
                f"id de revisión duplicado '{rev}' en {path.name} "
                f"(ya estaba en {revisions[rev]['file'].name})"
            )
        revisions[rev] = {"down": down, "file": path}
    return revisions


def graph_check(revisions: dict[str, dict]) -> tuple[list[str], list[str], list[str]]:
    """Valida la integridad del grafo. Devuelve (errores, warnings, heads).

    Invariantes de ERROR (rompen la cadena):
    - más de una cabeza (la bifurcación que este mecanismo previene),
    - down_revision apuntando a una revisión inexistente,
    - ciclo (cadena infinita de padres),
    - id de revisión > 32 chars.

    Invariantes de WARNING (legado, no rompen la cadena):
    - NNNN duplicado en nombres de archivo (p.ej. el triple 0009 histórico),
    - nombre de archivo fuera de la convención YYYYMMDD_NNNN_slug.py
      (p.ej. la merge 12c08d4fb858).
    """
    errors: list[str] = []
    warnings: list[str] = []

    children: dict[str, list[str]] = {}
    for rev, info in revisions.items():
        if len(rev) > REV_MAX_LEN:
            errors.append(
                f"{info['file'].name}: revision '{rev}' excede {REV_MAX_LEN} chars "
                f"(alembic_version.version_num es VARCHAR(32))"
            )
        downs = info["down"] if isinstance(info["down"], tuple) else (info["down"],)
        for down in downs:
            if down is None:
                continue
            if down not in revisions:
                errors.append(
                    f"{info['file'].name}: down_revision '{down}' no existe en versions/"
                )
            else:
                children.setdefault(down, []).append(rev)

    heads = [rev for rev in revisions if rev not in children]
    if len(heads) == 0:
        errors.append("el grafo no tiene cabeza: ¿ciclo? (toda revisión tiene hijos)")
    elif len(heads) > 1:
        details = "\n".join(
            f"  - head '{h}' ({revisions[h]['file'].name})" for h in sorted(heads)
        )
        errors.append(
            f"MULTIPLES CABEZAS ({len(heads)}) — bifurcación de la cadena:\n{details}\n"
            "Corrige re-apilando: la lane que anunció DESPUÉS debe cambiar su "
            "down_revision al head de la lane anterior (o emite una revisión merge). "
            "Vía bendecida: python db/scripts/migration_guard.py new --slug <slug>"
        )

    # Ciclos: caminar padres desde cada cabeza no debe exceder el total de nodos.
    for head in heads:
        seen, cur, steps = set(), head, 0
        while cur is not None and steps <= len(revisions):
            if cur in seen:
                errors.append(f"ciclo detectado alcanzando '{cur}' desde el head '{head}'")
                break
            seen.add(cur)
            if cur not in revisions:
                break  # padre inexistente: ya reportado como error arriba
            downs = revisions[cur]["down"]
            cur = downs[0] if isinstance(downs, tuple) else downs
            steps += 1

    # Convención de nombres (warning: el legado no debe romper el check).
    numbers: dict[str, list[str]] = {}
    for info in revisions.values():
        name = info["file"].name
        m = FILE_RE.match(name)
        if not m:
            warnings.append(f"{name}: fuera de la convención YYYYMMDD_NNNN_slug.py")
        else:
            numbers.setdefault(m.group(2), []).append(name)
    for nnnn, names in sorted(numbers.items()):
        if len(names) > 1:
            warnings.append(
                f"NNNN {nnnn} usado por {len(names)} archivos (legado del incidente "
                f"0009): {', '.join(sorted(names))}"
            )
    return errors, warnings, heads


def cmd_check(versions_dir: Path, quiet: bool = False) -> int:
    try:
        revisions = parse_versions(versions_dir)
        errors, warnings, heads = graph_check(revisions)
    except MigrationError as exc:
        if not quiet:
            print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if not quiet:
        print(f"versions dir : {versions_dir}")
        print(f"revisiones   : {len(revisions)}")
        for h in sorted(heads):
            print(f"head         : {h} ({revisions[h]['file'].name})")
        for w in warnings:
            print(f"WARN: {w}")
    if errors:
        if not quiet:
            for e in errors:
                print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not quiet:
        print("OK: cadena Alembic válida — única cabeza, sin duplicados ni ciclos.")
    return 0


def _next_nnnn(versions_dir: Path) -> int:
    """Siguiente número secuencial sobre el máximo existente (archivos e ids)."""
    max_n = 0
    for path in versions_dir.glob("*.py"):
        m = FILE_RE.match(path.name)
        if m:
            max_n = max(max_n, int(m.group(2)))
    for rev in parse_versions(versions_dir):
        m = re.match(r"^(\d{4})_", rev)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def _sanitize_slug(slug: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", slug.strip().lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    if not s:
        raise MigrationError("--slug vacío o inválido tras sanitizar")
    return s


TEMPLATE = '''"""Migración {nnnn}: {slug}

Revision ID: {rev}
Revises: {down}
Create Date: {date}

Creada vía db/scripts/migration_guard.py (lock de serialización — ADR 0001).
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401

revision = "{rev}"
down_revision = "{down}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TODO: DDL/catálogo aquí. Paridad obligatoria con db/schema.sql
    # (la exige db/tests/test_schema_parity.py).
    pass


def downgrade() -> None:
    # TODO: reverso exacto de upgrade().
    pass
'''


def cmd_new(versions_dir: Path, slug: str, lock_timeout: float) -> int:
    slug = _sanitize_slug(slug)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    # --- lock exclusivo con espera/retry: serializa workers simultáneos ----
    started = time.monotonic()
    notified = False
    with open(LOCK_FILE, "a+") as lock_fd:
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                waited = time.monotonic() - started
                if waited > lock_timeout:
                    print(
                        f"FAIL: no se obtuvo el lock de migraciones en "
                        f"{lock_timeout:.0f}s ({LOCK_FILE}). ¿Otro worker lo "
                        f"retiene? Reintenta o coordina en la card kanban.",
                        file=sys.stderr,
                    )
                    return 1
                if not notified:
                    print(
                        f"lock ocupado por otro worker — esperando (timeout "
                        f"{lock_timeout:.0f}s)..."
                    )
                    notified = True
                time.sleep(1.0)
        try:
            # --- dentro del lock: head REAL y numeración en el instante ----
            revisions = parse_versions(versions_dir)
            errors, _warnings, heads = graph_check(revisions)
            if errors:
                print(
                    "FAIL: la cadena actual ya está bifurcada/rota; corrígela "
                    "antes de crear migraciones nuevas:",
                    file=sys.stderr,
                )
                for e in errors:
                    print(f"  ERROR: {e}", file=sys.stderr)
                return 1
            head = heads[0]
            nnnn = _next_nnnn(versions_dir)
            date = datetime.now(timezone.utc).strftime("%Y%m%d")
            rev = f"{nnnn:04d}_{slug}"[:REV_MAX_LEN]
            path = versions_dir / f"{date}_{nnnn:04d}_{slug}.py"
            if path.exists():
                print(f"FAIL: ya existe {path.name}; elige otro --slug.", file=sys.stderr)
                return 1
            path.write_text(
                TEMPLATE.format(
                    nnnn=f"{nnnn:04d}", slug=slug, rev=rev, down=head, date=date
                ),
                encoding="utf-8",
            )
            print(f"creada      : {path}")
            print(f"revision    : {rev}")
            print(f"apilada sobre head anterior: {head}")
            print(
                "recuerda: (1) implementa upgrade()/downgrade(), (2) paridad con "
                "db/schema.sql, (3) al cerrar la lane: docker compose build migrate "
                "&& docker compose up -d, (4) comentario en tu card kanban."
            )
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    # Verificación post-creación: la cadena sigue teniendo una única cabeza.
    return cmd_check(versions_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check", help="valida el grafo (1 cabeza, sin duplicados)")
    p_check.add_argument("--versions", type=Path, default=DEFAULT_VERSIONS_DIR)
    p_new = sub.add_parser("new", help="crea una migración apilada bajo lock")
    p_new.add_argument("--slug", required=True, help="slug corto del cambio (a-z0-9_)")
    p_new.add_argument("--versions", type=Path, default=DEFAULT_VERSIONS_DIR)
    p_new.add_argument("--lock-timeout", type=float, default=180.0)
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args.versions)
    return cmd_new(args.versions, args.slug, args.lock_timeout)


if __name__ == "__main__":
    sys.exit(main())

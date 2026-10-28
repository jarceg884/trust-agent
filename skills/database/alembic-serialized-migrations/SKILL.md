---
name: alembic-serialized-migrations
description: Use when multiple agents/lanes create Alembic migrations concurrently in one repo and fork the chain. Serialize with flock + graph check wired into the migrate service.
category: database
version: 1.0.0
author: trust-agent dev
---

# Serializar migraciones Alembic entre workers simultáneos

**Problema real (board trust-agent, 2026-10-26)**: varios workers dev en
paralelo crearon migraciones sobre el mismo árbol; tres leyeron el head
"0008" y cada uno apiló su `0009` al lado → 3 heads paralelos, resueltos a
mano con una merge (`12c08d4fb858`). La convención documentada ("usa el head
actual") NO basta: no cierra la carrera leer-head → escribir-archivo.

## Mecanismo (implementado en `db/scripts/migration_guard.py`, ADR 0001)

Dos barreras complementarias:

1. **Preventiva — `new --slug <slug>`**: `flock` exclusivo sobre
   `migrations/.versions.lock` (espera activa, timeout 180 s). DENTRO del
   lock: leer head real + siguiente NNNN → escribir el archivo ya apilado.
   Si la cadena ya está bifurcada, se niega a crear (fail-closed).
2. **Detectiva — `check`**: parsea `versions/*.py` con `ast` (sin DB, sin
   importar): exige única cabeza, ids únicos, padres existentes, sin ciclos,
   ids ≤ 32 chars (`alembic_version.version_num` es VARCHAR(32)). Cableado
   al servicio `migrate` de compose ANTES de `upgrade head` → un árbol
   bifurcado muere con "MULTIPLES CABEZAS — re-apila" en vez del críptico
   `exit 255: Can't locate revision`.

## Lecciones

- **Un fork mergeado es válido**: el check debe exigir "una cabeza", no
  "un solo hijo por padre" — el caso 0009 mergeado tiene 3 hijos de 0008 y
  una sola cabeza. Rechazar hijos múltiples rompería el árbol legado.
- **Legado como WARNING, no ERROR**: NNNN duplicados y nombres fuera de
  convención (la merge) se reportan pero no rompen el check; si no, el
  mecanismo nuevo bloquea el árbol existente el día uno.
- **El walk de detección de ciclos debe tolerar padres inexistentes**
  (ya reportados como error) o revienta con `KeyError` — bug real encontrado
  por los tests del guardián.
- **El lock protege el ÁRBOL, no la DB**: un advisory lock de PostgreSQL no
  sirve; la bifurcación ocurre en archivos.
- **La imagen Docker copia `db/migrations` en build time**: el check corre
  dentro del contenedor desde `db/scripts/migration_guard.py` (solo stdlib,
  resuelve el directorio relativo a `__file__`); tras añadir migraciones hay
  que `docker compose build migrate && docker compose up -d`.
- **Evidencia de que funciona**: lanzar dos `new` con `subprocess.Popen`
  simultáneo → uno espera ("lock ocupado por otro worker") y se apila sobre
  el otro; `alembic heads` muestra UNA cabeza. Guardar la transcripción en
  `docs/evidencia/`.

## Referencias

- ADR: `docs/adr-0001-serializacion-migraciones-alembic.md`
- Convención: `db/README.md` § "Convención de serialización"
- Tests (13, sin DB): `db/tests/test_migration_guard.py`
- Evidencia real: `docs/evidencia/serializacion-migraciones-20261026.md`

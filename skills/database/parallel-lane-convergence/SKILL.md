---
name: parallel-lane-convergence
description: Use when multiple kanban lanes share one pytest suite/DB and collide during converge. Isolate runs per lane and attribute foreign failures correctly.
category: database
---

# Convergencia con lanes paralelas sobre infra de test compartida

**Problema real (board trust-agent, spec 007)**: varias lanes del board corran
la MISMA suite `platform/tests/` contra la MISMA base efímera
(`trust_agent_platform_test`) en el mismo PostgreSQL local. Síntomas vistos:

1. `UniqueViolation: tenants_pkey` en el seed — dos pytest concurrentes
   compitiendo por drop/create/seed de la misma BD.
2. La BD "desaparece" entre dos comprobaciones — el teardown de la otra lane
   la dropea.
3. Fallos que aparecen y desaparecen entre runs — la otra lane rompe y
   repara archivos compartidos (`conftest.py`, `app/config.py`) mientras
   iteras.

## Reglas

1. **Aislar la BD por lane**: `TRUST_TEST_DB` (env, default
   `trust_agent_platform_test`) en `tests/conftest.py`. Cada lane corre con
   `TRUST_TEST_DB=trust_platform_<spec>` y no compite por drop/create/seed.
   Los archivos que hardcodeaban el nombre (`test_audit_cli.py`,
   `test_rbac.py`) leen el mismo env.
2. **Correr el subconjunto propio para converger**: la suite completa incluye
   WIP de otras lanes (fallos con `OperationalError`/`NameError` =
   mid-edit). Evidencia de converge = (a) subconjunto de la lane + núcleo de
   specs previas en verde, y (b) un run completo en verde documentado con
   timestamp cuando las otras lanes están quietas.
3. **Atribuir antes de tocar**: un fallo nuevo en archivo ajeno
   (`test_budgets.py`, `test_policies_api.py`…) no es tu regresión. Verifica
   con `git`/mtime y el board antes de "arreglarlo". En cambio, un fallo en
   TU archivo causado por un cambio ajeno (p. ej. `config.py` con
   `database_url` requerido rompiendo un test sin fixture `env`) se corrige
   en tu archivo: haz que el test dependa del fixture `env`.
4. **Hotspots vivos**: `tests/conftest.py` y `db/migrations/versions/` se
   editan concurrentemente. Tras CUALQUIER edición, relee el archivo antes
   de escribir (el tool de patch avisa "modified since you last read").
   Deja `kanban_comment` con prefijo `hotspot:` al tocarlos.
5. **Semilla JSON en conftest**: los literales jsonb dentro de f-strings son
   frágiles (`'{{\"k\": v}}'` correcto; con `\\\"` extra PostgreSQL rechaza
   `Token "\" is invalid`). Verifica con `eval("f'" + literal + "'")` +
   `json.loads`, no a ojo.

## Comandos

```bash
# run aislado de lane
cd platform && TRUST_TEST_DB=trust_platform_007 .venv/bin/pytest tests/test_mi_lane.py -q
# suite db (paridad schema↔migraciones)
cd db && ../platform/.venv/bin/pytest -q
```

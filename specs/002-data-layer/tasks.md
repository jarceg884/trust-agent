# Tasks 002 — Capa de datos multi-tenant (PostgreSQL)

> Marcar `[x]` al completar. Cada tarea referencia los REQ-DB de specs/002-data-layer/spec.md.

## Fase specify/plan

- [x] T1 — Leer constitution, CONTEXTO-TAREAS, spec 001 y docs/arquitectura.md §5 (contratos normativos). Reconciliar diseño con §5.3/§5.5/§5.6 (spec §4).
- [x] T2 — Escribir specs/002-data-layer/spec.md (REQ-DB-001…042 con ACs EARS).
- [x] T3 — Escribir specs/002-data-layer/plan.md y este tasks.md.

## Fase implement

- [x] T4 — `db/scripts/bootstrap.sql`: roles `trust_app` / `trust_migrator` idempotentes. (REQ-DB-001) — verificado: doble ejecución sin error (DO ×2).
- [x] T5 — `db/schema.sql`: schemas catalog/ops, 16 tablas de negocio con `tenant_id`, FKs compuestos, CHECKs, índices `(tenant_id, …)`, seeds de catálogos, RLS ENABLE+FORCE + política única, grants y default privileges, funciones y triggers de audit (hash chain + immutabilidad + verify). (REQ-DB-002/003/010…033)
- [x] T6 — Aplicar schema.sql a la DB de desarrollo y dejarlo limpio (0 errores; solo NOTICEs de DROP IF EXISTS). (evidencia)
- [x] T7 — Proyecto Alembic: `db/alembic.ini`, `db/migrations/env.py`, migración inicial con el DDL embebido + downgrade que conserva `alembic_version`. (REQ-DB-040) — ciclo downgrade/upgrade exit 0.
- [x] T8 — `db/tests/`: conftest + test_rls_coverage + test_isolation + test_audit + test_constraints + test_schema_parity + test_spec_traceability. (REQ-DB-041)
- [x] T9 — Suite completa en verde: **153 passed** (≥ 40 exigidos) sobre DB efímera real. (AC-DB-041)

## Fase converge

- [x] T10 — `db/README.md` con runbook verificado de punta a punta sobre DB limpia (AC-DB-042).
- [x] T11 — Verificación de convergencia: trazabilidad REQ↔AC automatizada sin huérfanos (test_spec_traceability.py); todos los AC con evidencia real; desviaciones vs arquitectura §5 = solo nombres físicos (spec §4 + comentario en la tarea kanban).
- [x] T12 — Cierre: reporte al Orchestrator con archivos tocados y qué necesita QA.

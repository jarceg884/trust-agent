# Tasks 003 — Control plane core

Spec: `specs/003-control-plane/spec.md` · Plan: `specs/003-control-plane/plan.md`

- [x] T1. Spec 003 (specify): requisitos EARS + ACs + decisiones vinculantes.
- [x] T2. Plan técnico (estructura, flujo de request, riesgos).
- [x] T3. Scaffold `platform/`: pyproject, venv (uv), alembic.ini → db/migrations, config.py, db.py, errors.py, main.py con /healthz. (AC-CP-001/002/003/004)
- [x] T4. Auth: jwks.py (verificación completa + negativos), service.py (mint/lookup/revoke/refresh con hashes), router.py (/api/auth/*, /api/me). (AC-CP-020…026)
- [x] T5. Multi-tenant: authenticate + get_tx con set_config por transacción; tenant suspended → 403; cross-tenant → 404. (AC-CP-010/011/012)
- [x] T6. RBAC: carga data-driven desde catalog, require_permission, scope dept_admin, CSRF para consola, MfaGate. (AC-CP-024/025/030/031/032)
- [x] T7. Jobs: service (enqueue idempotente, claim SKIP LOCKED), worker (python -m app.jobs.worker), router GET /api/jobs/{id}. (AC-CP-040/041/042)
- [x] T8. Transversales: audit.emit + verify endpoint, governance combinator + /api/me/policies, vault.crypto, devices tokens + gate, catálogos models/connectors. (AC-CP-050…054)
- [x] T9. Suite de tests: conftest (DB efímera vía alembic canónico + mock IdP HTTP + seed 2 tenants), tests por módulo, e2e Keycloak real. (todos los AC)
- [x] T10. Converge: suite en verde (80/80), greps de seguridad (`verify=False`, `grant_type=password` en `app/`) = 0, arranque real uvicorn + curl `/healthz` 200, suite db/ intacta (153/153), trazabilidad REQ↔AC, checklist de spec §5 verificado.

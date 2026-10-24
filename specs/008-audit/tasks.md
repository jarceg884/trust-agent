# Tasks — Spec 008 Audit

| # | Tarea | Estado | Evidencia |
|---|---|---|---|
| T1 | `app/audit/service.py`: `query_events()` con filtros (actor_id, action, target_type, target_id, device_id, department_id, since, until, q) + paginación + scope por rol (D-AU-02) | [x] | test_audit_query.py::test_filters_and_pagination |
| T2 | `app/audit/router.py`: `GET /api/audit/events` (`audit.view` + MFA) | [x] | test_audit_query.py |
| T3 | `app/audit/router.py`: `GET /api/audit/events/export?format=csv\|json` (tope 10k) | [x] | test_audit_query.py::test_export_csv_json |
| T4 | `POST /api/admin/audit/verify` emite `audit.verified` (source=api) | [x] | test_audit_query.py::test_verify_emits_event |
| T5 | `app/audit/cli.py`: `verify <tenant_id> \| --all`, exit 0/1/2, `audit.verified` (source=cli); console script `verify-audit-chain` | [x] | test_audit_cli.py (subprocess real) |
| T6 | Auth: `auth.login` en mint, `auth.login_failed` post-tenant con commit explícito, `auth.logout`, `refresh_reuse` auditado | [x] | test_audit_auth_events.py |
| T7 | Jobs: `job.created`/`job.completed` (hit idempotente no re-emite) | [x] | test_audit_auth_events.py::test_job_events |
| T8 | Tests de tamper a nivel plataforma: UPDATE fila 2 → broken seq 2 (API+CLI); DELETE → broken; UPDATE/DELETE como trust_app → permiso denegado | [x] | test_audit_cli.py::test_tamper_* |
| T9 | REQ-AU-031: acciones de dominio representativas (tenant.provisioned, policy.updated, vault.credential_used, budget.cutoff, device.enrolled/revoked, model.consumed con cost_cents/device_id) encadenan y verifican OK | [x] | test_audit_query.py::test_domain_actions_chain |
| T10 | Contrato de emisión §5 notificado a specs 004–007 (comentarios en cards) | [x] | comentarios kanban t_8b113637/t_3b4fb364/t_4b540638/t_256e5e87 |
| T11 | Converge: suite completa en verde + checklist de spec.md §6 | [x] | evidencia en spec.md §6 |

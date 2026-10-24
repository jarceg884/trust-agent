# Plan — spec 013

| # | Tarea | Ítem | Artefactos |
|---|---|---|---|
| 1 | Seam de reloj `budgets.today()` + test de rollover daily (corte → avance de reloj → desbloqueo + contador en 0) | AC-013-010 | `app/governance/budgets.py`, `app/governance/spend.py`, `tests/test_budget_rollover.py` |
| 2 | Barrido síncrono REQ-CON-005 en `policies.upsert/deactivate` + notificación + audit | AC-013-005 | `app/governance/policies.py`, `app/governance/service.py`, `app/connectors/service.py`, `tests/test_connector_policy_sweep.py` |
| 3 | `GET /api/budgets/history` (ventanas cerradas, por nivel + by_model, reconciliación con ledger) | AC-013-012 | `app/governance/budgets.py`, `app/governance/router.py`, `tests/test_budget_history.py` |
| 4 | Retención/purga: schema §11 (checkpoint + trigger autorizado + `audit_purge_expired`), endpoints retention, CLI `purge` | AC-013-006 | `db/schema.sql`, `db/migrations/versions/20261023_0006_audit_retention.py`, `app/audit/router.py`, `app/audit/cli.py`, `tests/test_audit_retention.py` |
| 5 | WebAuthn: escalar decisión al humano (card bloqueada) — sin código | §5 | kanban card `needs_input` |
| 6 | Desktop: consumo runtime de `GET /api/branding` tras login | AC-013-BRD | `desktop-client/src/**`, build en verde |
| 7 | Overview con deptos sin presupuesto + tipo de consola | AC-013-ORG | `app/governance/budgets.py`, `console/src/lib/types.ts`, tests en `test_budgets.py` |
| 8 | Evidencia: suites platform + db (paridad schema) + builds desktop/console | todos | logs adjuntos |

Orden: 1 → 2 (MEDIUM) → 3/4/7 → 6 → 8.

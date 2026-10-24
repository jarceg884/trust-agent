# Tasks — Spec 005 Gobernanza

- [x] T1. Spec/plan/tasks (este documento).
- [x] T2. Capa de datos: sección 9 en `db/schema.sql` + migración `0004_governance` (encadenada sobre las migraciones hermanas) + conftest/aislamiento de db/tests actualizados. Verificación: suite db/ 171/171 verde (paridad pg_dump incluida).
- [x] T3. `models/litellm.py` (cliente Admin API, fail-closed) + settings + `errors.model_not_approved`.
- [x] T4. `models/access.py` + endpoints `/api/models/access`, `/allowed-for-me`, `/verify` (REQ-GOV-030/031/033/034).
- [x] T5. Emisión/revocación de virtual keys `/api/models/keys` (REQ-GOV-032/035).
- [x] T6. `governance/budgets.py` + endpoints (REQ-GOV-001…004, 012).
- [x] T7. `governance/policies.py` + endpoints (REQ-GOV-020…024).
- [x] T8. `governance/notifications.py` + `/api/me/notifications` (REQ-GOV-050).
- [x] T9. `governance/agents.py` + endpoints (REQ-GOV-040…044).
- [x] T10. `governance/spend.py` (ingest + enforcement) y `spend_worker.py` (REQ-GOV-005…011).
- [x] T11. Doble HTTP de LiteLLM (`tests/litellm_fake.py`, declarado D-GOV-15) + conftest (seed budgets/model_access + env LiteLLM).
- [x] T12. Tests de cada regla de gobernanza: `test_budgets.py` (7), `test_policies_api.py` (7), `test_model_access.py` (6), `test_agents.py` (5), `test_spend_cutoff.py` (8) — 33 tests nuevos.
- [x] T13. Converge: suite platform 183/183 + db 171/171 en verde sobre PostgreSQL 17 real; greps de seguridad limpios (verify=False=0, sin keys en claro, HS256 excluido); arranque live verificado (uvicorn :8000, /healthz 200, envelope 401, rutas registradas en OpenAPI); worker fail-closed sin LiteLLM (exit 2).

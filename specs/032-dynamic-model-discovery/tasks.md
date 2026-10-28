# Tasks — Spec 032

- [x] T1 — Verificación en vivo del contrato LiteLLM (/model/new, /model/delete por id, /model/info, /v1/models) + `store_model_in_db: true` en el proxy real.
- [x] T2 — Spec + plan (este documento).
- [x] T3 — Migración `0024_dynamic_model_discovery` (guard) + paridad schema.sql + rebuild migrate + upgrade. Incluye GRANT SELECT vault_connections → trust_migrator (D-CP-10, gap de la migración 0006 detectado por el job periódico).
- [x] T4 — `litellm.py`: add_model/delete_model/gateway_model_ids/list_gateway_models. Enmienda v1.1: pricing en `litellm_params` (el proxy real no aplica model_info al billing — spend=0 observado live).
- [x] T5 — `discovery.py`: fetch upstream, upsert/stale, registro gateway, refresh_connection/central/tenant, mark_connection_stale, audit. Robustez: refresh_tenant aísla upstreams caídos por conexión (error reportado, nunca bloquea las demás).
- [x] T6 — `connections.py`: kind llm + revoke hook (fail-closed con el gateway).
- [x] T7 — `access.py`: gate compuesto + overview por fuente (fix ORDER BY vc.name); `router.py`: POST /catalog/refresh.
- [x] T8 — jobs-worker: handler + encolado periódico idempotente (window key, cada 15 min de scheduling).
- [x] T9 — Tests (doble declarado upstream + litellm_fake extendido) — AC-032-01..04, 06, 07: 8/8 verde + regresión (model_access, custom_connections, jobs, catalogs, isolation, vault: 46) + db suite 207 (paridad schema↔alembic incluida).
- [x] T10 — Console: grupos por fuente (Central MaaS + una sección por conexión BYOK con status y Refresh) + Refresh global + badges stale — AC-032-06. Build next verde.
- [x] T11 — Evidencia live (AC-032-05, AC-032-08) + rebuild api/jobs-worker/console + converge.

# Plan técnico — Spec 032

## Componentes

1. **DB** — migración `0024_dynamic_model_discovery` (migration_guard, head 0023) + paridad `db/schema.sql` (§3.3 ampliada, §14.4 kind llm, RLS/grants §14.6-style para model_catalog).
2. **`app/models/discovery.py`** (nuevo) — fetch upstream (`GET {base}/models`, bearer, httpx), upsert/stale de filas byok, registro/baja en gateway, `refresh_connection` / `refresh_central` / `refresh_tenant` / `mark_connection_stale`, audit `model.catalog_refreshed`.
3. **`app/models/litellm.py`** — `add_model`, `delete_model`, `gateway_model_ids` (GET /model/info → {model_name: id} de db models), `list_gateway_models` (GET /v1/models). Contrato verificado en vivo contra 1.106.3.
4. **`app/vault/connections.py`** — kind `llm` (validación metadata base_url/pricing), revoke → `discovery.mark_connection_stale` (stale + gateway delete, fail-closed si el gateway no responde).
5. **`app/models/access.py`** — filtro compuesto en `effective_models_for_user` (byok: conexión active ∧ ∈ allowed_connections ∧ status available); `access_overview` expone source/connection/status/last_seen_at.
6. **`app/models/router.py`** — `POST /api/models/catalog/refresh` (connections.manage; body connection_id opcional).
7. **`app/jobs/worker.py`** — handler `model.catalog_refresh` + encolado periódico idempotente por ventana (config `catalog_refresh_hours`, default 6).
8. **Console** — `models-page.tsx` agrupado por fuente + Refresh (global/por conexión); types.
9. **Tests** — `platform/tests/test_dynamic_discovery.py` (DB efímera canónica, doble declarado: fake upstream OpenAI-compatible + `litellm_fake` extendido con /model/new, /model/delete, /v1/models). Aislamiento RLS incluido.
10. **Evidencia** — `platform/scripts/evidence_dynamic_discovery.py` (flujo AC-032-08 real).

## Orden

DB → litellm client → discovery → connections/access/router → jobs → tests → console → evidencia live → rebuild imágenes (migrate/api/console) + `docker compose up -d`.

## Riesgos

- **Hotspot migraciones**: guard + anuncio previo (hecho en la card).
- `/model/delete` por `model_id` (no nombre) → persistir `gateway_model_id`.
- Registration idempotente: re-refresh no debe duplicar en el gateway → upsert por `model_name` (skip si ya está en /model/info).
- RLS nueva en tabla de catalog: verificar que ningún lector central quede ciego (filas centrales `tenant_id IS NULL` siempre visibles).

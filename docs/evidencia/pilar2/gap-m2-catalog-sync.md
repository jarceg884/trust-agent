# Evidencia en vivo — GAP M-2: catálogo MaaS desalineado (AC-018-07, spec 018 §5)

**Tarea**: t_bf7eff20 · **Fecha**: 2026-10-27 (21:28–21:33 UTC) · **Veredicto: CONVERGIDO — 27 PASS, 0 FAIL**

## El gap (reporte revisión Pilar 2, BUG M-2)

`catalog.model_catalog` y el plan `standard` ofrecían `qwen3-32b`, un deployment que MaaS ya no sirve: cualquier tenant que lo aprobara recibía **404 `ModelArts.81009 "Invalid model."`** en cada uso (verificado 2026-10-26 y re-verificado en vivo esta corrida).

## Re-verificación en vivo (2026-10-27T20:55Z)

- `GET {MAAS_ENDPOINT}/models` (key del proveedor) → HTTP **200** con 6 modelos:
  `['deepseek-v4-flash', 'deepseek-v4.1-flash', 'deepseek-v4.1-flash-1017', 'glm-5.1', 'glm-5.2', 'glm-5.3']` — **SIN qwen3-32b**.
- `POST {MAAS_ENDPOINT}/chat/completions` con `qwen3-32b` → **404** `ModelArts.81009 "Invalid model."` (span_id 95c42080…).
- **La deriva es bidireccional**: el mismo día apareció upstream el deployment nuevo `deepseek-v4.1-flash-1017` (no estaba en la verificación del 2026-10-26). Doc oficial ya lo publica: Model List intl (2026-10-27 GMT+8: contexto 1M, máx. output 384K) y price-maas-0002 (2026-10-27 GMT+8: P1 $0.30/$1.20 por M).

## Cambios (spec-first, constitution II)

| Pieza | Cambio |
|---|---|
| `specs/018-model-access-maas/spec.md` | §5 nueva (D-018-07) + enmienda a AC-018-01: la fuente autoritativa del CONJUNTO de modelos centrales es el `GET /models` vivo; la doc oficial queda para metadatos (precios/límites) |
| Migración `0026_retire_qwen3_32b` (guard, anunciada en card c-213) | `qwen3-32b` → `stale` (conserva `model_access` e historia); alta de `deepseek-v4.1-flash-1017` con metadatos oficiales (30/120 ¢/M P1, 1M/384K) |
| Migración `0028_nullable_catalog_prices` (guard, anunciada en card c-215) | `input/output_price_cents_per_m` DROP NOT NULL: el alta automática inserta precios NULL = coste desconocido hasta sellado contra la doc (el CHECK >= 0 se conserva) |
| `db/schema.sql` | Paridad: seed con 7 filas centrales (6 available + qwen3-32b stale) y precios nullable (test_schema_parity 51 passed) |
| `platform/app/models/discovery.py` | `refresh_central` reconcilia contra el upstream vivo: retiro (ausente upstream → stale aunque el gateway lo enrute — el modo de fallo exacto del GAP M-2), alta (nuevo deployment → catálogo con precios NULL + registro inmediato en el gateway vía `/model/new`), restore (reaparece → available). Sin `TRUST_MAAS_*` degrada al chequeo de routing (nunca fail-open) |
| `platform/app/jobs/worker.py` + `discovery.active_tenants` | El job periódico `model.catalog_refresh` se encola para TODOS los tenants activos (antes: solo con conexiones llm) — la deriva central afecta a todos |
| `platform/app/config.py` + `docker-compose.yml` + `.env` | `TRUST_MAAS_ENDPOINT`/`TRUST_MAAS_API_KEY` (key en memoria por uso, jamás en respuestas/logs/audit — verificado E6c) |
| `litellm-real/config.yaml` + reinicio del proxy real (:4000) | Fuera `qwen3-32b`, dentro `deepseek-v4.1-flash-1017` (AC-018-03: model_list = deployments vivos). `GET /v1/models` del proxy: 6 deployments + probe residual de spec 032 |
| Tests | `test_central_sync_against_live_upstream` nuevo (retiro/alta/restore contra doble upstream declarado); `test_catalogs`, `test_model_access`, `test_global_refresh_and_central_sync`, `test_periodic_refresh_job_idempotent` actualizados al catálogo enmendado |

## Evidencia (script `platform/scripts/evidence_catalog_sync.py`, corridas reales)

- **Corrida 1** (`--slug catalog-sync-1027a`, 21:28Z): 32 PASS, 1 FAIL — capturó la TRANSICIÓN `restored: 1` (el refresh central restauró `deepseek-v4.1-flash-1017` a available tras el reinicio del proxy) y el 403 upstream del modelo nuevo.
- **Corrida 3** (`--slug catalog-sync-1027c`, 21:32Z): **27 PASS, 0 FAIL** (log: `evidencia-live-catalog-sync.log`).

Puntos clave de la corrida verde:

- **E1** `GET /models` vivo citado con fecha → 6 modelos, sin qwen3-32b.
- **E2** Tenant NUEVO con plan `standard` → aprueba exactamente `['deepseek-v4-flash', 'deepseek-v4.1-flash', 'deepseek-v4.1-flash-1017', 'glm-5.1', 'glm-5.2', 'glm-5.3']` — **qwen3-32b NO se ofrece** (AC-018-07b). Login real PKCE+TOTP.
- **E3** `POST /api/models/catalog/refresh` (admin) → 200 `{scope: central, stale: 0, restored: 0, added: [], gateway_models: 7, upstream_models: 6}` — el refresh ve el upstream vivo.
- **E4** Centrales `available` en DB == `GET /models` vivo, **1:1** (AC-018-07a); `qwen3-32b` `stale` en DB con su `model_access` conservado.
- **E5** Por cada modelo del catálogo: `verify` → 200 y llamada REAL `chat/completions` vía gateway → **200** (`deepseek-v4-flash`, `deepseek-v4.1-flash`, `glm-5.1`, `glm-5.2`, `glm-5.3`). `qwen3-32b`: `verify` → **403 `model_not_approved`** por policy (fail-closed, jamás 404 de modelo inexistente) (AC-018-07c/e).
- **E6** Audit `model.catalog_refreshed` con payload `{scope, stale, restored, added, gateway_models, upstream_models}` — sin keys (E6c).

## Pendiente humano (no bloquea el cierre del gap)

- **`deepseek-v4.1-flash-1017` responde 403 `ModelArts.81004`** ("you do not have access to it") con la key del proveedor: el deployment EXISTE upstream (listado en `/models`) pero la key "Inforum" no tiene el acceso habilitado — decisión de operador en la consola MaaS. El catálogo es correcto (el modelo existe); la plataforma lo gobierna con normalidad (verify 200, vkey lo incluye). No es un 404 de modelo inexistente: clase de error distinta y diagnosticable.
- El `probe-pricing-test` residual en el gateway DB es artefacto de la evidencia spec 032 (no está en el catálogo ni es aprobble); limpieza opcional.

## Cómo re-verificar

```bash
cd /root/trust-agent/platform
.venv/bin/python scripts/evidence_catalog_sync.py --slug <slug-nuevo>
.venv/bin/python -m pytest tests/test_dynamic_discovery.py tests/test_catalogs.py tests/test_model_access.py -q
cd ../db && TRUST_DB_CONTAINER=trust-agent-db ../platform/.venv/bin/python -m pytest tests/ -q
```

El mecanismo anti-deriva quedó operativo: el jobs-worker encola `model.catalog_refresh` para todos los tenants activos cada `TRUST_CATALOG_REFRESH_HOURS` (default 6h) y el refresh central reconcilia catálogo ↔ upstream vivo ↔ gateway en cada corrida.

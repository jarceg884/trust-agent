# Spec 036 — Model Providers: proveedores de modelos operados por el provider (repro Miller b)

| Campo | Valor |
|---|---|
| **Padre** | specs/018-model-access-maas · specs/032-dynamic-model-discovery · specs/014-provider-console · specs/025-provider-superuser · repro Miller t_64e645c1 (hallazgo b) |
| **Alcance** | El provider registra proveedores de modelos (nombre, base_url OpenAI-compatible, key en envelope del vault) desde el Provider Console, hace fetch REAL de sus modelos (`GET {base_url}/models`) y decide por tenant qué proveedores quedan habilitados ("qué accesos le doy a este cliente"). Los modelos de los proveedores habilitados aparecen POR DEFECTO en el Model Access del cliente como opciones incluidas en la plataforma (marcadas por proveedor), aprobadas a nivel org al habilitarse y restringibles por nivel como ya existe. |
| **Fuente** | Verificación en vivo del repro (a): sesión browser real (login PKCE S256 + TOTP) — Model Access renderiza los 6 deployments MaaS centrales y `POST /api/models/catalog/refresh` → 200; fetch upstream contra MaaS intl real (key "Inforum", `litellm-real/.env`). Evidencia completa: `docs/evidencia/model-providers/` (README + 4 screenshots + `05-evidence-live.log`, 37/37 PASS de `scripts/evidence_model_providers.py`). |
| **Reglas** | Nada simulado: el fetch es contra APIs reales. Keys JAMÁS en plaintext en respuestas/logs/audit (envelope crypto del vault, descifrado en memoria por uso — mismo contrato REQ-024-007/D-24-05). USD. Paridad schema.sql ↔ Alembic (AC-DB-040). SDD completo. |

## Diagnóstico del hallazgo (a) — "no me hace fetch de los modelos del MaaS"

**Evidencia (sesión browser real + audit trail del tenant `test2` de Miller, 2026-10-28 19:25–19:37 UTC)**: el catálogo central MaaS SÍ aparece en Model Access para una sesión org_admin (repro browser: 6 deployments `available` + `qwen3-32b` stale; API log de la propia sesión de Miller: `GET /api/models/access` → 200, `POST /api/models/catalog/refresh` → 200 con `{scope: central, upstream_models: 6}`; audit `model.catalog_refreshed` ×2). El refresh central funciona y es en vivo.

**Causa raíz del reporte**: NO es un bug de carga — es un hueco de producto. El único mecanismo de "fetch de modelos" visible para el cliente es el de conexiones BYOK (su PROPIA key, spec 032); el catálogo central "ya está" (seeds por migración + refresh central silencioso) y el botón "Refresh catalog" sobre un catálogo ya sincronizado no muestra fetch alguno (`0 discovered … across 0 connection(s)`). Miller buscaba el flujo de (b): registrar un proveedor con endpoint+key y ver SUS modelos fetcheados y ofrecidos a los clientes por defecto. Ese flujo no existía; esta spec lo construye. El entregable 1 queda cubierto por la evidencia del repro (screenshot de Model Access con los modelos MaaS visibles) + esta sección.

## 1. El registro de proveedores (D-036-01)

**Decisión**: nueva tabla `ops.model_providers` (dominio del provider, mismo patrón que `ops.provider_operators`; sin RLS — el acceso es solo vía API del provider, grants a trust_app/trust_migrator como las demás ops):

- `id uuid PK`, `name text UNIQUE NOT NULL`, `base_url text NOT NULL` (http/https, endpoint OpenAI-compatible), `status text NOT NULL DEFAULT 'active' CHECK (active|disabled)`.
- Key en envelope del vault (mismo crypto que `vault_connections`): `ciphertext_secret bytea NOT NULL`, `ciphertext_dek bytea NOT NULL`, `nonce bytea NOT NULL`. La key se descifra EN MEMORIA, por uso; jamás en respuestas, logs ni audit.
- Pricing declarado por el provider (mismo contrato BYOK declarado-0, D-032-01): `input_price_cents_per_m numeric(12,3) NULL`, `output_price_cents_per_m numeric(12,3) NULL` — NULL/0 = coste desconocido ⇒ spend computado por lo que LiteLLM pueda saber; el corte de budget sigue operando.
- `display_name text NULL` (etiqueta para clientes), `created_at/updated_at timestamptz`.

Habilitación por cliente: `ops.model_provider_tenants` (`provider_id FK`, `tenant_id FK → tenants`, `enabled boolean NOT NULL DEFAULT true`, `enabled_at/disabled_at timestamptz`, PK compuesta). Es la "política de accesos del proveedor": sin fila o con `enabled=false`, el cliente NO ve los modelos del proveedor.

> **AC-036-01**: `POST /api/admin/model-providers` (provider operator) crea el proveedor con key en envelope (ninguna respuesta/log contiene la key); `GET` lista sin material de key; base_url inválida o name duplicado → 422/409.

## 2. Fetch real + catálogo por tenant (D-036-02)

**Decisión**: el discovery reutiliza la maquinaria de spec 032 (`fetch_upstream_models`: normalización `/v1`, bearer key, 503 en error — el catálogo nunca inventa modelos). Cada modelo descubierto de un proveedor habilitado para un tenant se materializa en `catalog.model_catalog`:

- `source='provider'` (nuevo valor del CHECK), `tenant_id` = cliente habilitado, `provider_id` FK → `ops.model_providers`, `connection_id NULL`.
- `model_name = prov:{provider_id}:{upstream_id}` — namespacing por UUID del proveedor: sin colisión con centrales ni byok. El MISMO `model_name` se comparte entre los tenants habilitados (una fila por tenant; UNIQUE parcial `(tenant_id, model_name) WHERE source='provider'`). Todas las lecturas por nombre viven bajo RLS del tenant, así que `_model_id` y el gate son inequívocos.
- `display_name` = upstream id, `provider` = nombre del proveedor, precios = declarados del proveedor.
- Gateway LiteLLM: UN registro por modelo de proveedor (`POST /model/new`, api_base = base_url, api_key = key del proveedor, pricing en `litellm_params` Y `model_info` — enmienda v1.1 de spec 032); `gateway_model_id` se persiste en cada fila. Compartido entre tenants: se elimina del gateway solo cuando ningún tenant lo tiene `available`.

Ciclo de vida (espejo de D-032-02): modelo ausente del upstream en un refresh → `stale` (fail-closed: fuera del conjunto efectivo, `verify` 403); reaparece → `available`. Proveedor deshabilitado para un tenant → las filas del tenant pasan a `stale` (+ delete del gateway si es el último consumidor). Proveedor `disabled` global → todas las filas `stale` + delete del gateway.

> **AC-036-02**: con un proveedor real (MaaS intl + key "Inforum"), `POST /api/admin/model-providers/{id}/refresh` descubre sus modelos vía `GET /models` real y por cada tenant habilitado aparecen en `GET /api/models` con `source=provider`, `provider=<nombre>`; sin keys en ninguna respuesta.

## 3. Por defecto para el cliente + gobernanza existente (D-036-03)

**Decisión**: habilitar un proveedor para un tenant = "incluido en la plataforma" para ese cliente, en el mismo sentido que el `model_plan` del provisioning (spec 004): el endpoint de habilitación materializa las filas de catálogo Y aprueba a nivel org los modelos descubiertos (`model_access` level=org, `mode='provider'`, status=approved) — SOLO los que no tienen fila previa (una revocación previa del admin JAMÁS se re-aprueba: una revocación no relaja, D-GOV-11). A partir de ahí la gobernanza es la existente sin cambios: la matriz de Model Access los muestra agrupados por proveedor (badge "Provider"), el admin aprueba/restringe por nivel (org→dept→user, subset rule, revoke), `allowed_models` los intersecta, y el conjunto efectivo los gatea.

`model_access.mode` admite `'provider'` (nuevo valor del CHECK; `resolve_mode` lo estampa en el ledger).

**Gate compuesto (extensión de D-032-04)**: un modelo `source='provider'` es efectivo SOLO si (a) su fila está `available`, (b) su proveedor está `active`, y (c) la habilitación del tenant está `enabled`. Cualquier negación gana (deny-overrides). `effective_models_for_user`, `assert_model_allowed`, `allowed-for-me`, `verify` y la emisión de vkeys heredan el gate.

> **AC-036-03**: tenant con proveedor habilitado ve los modelos del proveedor en `GET /api/models/access` marcados por proveedor y aprobados a nivel org; `allowed-for-me` los incluye; deshabilitar el proveedor para el tenant → desaparecen del conjunto efectivo y `verify` → 403 + audit, aunque las aprobaciones org sigan existiendo; una revocación org previa NO se re-aprueba al re-habilitar.

## 4. Superficie API del provider (D-036-04)

Bajo `/api/admin/model-providers`, auth de operador (`get_provider_principal`) + permiso nuevo data-driven `providers.manage` (seed en `catalog.permissions` + `role_permissions` para `platform_admin`):

- `GET ""` — lista (name, base_url, status, pricing, conteos de modelos/tenants habilitados; sin key).
- `POST ""` — crea (name, base_url, api_key, display_name?, pricing?); 409 name duplicado; 422 base_url/precio inválido.
- `GET "/{id}"` — detalle + modelos descubiertos (por tenant) + tenants habilitados.
- `POST "/{id}/refresh"` — fetch REAL `GET {base_url}/models`; upsert de filas para TODOS los tenants habilitados + registro en gateway; stale de los desaparecidos (+ gateway delete). 503 upstream caído (fail-closed, nada inventado).
- `POST "/{id}/tenants"` `{tenant: slug|id}` — habilita: fila en `ops.model_provider_tenants` + discovery inmediato para ese tenant + aprobaciones org de los descubiertos (D-036-03). 404 tenant desconocido; 409 ya habilitado.
- `DELETE "/{id}/tenants/{tenant_id}"` — deshabilita: filas del tenant → stale (+ gateway delete si último consumidor). Idempotente.
- `POST "/{id}/suspend"` / `POST "/{id}/reactivate"` — ciclo de vida global del proveedor (suspend = todo stale + gateway delete).

Todo evento de operador a `ops.provider_audit` (cadena global, spec 025); los eventos SOBRE un tenant (habilitar/deshabilitar, refresh) además emiten en el `audit_log` DEL tenant (`model.provider_enabled`, `model.provider_disabled`, `model.catalog_refreshed` scope=provider — nuevas acciones seedeadas en `catalog.audit_actions`).

> **AC-036-04**: la superficie completa responde según contrato contra el stack real; un operador sin `providers.manage` recibe 403; el audit del operador registra cada acción con payload sin keys.

## 5. Refresh periódico y por-tenant (D-036-05)

**Decisión**: `refresh_tenant` (spec 032) gana una pata "providers": para cada proveedor habilitado del tenant hace fetch upstream y sincroniza SOLO las filas del tenant (upsert available / stale desaparecidos) — el job periódico `model.catalog_refresh` existente las cubre sin cambios de scheduling. El registro/eliminación en el gateway queda en el camino del provider (§2: refresh del proveedor, habilitar/deshabilitar): la pata tenant nunca borra rutas compartidas que otros clientes aún usan. El botón "Refresh catalog" del Admin Console (mismo endpoint) incluye la pata providers en su resumen.

> **AC-036-05**: el refresh global del tenant sincroniza también sus proveedores habilitados (evidencia con job `--once` y con el botón); un modelo retirado upstream queda `stale` para el tenant.

## 6. UI (D-036-06)

- **Provider Console**: sección "Model Providers" (nav nueva): tabla de proveedores (name, base_url, status, modelos, clientes habilitados), diálogo de alta (name, base_url, key, pricing opcional), botón "Fetch models" por proveedor (resultado real del upstream), panel de habilitación por cliente (multi-tenant con toggle). En el detalle de tenant, bloque "Model providers" con el estado de habilitación.
- **Admin Console → Model Access**: los modelos `source=provider` se agrupan en su propia sección por proveedor (badge "Provider · <name>", subtítulo con base_url), igual que las secciones BYOK existentes; el flujo de aprobación por nivel aplica igual (modo `provider`).

> **AC-036-06**: el Provider Console registra un proveedor, fetea sus modelos y lo habilita para un cliente; el Model Access del cliente muestra la sección del proveedor con sus modelos aprobados a nivel org (screenshot).

## 7. Datos y aislamiento (D-036-07)

Migración `0032_model_providers` (guard, head 0031): tablas `ops.model_providers` + `ops.model_provider_tenants`; `catalog.model_catalog.provider_id` + CHECKs ampliados (`source IN (central,byok,provider)`, scope: provider ⇒ tenant_id NOT NULL AND provider_id NOT NULL AND connection_id NULL); UNIQUE parcial `(tenant_id, model_name) WHERE source='provider'`; `model_access.mode` + `'provider'` **y su espejo `spend_ledger.mode` + `'provider'`** (D-GOV-06: `resolve_mode` estampa el ledger en el ingest — sin el espejo, el spend-worker muere con CheckViolation al ingerir la primera invocación de un modelo provider y congela el cursor para todos los tenants); permiso `providers.manage`; acciones de audit nuevas. Grants: trust_app SELECT/INSERT/UPDATE en ambas tablas ops (el envelope protege la key; el descifrado requiere el KEK server-side), trust_migrator ALL. Paridad schema.sql.

RLS: las filas `source='provider'` tienen `tenant_id` → la política existente de `catalog.model_catalog` las aísla por tenant (AC-032-07 se mantiene: un tenant no ve los modelos-provider de otro, y los ve solo si su fila existe — la habilitación del provider materializa exactamente las filas del tenant habilitado).

> **AC-036-07**: tenant B (sin habilitación) no ve modelos del proveedor ni en API ni en SQL bajo RLS; el tenant habilitado ve solo los suyos.

## 8. E2E real (D-036-08)

`platform/scripts/evidence_model_providers.py` contra el stack vivo: login de operador REAL → crear proveedor (MaaS intl + key "Inforum" de `litellm-real/.env`, jamás impresa) → fetch real (6 deployments) → habilitar para un tenant de prueba → Model Access del cliente con la sección del proveedor + aprobaciones org → `allowed-for-me` incluye un modelo → invocación REAL vía gateway con vkey → spend en ledger con `mode='provider'` → deshabilitar → stale + `verify` 403. Sin secrets en el output.

> **AC-036-08**: el script ejecuta el flujo completo contra APIs reales y emite evidencia (HTTP codes, catálogo, ledger) sin exponer keys.

## Out of scope

- BYOK del cliente (spec 032): sigue siendo el caso "su propia key" — esta spec NO lo toca; el provider nunca ve las keys BYOK de los clientes y el cliente nunca ve la key del proveedor.
- Pricing automático de proveedores arbitrarios (lo declara el provider; NULL/0 = desconocido).
- Rate limiting/cuotas por proveedor-tenant (los budgets existentes gobiernan el consumo).
- Modelos no-OpenAI-compatible (el mandato cubre `GET /v1/models`).

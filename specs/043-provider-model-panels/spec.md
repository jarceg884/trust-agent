# Spec 043 — Provider Model Panels: test connections + BYOK gating per client (mandato Miller)

| Campo | Valor |
|---|---|
| **Padre** | specs/036-model-providers · specs/032-dynamic-model-discovery · specs/014-provider-console · specs/009-admin-console |
| **Alcance** | (1) Provider Console → Model Providers rediseñado estilo Dify: tarjetas de proveedor con nombre, endpoint, estado, catálogo y botón **Test connection** (GET /models real — modelos, latencia, error). (2) Gating por cliente: qué proveedores habilitados por default + switch BYOK on/off por tenant. (3) Ocultación total: BYOK deshabilitado ⇒ la opción NO EXISTE en el Admin Console del cliente (no gris, no candado). (4) Admin Console → Model Access: solo proveedores habilitados + (si BYOK permitido) sección BYOK con test connection propio. |
| **Fuente** | Mandato Miller textual. Spec 036 (backend de model providers) ya implementada y verificada en vivo. |
| **Reglas** | Nada simulado — test connections reales. Keys jamás en respuestas/logs. USD. Español UI. |

## 1. Test connection (D-043-01)

`POST /api/admin/model-providers/{id}/test-connection` (operator auth + `providers.manage`):
- Descifra la key del envelope en memoria, hace `GET {base_url}/models` real con timer.
- Response 200: `{ok: true, models: ["model-id", ...], count: N, latency_ms: M}`.
- Response 200 con error: `{ok: false, error: "mensaje claro", latency_ms: M}` — nunca 5xx para errores de upstream (la UI muestra el error; un 5xx rompe el toast).
- La key jamás aparece en el payload.

`POST /api/vault/connections/{id}/test-connection` (admin auth, tenant-scoped):
- Solo conexiones `kind='llm'` (BYOK). Descifra la key, GET /models real.
- Mismo formato de response.
- Requiere `allowed_connections` policy (igual que el uso runtime).

> **AC-043-01**: test connection contra un proveedor real retorna los modelos y latencia; contra un endpoint caído retorna `{ok: false, error: "..."}` sin 5xx.

## 2. BYOK gating por tenant (D-043-02)

Nueva tabla `ops.tenant_byok_policy`:
- `tenant_id uuid PK FK → tenants(id)`, `byok_allowed boolean NOT NULL DEFAULT true`, `set_at timestamptz NOT NULL DEFAULT now()`, `set_by text` (operator username).
- Sin RLS — acceso solo vía API del provider (grants trust_app/trust_migrator).
- Un tenant sin fila = BYOK permitido por defecto (default true).

API del provider:
- `GET /api/admin/model-providers/byok-policy` — lista todos los tenants con su flag byok_allowed (cross-tenant, migrator read).
- `PUT /api/admin/model-providers/byok-policy/{tenant_id}` `{byok_allowed: bool}` — upsert + audit en ops.provider_audit.

> **AC-043-02**: el provider puede ver y toggle BYOK por cliente; el cambio persiste y se audita.

## 3. Ocultación total en el Admin Console (D-043-03)

`GET /api/models/access` (tenant-scoped, admin auth):
- Lee `ops.tenant_byok_policy` para el tenant actual (cross-schema read, trust_app).
- Si `byok_allowed = false`: filtra TODAS las filas `source='byok'` del response — no aparecen en `models`, no hay sección BYOK, no hay mención.
- Expone `byok_allowed: bool` en el response para que el frontend pueda ocultar la UI de BYOK (sección de conexiones LLM, botón "Add LLM connection", etc.).

`GET /api/vault/connections` (tenant-scoped):
- Si `byok_allowed = false`: filtra conexiones `kind='llm'` del listado. El admin no ve sus conexiones LLM existentes (no puede crear, editar ni verlas — la opción no existe).

`POST /api/vault/connections` (create):
- Si `byok_allowed = false` y `kind='llm'`: 403 "BYOK is not enabled for your organization."

> **AC-043-03**: tenant con BYOK deshabilitado no ve NINGUNA mención de BYOK en Model Access ni en Credentials — ni sección, ni botón, ni conexiones existentes. Cero.

## 4. UI Provider Console — rediseño Dify (D-043-04)

Reemplaza la tabla por **tarjetas** (estilo Dify):
- Una tarjeta por proveedor: header con nombre + badge de estado (active=verde, disabled=rojo), body con base_url (mono), conteo de modelos y clientes, y botones: **Test connection** (con feedback visual: spinner → resultado inline con modelos/latencia o error), **Fetch models**, **Clients**, **Suspend/Reactivate**.
- Tarjeta MaaS central siempre presente (info del gateway, test connection contra el gateway).
- Sección **BYOK gating por cliente**: panel con tabla de tenants, cada fila con toggle de BYOK on/off + lista de proveedores habilitados/deshabilitados.
- Dialog de "Test connection" muestra resultado con icono de check/cross, lista de modelos, latencia.

> **AC-043-04**: el Provider Console muestra tarjetas estilo Dify con test connection funcional y panel de gating BYOK por cliente.

## 5. UI Admin Console — Model Access (D-043-05)

- Si `byok_allowed = false`: la sección "BYOK connection" NO se renderiza. Cero mención. El botón "Add LLM connection" en Credentials no se renderiza. Las conexiones LLM existentes no aparecen.
- Si `byok_allowed = true`: comportamiento existente + botón **Test connection** en cada sección BYOK (llama al endpoint admin).
- Los modelos `source='provider'` se muestran como ya existe (agrupados por proveedor habilitado).

> **AC-043-05**: el Admin Console del cliente con BYOK off no muestra NINGUNA opción BYOK; con BYOK on muestra todo + test connection.

## 6. E2E (D-043-06)

Flujo completo contra el stack real:
1. Provider registra 2 proveedores + test connection real en ambos.
2. Habilita solo 1 al cliente + BYOK off.
3. Admin del cliente ve SOLO ese proveedor y CERO mención de BYOK.
4. Provider activa BYOK → la opción aparece en el Admin Console.
5. Admin agrega su key propia (BYOK) → test connection → modelos al catálogo.

> **AC-043-06**: el flujo E2E se ejecuta contra APIs reales y verifica ocultación total + aparición al toggle.

## 7. Datos y migración (D-043-07)

Migración `0035_tenant_byok_policy` (guard, head 0034): tabla `ops.tenant_byok_policy`; grants trust_app SELECT/INSERT/UPDATE, trust_migrator ALL. Paridad schema.sql. Seed: ningún tenant recibe fila (default true = BYOK permitido por defecto, backwards-compatible).

> **AC-043-07**: la migración aplica sin error y no rompe tenants existentes (BYOK sigue permitido por defecto).

## Out of scope

- Pricing automático (lo declara el provider).
- Rate limiting por proveedor-tenant.
- Modelos no-OpenAI-compatible.

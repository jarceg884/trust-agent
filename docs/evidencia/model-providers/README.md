# Spec 036 — Model Providers: evidencia en vivo (repro Miller t_64e645c1)

Fecha: 2026-10-28 (UTC-6) · Stack real: api/idp/db/litellm-real (proxy v1.106.3,
upstream MaaS intl real, key "Inforum" de `litellm-real/.env` — jamás impresa).

## Hallazgo (a) — "no me hace fetch de los modelos del MaaS"

**Veredicto: NO es bug de carga.** El catálogo central MaaS SÍ aparece y se
sincroniza en vivo en el Model Access del cliente:

- `01-model-access-central-maas.png` — Model Access (sesión browser real del
  admin del tenant `miller-prov-1028c`, login PKCE S256 + TOTP contra
  Keycloak): sección "Central MaaS — Platform gateway (Huawei Cloud ModelArts
  Studio) — shared, provider-operated" con los 6 deployments del catálogo
  central (deepseek-v4.1-flash, deepseek-v4.1-flash-1017, deepseek-v4-flash,
  glm-5.1, glm-5.2, glm-5.3) y `POST /api/models/catalog/refresh` → 200.
- La causa raíz del reporte es un hueco de producto (el hallazgo b): lo que
  Miller buscaba era registrar un proveedor con endpoint+key y ver SUS
  modelos fetcheados y ofrecidos a los clientes por defecto — ese flujo no
  existía; la spec 036 lo construye (ver abajo).

## Hallazgo (b) — proveedores BYOK del provider habilitados por cliente

Construido (spec 036, SDD completo en `specs/036-model-providers/spec.md`):

- **Provider Console → Model Providers** (`03-provider-console-model-providers.png`,
  `04-provider-console-clients-models.png`): registro de proveedores
  (nombre, base_url OpenAI-compatible, key en envelope del vault — jamás en
  respuestas/logs), "Fetch models" (GET {base_url}/models REAL), habilitación
  por cliente ("Clients" — la política de accesos del proveedor) y
  suspend/reactivate global.
- **Admin Console → Model Access** (`02-model-access-provider-section.png`):
  los modelos del proveedor habilitado aparecen POR DEFECTO como opciones
  incluidas en la plataforma (sección propia "MaaS Intl (platform-included)",
  badge provider/active, subtítulo con base_url), aprobados a nivel org
  (mode='provider') y restringibles por nivel como ya existía.
- **BYOK del cliente** (spec 032) sigue intacto como caso aparte.

## Cadena E2E en vivo — `platform/scripts/evidence_model_providers.py`

`05-evidence-live.log` — 37/37 PASS contra APIs reales (nada simulado):
login REAL de operador (sesión pst1) → alta de proveedor (key en envelope,
higiene estricta: la key no aparece en NINGUNA respuesta ni payload de
audit) → fetch REAL (6 deployments) → provisioning + login REAL PKCE+TOTP →
habilitación por cliente (discovery + aprobaciones org) → Model Access con
source=provider marcado por proveedor → allowed-for-me (gate compuesto) →
refresh global del tenant con la pata providers (D-036-05) → vkey +
invocación REAL vía gateway LiteLLM (MaaS responde) → spend_ledger
mode='provider' + budget org descontado (USD) → deshabilitación → stale +
verify 403 + audit + vkey sin routing.

## Bugs reales encontrados y corregidos durante la validación

1. **`spend_ledger_mode_check` sin `'provider'`** (migración 0032 extendió
   `model_access.mode` pero no su espejo D-GOV-06): el ingest del
   spend-worker moría con CheckViolation al ingerir la primera invocación de
   un modelo provider y CONGELABA el cursor global para todos los tenants.
   Fix: migración 0032 + schema.sql + test de regresión
   (`test_ac036_spend_ledger_accepts_provider_mode`).
2. **Conteos del provider surface siempre 0** (RLS): `list_providers`/
   `get_provider` leían `catalog.model_catalog` con el engine del app sin
   contexto de tenant → RLS ocultaba TODAS las filas (la columna "models" de
   la UI y el detalle llegaban vacíos). Fix: lectura cross-tenant por la
   conexión migrator (BYPASSRLS, patrón D-CP-10/D-ONB-10) + test de
   regresión (`test_ac036_provider_listing_counts_cross_tenant`).
3. **DB viva desalineada del borrador previo de la migración**: el intento
   anterior aplicó un borrador de la 0032 sin la columna `key_id` (INSERT
   moría con UndefinedColumn). Realineada con ALTER canónico.

## Tests

- `platform/tests/test_model_providers.py` — 10/10 (AC-036-01…07 + 2
  regresiones de los bugs arriba).
- Paridad schema.sql ↔ Alembic: 2/2 (`db/tests/test_schema_parity.py`).
- Regresión models (test_model_access, test_dynamic_discovery,
  test_catalogs): en verde.
- tsc --noEmit limpio en ambas consolas.

## Residuos de la validación en el stack dev (intencionales, referenciados por la evidencia)

- Tenants `miller-prov-1028b` / `miller-prov-1028c` (provisioning idempotente
  por slug) y proveedores `maas-intl-miller-prov-1028b` (deshabilitado,
  modelos stale) / `maas-intl-miller-prov-1028c` (habilitado para
  miller-prov-1028c, 6 modelos available).

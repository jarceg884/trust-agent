# Plan técnico — Spec 024 (conexiones personalizadas)

## Capas

1. **DB** (migración `0012_custom_connections`, down_revision = `0011_gov_agent_policy_keys`):
   - Tabla `public.vault_connections` (id, tenant_id, created_by, name, kind, metadata
     jsonb, status active|revoked, ciphertext_secret/ciphertext_dek/nonce/key_id,
     last_rotated_at, last_used_at, created_at, updated_at; UNIQUE(tenant_id, id),
     UNIQUE(tenant_id, name), FK users(tenant_id, created_by)).
   - Trigger `set_updated_at()`; índice `(tenant_id, created_at DESC)`; RLS ENABLE+FORCE
     + política `tenant_isolation` (patrón §7/9.6); GRANT DML a trust_app (patrón 0004).
   - Catálogo: policy key `allowed_connections` (list, default `["*"]`); permiso
     `connections.manage` (solo org_admin); audit actions `connection.created/updated/
     revoked/used`.
   - Paridad: mismas sentencias en `db/schema.sql` (§3.2 key, §7 array RLS, §14 nuevo).
   - `db/tests/conftest.py`: BUSINESS_TABLES += `vault_connections`;
     `test_rls_coverage.py`: policy_keys 16 → 17.
2. **Backend** (`platform/app/`):
   - `vault/connections.py` (nuevo): servicio + router
     `/api/vault/connections` (POST/GET/GET id/PUT id/POST revoke/POST use), MFA gate,
     permiso `connections.manage` en escrituras, validación metadata por kind (D-24-07
     + anti-secret-keys), one-time secret, audit, inyección runtime con gate de policy.
   - `governance/policies.py`: validación de tokens de `allowed_connections` en
     `upsert` (422 si token desconocido); `_sweep_connectors` extendida a la key.
   - `connectors/service.py`: `_check_instance_governance`, `list_available` y
     `sweep_after_policy_change` aplican la regla combinada catálogo (D-24-03).
   - `vault/service.py`: `_check_governance` aplica la regla combinada a credenciales
     de conectores del catálogo.
   - `governance/bundle.py`: claim aditivo `allowed_connections` (D-24-06) — parche
     mínimo, anunciado a t_f99e12ab (dueño del archivo).
   - `main.py`: registrar el router nuevo.
3. **UI** (`platform/console/`):
   - `lib/types.ts`: tipos Connection*.
   - Componente compartido `connections-policy-selector.tsx` (catálogo + "*" +
     personalizadas, por nivel).
   - `credentials-page.tsx`: card "Custom connections" (CRUD + one-time + revoke).
   - `organization-page.tsx` (org), `departments-page.tsx` (depto) y `users-page.tsx`
     (usuario): selector de conexiones permitidas.
4. **Tests**: `platform/tests/test_custom_connections.py` (AC-24-001..005 contra la DB
   efímera canónica) + suites afectadas (sweep, bundle, policies, isolation, rbac) +
   `db/tests` (RLS/paridad).
5. **Evidencia UI**: API + console levantados localmente, browser automation real,
   screenshots en `docs/evidencia/custom-connections/`, reporte
   `docs/custom-connections.md`.

## Riesgos / hotspots

- `db/migrations/versions/` (hotspot declarado): migración 0012 anunciada en la card
  ANTES de crear el archivo; down_revision = head actual (`0011_gov_agent_policy_keys`).
- `governance/bundle.py` pertenece a la lane t_f99e12ab (en vuelo): parche aditivo de
  ~6 líneas anunciado en su card.
- `connectors/service.py` y `vault/service.py` compartidos: ediciones aditivas mínimas.

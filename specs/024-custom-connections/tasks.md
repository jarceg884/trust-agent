# Tasks — Spec 024 (conexiones personalizadas)

- [x] T1. Migración `0012_custom_connections` + paridad `db/schema.sql` + db/tests
      (BUSINESS_TABLES, conteo policy_keys) — hotspot anunciado en la card.
      (seed `vault_connections` en db/tests/conftest.py completado en el run
      final: filas tenant A/B con ciphertext opaco.)
- [x] T2. Servicio + router `app/vault/connections.py` (CRUD, one-time, audit,
      inyección runtime con gate de policy, validación metadata por kind).
- [x] T3. Governance: validación de tokens de `allowed_connections` en policies.upsert
      (422 token desconocido) + sweep en la key.
- [x] T4. Regla combinada catálogo (`"*"`/slug) en connectors/service.py (instance gate,
      list_available, sweep) y vault/service.py (_check_governance).
- [x] T5. Claim aditivo `allowed_connections` en governance/bundle.py (coordinado con
      t_f99e12ab) + `effective_connections_for_user` canónica.
- [x] T6. UI: types + selector compartido (`components/connection-policy.tsx`) + CRUD en
      Credentials + selectores en Organization / Departments / Users.
- [x] T7. Tests `platform/tests/test_custom_connections.py` (AC-24-001..005) y suites
      afectadas en verde (53 passed: custom_connections + connector_policy_sweep +
      governance_bundle + vault_connectors + vault_crypto; db/tests 180 passed,
      2 fallos pre-existentes atribuidos a la migración 0014 de otra lane).
- [x] T8. Evidencia UI real (browser automation, screenshots) +
      `docs/custom-connections.md` + checklist de convergencia en la spec.
      (Verificada de primera mano en la corrida final 2026-10-26 12:20 CST:
      6 screenshots + gate runtime 200/410/403/422 en vivo + 53 tests —
      ver `docs/evidencia/custom-connections/README.md`.)

## Checklist de convergencia (implementación == spec)

- [x] REQ-024-001 CRUD con name único, kinds validados, envelope crypto existente.
- [x] REQ-024-002 one-time: secret SOLO en create/update; list/get sin secreto/ciphertext
      (test_ac24_002).
- [x] REQ-024-003 RBAC `connections.manage` solo org_admin → 403 auditado.
- [x] REQ-024-004 audit `connection.created/updated/revoked/used` sin secreto.
- [x] REQ-024-005 key `allowed_connections` 3 niveles deny-overrides; token desconocido
      → 422 (D-24-07).
- [x] REQ-024-006 default `["*"]` compatible con tenants existentes.
- [x] REQ-024-007 uso runtime: id en efectiva o 403 `connector_not_allowed`; revocada
      → 410; inyección por-uso sin estado (D-24-05).
- [x] REQ-024-008 regla combinada catálogo + sweep en la misma transacción
      (`connector.disconnected` reason `policy_updated`).
- [x] REQ-024-009 RLS ENABLE+FORCE `tenant_isolation` + índice tenant-first +
      aislamiento verificado (db/tests/test_isolation.py).
- [x] REQ-024-010 claim aditivo en el bundle; bundles previos siguen verificando
      (test_governance_bundle).
- [x] REQ-024-011 metadata por kind validada; claves secretas en metadata → 422.
- [x] REQ-024-012 UI: CRUD en Credentials + selectores por nivel en
      Organization/Departments/Users (evidencia de navegador real en la card).

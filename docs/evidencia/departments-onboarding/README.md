# Evidencia spec 038 — Estructura real de departamentos al nacer (t_4bf95d12)

Fecha: 2026-10-28 · Stack vivo (docker compose, Keycloak real :8080, PostgreSQL
:55432) · Journey AC-38-010 completo por navegador + SQL.

## 1. Nacimiento: self-signup → org VACÍA (AC-38-003, D-38-01)

```
POST /api/auth/signup-org
{"organization_name":"Estructura Viva Corp","slug":"estructura-viva",
 "admin_email":"admin@estructura-viva.test","admin_password":"…","admin_display_name":"Admin Viva"}
→ 201 {"org":"estructura-viva","status":"pending_activation",
        "tenant":{"counts":{"departments":0,"users":1,"org_budgets":1,
                            "active_org_policies":3,"approved_models":6}}}
```

**0 departamentos al nacer** (antes: seed fijo de 4). Sin eventos
`department.created` en el audit del alta.

## 2. Activación por el proveedor

```
POST /api/admin/tenants/estructura-viva/activate  (Bearer provider)
{"initial_budget_usd": 500} → 200, status=active
SELECT status, structure_onboarding_pending FROM tenants WHERE slug='estructura-viva'
→ active | t      ← flag de onboarding PENDIENTE (D-38-02)
```

La migración 0033 (aplicada al stack vivo vía `docker compose run --rm migrate`)
dejó el flag pendiente en los **17 tenants existentes** — la org de Miller lo
verá en su próximo login:

```
SELECT count(*) FROM tenants WHERE structure_onboarding_pending → 17
```

## 3. Primer login → wizard "Organization structure" (AC-38-004)

Login real por navegador (PKCE contra el realm `estructura-viva`, tema
white-label 🐴). El gate del layout presenta el wizard UNA vez con las 12
plantillas del catálogo en español:

```
SUGGESTED TEMPLATES
+ Operaciones de Nube  + Operaciones de Clientes  + Datos y Analítica
+ Ingeniería           + Finanzas                 + Recursos Humanos
+ Operaciones de TI    + Legal                    + Marketing
+ Producto             + Ventas                   + Seguridad y Cumplimiento
```

Estructura declarada desde el wizard (2 con plantilla, 2 custom — D-38-04,
nombres editables default=display es): **Finanzas** (finance), **Marketing**
(marketing), **Ventas**, **Recursos Humanos** → "Save structure".

SQL contra la DB viva:

```
SELECT action, count(*) FROM audit_log WHERE tenant_id='53cc7241-…'
 AND action IN ('department.created','onboarding.structure_completed') GROUP BY action
→ department.created             | 4
  onboarding.structure_completed | 1

SELECT payload FROM audit_log WHERE action='onboarding.structure_completed'
→ {"source": "wizard", "created": 4, "deleted": 0, "renamed": 0}

SELECT name, profile_template FROM departments … ORDER BY name
→ Finanzas | finance · Marketing | marketing · Recursos Humanos | · Ventas |
```

El overview de budgets lista EXACTAMENTE los 4 departamentos en español
(LEFT JOIN D-013-07, sin budgets aún) — ver
`overview-estructura-real.png`. El wizard NO reaparece en el siguiente login
(flag `f`).

## 4. Tenant existente ve el wizard UNA vez (AC-38-010b, AC-38-005)

Se re-puso el flag pendiente por SQL (el estado exacto con el que quedaron los
17 tenants pre-spec) y se recargó:

```
UPDATE tenants SET structure_onboarding_pending=true WHERE slug='estructura-viva'
→ reload /departments → wizard visible + botón "Set up structure" en la página
→ "Skip for now"
→ audit: {"source": "skip", "created": 0, "deleted": 0, "renamed": 0}
→ flag f; el wizard NO reaparece en el siguiente login
```

## 5. Suites

- `tests/test_departments_onboarding.py` (nueva, AC-38-001..009): **12/12 en
  verde** sobre PostgreSQL real (4 tenants purpose-built, mock-IdP con JWKS
  real, sessions reales).
- Enmiendas por fin del seed: `test_provision` (12/12, incluye 2 tests nuevos
  de declaración mixta AC-38-002), `test_tenant_self_signup`, `test_deprovision`,
  `test_profiles_templates` (12 plantillas es+en), `e2e/test_control_plane_flow`
  (provisiona con declaración explícita).
- Suite completa platform/tests: 725 passed; rojos ajenos documentados abajo.
- `db/tests` con `TRUST_DB_CONTAINER=trust-agent-db`: paridad
  schema.sql §24 ≡ migración 0033 **en verde** (205 passed; 2 fallos
  pre-existentes de lanes hermanas, ver §6).

## 6. Rojos ajenos encontrados (NO causados por este diff — atribución verificada)

1. `test_auth_panel.py` + `test_vault_connectors.py` colisionan por TENANT_C
   fijo (`33333333-…`) en fixtures de módulo: reproducible con SOLO esos dos
   módulos (16 passed + 14 errors). Pre-existente.
2. `db/tests/test_rls_coverage.py` (2 tests): esperan sets sin
   `model_providers`/`model_provider_tenants` (spec 036, lane hermana) ni
   `tenants.delete`/`providers.manage` (deprovisioning 0031 + spec 036).
3. `test_model_providers.py::test_ac036_spend_ledger_accepts_provider_mode`
   (lane hermana, flako order-dependiente en suite completa).

## 7. AC-38-008 (Provider Console) — verificación parcial documentada

El formulario nuevo (catálogo 12 con display es + nombres custom + preview +
aviso "starts empty", default sin selección) está implementado, typecheck
limpio y construido en la imagen viva `trust-agent/provider-console:local`.
El payload exacto que envía (`departments: [...claves, ...{name}]`) está
verificado EN VIVO a nivel API contra Keycloak real
(`test_provision_declared_departments_mixed`: 201 con estructura exacta
`Finance/Finanzas/Ventas` + rollback total en inválidos). El recorrido por la
UI requiere credenciales del operador del provider (password one-time no
recuperable por diseño, D-PS-03) — no se ejercitó el click-path visual.

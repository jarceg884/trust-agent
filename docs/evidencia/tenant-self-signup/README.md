# Evidencia — Spec 026: Self-signup de organizaciones (mandato Miller)

Journey completo REQ-SS-007 verificado contra el stack vivo el **2026-10-26
(20:30–21:00 UTC)** — nada simulado. El Admin Console ES el SaaS: un cliente
nuevo crea su organización en `/signup`; nace `pending_activation` y SIN
budget; el proveedor la activa y le asigna el budget inicial.

## Corrida de evidencia (API + Keycloak + LiteLLM/MaaS reales)

Script: `platform/scripts/evidence_tenant_self_signup.py`
Log completo: `evidence-026-journey.log` — **26 PASS, 0 FAIL** con el tenant
`selfsignup-e2e-2035` (presupuesto inicial USD 75.50):

| Fase | Checks | Resultado |
|------|--------|-----------|
| S1 Signup público `POST /api/auth/signup-org` | 201, `pending_activation`, sin credenciales en la respuesta | PASS |
| S2 Provider ve el tenant apagado | lista con status `pending_activation`, budget org **0¢** | PASS |
| S3 Login REAL del admin | baile OIDC PKCE S256 contra el realm KC nuevo | PASS |
| S4 Fail-closed | `/api/me`, models, agents, devices, budgets, policies, vault, departments → **423 `tenant_pending_activation`**; `logout` → 204 | PASS |
| S5 Validaciones | slug duplicado → 409; password < 12 → 422; slug inválido → 422; abuso IP → **429** | PASS |
| S6 Activación por el proveedor | `POST /api/admin/tenants/{slug}/activate` → 200; status `active`; budget org = 7550¢; ya-active → 409; budget ≤ 0 → 422; sin token → 401 | PASS |
| S7 Admin re-entra | re-login real; `/api/me` → 200; `/api/models` → 200; notificación `tenant_activated` visible | PASS |
| S8 Spend REAL | invocación glm-5.2 (key virtual → proxy LiteLLM → Huawei MaaS) → 200; **budget org descuenta 0¢ → 3¢**; corte armado (3¢/7550¢) | PASS |

### Cadena de audit en la BD live (tenant `selfsignup-e2e-2035`)

```
seq 1-16  department.created ×4, user.created, role.assigned,
          budget.updated (0¢), policy.updated ×3, model.approved ×6   (actor user)
seq 17    tenant.self_registered                                        (actor user)
seq 18-19 auth.login / auth.logout                                      (org_admin)
seq 20    tenant.activated                                              (actor provider)
seq 21    budget.assigned                                               (actor provider)
seq 22-23 auth.login, model.key_issued                                  (org_admin)
```

## UI Admin Console (navegador real, clics reales)

| Screenshot | Qué muestra |
|------------|-------------|
| `01-signup-public-form.png` | `/signup` público: form org name + slug auto-derivado + admin email/nombre/password |
| `02-signup-success-pending.png` | "Organization created — pending activation by the provider" con CTA a login |
| `03-pending-activation-gate.png` | Tras el login OIDC real del admin pending: pantalla dedicada "Your organization is pending activation" — sin shell, sin nada funcional, solo Sign out |

- El seam de la entrada pública renderiza los tres caminos: **Continue to
  sign-in** (`/login`), **Join your organization** (`/register?org=`, spec
  015 — usuario dentro de org existente) y **Create your organization**
  (`/signup`, esta spec — organización nueva).
- Tenants creados vía UI en esta corrida: `acme-browser-2040`,
  `screenshot-org-2041` (ambos `pending_activation` en la BD live).

## UI Provider Console

- El bundle servido por el contenedor `trust-agent-provider-console`
  (:3200, verificado en `/app/.next`) contiene el badge "Pending", el filtro
  por estado y el panel **Activate + initial budget (USD)** del detalle.
- El flujo que ese panel conduce (lista de pendientes → activate → budget →
  notificación) está probado en vivo 26/26 (fases S2/S6/S7).
- **Click-through pendiente**: el login de operador del Provider Console
  (spec 025, lane t_6c2be725 en vuelo) rotó el password one-time del
  bootstrap; se pidió credential dev por comentario en su card. El gate QA
  (t_e8a28fce) puede validarlo cuando esa lane converja.

## Tests

- `platform/tests/test_tenant_self_signup.py` — **9/9 PASS** (AC-26-001..006,
  DB aislada `trust_agent_t04dc184b_test`).
- Regresión: `test_provider_admin` + `test_provision` + `test_auth_ux` +
  self-signup = **44 passed**.
- `db/tests/test_schema_parity.py` — **2/2 PASS** (migración 0015 ↔ schema.sql).
- Builds: `npm run build` exit 0 en console (rutas `/signup` + gate) y
  provider-console; lint console 0 problems.

## Migración

`db/migrations/versions/20261026_0015_tenant_self_signup.py`
(`down_revision = 0014_auth_ux_policies`, anunciada en la card antes de
crearse): CHECK de `tenants.status` extendido a `pending_activation` + 3
acciones de audit (`tenant.self_registered`, `tenant.activated`,
`budget.assigned`). Aplicada a la BD live (head actual del árbol:
`0017_live_policy_presence`).

## Nota operativa

La corrida S5 quema el contador IP de signup (`ops.login_throttle`,
clave `signup:ip:127.0.0.1`, ventana 15 min) — es el comportamiento esperado
del rate limit. Para re-correr el script antes de que expire la ventana,
borrar solo esa fila (residuo de prueba, IP loopback del dev stack).

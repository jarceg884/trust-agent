# QA del core del control plane — suite e2e + auditoría de seguridad

> Tarea kanban `t_974c6a5c` (perfil QA) · 2026-10-23 · Reporte con evidencia real.
> Alcance: TODO el control plane (specs 003→008, tareas t05–t10) contra
> `auditoria/AUDITORIA-AIDLC.md` (hallazgos C1–C6 + altos) y el constitution.

## Veredicto

| Área | Veredicto | Evidencia |
|---|---|---|
| (1) Suite e2e flujo completo | **PASS** | 10/10 pasos, `tests/e2e/test_control_plane_flow.py` |
| (2) Checklist seguridad AIDLC | **PASS** | 0 recurrencias de C1–C6 y altos, `tests/e2e/test_security_checklist.py` |
| (3) Aislamiento multi-tenant | **PASS** | API 404/403 + RLS fail-closed, `tests/e2e/test_isolation_e2e.py` |
| Suite completa platform | **PASS** | **211 passed** (183 previas + 28 e2e nuevas), 0 regresiones |
| Suite db | **PASS** | **171 passed** |

**Hallazgo menor (LOW)**: `device_token_secret` con default no-vacío
(`platform/app/config.py:40`) — inconsistente con el patrón fail-closed del
resto de secrets. Tarea dev creada (ver §5). No bloquea: el DeviceGate exige
además sesión válida + fila `devices` activa, y el default está marcado
`change-me` en un proyecto dev-local (constitution VII).

## Infraestructura real utilizada

- **PostgreSQL 17** real (contenedor `trust-db`, 127.0.0.1:55432), DB efímera
  `trust_agent_platform_test` creada por la suite vía Alembic canónico.
- **Keycloak 26.2** real (contenedor `keycloak`, 127.0.0.1:8080): realm
  `e2e-corp` creado de verdad por el provisioning, cliente público PKCE S256,
  mapper `tenant_id`, usuarios reales con atributo.
- La app corre SIEMPRE como `trust_app` (sujeto a RLS, sin BYPASSRLS).

**Dobles declarados** (constitution I — nada simulado sin declararlo):

1. **LiteLLM** = doble HTTP `tests/litellm_fake.py` (D-GOV-15) con la
   semántica documentada de la Admin API (`/key/generate|block|unblock|info`,
   `/spend/logs/v2`, `/chat/completions` con corte nativo 429 / key bloqueada
   401 / modelo fuera de lista 403). Contra un proxy real solo cambia
   `TRUST_LITELLM_BASE_URL`.
2. **IdP para pasos con MFA** = mock JWKS de `tests/conftest.py` (RSA real,
   verificación PyJWKClient real). Motivo, con probe empírico: **Keycloak 26
   NO permite enrolar TOTP vía Admin REST** (`POST /users/{id}/credentials`
   → `404`, verificado), y el scraping de HTML/QR está prohibido por diseño
   (hallazgo C5). El login PKCE REAL contra el realm real se ejercita ANTES
   de la sustitución (pasos 2 y 3), y la sustitución se declara en el propio
   test con `UPDATE tenants.oidc_issuer_url`.

## 1. Suite e2e — flujo completo (`platform/tests/e2e/test_control_plane_flow.py`)

Un solo tenant recién aprovisionado atraviesa todos los dominios en orden:

| Paso | Test | Qué verifica (evidencia ejecutada) |
|---|---|---|
| 1. Alta de tenant | `test_01_provision_tenant` | `POST /api/admin/tenants` (token de proveedor) → 201; healthcheck ok; realm KC real existe; admin con `CONFIGURE_TOTP`; 4 deptos seed; presupuesto org 50000¢; políticas default del catálogo; `audit_verify_chain()` = ok |
| 2. Login real + MFA | `test_02_real_pkce_login_and_mfa_gate` | El password one-time NO entrega code (required actions); PKCE S256 real tras completar acciones vía Admin REST → `id_token` con `tenant_id` firmado por el realm; `POST /api/auth/session` → 201 con JWKS real; sin OTP → `mfa_verified=false` y `/api/me` → **403 mfa_required** |
| 3. Usuarios | `test_03_worker_user_from_idp` | Usuario obrero REAL en el realm KC (mapper tenant_id) + fila de directorio + rol; login PKCE real del obrero → puerta MFA; sesión MFA vía doble declarado |
| 4. Políticas | `test_04_policies` | `allowed_connectors` default `[]` → catálogo vacío para el obrero; política org `["vmware","email"]`; depto estrecha a `["vmware"]`; **relajación del depto → 422**; clave invariable (`require_mfa`) → 422; efecto visible: obrero ve SOLO vmware |
| 5. Presupuestos | `test_05_budgets` | Depto 20000¢, usuario 5000¢; Σ usuarios > depto → **422**; Σ deptos > org → **422**; overview con `organization.budget_cents` y deptos; `/api/budgets/me` del obrero |
| 6. Uso de modelo | `test_06_model_usage` | `allowed-for-me` incluye qwen; `/verify` 200; emisión de key virtual → **plaintext EXACTAMENTE una vez**, `max_budget_usd=50.0`; DB guarda SOLO `sha256(key)` (verificado contra el hash calculado); 2 invocaciones al data plane → 200 |
| 7. Corte | `test_07_spend_cutoff_and_recovery` | Worker spend-aggregator (`run_once`) ingiere ≥2 logs; counter user ≥200¢; presupuesto por debajo del gasto con `force` → key **bloqueada por hash** en el gateway; invocación → **401**; notificación `budget_cutoff` + `/api/budgets/me` `cutoff=true, level=user`; audit `budget.cutoff`; subir presupuesto → desbloqueo → invocación 200 |
| 8. Dispositivo | `test_08_device_enrollment` | Enroll (JWT MFA + org_code + keypair RSA real) → 202 + `enrollment_token` one-time; polling pending → 202 repetible; approve → active; canje → 200 device token; **2º canje → 410**; `/api/devices/me`; sesión ligada al dispositivo → revoke → **401 inmediato**; audit `device.enrolled/approved/revoked` con `device_id` |
| 9. Conector | `test_09_connector_and_vault` | Credencial vault → **secreto una sola vez** en el 201; GET = metadatos; canario ausente de `vault_credentials`/`audit_log`/`connector_instances` (grep SQL = 0); instancia vmware 201; telegram → **403 connector_not_allowed** (deny-overrides); `/use` del propietario → secreto + audit `vault.credential_used` |
| 10. Audit | `test_10_audit_verified` | `GET /api/audit/events` paginado contiene las 14 acciones del flujo (tenant.provisioned, user.created, role.assigned, policy.updated, budget.updated, model.approved, model.key_issued, budget.cutoff, device.enrolled/approved/revoked, vault.credential_created/used, connector.connected); `POST /api/admin/audit/verify` → ok; **CLI real** `verify-audit-chain verify <tenant>` → **exit 0** |

Ejecución:

```
$ cd /root/trust-agent/platform && .venv/bin/python -m pytest tests/e2e/ -q
28 passed, 4 warnings in 6.62s
```

## 2. Checklist de seguridad — recurrencias AIDLC (`tests/e2e/test_security_checklist.py`)

| Hallazgo AIDLC | Verificación ejecutada | Veredicto |
|---|---|---|
| **C1** password en respuestas | Entrega one-time estricta: password de activación SOLO en el 201 de provisioning (re-run 200 → `one_time_activation: null`, cubierto por spec 004 y re-verificado en paso 1); secreto vault SOLO en 201/`use`; key LiteLLM SOLO en 201. Grep SQL de plaintexts en `users`, `audit_log`, `policies`, `vault_credentials`, `api_keys`, `sessions` = **0** (test `test_c1_c4_secrets_one_time_and_never_in_db`) | **PASS** |
| **C2** sesiones en memoria | Sesión en PostgreSQL (`sessions.jti` = hash, no el secreto); **reinicio simulado** (`reset_engine()` + `TestClient(create_app())`) → la sesión sigue viva; token `sts1.<uuid>.<uuid>.<aleatorio>` sin ninguna pista del username; grep `_SESSIONS = ` / `_JOBS = ` en `app/` = 0 | **PASS** |
| **C3** single-tenant | `tenant_id` en todo + RLS (ver §3); el tenant sale del claim firmado o de la fila `sessions`, JAMÁS de headers (`X-Tenant-Id` forjado → sin efecto, `test_c3_tenant_never_from_headers`) | **PASS** |
| **C4** secretos en claro | Vault con envelope encryption (KEK fuera de DB); `api_keys` guarda solo `sha256`; canarios grep-ados en DB = 0; control positivo: el hash sha256 de la key SÍ está en `api_keys` | **PASS** |
| **C5** MFA por scraping | grep `BeautifulSoup|cv2|pyzbar|decode_qr|re.search(.*action` en `app/` = **0**; enrolamiento de dispositivo de primera clase (keypair + org_code); probe real: KC26 Admin REST no enrola TOTP (404) — la plataforma no scrapea, usa el claim `amr` del IdP | **PASS** |
| **C6** `verify=False` | grep `verify\s*=\s*False` en `app/` = **0** | **PASS** |
| **Alto:** JWT sin firma | `jwks.py`: decode sin verificar SOLO para localizar JWKS con issuer pineado a bases configuradas, después `jwt.decode` con firma RS256/ES256 + `exp/iat/aud/iss/sub/tenant_id`; tests: firma ajena → 401, HS256 → 401, sin `tenant_id` → 401, `aud` incorrecta → 401 | **PASS** |
| **Alto:** superadmin hardcodeado | grep `jose-arce|PANEL_SUPERADMIN|superadmin` = **0**; sin literales de secretos en `app/`; provider token por env con comparación constant-time | **PASS** |
| **Alto:** audit manipulable | `audit_log` append-only: `trust_app` recibe **permission denied** (ni UPDATE ni DELETE privilegiados) y el trigger `audit_log_reject_mutation` niega a TODO rol; cadena hash verificada por API y CLI (exit 0) | **PASS** |
| **Medio:** sin CSRF / sin rate limit | CSRF en mutaciones con cookie (spec 003, `test_mfa_csrf`); rate limiting de login persistido en `ops.login_throttle` (spec 003, `test_login_throttle`) | **PASS** |
| **Medio:** presupuestos solo por usuario | Jerarquía org→depto→usuario con validación de suma, corte por nivel con bloqueo de keys y notificaciones (paso 7 del flujo) | **PASS** |
| **Medio:** constantes hardcodeadas | Catálogos por datos (modelos, conectores, políticas, permisos RBAC); RBAC data-driven desde `catalog.role_permissions` | **PASS** |

No aplicables por etapa (dev-local, constitution VII): parches postStart al
core de Hermes (integración por plugins, cero parches — regla 5 del
contexto), NetworkPolicy/PodSecurityContext, imágenes productivas (los
contenedores locales ya están pineados: `postgres:17`, `keycloak:26.2`),
mem0 con master key (no existe mem0 en la plataforma).

## 3. Aislamiento multi-tenant (`tests/e2e/test_isolation_e2e.py`)

**Por API** (tenant A vs B vs e2e-corp):

- org_admin A → dispositivo de B: **404** (sin oráculo); depto de B: **404**;
  usuario de B: **404**.
- user B sin `devices.manage` → dispositivo de A: **403 permission_denied**
  (puerta RBAC por rol — no filtra información del recurso ajeno).
- Audit ajeno invisible: `GET /api/audit/events?actor_id=<user de B>` como
  admin de A → `total: 0`; inboxes disjuntos por tenant.
- **Hint de tenant forjado**: `sts1.<tenantB>.<sesión-de-A>.<secreto>` → **401**
  (el lookup está scoped por hint + hash).
- El org_admin del tenant e2e provisionado no ve deptos ni dispositivos de
  A/B, y alice no ve dispositivos del tenant e2e.

**Por DB** (conexión real como `trust_app`, el rol de la app):

- Sin `set_config('app.tenant_id')` → **0 filas** en `users`/`sessions`
  (fail-closed).
- Scope A → usuarios de A visibles, **0 filas** de B en `users` y `devices`.
- INSERT sin scope → error RLS (`WITH CHECK`); INSERT cross-tenant con scope
  A (fila de B) → error RLS.
- UPDATE cross-tenant (revocar sesión de B con scope A) → **0 filas**
  tocadas; la sesión de B sigue operativa.
- `audit_log`: UPDATE y DELETE como `trust_app` → **permission denied**
  (el rol ni siquiera tiene el privilegio; el trigger niega al resto).

## 4. Ejecución completa (reproducible)

```
$ cd /root/trust-agent/platform && .venv/bin/python -m pytest tests/ -q
211 passed, 27 warnings in 36.33s      # 183 previas + 28 e2e, 0 regresiones

$ cd /root/trust-agent/db && ../platform/.venv/bin/python -m pytest tests/ -q
171 passed in 5.21s
```

Greps de seguridad (evidencia cruda, 2026-10-23):

```
verify=False en app/            → 0
grant_type=password             → app/tenants/keycloak_admin.py:61 (bootstrap admin-cli del proveedor, ADR-015, excepción documentada del constitution IV)
superadmin/fallback hardcodeado → 0
scraping HTML/QR (cv2/BS4/QR)   → 0
_SESSIONS/_JOBS en memoria      → 0
JWT decode sin verificar        → 1 uso legítimo en jwks.py:61 (localizar JWKS; verificación completa después)
secretos literales en app/      → 0
```

## 5. Hallazgos y tareas derivadas

1. **LOW — `device_token_secret` con default no-vacío**
   (`platform/app/config.py:40`,
   `"device-token-dev-secret-change-me"`). El resto de los secrets
   (`vault_kek_b64`, `provider_token`, `litellm_master_key`,
   `keycloak_admin_password`) es fail-closed (vacío → no arranca / 503);
   el secret de firma de device tokens no. Mitigación estructural: el
   DeviceGate exige además sesión válida y fila `devices` activa del propio
   usuario, y el default está marcado `change-me` (dev-local). → **Tarea dev
   creada** para dejar el default vacío (fail-closed) con migración de
   runbook. No bloquea la convergencia del core.
2. **Observación (comportamiento documentado, no bug)**: la puerta RBAC
   (403 `permission_denied`) se evalúa ANTES del scope de tenant (404). No
   filtra información cross-tenant (el permiso es por rol), pero conviene
   documentarlo en la spec de errores. Sin acción.
3. **Limitación declarada del e2e**: los pasos con MFA usan el doble JWKS
   porque KC26 no permite enrolar TOTP vía Admin REST (probe: 404). Cuando
   exista un flujo de MFA real para tests (p. ej. WebAuthn del realm), el
   paso 2 puede re-ejecutarse contra el IdP real sin cambiar la suite.

## 6. Archivos

- `platform/tests/e2e/test_control_plane_flow.py` — flujo completo (10 pasos)
- `platform/tests/e2e/test_isolation_e2e.py` — aislamiento API + DB (8 tests)
- `platform/tests/e2e/test_security_checklist.py` — checklist AIDLC (10 tests)
- `platform/README.md` — conteo de tests actualizado (211)

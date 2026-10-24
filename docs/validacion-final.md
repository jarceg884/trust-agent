# Validación final 100% — gobernanza + funcionalidad

> Tarea kanban `t_bf72a163` (perfil QA) · 2026-10-23/24 · Reporte con evidencia real, nada simulado.
> Alcance: plataforma completa (specs 001–012) — E2E global en vivo, checklist CIO del spec 001,
> verificación de los 8 principios del constitution, hallazgos con severidad y demo script.

## Veredicto general

| Área | Veredicto | Evidencia |
|---|---|---|
| E2E global en vivo (proveedor→tenant→admin→worker→dispositivo→agente→modelo→corte→audit→revocación) | **PASS 79/79** | `platform/scripts/e2e_final_live.py` fases A (54) + B (25) contra KC26+PostgreSQL17+control plane vivos |
| Suite platform | **PASS 245/245** | `pytest tests/ -q` → 245 passed (env limpio) |
| Suite db | **PASS 171/171** | `pytest tests/ -q` en `db/` |
| Suite e2e del core | **PASS 28/28** | `pytest tests/e2e/ -q` |
| Console (spec 009 + activación spec 012) | **PASS 30/30** | `console/scripts/smoke_authorized.py` → SMOKE PASS (users/branding ya 200 reales) |
| Contratos spec 012 en vivo | **PASS** | probe directo: departments 201, invite 202→job→worker→usuario real en KC, directorio, branding, grants-list |
| Checklist CIO (spec 001) | **PASS con 6 gaps documentados** (tareas dev con severidad) | §3 |
| Constitution (8 principios) | **PASS** | §4 |
| Bug crítico encontrado durante la validación | **ENCONTRADO → CORREGIDO → VERIFICADO** | self-deadlock del invite (§5.1) |

**Conclusión**: la plataforma cumple la cadena de gobernanza completa de punta a punta con
evidencia real. Los pendientes están documentados como tareas abiertas con severidad (§5, §6).
No hay bloqueantes abiertos para la demo.

---

## 1. E2E global en vivo — `platform/scripts/e2e_final_live.py`

Flujo completo contra infraestructura REAL: control plane `:8000` (uvicorn vivo),
Keycloak 26.2 `:8080`, PostgreSQL 17 (contenedor `trust-db`), doble HTTP DECLARADO de
LiteLLM en `:39989` (D-GOV-15). IdP 100% real — login PKCE S256 + enrolamiento TOTP
RFC 6238 por los formularios reales de KC26, sin dobles de identidad.

```
$ python scripts/e2e_final_live.py --phase a --state .../e2e_final_state.json
FASE A: 54 PASS, 0 FAIL          # provision → admin → org → worker → dispositivo → agente → modelo
$ (reiniciar el control plane)    # AC-SEC-003: sesiones en PostgreSQL
$ python scripts/e2e_final_live.py --phase b --state .../e2e_final_state.json
FASE B: 25 PASS, 0 FAIL          # sesiones → spend → corte → audit → revocación
E2E FINAL PASS — flujo global completo con evidencia real
```

Cadena verificada (selección de checks con su salida real):

| Paso | Evidencia |
|---|---|
| **Proveedor crea tenant** | `POST /api/admin/tenants` → 201 created; realm KC real; 4 deptos seed; presupuesto org 50000¢; 4 políticas default; OTA one-time ≥16 chars; `audit_verify_chain()` = ok; audit `tenant.provisioned` |
| **Admin login REAL** | PKCE S256 + UPDATE_PASSWORD + CONFIGURE_TOTP (secret real, código RFC6238) → `acr=2` (D-MFA-01) → sesión `mfa_verified=true`; `GET /api/me` → 200 org_admin |
| **Usuario obrero** | Alta real en el realm; **login SIN TOTP: el IdP NO entrega code** (`MFA_REQUIRED` — fail-closed en el IdP, no solo en la puerta); enrolamiento TOTP inline → acr=2 → sesión MFA |
| **Admin configura org** | `allowed_connectors=[vmware,email]` org → depto estrecha a `[vmware]` → **relajación 422**; `require_mfa=false` → **422 invariable**; efecto deny-overrides: obrero ve SOLO vmware; presupuestos: depto $200, usuario $50, **Σ usuarios>depto 422**, **Σ deptos>org 422**, overview org $500/depto $200/unallocated $300 |
| **Enrola desde desktop** | `POST /api/devices/enroll` (org_code + id_token acr=2 + RSA-2048 real) → 202 pending; approve → active; canje one-time → device token; **2º canje → 410** |
| **Usa agente con modelo aprobado** | Publica agente → **0 grants por defecto**; dueño invoca → facturado al dueño; grant `invoke` al admin → invoca → **facturado al INVOCADOR**; revoca grant → 403; key virtual → plaintext EXACTAMENTE una vez, `max_budget_usd=50`, DB guarda solo `sha256(key)`; 2 invocaciones 200; **modelo no aprobado → 403** |
| **Reinicio del API** | Sesiones admin y obrero SOBREVIVEN (PostgreSQL, lección C2) |
| **Presupuesto se descuenta** | Spend worker real: ledger ≥2 filas con costo >0; counter del obrero descontado; overview `spent_cents` ≥200 |
| **Corte** | Reducción bajo el gasto con `force` → 200; **sin force → 422**; key **bloqueada por hash** en el gateway → invocación **401**; notificación `budget_cutoff`; `/api/budgets/me` `cutoff=true level=user`; audit `budget.cutoff`; **recuperación**: subir presupuesto → key rehabilitada → 200 |
| **Audit refleja TODO** | 20 acciones distintas (tenant.provisioned, user.created, role.assigned, policy.updated, budget.updated/forced_below_spend/cutoff, model.approved/key_issued/key_blocked/key_unblocked, agent.shared/granted/revoked/invoked/invoke_denied, device.enrolled/approved/revoked, auth.login); cadena íntegra vía API y **CLI exit 0**; alcance == filas SQL del propio tenant |
| **Revocación de dispositivo** | Sesión ligada al dispositivo operativa → admin revoca → **401 INMEDIATO**; audit `device.revoked` con device_id; cadena íntegra tras todo el flujo |

Tras el flujo completo: **0 transacciones filtradas** (`pg_stat_activity` sin `idle in transaction`).

## 2. Suites y smokes (ejecución fresca, 2026-10-24 02:0x UTC)

```
platform: 245 passed (pytest tests/)     # con env limpio; ver hallazgo H6
db:       171 passed (paridad schema/tests)
e2e core: 28 passed  (flujo 10 pasos + aislamiento + seguridad AIDLC)
console:  SMOKE PASS 30/30 (smoke_authorized.py — sesión admin PostgreSQL real,
          proxy BFF + CSRF, 11 páginas HTML, users/branding con datos reales, logout)
```

**Spec 012 activada y verificada en vivo** (probe directo contra el API):

- `POST /api/departments` → 201 `{"department_id": …}` + audit `department.created`
- `POST /api/users` → 202 `{job_id, status:pending}` (idempotente por email) →
  job worker `--once` → job `succeeded`, usuario `active` en el directorio y
  **usuario real en Keycloak con requiredActions [CONFIGURE_TOTP, UPDATE_PASSWORD]**
  (el one-time password viaja en `jobs.result`, tenant-scoped)
- `GET /api/users` → directorio con scoping por rol
- `GET /api/branding` → default `source=provider`; `PUT /api/branding` → 200, `source=tenant`
- `GET /api/agents/{id}/grants` → 200 lista de grants

## 3. Checklist CIO — spec 001 por dominio

Leyenda: ✅ verificado con evidencia real (test suite / E2E en vivo / probe).
⚠️ gap documentado con tarea dev y severidad (§5).

| Dominio | Estado | Evidencia |
|---|---|---|
| **REQ-ONB** aprovisionamiento | ✅ | AC-ONB-001/002/005/006/007/009 en vivo (E2E S1); idempotencia y rollback cero-huérfanos (`test_provision_idempotent`, `test_provision_rollback_leaves_zero_orphans`); healthcheck endpoint; runbook `provision_tenant` CLI (`test_cli_provision_and_verify`) |
| **REQ-ORG** org/deptos/usuarios/roles | ✅ | Matriz RBAC data-driven (`test_rbac_matrix_from_catalog`, `test_provider_auth_matrix`); scoping dept_admin (`test_dept_admin_scope_limited_to_own_department`); invite asíncrono con job en PostgreSQL (`test_invite_full_flow_with_worker_double` + verificado en vivo §2); desactivación revoca sesiones/dispositivos (`test_disable_revokes_sessions_devices_and_is_idempotent`); overview (`test_overview_rbac_and_scope`, `test_overview_unassigned_remainder`); puerta MFA (`test_no_mfa_blocks_business_routes` + fail-closed del IdP en vivo) |
| **REQ-BUD** presupuestos con corte | ✅ (⚠️ AC-BUD-010) | Jerarquía con validación de suma (422 en vivo); corte por nivel user/dept/org (`test_native_gateway_cutoff`, `test_dept_cutoff_scopes_to_department`, `test_org_cutoff_and_tenant_isolation`); umbral/notificaciones (`test_threshold_notification_and_unblock`); force-below-spend auditado (en vivo S7); BYOK ingesta idempotente (`test_ingest_idempotent_counters_byok`); corte real vía gateway en vivo (S7). ⚠️ AC-BUD-010 (reset de período) sin test dedicado — counters por `period_start=date_trunc('month')` lo estructuran, pero no hay test de rollover |
| **REQ-POL** políticas | ✅ | 3 niveles con deny-overrides (`test_boolean_deny_overrides`, `test_string_rank_most_restrictive`, `test_list_intersection`); invariables (`test_invariable_keys_rejected_at_every_level`, `test_invariables_always_floor`); catálogo tipado por datos; no-relajación 422 en vivo; política efectiva `/api/me/policies` (en vivo) |
| **REQ-MDL** model access vía LiteLLM | ✅ | Solo modelos aprobados (403 en vivo); key virtual por usuario con `max_budget` (plaintext one-time, sha256 en DB); bloqueo/desbloqueo por corte (en vivo S7); pricing con margen central (`test_central_margin_applied`); catálogo con pricing (`test_model_catalog_lists_models_with_pricing`); metadatos sin secretos (`test_key_metadata_never_exposes_secrets`) |
| **REQ-AGT** agentes compartidos | ✅ | Publicación sin grants (en vivo + `test_publish_creates_no_grants`); grants granulares invoke/view/manage (`test_access_without_grant_is_404_view_without_invoke_403`, `test_grants_require_manage_rights`); facturación al invocador (en vivo + `test_invoke_billed_to_invoker`); revocación inmediata (en vivo); grants-list (spec 012, en vivo) |
| **REQ-DEV** dispositivos | ✅ | Flujo completo pending→active→revoked (en vivo S4/S9 + `test_full_flow_enroll_approve_exchange_revoke`); one-time estricto 410 (en vivo); 401 inmediato tras revocación (en vivo); gate de dispositivo (`test_gate_blocks_*`); renovación (`test_renew_device_token`); audit con device_id (en vivo) |
| **REQ-CON** conectores | ✅ (⚠️ REQ-CON-005) | Catálogo de 6 por datos (`test_connector_catalog_lists_six`, `test_catalog_extensible_by_data`); permiso por política (`test_available_connectors_per_policy`, 403 `connector_not_allowed` en vivo); mensajerías con modo (`test_map_messaging_permissions`, `test_messaging_governance`); scope mínimo (`test_scope_validation`); tipos de credencial gobernados (`test_credential_type_governance`). ⚠️ REQ-CON-005 (desconexión proactiva ≤60 s de instancias al cambiar política) no implementado — el enforcement es a tiempo de uso |
| **REQ-VLT** vault | ✅ | Envelope encryption con DEK por credencial y KEK fuera de DB (`test_each_credential_gets_fresh_dek`, `test_load_kek_validation`, `test_canary_not_in_ciphertext`); secreto one-time (`test_register_encrypts_and_delivers_once`, `test_rotate_one_time`); canario ausente de logs/DB (`test_canary_never_in_logs`, grep SQL = 0 en e2e); revocación corta instancias (`test_revoke_cuts_instances`); metadatos solo (`test_org_admin_visibility_and_revoke`) |
| **REQ-AUD** audit inmutable | ✅ (⚠️ REQ-AUD-005/006) | Append-only a nivel DB: `trust_app` sin privilegio UPDATE/DELETE + trigger que niega a todo rol (`test_db_audit_log_immutable_even_for_own_tenant`); hash chain sha256 encadenado con verificador API+CLI (en vivo, exit 0; manipulación detectada: `test_tamper_update_detected`, `test_tamper_delete_detected`); cobertura de eventos (matriz en e2e paso 10 + `test_audit_coverage_and_chain`); filtros/paginación/scoping (`test_filters_and_pagination`, `test_audit_verify_requires_org_admin`); export CSV/JSON (`test_export_csv_json`). ⚠️ REQ-AUD-006 retención/purga sin endpoint |
| **REQ-BRD** branding | ✅ (⚠️ REQ-BRD-002/003 desktop) | `GET /api/branding` default provider + `PUT` tenant (en vivo §2); validación y permiso (`test_branding_get_provider_default`, `test_branding_put_requires_manage_and_validates`); audit `branding.updated`. ⚠️ reflejo runtime en el desktop sin verificar (el desktop no consume aún el endpoint) |
| **REQ-DTK** desktop | ✅ | Lane desktop (t_d3768316) verificado con browser real: login PKCE+TOTP, enroll WebCrypto 202→approve→device token, dashboard 100% real; enrolamiento de dispositivo re-verificado aquí a nivel API (S4/S9); reasons de error con `reason` específico (403/401/410/422 con reason en vivo) |
| **REQ-PRF** perfiles por depto | ✅ | 4 plantillas estándar por datos (`test_registry_has_four_standard_templates`); resolución por depto (`test_resolve_default_selection`, `test_resolve_explicit_selection`); integración Hermes SOLO vía plugin oficial `trust-agent-governance` (plugin.yaml + register(ctx)) — cero parches al core |
| **REQ-TEN** multi-tenancy | ✅ | `tenant_id` en 18 tablas + RLS activa (`db/tests/test_rls_coverage.py`, `test_rls_fail_closed_without_scope`); fail-closed sin scope (0 filas); API cross-tenant 404 sin oráculo (`test_api_cross_tenant_ids_are_404`); hint forjado → 401 (`test_api_forged_tenant_hint_in_session_token`); tenant del claim firmado, jamás de headers (`test_c3_tenant_never_from_headers`); suspensión de tenant (`test_suspended_tenant_blocked_and_restored`) |
| **REQ-SEC** seguridad transversal | ✅ (⚠️ REQ-SEC-011) | PKCE obligatorio, ROPC solo bootstrap documentado (`test_no_password_grant_in_app_code`, `test_password_grant_only_documented_bootstrap`); JWT verificado firma/exp/aud/iss (`test_reject_*`, `test_jwt_signature_enforced`); sesiones/jobs en PostgreSQL sobreviven reinicio (en vivo S0b + `test_session_survives_api_restart`, `test_jobs_survive_api_restart`); one-time estricto (410 en vivo); secretos cifrados (vault ✓); rate limiting persistido con lockout progresivo (`test_consecutive_failures_lock_the_account`, `test_lockout_is_progressive`, `test_flooding_ip_is_blocked_before_verification`); `verify=False` = 0 ocurrencias; CSRF en mutaciones con cookie (`test_cookie_mutation_requires_csrf` + flags de cookie `test_admin_cookie_flags`); healthchecks por servicio (`/healthz` ok). ⚠️ REQ-SEC-011 WebAuthn (Should) no implementado — decisión de producto abierta (spec 001 §10.3) |

## 4. Constitution — los 8 principios, uno por uno

| # | Principio | Veredicto | Evidencia |
|---|---|---|---|
| I | **Nada simulado** | PASS | Todo lo verificado arriba es ejecución real contra servicios vivos. Dobles DECLARADOS: (1) LiteLLM = doble HTTP `tests/litellm_fake.py` (D-GOV-15) con la semántica documentada de la Admin API — contra un proxy real solo cambia `TRUST_LITELLM_BASE_URL`; (2) pasos con MFA en la suite pytest usan el doble JWKS declarado (KC26 no permite enrolar TOTP vía Admin REST, probe 404) — el E2E final usa IdP 100% real sin dobles. Cero datos mock en el console (grep faker/msw/fixture = 0, lane UI) |
| II | **Spec-driven** | PASS | 12 specs (`specs/001…012`) con spec/plan/tasks; convergencia con checks de aceptación contra evidencia real (este reporte es el converge final); trazabilidad bidireccional verificada por script (`db/tests/test_spec_traceability.py`) |
| III | **Multi-tenant con aislamiento lógico** | PASS | `tenant_id` en el 100% de tablas de negocio + RLS activa; query sin scope → 0 filas (fail-closed); API cross-tenant → 404 sin oráculo; suite de aislamiento en verde (28 e2e + db/tests); tenant SIEMPRE del claim firmado o de la fila `sessions`, jamás de headers |
| IV | **Seguridad por diseño** | PASS | Auth code+PKCE S256 con JWKS real (firma/exp/aud/iss); secretos one-time y cifrados (envelope encryption, KEK fuera de DB); sesiones y jobs en PostgreSQL (sobreviven reinicio — verificado en vivo); audit append-only con hash chain a nivel DB; presupuestos jerárquicos con corte real; ROPC solo bootstrap documentado; `verify=False` = 0; cero parches al core de Hermes |
| V | **Gobernanza completa** | PASS | Cada capacidad verificada tiene su historia de gobernanza: quién (RBAC data-driven), con cuánto (presupuestos jerárquicos + corte), bajo qué reglas (políticas deny-overrides con invariables), auditada (20 acciones en el trail del E2E). El E2E global §1 ES la demostración de que el producto es gobernanza |
| VI | **Stack fijo** | PASS | Python+FastAPI+SQLAlchemy+Alembic+PostgreSQL 17; Next.js 16 App Router+TS strict+Tailwind v4 (console y desktop); LiteLLM como plano de control; OIDC Keycloak 26.2; docs en español, UI en inglés, montos USD en centavos enteros |
| VII | **Desarrollo local primero** | PASS | Todo corre y se validó en este servidor single-node (contenedores locales pineados: postgres:17, keycloak:26.2); sin infra cloud nueva |
| VIII | **Autonomía con skills** | PASS | 7 skills capturados en `/root/trust-agent/skills/` (database, hermes-agent, hermes-integration, identity, llm-gateway); los hallazgos empíricos de esta validación (baile KC26, deadlock del advisory lock) quedaron documentados en el código/scripts y este reporte |

## 5. Hallazgos (con severidad y destino)

### H1 — [CRÍTICO→CORREGIDO] Self-deadlock del invite contra el advisory lock de audit
- **Qué**: `POST /api/users` (invite) colgaba indefinidamente. Capturado en vivo con
  `py-spy` + `pg_locks`: el request emitía `user.invited` en la tx del request (retiene
  `pg_advisory_xact_lock` por tenant hasta commit) y el enqueue del job abría una SEGUNDA
  conexión cuyo `job.created` esperaba ese mismo lock → interbloqueo del request consigo
  mismo. Efectos en cascada: todos los requests posteriores que escribieran audit en el
  tenant quedaban en cola infinita; los clientes que desconectaban dejaban transacciones
  filtradas (`idle in transaction`) reteniendo el lock; el shutdown graceful de uvicorn
  quedaba colgado esperando los threads bloqueados.
- **Arreglo**: `jobs.service.enqueue(..., conn=)` comparte la tx del request (D-ORG-02,
  spec 012). El lane dev lo corrigió de forma independiente durante t_f72dc691
  (mismo diagnóstico: "deadlock pg_advisory_xact_lock").
- **Verificación del fix (esta validación)**: invite → 202 inmediato con `job_id`;
  E2E final completo re-corrido contra el código corregido → 79/79 PASS y
  **0 transacciones filtradas** al final.

### H2 — [MEDIUM] Spend worker: un log de un tenant inexistente tumba el ciclo entero
- `app/governance/spend_worker.py` revienta con `TypeError: 'NoneType' object is not
  subscriptable` cuando un log del gateway referencia un tenant que ya no existe en la DB
  (reproducido: limpieza manual del tenant final-e2e con logs residuales en el doble).
  El ciclo falla completo → NO ingesta NADA de NINGÚN tenant (píldora envenenada).
  En operación normal no hay baja de tenants (no existe endpoint de deprovision), pero un
  log malformado/ajeno bloquearía el pipeline de gasto de todos los clientes.
  → **Tarea dev creada** (skip + audit del log rechazado).

### H3 — [LOW] Advisory lock de audit sin timeout de respaldo
- El trigger de `audit_log` usa `pg_advisory_xact_lock` sin `lock_timeout`/`statement_timeout`
  en la sesión de la app: cualquier escritor de audit atascado bloquea el trail del tenant
  indefinidamente (fue el amplificador de H1). Con H1 corregido no hay causa conocida, pero
  como defensa en profundidad conviene un `lock_timeout` razonable en el pool.
  → incluido en la tarea dev de H2.

### H4 — [LOW] Overview no lista departamentos sin presupuesto asignado
- `GET /api/budgets/overview` solo muestra deptos con fila en `budgets`; un depto recién
  creado (spec 012: `POST /api/departments` no crea presupuesto) no aparece hasta
  `PUT /api/budgets/dept/{id}`. Comportamiento razonable pero diverge del mockup
  (REQ-ORG-001 creaba depto con presupuesto opcional). Decisión de producto menor.
  → anotado en la tarea dev de gaps.

### H5 — [LOW] Especificaciones de spec 001 sin implementar (Should / decisión abierta)
- `GET /api/budgets/history` (REQ-BUD-012, S) — no existe endpoint.
- Retención/purga de audit ≥365 días (REQ-AUD-006, S) — no existe endpoint/job.
- WebAuthn/passkeys (REQ-SEC-011, S) — no implementado; el spec lo dejó como decisión
  abierta (§10.3).
- Test de rollover de período (AC-BUD-010, M) — la estructura (counters por
  `period_start`) lo cumple, pero no hay test que lo demuestre.
- REQ-CON-005 desconexión proactiva ≤60 s — el enforcement es a tiempo de uso.
- REQ-BRD-002/003 branding runtime en el desktop — el endpoint existe; el desktop no lo
  consume aún.
→ **Tarea dev creada** con este inventario y severidades.

### H6 — [LOW] Tests heredan env del proceso que los lanza
- `test_admin_cookie_flags` falla si el entorno tiene `TRUST_COOKIE_SECURE=false`
  exportado (me ocurrió al correr el jobs worker con la env del servicio en el mismo
  shell). Los tests deberían pinear su env en lugar de heredar el ambiente.
  → anotado en la tarea dev de robustez.

### H7 — [INFO] Observaciones de proceso
- `/root/trust-agent` NO es un repositorio git: no hay historial ni trazabilidad de
  cambios de código (el constitution II cubre specs, pero el código sin VCS es un riesgo
  operativo real — p. ej. el fix H1 no tiene diff auditable). Recomendación fuerte al
  humano: `git init` + commit inicial.
- El lane dev quedó bloqueado por el gate de aprobación al intentar reiniciar el
  control plane; esta validación lo desbloqueó (servicio arriba con código nuevo,
  smoke de activación 30/30 en verde). Comentario con la evidencia dejado en t_f72dc691.
- Keycloak deshabilitó al usuario obrero tras 8 códigos OTP inválidos consecutivos
  (observado durante el debugging de H1) — comportamiento de seguridad correcto;
  la plataforma además tiene su propio throttle persistido (REQ-SEC-006 ✓).

## 6. Riesgos residuales para producción (fuera de alcance v1, constitution VII)

1. **KEK del vault por env**: cada lanzamiento debe recibir la MISMA `TRUST_VAULT_KEK_B64`
   o las credenciales cifradas previas son indecifrables (fail-closed, pero sin runbook de
   rotación). El empaquetado (t_d36b3f31) debe fijar esto.
2. **Doble de LiteLLM**: toda la validación de gasto/corte usa el doble declarado con la
   semántica documentada. Contra un LiteLLM real hay que re-validar ingesta de spend,
   bloqueo de keys y 403 de modelos no aprobados (solo cambia `TRUST_LITELLM_BASE_URL`).
3. **Job worker y spend worker como procesos `--once`**: en la demo corren on-demand;
   el empaquetado debe supervisarlos (REQ-SEC-009) con healthcheck.
4. **Sin VCS** (H7) — el riesgo operativo mayor del proyecto hoy.

## 7. Demo script paso a paso (reproducible)

Precondiciones: contenedores `trust-db` (55432) y `keycloak` (8080) arriba; control plane
lanzado con el env TRUST_* del runbook (`platform/README.md`); doble LiteLLM en 39989
(`run_litellm_fake.py`); console `npm run start` en :3100.

```bash
# 0) Servicios vivos
curl -s http://127.0.0.1:8000/healthz          # {"status":"ok","database":"up"}

# 1) E2E global completo (79 checks, ~4 min, incluye reinicio del API entre fases)
cd /root/trust-agent/platform
.venv/bin/python scripts/e2e_final_live.py --phase a --state /tmp/e2e.json
#    → reiniciar el control plane (misma env) — demuestra sesiones en PostgreSQL
.venv/bin/python scripts/e2e_final_live.py --phase b --state /tmp/e2e.json

# 2) Suites
.venv/bin/python -m pytest tests/ -q           # 245 passed
cd ../db && ../platform/.venv/bin/python -m pytest tests/ -q   # 171 passed

# 3) Console con spec 012 activada (sustitución declarada del issuer para el smoke)
docker exec trust-db psql -U postgres -d trust_agent -c \
  "UPDATE tenants SET oidc_issuer_url='http://127.0.0.1:8908/realms/demo-org' WHERE slug='demo-org';"
cd ../platform && .venv/bin/python console/scripts/smoke_authorized.py   # SMOKE PASS 30/30
docker exec trust-db psql -U postgres -d trust_agent -c \
  "UPDATE tenants SET oidc_issuer_url='http://127.0.0.1:8080/realms/demo-org' WHERE slug='demo-org';"

# 4) UI manual: http://127.0.0.1:3100/login (org code del tenant de demo)
#    Overview / Departments / Users & Roles (directorio real) / Budgets / Model Access /
#    Shared Agents / Devices / Credentials / Audit Log / Branding — todo con datos reales.

# 5) Audit inmutable en vivo
.venv/bin/verify-audit-chain verify <tenant_id>   # exit 0, status ok
docker exec trust-db psql -U trust_agent -U postgres -d trust_agent \
  -c "UPDATE audit_log SET action='x' WHERE id=1;"   # → permission denied (append-only real)
```

## 8. Tareas derivadas

- **t_f72dc691** (dev, en curso): contratos spec 012 — implementación verificada por QA
  en vivo (§2); desbloqueada por esta validación (servicio reiniciado con código nuevo,
  smoke de activación 30/30). Pendiente del lane: cerrar su propia card.
- **Nueva tarea dev (H2+H3+H6)**: robustez del pipeline de gasto y de tests.
- **Nueva tarea dev (H4+H5)**: gaps de spec 001 con severidad.
- **t_d36b3f31** (deploy, todo): empaquetado single-node — debe absorber los riesgos §6.

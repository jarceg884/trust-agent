# Revisión Pilar 2 — Estado real de tareas vs alcance E2E (10 puntos)

**Tarea QA**: t_d9b3d2eb (hija de la revisión maestra t_ef306f32). **Fecha**: 2026-10-26 (22:0x CST).
**Método**: estado real de cada card en el board (`kanban_show`) + contraste de lo declarado contra código en `/root/trust-agent/`, specs en `specs/`, openapi del API vivo (:8000), imagen desplegada del console (:3100, strings en `/app/.next`) y logs de evidencia. Nada simulado; se distingue **declarado** vs **verificado**.

**Stack vivo al momento de la revisión** (verificado): `docker ps` → 10 contenedores trust-* Up/healthy (api, console, provider-console, idp, db, litellm, workers); `GET /healthz` → 200; `GET :3100/login` → 200.

---

## (a) Estado real por tarea

### t_955c98d6 — Departments CRUD (spec 016, feedback #3) — **done**
- **Declarado**: POST con budget inicial, PUT rename, DELETE con 409+conteos, audit `department.renamed/deleted`, UI completa, tests 14/14, evidencia UI en audit_log (tenant dept-crud-ux, seq 20-26).
- **Verificado**: `platform/app/org/departments.py` tiene `rename_department` (:111, audit :142) y `delete_department` (:149, audit :217); `org/router.py` expone PUT/DELETE (:283,:299). **Openapi vivo**: `GET,POST /api/departments` + `DELETE,PUT /api/departments/{department_id}`. UI `departments-page.tsx` con diálogos Rename/Delete (spec 016 D-16-02/03). Migración `20261026_0009_departments_crud.py` + spec `specs/016-departments-crud/` presentes. Evidencia: `docs/evidencia/departments-crud/` (5 screenshots del flujo UI).
- **Cubre punto 1 (Departments CRUD): TOTAL** — verificado en código + API viva + UI.

### t_5576ee94 — Roles & access (spec 017, feedback #4) — **done**
- **Declarado**: POST/DELETE `/api/users/{id}/roles`, PUT `/api/users/{id}` (depto/status), gate `roles.assign` solo org_admin, sin auto-escalada (403/422), UI inline, audit `role.assigned/revoked/user.disabled`, tests 15/15 + smokes 28/28 y 16/16.
- **Verificado**: `platform/app/org/roles.py` (`assign_role` :32 audit :83, `revoke_role` :93 audit :141); `org/router.py` gate `roles.assign` (:102-103,:120-121). **Openapi vivo**: `POST /api/users/{user_id}/roles`, `DELETE /api/users/{user_id}/roles/{role_key}`, `PUT /api/users/{user_id}`, `POST /api/users/{user_id}/disable`. UI `users-page.tsx`: assign/revoke/disable inline, controles por permisos reales de `/api/me`, fila propia sin auto-modificación (D-17-02). Evidencia `docs/evidencia/017-roles-access-evidencia.md` + screenshot UI viva.
- **Cubre punto 2 (Usuarios y roles): TOTAL** — verificado en código + API viva + UI.

### t_877b0cb5 — QA console Budgets+Devices (feedback #5 #8) — **done** (perfil QA, repro)
- **Declarado**: Budgets org/depto PASS (B1-B4) con persistencia reload+DB y validación Σ deptos>org; falta edición por usuario (F3). Devices enroll→approve/reject/revoke→canje PASS (D1-D4) PERO login roto (F1 CRÍTICO) + F2/F4/F5/F6. 6 tareas dev hijas creadas.
- **Verificado**: reporte `docs/qa-console-repro.md` (17.5 KB, repro con archivo:línea). **Las 6 tareas dev hijas están hoy `done` en el board** (t_38ce1e82 F1, t_6f8a24a5 F2, t_6b40c758 F3, t_c076152d F4, t_9fddcb75 F5, t_d3fd75db F6 — verificado por lectura directa del board). Fixes confirmados en código/imagen: F1 → `auth/callback/route.ts` usa `requestOrigin(request)` derivado del Host header (fix completo documentado en `docs/evidencia/f1-login-loop/README.md`); F3 → `budgets-page.tsx` ahora edita presupuesto **por usuario** (`PUT /api/budgets/user/{userId}`, gate `budget.user.manage`, Σ usuarios ≤ depto) y **está en la imagen desplegada** (string `budgets/user` presente en `/app/.next/server` del contenedor :3100); **openapi vivo**: `PUT /api/budgets/user/{user_id}`. Bug conexas t_378503a1 (500→422 UUID) también `done` (evidencia `docs/evidencia/uuid-validation*`).
- **Cubre punto 3 (Budgets jerárquicos): TOTAL tras fixes** — org/depto verificado por QA en vivo (B1-B4), usuario verificado en código+imagen+API (fix F3 convergido). **Cubre punto 7 (Devices): PARCIAL→casi total** — approve/reject/revoke/canje desde la UI verificados en vivo por QA (D1-D4) y los 6 bugs tienen fix convergido con evidencia propia, PERO no existe aún una re-validación E2E independiente post-fixes del flujo completo (es exactamente el pendiente del GATE FINAL t_e8a28fce, hoy `todo`).
- **Nota crítica de contexto**: la evidencia D1-D4 de QA fue contra el **desktop-client demo (:9090), que fue RETIRADO del repo después** (t_b8769ecb, 20:12 CST — verificado: `platform/desktop-client` ya no existe). El camino de enrolamiento actual es auto-enroll zero-UI vía link de descarga (spec 023) + plugin Hermes (spec 022, Pilar 3). La página Devices del console (approve/reject/revoke) sigue existiendo (`devices-page.tsx`).

### t_e0943bd8 — Model access MaaS + policy (spec 018, feedback #6) — **done**
- **Declarado**: catálogo = 6 modelos oficiales Huawei MaaS intl con contexto y precios USD/M de la doc oficial, integrado a policy (`model_approvals` en `/api/policies`, sección policy en `/api/models/access`, deny-overrides), LiteLLM con deployment names reales, evidencia 21/21 PASS en vivo (login PKCE+TOTP real, invocación real glm-5.2 → proxy → MaaS, 403 fuera de policy).
- **Verificado**: migración `20261026_0009_model_catalog_maas.py` siembra los 6 modelos reales con precios USD (glm-5.2: 1M/128K, 140/440¢; deepseek-v4-flash 13.5/27¢; qwen3-32b 27/269.5¢...) y retira los 2 inexistentes; `litellm-real/config.yaml` con los deployment names reales; `governance/policies.py::model_approvals` (:338) expuesto en router (:215); UI `models-page.tsx` con bloque "Governance policy" (spec 018 D-018-02). **Log de evidencia leído**: `docs/evidencia/model-access-maas/04-evidence-r15-reuse.log` termina "Evidencia spec 018: **21 PASS, 0 FAIL**" con E5 (aprobación→policy), E6 (deny-overrides, verify→403) y E7 (invocación REAL glm-5.2 200 vía proxy→MaaS; deepseek-v4-flash 403).
- **Cubre punto 5 (Model access sobre MaaS oficial): TOTAL** — verificado en código + log de evidencia en vivo. **Contribuye al punto 4 (Policies)**: model approvals integrados a la policy con deny-overrides verificado.

### t_0ed22c94 — Shared agents: perfiles default (spec 020, feedback #7) — **done**
- **Declarado**: 4 perfiles default (analyst/coder/ops/support) adoptables desde la UI; adopción = instancia tenant-scoped con grants explícitos; policy engine deny-overrides aplica igual; evidencia E2E real (adopt 201, invoke glm-5.2 200, gpt-4o 403, audit verificado), tests 15/15.
- **Verificado**: `governance/agent_profiles.py` con los 4 templates (:40 analyst, :58 coder, :76 ops, :94 support) y `adopt` (:180, audit `agent.profile_adopted` :226). **Openapi vivo**: `GET /api/agent-profiles`, `POST /api/agent-profiles/{key}/adopt`. UI `agents-page.tsx` con sección "Default profiles" + "Effective policy" + Adopt (:181-246), **presente en la imagen desplegada** (string `Default profiles` en `/app/.next/server`). Evidencia `docs/evidencia/default-agent-profiles/` (README + screenshot).
- **Cubre punto 6 (Agentes default): TOTAL** — verificado en código + API viva + UI desplegada. **Contribuye al punto 4**: perfiles gobernados por policy deny-overrides.

### t_c14d6703 — Audit UX (spec 021, feedback #9) — **done** (perfil ui)
- **Declarado**: filtros (actor/action/recurso/device/fechas/texto), timeline por actor/sesión, pill de integridad vía `POST /api/admin/audit/verify`, export CSV/JSON, cero backend, 10/10 checks PASS.
- **Verificado**: `audit-page.tsx` (tabs Table|Timeline, filtros, export), `audit-integrity.tsx`, `audit-timeline.tsx` existen; **presente en la imagen desplegada** (string `Timeline` en `/app/.next/server` del :3100). **Openapi vivo**: `GET /api/audit/events`, `GET /api/audit/events/export`, `POST /api/admin/audit/verify`. Evidencia `docs/audit-ux-evidencia.md` + CSV/JSON exportados con hashes reales en `docs/evidencia/audit-ux/`. Derivó bug t_378503a1 (budgets 500→422), hoy `done`.
- **Cubre punto 8 (Audit con análisis): TOTAL** — verificado en código + API viva + UI desplegada + artefactos de export reales.

### t_1844fb06 — Auth UX (spec 015, feedback #10) — **done**
- **Declarado**: KC invisible (tema white-label `trust-agent`), auto-registro con contraseña, MFA opcional con banner persistente + enrolamiento TOTP inline, `require_mfa` org-tunable, Google brokering fail-closed (**AC-AUTH-006 gap explícito** por credenciales del operador), suites db 182 / platform 479, evidencia con navegador real + 6 screenshots.
- **Verificado**: `platform/app/auth/register.py` (auto-registro gateado por `allow_self_registration`, audit `user.self_registered` :162, `auto_provision_brokered` :170); `keycloak_admin.py` con tema white-label + `ensure_google_idp` (:937, D-AUTH-02) + mapper tenant_id (D-AUTH-03); `mfa-banner.tsx` ("Set up MFA", se oculta con `mfa_verified`); páginas `/login` y `/register` del console; tema `platform/idp-themes/trust-agent/`. **Evidencia leída**: `docs/evidencia/auth-ux/EVIDENCIA.md` documenta AC-AUTH-001 (0 menciones de Keycloak en DOM) y AC-AUTH-002 (auto-registro verificado por SQL) con tenant real `authux-ev`; 6 screenshots presentes. **Gap verificado EN VIVO hoy**: `config.py:112` `google_client_id: str = ""` y el contenedor `trust-agent-api` NO tiene env GOOGLE* (verificado con `docker exec ... env`) → el login con Google sigue sin poder verificarse punta a punta (fail-closed por diseño).
- **Cubre punto 10 (Auth UX): PARCIAL** — Keycloak invisible ✓, auto-registro ✓, MFA opcional sugerido ✓ (verificados con evidencia real); **Google ✗ (gap explícito AC-AUTH-006, bloqueado por credenciales del operador, no por código)**.

---

## (b) Mapa de cobertura — los 10 puntos del Pilar 2

| # | Punto del alcance | Cobertura | Tareas que lo cubren | Estado verificado |
|---|---|---|---|---|
| 1 | Departments CRUD | **TOTAL** | t_955c98d6 (spec 016) | Código + openapi vivo + UI + evidencia screenshots |
| 2 | Usuarios y roles | **TOTAL** | t_5576ee94 (spec 017); t_1844fb06 aporta auto-registro | Código + openapi vivo + UI + evidencia |
| 3 | Budgets jerárquicos (org→depto→usuario) | **TOTAL tras fixes** | t_877b0cb5 (QA B1-B4 en vivo) + t_6b40c758 (F3: edición por usuario, convergido) + t_378503a1 (500→422) | org/depto verificado en vivo por QA; usuario verificado en código+imagen+openapi; falta re-validación E2E integral (gate) |
| 4 | Policies | **TOTAL funcional, SIN página dedicada** | Sin tarea dedicada en el set. Cubierto transversalmente: t_e0943bd8 (model_approvals + deny-overrides, evidencia E6) + t_0ed22c94 (agentes default bajo policy) + UI distribuida: org-level en `organization-page.tsx` (GET/PUT/DELETE `/api/policies`), user-level en `users-page.tsx`, dept-level referenciado en `departments-page.tsx`; API jerárquica completa en openapi (`PUT /api/policies/org|dept/{id}|user/{id}`, `DELETE /api/policies/{level}/{key}`) | API y policy engine verificados; UI distribuida verificada en código. **Ojo**: el mockup de Miller muestra una pestaña "Policies" — no existe página dedicada en el console |
| 5 | Model access sobre MaaS oficial | **TOTAL** | t_e0943bd8 (spec 018) | Migración con 6 modelos reales + precios USD, LiteLLM real, log 21/21 PASS leído (invocación real a MaaS) |
| 6 | Agentes default | **TOTAL** | t_0ed22c94 (spec 020) | 4 templates en código, adopt en openapi vivo, UI desplegada, evidencia E2E |
| 7 | Devices | **CASI TOTAL — re-validación pendiente** | t_877b0cb5 (QA D1-D4 en vivo + 6 bugs documentados) + fixes dev todos `done`: t_38ce1e82 (F1 login loop, con evidencia propia), t_6f8a24a5 (F2 cookies), t_c076152d (F4 audit reject), t_9fddcb75 (F5 id_token), t_d3fd75db (F6 feedback) | approve/reject/revoke/canje verificados en vivo por QA (pre-fix); fixes convergidos con evidencia; **falta re-validación E2E post-fixes del flujo completo** (GATE FINAL t_e8a28fce = `todo`). **Además el camino de enrolamiento cambió**: el desktop-client demo (:9090) contra el que se probó fue retirado (t_b8769ecb); el enrolamiento actual es auto-enroll vía link (spec 023, Pilar 1/3) |
| 8 | Audit con análisis | **TOTAL** | t_c14d6703 (spec 021) | Filtros/timeline/integridad/export en UI desplegada + openapi vivo + CSV/JSON reales exportados |
| 9 | Branding | **TOTAL, pero FUERA del set de 7** | **Ninguna de las 7 tareas revisadas cubre Branding.** Cobertura existe fuera del set: t_6d3f5016 ([ALTO] Save branding no llamaba a PUT — fix `done` 21:04 CST hoy) + spec 028-branding-versioning | Verificado: `branding-page.tsx` con PUT draft/publish/rollback/versions/assets; openapi vivo con el set completo (`GET,PUT /api/branding`, `/draft`, `/publish`, `/rollback`, `/versions`, `/assets`) |
| 10 | Auth UX (Google + auto-registro + MFA opcional + KC invisible) | **PARCIAL (3 de 4 verificados)** | t_1844fb06 (spec 015) | KC invisible ✓, auto-registro ✓, MFA opcional sugerido ✓ (evidencia real); **Google = GAP**: sin `TRUST_GOOGLE_CLIENT_ID/SECRET` del operador (verificado vivo: env vacío en el API); mecánica de brokering implementada y fail-closed |

---

## Gaps y riesgos para la tarea de consolidación

1. **Google login punta a punta (AC-AUTH-006)** — único gap funcional del punto 10. Bloqueado por credenciales del operador (Google Cloud Console), no por código. La mecánica (IdP google por realm, mapper tenant_id, auto-provisión D-AUTH-05) está implementada y probeada; fail-closed sin creds.
2. **Re-validación E2E post-fixes de Devices y Budgets por-usuario** — los fixes F1-F6 y F3 convergieron con evidencia por-fix, pero no hay una corrida integral independiente posterior. Es exactamente el GATE FINAL **t_e8a28fce** (hoy `todo`, asignado a qa).
3. **Cambio de superficie del punto 7**: la evidencia QA de Devices fue contra el desktop-client demo (:9090), retirado después (t_b8769ecb). El enrolamiento vigente es auto-enroll zero-UI vía link (spec 023) + Hermes real (spec 022). La consolidación debe validar Devices sobre el camino vigente, no sobre el demo retirado.
4. **Policies sin página dedicada**: funcionalmente completo (API jerárquica + deny-overrides + UI distribuida en Organization/Users/Departments/Models), pero el mockup de Miller muestra pestaña "Policies" propia. Decisión de consolidación: ¿se exige página dedicada o se acepta la distribución actual?
5. **Branding**: cubierto (t_6d3f5016 + spec 028, ambos convergidos) pero **no por ninguna de las 7 tareas revisadas** — el mapa de la revisión maestra no debe buscar cobertura de Branding en este set.
6. **Suites**: las lanes reportan fallos residuales de suite atribuidos cruzadamente (order-dependence en test_spend_cutoff — diagnosticado por QA en t_710f0acc; semántica spec 025). No invalidan la cobertura del Pilar 2, pero el gate global los hereda.

## Veredicto

**7/7 tareas en estado board `done` con respaldo real verificado en código/specs/API vivo/imagen desplegada.** Cobertura del Pilar 2: **8 de 10 puntos TOTAL** (1, 2, 3, 5, 6, 8, 9, 4-funcional), **1 CASI TOTAL pendiente de re-validación** (7 Devices), **1 PARCIAL con gap externo** (10 Auth UX — Google). Ninguna afirmación clave de las tareas resultó inflada: todo lo declarado verificado tiene código, spec y evidencia; los gaps que existen están declarados por las propias lanes (AC-AUTH-006) o son trabajo pendiente visible en el board (t_e8a28fce).

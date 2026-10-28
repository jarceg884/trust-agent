# Cierre del Pilar 2 — Consolidación de hallazgos (protocolo de gaps)

**Tarea**: t_9c6d892b (consolidación, hija de la revisión maestra t_ef306f32, cierra el Pilar 2 t_1c65ff67).
**Fecha**: 2026-10-27 (CST). **Idioma**: español. **Nada simulado**: todo lo citado es evidencia real producida por las 4 verificaciones E2E contra el stack vivo.

**Insumos consumidos** (los 4 hijos de verificación, todos `done`):
- t_db7b2fa9 — E2E identidad/auth (puntos 1, 2, 10) → `docs/evidencia/revision-pilar2-qa/EVIDENCIA.md` + 3 screenshots.
- t_a296ae9b — E2E governance (puntos 3, 4, 5) → `docs/revision-pilar2-governance-e2e.md` + scripts/capturas en `docs/evidencia/pilar2-governance/`.
- t_108d8de5 — E2E agentes/devices/audit/branding (puntos 6-9) → `docs/evidencia/pilar2/qa-108d-puntos6-9-evidencia.md` + logs adjuntos a la card.
- t_d9b3d2eb — Mapa de cobertura de tareas relacionadas → `docs/evidencia/pilar2/revision-tareas-pilar2.md`.

**Resultado del protocolo de gaps**: 7 tareas nuevas creadas (hijas de t_1c65ff67, todas enlazadas al gate final t_e8a28fce para que las espere):

| Gap | Nueva tarea | Assignee | Prioridad |
|---|---|---|---|
| M-1 model access nivel user → 500 | t_f75e6a18 | dev | ALTA |
| M-2 catálogo ofrece qwen3-32b retirada de MaaS | t_bf7eff20 | dev | MEDIA |
| B-1 /api/budgets/me cutoff=false con amount=0 | t_ac2d2611 | dev | MEDIA |
| B-2 spend sub-centavo truncado a 0¢ | t_8bd1fee8 | dev | MEDIA |
| B-3 DELETE /api/policies/user sin department_id → 404 | t_fdc728d2 | dev | BAJA |
| GAP-10.2 users.mfa_enrolled sin sincronizar post-TOTP | t_edbcface | dev | BAJA |
| Branding: page titles estáticos + refresh sin reload | t_42e23b01 | ui | BAJA |

Gaps ya cubiertos por tareas existentes (no se duplicaron): Google login → t_f372f47f (blocked, creds del operador); favicon KC → t_13f8b623 (running); re-validación E2E post-fixes Devices/Budgets → gate t_e8a28fce (handoff publicado como comentario en esa card); branding base → t_6d3f5016 (done) + spec 028.

---

## Reporte punto por punto (1-10)

### Punto 1 — Departments CRUD → **VERIFICADO OK**
- **Evidencia reproducible**: pytest `test_departments_crud.py` 14 passed; flujo UI completo con navegador real contra :3100 (tenant fresco `qa-p2rev-223359` aprovisionado en vivo): CREATE con budget atómico $100, RENAME, DELETE con toasts; 409 real mostrado al operador (depto con 1 usuario); audit `department.created/renamed/deleted` con payloads D-16-05 verificado por SQL directo. Screenshots: `docs/evidencia/revision-pilar2-qa/02-departments-page.png`.
- **Tareas que lo cierran**: t_955c98d6 (done, spec 016). Sin gaps abiertos.

### Punto 2 — Usuarios y roles → **VERIFICADO OK**
- **Evidencia reproducible**: pytest `test_roles_access.py`+`test_auth_ux.py` 27 passed; `scripts/smoke_roles_access.py` SMOKE PASS (28 checks: 201/204 roles con scope, 403 anti-escalada, 422 auto-modificación, disable revoca sesión, audit chain ok); UI real: invite (job asíncrono real del worker → `user.invited`+`user.created`), asignar/quitar dept_admin con scope, disable/reactivate, move department, permisos efectivos (panels `permission_denied` para rol user); audit `role.assigned/revoked` verificado en DB. Screenshot: `03-users-roles-page.png`.
- **Tareas que lo cierran**: t_5576ee94 (done, spec 017) + t_1844fb06 (auto-registro, done). Sin gaps abiertos.

### Punto 3 — Budgets jerárquicos (org→depto→usuario) → **GAP PARCIAL → tareas nuevas creadas**
- **Verificado OK**: cascada de cortes user→dept→org con keys reales bloqueadas (401 real), notificaciones y audit emitidos, aislamiento entre deptos, recuperación tras restaurar budget; 73 checks en vivo (tenant `qa-gov-e2e`); edición por usuario post-fix F3 presente en imagen desplegada y openapi vivo.
- **Gaps (evidencia en `docs/revision-pilar2-governance-e2e.md` §B-1/§B-2, scripts `gov_b_cascada.py`/`gov_a_budgets_models.py`)**:
  - **B-1**: `GET /api/budgets/me` → `cutoff:false, cutoff_level:null` con budget $0 y spend 1¢ (capturado en vivo; root cause `budgets.py::_level_status` guard `> 0`). Viola AC-GOV-012 en el borde amount=0.
  - **B-2**: `round(spend_usd*100)` + `spend_ledger.cost bigint` trunca a 0¢ llamadas reales < medio centavo — 9/10 filas del ledger en 0¢; los presupuestos sub-contan gasto real de MaaS → corte tardío.
- **Tareas que lo cierran**: **t_ac2d2611** (B-1) y **t_8bd1fee8** (B-2), nuevas, enlazadas al gate; base convergida previa: t_877b0cb5 + t_6b40c758 (F3) + t_378503a1 (done). Re-validación integral: gate t_e8a28fce.

### Punto 4 — Policies → **VERIFICADO OK (funcional) + 1 bug menor a tarea + DECISIÓN HUMANA abierta**
- **Evidencia reproducible**: CRUD de policies desde la UI (org-level en Organization, user-level en Users), deny-overrides efectivo (verify→403 sobre agentes y modelos), gate sobre agentes default; script `gov_c_policies.py` en vivo; captura `evidence_budgets_page.png`/`evidence_models_page.png`.
- **Gap menor**: **B-3** — `DELETE /api/policies/user/{key}` sin `department_id` → 404 (asimetría PUT/DELETE en `policies.py::deactivate()`; workaround verificado con `&department_id=` → 204). No expuesto por UI (solo borra org).
- **Tareas que lo cierran**: **t_fdc728d2** (B-3), nueva, enlazada al gate; base: t_e0943bd8 + t_0ed22c94 (done).
- **DECISIÓN HUMANA** (registrada en comentario durable de t_1c65ff67): ¿página Policies dedicada (mockup de Miller) o se acepta la UI distribuida actual? El punto está VERIFICADO OK funcional; la respuesta define si nace una tarea ui o el punto queda cerrado.

### Punto 5 — Model access sobre MaaS oficial → **GAP PARCIAL → tareas nuevas creadas**
- **Verificado OK**: catálogo con precios oficiales USD, aprobación por subconjunto, retiro a nivel org, keys reales y respuestas 200/403 reales (11 llamadas vía proxy :4000 + 1 directa + `GET /models` oficial vivo); base t_e0943bd8 con log 21/21 PASS.
- **Gaps (evidencia en `docs/revision-pilar2-governance-e2e.md` §M-1/§M-2)**:
  - **M-1 (ALTA, bloqueante)**: `PUT /api/models/access level=user` → 500 `CheckViolation` (`approve()` inserta `department_id` NULL; el check exige NOT NULL). Reproducido desde API y UI. El nivel usuario de la matriz de aprobaciones (incluye BYOK user) es inoperante. Lección: la suite pytest pasa porque los dobles no reproducen el constraint del schema vivo.
  - **M-2**: `GET /models` oficial vivo devuelve 5 modelos — `qwen3-32b` ya no existe (llamada real → 404 `ModelArts.81009`) pero catálogo y provisioning (plan standard) la siguen ofreciendo. AC-018-01 incumplido hoy.
- **Tareas que lo cierran**: **t_f75e6a18** (M-1) y **t_bf7eff20** (M-2), nuevas, enlazadas al gate.

### Punto 6 — Agentes default → **VERIFICADO OK**
- **Evidencia reproducible**: 4 perfiles default (analyst/coder/ops/support) + adopt + invoke 200/403 desde la UI, contra tenant fresco `p2r108d-223541` con login UI PKCE+TOTP acr=2; 12/12 checks de provisioning; evidencia en `docs/evidencia/pilar2/qa-108d-puntos6-9-evidencia.md`.
- **Tareas que lo cierran**: t_0ed22c94 (done, spec 020). 0 gaps nuevos.

### Punto 7 — Devices → **VERIFICADO OK, condicionado a re-validación del gate sobre el camino vigente**
- **Evidencia reproducible**: approve/reject/revoke desde la UI + exchange one-time (200/410/410) + invalidación inmediata (heartbeat 200→401) + re-adopt 422 + key 404, en vivo (logs `qa_108d_provision.log`, `qa_108d_p7_exchange.log`, `qa_108d_final_db.log` adjuntos a t_108d8de5); audit completo en DB (36 eventos, cadena intacta).
- **Condición**: la evidencia D1-D4 previa fue contra el desktop-client demo (:9090), RETIRADO después (t_b8769ecb); los fixes F1-F6 están done con evidencia por-fix pero falta la re-validación E2E integral post-fixes sobre el camino vigente (auto-enroll zero-UI vía link, spec 023 + Hermes real, spec 022). Eso es exactamente el gate final.
- **Tareas que lo cierran**: fixes t_38ce1e82/t_6f8a24a5/t_c076152d/t_9fddcb75/t_d3fd75db (done); t_0b6e8e2d (renovación device token, running, ya esperada por el gate); **re-validación = t_e8a28fce** (handoff con el alcance mínimo publicado como comentario en esa card).

### Punto 8 — Audit con análisis → **VERIFICADO OK**
- **Evidencia reproducible**: filtros combinables con paridad UI==API, timeline por actor/sesión, pill de integridad (Chain intact), export CSV/JSON trazable — todo en vivo desde la UI; evidencia en `qa-108d-puntos6-9-evidencia.md` + artefactos de export reales en `docs/evidencia/audit-ux/`.
- **Tareas que lo cierran**: t_c14d6703 (done, spec 021). 0 gaps nuevos.

### Punto 9 — Branding → **VERIFICADO OK con 2 observaciones menores → 1 tarea BAJA creada**
- **Evidencia reproducible**: draft→publish v1/v2→rollback v3 desde la UI con uploads reales; sidebar+logo+favicon aplicados en runtime; REQ-BRD2-005 satisfecho. Evidencia: `qa-108d-puntos6-9-evidencia.md` + `qa-rerev-p9-branding.log`.
- **Observaciones (divergencia copy/comportamiento, ninguna tarea las cubría)**: page titles estáticos ("Trust Agent Admin Console") pese a copy que promete `product_name` en titles; sidebar/favicon solo refrescan tras reload.
- **Tareas que lo cierran**: t_6d3f5016 (done) + spec 028 (base); **t_42e23b01** (observaciones, BAJA, ui), nueva, enlazada al gate.

### Punto 10 — Auth UX (Google + auto-registro + MFA opcional + KC invisible) → **VERIFICADO OK con gaps declarados → 1 tarea nueva + 2 existentes**
- **Evidencia reproducible**: Keycloak invisible (tema trust-agent, 0 menciones en DOM, título servido limpio verificado por curl); auto-registro E2E (register→login→rol user→audit `user.self_registered`); MFA opcional (acr=1 + banner persistente + enrolamiento TOTP inline themed → banner desaparece, sesión acr=2); org-config rate-limited anti-enumeración (5×404→429). REFUTADO el P10e previo: el banner MFA sí existe (componente UI del shell, `mfa-banner.tsx`/`me.mfa_verified`). Evidencia: `docs/evidencia/revision-pilar2-qa/EVIDENCIA.md` + `01-idp-login-themed.png`.
- **Gaps**:
  - **Google punta a punta (AC-AUTH-006)**: NO VERIFICABLE sin dependencia externa — verificado vivo: sin env GOOGLE en el API, 0 IdP instances, `google_login_enabled=false`, sin botón. Fail-closed por diseño (D-AUTH-02). → **t_f372f47f** (blocked, requiere `TRUST_GOOGLE_CLIENT_ID/SECRET` del operador — DECISIÓN/ACCIÓN HUMANA). Menor: copy de /register promete Google sign-in inexistente.
  - **Favicon KC (cosmético)**: → **t_13f8b623** (running, ya esperada por el gate).
  - **GAP-10.2 (nuevo)**: `users.mfa_enrolled` no se sincroniza tras el enrolamiento TOTP inline (solo se fija en INSERT: `register.py:132/190`, `invite.py:168`, `provision.py:344`) — el directorio puede mostrar "sin MFA" de un usuario que la tiene. → **t_edbcface**, nueva, enlazada al gate.

---

## Estado del bloqueo de t_1c65ff67 y decisiones humanas

- **Convergencia**: t_1c65ff67 queda a la espera de t_f75e6a18, t_bf7eff20, t_ac2d2611, t_8bd1fee8, t_fdc728d2, t_edbcface, t_42e23b01 (nuevas, todas también enlazadas al gate t_e8a28fce) y t_f372f47f (Google). El gate final t_e8a28fce NO puede pasar hasta que todas converjan — esa es la garantía estructural.
- **Nota operativa**: el `hermes kanban block` de t_1c65ff67 prescrito por el protocolo fue intentado por ambas vías y rechazado por el runtime (tool scoped al worker: "refusing to mutate t_1c65ff67"; CLI: "delegate_task child contexts cannot mutate Kanban tasks via the CLI"). La nota de bloqueo quedó registrada como **comentario durable en t_1c65ff67** (comment_id 178), que es el fallback sancionado por el propio error. El orchestrator/humano debe aplicar el bloqueo formal o tratar el comentario como tal.
- **Decisiones humanas pendientes** (en el comentario de t_1c65ff67):
  1. Credenciales Google (`TRUST_GOOGLE_CLIENT_ID/SECRET`) para desbloquear t_f372f47f y poder verificar AC-AUTH-006 punta a punta.
  2. Policies: ¿página dedicada (mockup Miller) o UI distribuida actual? Define si nace una tarea ui o el punto 4 queda cerrado como está.

## Veredicto de cierre (resumen para t_50422874)

6/10 puntos VERIFICADO OK (1, 2, 4, 6, 8, 9 — el 4 con decisión humana de forma pendiente, el 9 con observaciones menores a tarea BAJA) · 2 GAP PARCIAL con tareas nuevas en marcha (3 → t_ac2d2611+t_8bd1fee8; 5 → t_f75e6a18+t_bf7eff20) · 1 VERIFICADO OK condicionado a re-validación del gate sobre camino vigente (7) · 1 VERIFICADO OK con gap externo que exige acción humana (10 → Google/t_f372f47f). **Los 10 puntos quedan o verificados con evidencia reproducible o convertidos en tarea enlazada al gate t_e8a28fce. Nada simulado.**

---

## Addendum 2026-10-27 14:16 UTC — Re-verificación post-convergencia t_13f8b623 (run 298, QA t_1c65ff67)

### B4 (favicon del realm) → **CERRADO — re-verificado PASS desde el estado REAL**

t_13f8b623 (fix del favicon, AC-AUTH-001) convergió y su `needs_qa` era la re-verificación del favicon contra el realm vivo. Evidencia independiente de QA, capturada en vivo el 2026-10-27 14:15-14:16 UTC contra el Keycloak 26.2.5 real (:8080):

1. **HTML servido del realm** (`demo-org`, endpoint OIDC auth): contiene `link rel="icon" href="/resources/8zyis/login/trust-agent/img/favicon.ico"` — ya no hay fallback al favicon de Keycloak.
2. **Recurso servido** (curl directo a esa URL): **md5 `c7c057a90fa1e51ed1fc4ec2f812ba93`, 641 bytes, PNG 32×32 RGBA** (`file`: "PNG image data, 32 x 32, 8-bit/color RGBA") — byte-idéntico al reportado por el fix; el md5 del caballo (`0980755a…`) ya no se sirve.
3. **Navegador real (Chromium)**: la página del realm carga con `link[rel=icon]` → path del tema trust-agent; **fetch in-page del favicon → HTTP 200, 641 bytes, PNG magic** correcto; **0 menciones de "Keycloak"** en el texto del DOM.
4. **Título servido** (curl, sin harness): `<title>Sign in to Trust Agent</title>` — limpio. (El prefijo 🐴 observado en `document.title` del navegador de tooling es el artefacto de harness CDP ya atrapado con MutationObserver en el run 219; el título servido es el concluyente.)

Con esto, el punto 10 queda: **VERIFICADO OK** en Keycloak-invisible (tema + título + favicon propios), auto-registro y MFA opcional; gaps residuales abiertos: Google punta a punta (**t_f372f47f**, blocked por credenciales del operador — decisión humana) y mfa_enrolled sync (**t_edbcface**, todo).

### Estado REAL de los hijos al cierre de este run (kanban_show, 2026-10-27 ~14:10 UTC)

| Tarea | Gap | Estado |
|---|---|---|
| t_f75e6a18 | M-1 model access user 500 (ALTA) | todo (dependency_wait: parent_not_done) |
| t_bf7eff20 | M-2 catálogo qwen3-32b | todo |
| t_ac2d2611 | B-1 cutoff amount=0 | todo |
| t_8bd1fee8 | B-2 spend sub-centavo | todo |
| t_fdc728d2 | B-3 DELETE policy 404 | todo |
| t_edbcface | GAP-10.2 mfa_enrolled | todo |
| t_42e23b01 | branding titles (@ui) | todo |
| t_f372f47f | Google AC-AUTH-006 | **blocked** (decisión humana: creds del operador) |

Las 7 tareas de gaps + t_f372f47f tienen a **t_e8a28fce como padre** (verificado card por card): el gate final no puede correr hasta que todas converjan — esa es la garantía estructural de que nada se declara completo sin los fixes.

### Por qué `kanban_complete` y no un nuevo bloqueo (precedente Pilar 3, t_79df45e6)

El protocolo prescribe bloquear t_1c65ff67 "esperando convergencia", pero el estado REAL del runtime hace que un bloqueo congele el pipeline en lugar de esperarlo:
1. Las 7 tareas de gaps están en `dependency_wait` con `parent_not_done` sobre ESTA card — el dispatcher no las promueve mientras t_1c65ff67 esté running/blocked. Bloquear = dev no puede empezar los fixes (deadlock). Completar las libera.
2. El block-loop ya llegó a 2/2 en el run 219 (`block_loop_detected`); el watchdog ya auto-desbloqueó espuriamiente 2 veces (21:01 y 21:11 del 2026-10-26).
3. La garantía de convergencia NO depende del bloqueo: el gate t_e8a28fce espera estructuralmente a las 8 tareas (7 gaps + Google).
4. Las 2 decisiones humanas ya están surficadas por vías más fuertes que un bloqueo de esta card: t_f372f47f está blocked con la pregunta específica (creds Google o descope con enmienda), y el comentario durable (id 178) de esta card registra ambas.

La re-verificación post-fix de los puntos 3, 4, 5, 9 y 10 queda como responsabilidad explícita del gate final t_e8a28fce (re-validación integral Devices/Budgets post-fixes ya acordada por handoff en esa card).

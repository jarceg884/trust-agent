# Auditoría de tareas gap — enlace a t_e8a28fce y convergencia real

|| Campo | Valor |
|---|---|---|
| Tarea | t_17c41854 (auditoría de gaps, hija de la revisión maestra t_ef306f32) |
| Perfil | qa |
| Fecha | 2026-10-27 (CST) |
| Método | Enumeración de gaps desde los 4 reportes de cierre (Pilar 1, Pilar 2 consolidación, Pilar 3, maestra) + verificación del estado REAL del board **card por card con `kanban_show`** (status, parents/children, runs, eventos linked/unlinked, heartbeats). Nada simulado. |
| Alcance | Solo auditoría del board; no se implementó nada, no se tocó t_50422874. |

---

## 1. Lista completa de tareas gap → enlace al gate → estado

### 1.1 Gaps del Pilar 1 (SaaS proveedor) — CONVERGIDAS

| Tarea | Gap | ¿Enlazada a t_e8a28fce? | Estado |
|---|---|---|---|
| t_526030f3 | Backend download link (spec 023, seam link→app) | ✅ SÍ — **padre** del gate (children incluye t_e8a28fce) — dirección CORRECTA | ✅ **Convergida con evidencia**: done (run 211), spec 023 CONVERGIDA, E2E real completo (AC-HDL-001..010), suite 512 passed; re-verificada en vivo por QA (Pilar 1 flujo 5: `GET /download/qa-pilar1-review` → 200, toggle auditado, suite 9/9). |
| t_ccf901f4 | Copy OTA que sobreprometía TOTP forzado | ✅ SÍ — **padre** del gate — CORRECTA | ✅ **Convergida con evidencia**: done (run 204), copy corregido en 6 puntos, verificado en vivo contra el tenant real `qa-pilar1-review` (org-config, catálogo, Keycloak Admin REST) y contra el bundle servido; tests 44+13 passed. |

### 1.2 Gaps del Pilar 2 (panel admin) — 1 convergida, 7 congeladas, 1 bloqueada

| Tarea | Gap | Severidad | ¿Enlazada a t_e8a28fce? | Estado EXACTO |
|---|---|---|---|---|
| t_13f8b623 | B4 favicon del realm (AC-AUTH-001) | — | ✅ SÍ — **padre** del gate — CORRECTA (evento `unlinked` del link invertido original a las 1793109419, corregido por el orchestrator) | ✅ **Convergida con evidencia**: done (run 296), favicon propio servido (md5 `c7c057a90fa1e51ed1fc4ec2f812ba93`, PNG 32×32, 641 B), verificado en vivo contra KC 26.2.5 real + **re-verificación independiente de QA** (addendum del cierre Pilar 2: navegador real fetch 200, 0 "Keycloak" en DOM, título servido limpio). |
| t_f75e6a18 | M-1: aprobar modelo a nivel user → 500 `CheckViolation` | **ALTA** (inoperante) | ⚠️ SÍ, pero **INVERTIDA**: el gate figura como su **padre** (`parents: [t_1c65ff67, t_e8a28fce]`, `unsatisfied_parents: [t_e8a28fce todo]`) → la tarea **espera al gate** en vez de al revés | ❌ **No convergida — CONGELADA**: columna `todo`, **0 runs** (nunca despachada), `dependency_wait parent_not_done` sobre el gate. Qué falta: (a) corrección del link (`unlink t_e8a28fce t_f75e6a18 && link t_f75e6a18 t_e8a28fce`), (b) dispatch a dev, (c) fix + evidencia contra DB real (la suite pytest no reproduce el constraint vivo). **No requiere decisión humana.** |
| t_bf7eff20 | M-2: catálogo ofrece qwen3-32b retirada de MaaS (AC-018-01) | MEDIA | ⚠️ SÍ, **INVERTIDA** (mismo patrón verificado en su card) | ❌ **No convergida — CONGELADA**: `todo`, 0 runs, mismo blocker estructural. No requiere decisión humana. |
| t_ac2d2611 | B-1: `/api/budgets/me` cutoff=false con amount=0 (AC-GOV-012) | MEDIA | ⚠️ SÍ, **INVERTIDA** | ❌ **No convergida — CONGELADA**: `todo`, 0 runs. No requiere decisión humana. |
| t_8bd1fee8 | B-2: spend sub-centavo truncado a 0¢ (round + bigint) | MEDIA | ⚠️ SÍ, **INVERTIDA** | ❌ **No convergida — CONGELADA**: `todo`, 0 runs. No requiere decisión humana. |
| t_fdc728d2 | B-3: DELETE policy user sin department_id → 404 | BAJA | ⚠️ SÍ, **INVERTIDA** | ❌ **No convergida — CONGELADA**: `todo`, 0 runs. No requiere decisión humana. |
| t_edbcface | GAP-10.2: `users.mfa_enrolled` no se sincroniza post-TOTP | BAJA | ⚠️ SÍ, **INVERTIDA** | ❌ **No convergida — CONGELADA**: `todo`, 0 runs. No requiere decisión humana. |
| t_42e23b01 | Branding: page titles estáticos + refresh sin reload | BAJA (@ui) | ⚠️ SÍ, **INVERTIDA** | ❌ **No convergida — CONGELADA**: `todo`, 0 runs. No requiere decisión humana (el body nota que el gate puede descopearla si el humano lo decide — decisión opcional, no bloqueante del dispatch). |
| t_f372f47f | Google login punta a punta (AC-AUTH-006) | — | ⚠️ SÍ, **INVERTIDA** | ❌ **No convergida — BLOCKED**: columna `blocked` desde su creación (evento `blocked initial_status`). **Blocker: decisión humana** — entregar `TRUST_GOOGLE_CLIENT_ID/SECRET` del operador o descopear AC-AUTH-006 con enmienda documentada a la spec 015. Producto fail-closed mientras tanto (verificado en vivo: sin env GOOGLE, `google_login_enabled=false`, sin botón). |

### 1.3 Gaps del Pilar 3 (app ligada a gobernanza) — en marcha, link correcto

| Tarea | Gap | Severidad | ¿Enlazada a t_e8a28fce? | Estado EXACTO |
|---|---|---|---|---|
| t_0b6e8e2d | Renovación del device token (TTL 15 min sin camino de renovación; D-HRB-03 rota) | **ALTO** | ✅ SÍ — **padre** del gate (children: `[t_e8a28fce]`) — CORRECTA (el orchestrator invirtió el link original tras el flag del Pilar 3) | ⚠️ **No convergida — EN MARCHA (running, run 304, dev activo)**: heartbeats con progreso real — "Suite plataforma 517/517 en verde (server + plugin + migración 0023)", "Evidencia live en curso: setup 10/10 PASS (device df3628ef enrolado)", "**Renovación deslizante confirmada en vivo** (device_token_renewed en plugin.log). Fase renewal esperando superar TTL. Suites: plataforma 517/517, db 207/207". 2 intentos previos `rate_limited` (requeued sin contar failure). Qué falta: fase de evidencia que supere el TTL de 15 min + revocación fail-closed + cierre con evidencia + re-verificación puntual de QA. **No requiere decisión humana.** |

### 1.4 Tareas relacionadas verificadas por completitud (cobertura REGLA DE ORO / 29 keys, creadas desde la matriz de capacidades t_d8ed9362 y flaggeadas por las revisiones)

| Tarea | Qué es | ¿Enlazada al gate? | Estado |
|---|---|---|---|
| t_fb62560f | Catálogo tipado: 29 policy keys (spec 029, migración 0022) | ✅ **padre** del gate — CORRECTA (link invertido original corregido, evento `unlinked` a las 1793109408) | ✅ Convergida: done (run 292), 47 keys en catálogo, suite 507 passed, paridad schema verificada. |
| t_150e8931 | Bundle JWS extensible (schema versionado, deny-by-default) | ✅ **padre** del gate — CORRECTA (corregido a las 1793109416) | ✅ Convergida: done (run 303), suite completa 535 passed / 0 failed. |
| t_7e2852f8 | Enforcement batch 1 (config-level) | ✅ **padre** del gate — CORRECTA | ⚠️ running (run 314; 3 intentos previos rate_limited). El gate la espera correctamente. |
| t_a868974a | Enforcement batch 2 (capability-level + E-1..E-3) | ✅ **padre** del gate — CORRECTA | ⚠️ running (run 315; 2 intentos previos rate_limited). El gate la espera correctamente. |

---

## 2. Gaps huérfanas (creadas durante las revisiones sin enlace a t_e8a28fce)

- **Ninguna gap ABIERTA huérfana.** Todas las gaps abiertas de las revisiones están enlazadas al gate (las 8 del Pilar 2 con dirección invertida, pero enlazadas).
- **Un caso documentado**: **t_dd2f5f65** ([MEDIO] race multi-proceso del auto-enroll, 410s mutuos). Fue creada durante el E2E de la spec 023 (hallazgo real del runbook, regla 8) y referenciada por el reporte del Pilar 1 como "trackeada en su lane" — **sin enlace al gate** (su único padre es t_526030f3). **Está done con evidencia** (run 213: before/after 3 POST/2 410s → 1 POST/0 410s, regresión multi-proceso permanente, 32/32 tests), por lo que no representa un riesgo de gate-ciego: no hay nada abierto fuera del radar del gate. Se documenta como observación, no como hallazgo abierto.

---

## 3. Hallazgo estructural persistente: los 8 links invertidos del Pilar 2

Verificado card por card HOY: **el orchestrator NO ha corregido los links de las 8 tareas del Pilar 2** (t_f75e6a18, t_bf7eff20, t_ac2d2611, t_8bd1fee8, t_fdc728d2, t_edbcface, t_42e23b01, t_f372f47f). En las 8, `parents` incluye a `t_e8a28fce` y `unsatisfied_parents` = `[{t_e8a28fce, todo}]` — es decir, **esperan al gate** (dependency_wait `parent_not_done`) en vez de ser esperadas por él.

Contraste verificado: para t_0b6e8e2d, t_13f8b623, t_fb62560f y t_150e8931 el orchestrator SÍ aplicó la corrección (eventos `unlinked` del link invertido + relink en dirección correcta, visibles en sus eventos). **El mismo patrón falta para las 8 del Pilar 2.**

Consecuencia actual: mientras el gate esté `todo`, dev/ui no puede empezar ninguno de los 7 fixes; y si los padres actuales del gate convergen y la maestra se completa, el gate correría sin los 7 fixes del Pilar 2 ni Google. Corrección requerida (orchestrator), por cada una:

```bash
hermes kanban unlink t_e8a28fce <tarea> && hermes kanban link <tarea> t_e8a28fce
```

---

## 4. Veredicto global

**¿Todas las gaps están convergidas? NO.** Contabilidad exacta al momento de esta auditoría:

| Categoría | Tareas | Total |
|---|---|---|
| ✅ Convergidas con evidencia reproducible | t_526030f3, t_ccf901f4, t_13f8b623 (+ relacionadas t_fb62560f, t_150e8931, t_dd2f5f65) | 6 |
| ⚠️ En marcha, link correcto, sin blocker humano | t_0b6e8e2d (ALTO, run 304 activo) (+ batches t_7e2852f8, t_a868974a) | 3 |
| ❌ Congeladas en `todo`, 0 runs, por link invertido | t_f75e6a18 (ALTA), t_bf7eff20, t_ac2d2611, t_8bd1fee8, t_fdc728d2, t_edbcface, t_42e23b01 | 7 |
| ❌ Blocked por decisión humana (+ link invertido) | t_f372f47f (creds Google o descope AC-AUTH-006) | 1 |

**¿Todas las gaps tienen su estado exactamente documentado? SÍ.** Las 12 gaps de las revisiones de pilar quedan arriba con: enlace al gate (sí/no/dirección), columna del board, blocker, qué falta y si requiere decisión humana. Resumen de blockers:
- **Blocker estructural (orchestrator, no humano)**: 8 links invertidos del Pilar 2 pendientes de corrección — es lo que congela los 7 fixes en `todo`.
- **Blocker humano (1)**: t_f372f47f — credenciales Google del operador o descope documentado de AC-AUTH-006.
- **Sin blocker**: t_0b6e8e2d y los 2 batches siguen corriendo con heartbeats activos.

**El gate t_e8a28fce NO debe correr todavía**: 8 de sus esperas estructurales siguen invertidas y 7 fixes + 1 decisión humana no han convergido.

---

## 5. Cómo reproducir esta auditoría

```bash
# Estado y links de cada gap (card por card):
#   kanban_show(t_<id>) → campos: status, parents, unsatisfied_parents, children, runs, events (linked/unlinked)
# IDs: t_526030f3 t_ccf901f4 t_13f8b623 t_f75e6a18 t_bf7eff20 t_ac2d2611 t_8bd1fee8
#      t_fdc728d2 t_edbcface t_42e23b01 t_f372f47f t_0b6e8e2d t_dd2f5f65
#      t_fb62560f t_150e8931 t_7e2852f8 t_a868974a

# Insumos (reportes de cierre):
#   /root/trust-agent/docs/revision-pilar1-saas-proveedor.md
#   /root/trust-agent/docs/evidencia/pilar2/cierre-pilar2-consolidacion.md
#   /root/trust-agent/docs/revision-pilar3-cierre.md
#   /root/trust-agent/docs/revision-maestra-producto.md
```

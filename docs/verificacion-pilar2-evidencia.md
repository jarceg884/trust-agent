# Verificación de evidencia e2e del Pilar 2 (panel admin por cliente) — t_5261032b

**Tarea**: t_5261032b (hija de la revisión maestra t_ef306f32; insumo del reporte final t_50422874) · **Perfil**: qa · **Fecha**: 2026-10-27 (CST)
**Objeto verificado**: el reporte de cierre del Pilar 2 — `docs/evidencia/pilar2/cierre-pilar2-consolidacion.md` (t_9c6d892b + addendum run 298) y `docs/revision-pilar2-panel-admin.md` (t_1c65ff67, runs 219/298).
**Método**: contraste del estado REAL (board por SQL directo a `boards/trust-agent/kanban.db`, código en `platform/`, specs en `specs/`, logs de evidencia en `docs/evidencia/`) + **re-ejecución en vivo** de tests y scripts reproducibles contra el stack vivo (API :8000 healthy, console :3100 healthy, Keycloak :8080, PostgreSQL :55432 — `docker ps` verificado). Nada simulado.

## Re-ejecuciones EN VIVO realizadas por esta verificación (hoy)

1. **Punto 4 (fix B2 deny-all)**: `evidencia_f0d3122d_deny_all.py` re-ejecutado dentro del contenedor `trust-agent-api` contra el stack actual, tenant fresco `f0d3-145303` aprovisionado en caliente, login real PKCE S256 → **24 PASS / 0 FAIL** (log: `deny_all_rerun.log` del workspace). El fix sigue vivo: `allowed-for-me = []` con deny-all explícito, verify → 403, audit `model.access_denied` en DB.
2. **Puntos 1-2 (tests)**: `pytest tests/test_departments_crud.py tests/test_roles_access.py` → **29 passed**; `pytest tests/test_auth_ux.py` → **12 passed** (roles 15 + auth_ux 12 = 27, coincide con el reporte).
3. **Punto 10 (fix B4 favicon)**: sonda viva contra Keycloak — `<link rel="icon" href="/resources/8zyis/login/trust-agent/img/favicon.ico">`, recurso servido **md5 `c7c057a90fa1e51ed1fc4ec2f812ba93`, PNG 32×32 RGBA** (idéntico al reportado; el md5 del caballo de KC ya no se sirve); título servido `<title>Sign in to Trust Agent</title>`; **0 menciones de "Keycloak" en el texto visible** del DOM (las 4 coincidencias del HTML crudo son URLs de recursos vendor, no texto).
   - NOTA menor: el reporte cita la ruta del favicon como `platform/idp-themes/trust-agent/login/img/favicon.ico`; la ruta REAL es `platform/idp-themes/trust-agent/login/resources/img/favicon.ico` (misma función, cita imprecisa).

## Tabla punto a punto

| # | Punto | Estado declarado en el cierre | Verificación contra estado REAL | Veredicto |
|---|---|---|---|---|
| 1 | Departments CRUD | VERIFICADO OK | pytest re-ejecutado hoy: 14 passed; `docs/evidencia/revision-pilar2-qa/EVIDENCIA.md` + screenshot `02-departments-page.png` existen con flujo UI completo (409 real, audit); spec `016-departments-crud` completa (spec/plan/tasks); t_955c98d6 **done** en board | **VERIFICADO** |
| 2 | Usuarios y roles | VERIFICADO OK | pytest re-ejecutado hoy: roles 15 + auth_ux 12 = 27 passed; EVIDENCIA.md + `03-users-roles-page.png`; spec `017-roles-access` completa; t_5576ee94 **done** | **VERIFICADO** |
| 3 | Budgets jerárquicos | GAP PARCIAL (B-1, B-2) → tareas nuevas | Núcleo verificado con evidencia reproducible (`docs/evidencia/pilar2-governance/`: gov_setup/gov_a/gov_b/gov_c/maas_directo.py + `docs/revision-pilar2-governance-e2e.md`); **root causes siguen EN el código**: B-1 `app/governance/budgets.py:347` (`amount_cents > 0 and spent >= …` excluye el 0 explícito), B-2 `spend_worker.py` round+bigint; t_ac2d2611 y t_8bd1fee8 **todo** (sin empezar) | **VERIFICADO como GAP PARCIAL — NO completo** |
| 4 | Policies | VERIFICADO OK funcional + B-3 menor | Fix B2 **re-verificado EN VIVO hoy: 24 PASS / 0 FAIL** (tenant fresco f0d3-145303); fix presente en `app/models/access.py:237-240` (`isinstance(list)` → intersección incluso con `[]`); B-3 root cause presente (`policies.py` filtro `(department_id IS NULL) = :dept_null`); t_fdc728d2 **todo**; decisión humana (página Policies dedicada) sigue abierta (comment 178) | **VERIFICADO** (fix vivo); B-3 abierto |
| 5 | Model access MaaS | GAP PARCIAL (M-1 ALTA, M-2) → tareas nuevas | Evidencia del núcleo real (11 llamadas MaaS vía proxy :4000, keys reales, 200/403 reales) documentada y scriptada; **M-1 confirmado ABIERTO en código**: `approve()` (`app/models/access.py:87-98`) inserta `department_id` del body sin resolver el depto del usuario → `CheckViolation` con el constraint del schema vivo; t_f75e6a18 **todo**; M-2 (qwen3-32b retirada de MaaS) t_bf7eff20 **todo** | **VERIFICADO como GAP PARCIAL — NO completo** (M-1 bloqueante del nivel user) |
| 6 | Agentes default | VERIFICADO OK | `docs/evidencia/pilar2/qa-108d-puntos6-9-evidencia.md` con flujo UI+API+audit (adopt/invoke 200/403, 12/12 provisioning); spec `020-default-agent-profiles` completa; t_0ed22c94 **done** | **VERIFICADO** |
| 7 | Devices | VERIFICADO OK condicionado a re-validación del gate | 3 logs de evidencia adjuntos a t_108d8de5 verificados en el board (`qa_108d_provision.log`, `qa_108d_p7_exchange.log`, `qa_108d_final_db.log`); fixes F1/F2/F4/F5/F6 **done**; PERO la re-validación integral post-fixes sobre el camino vigente NO ha corrido y **t_0b6e8e2d (renovación device token, ALTO) sigue running** | **VERIFICADO con condición PENDIENTE** |
| 8 | Audit con análisis | VERIFICADO OK | qa-108d-puntos6-9-evidencia.md (filtros, timeline, Chain intact, export CSV/JSON reales); spec `021-audit-ux` completa; t_c14d6703 **done** | **VERIFICADO** |
| 9 | Branding | VERIFICADO OK + 2 obs menores → 1 tarea BAJA | `qa-rerev-p9-branding.log` leído: **27 PASS / 0 FAIL** (draft→publish→rollback→assets con sha256/ETag); script `qa_rerev_p9_branding.py` existe; fix UI B1 verificado en código: `branding-page.tsx:163` hace `PUT /api/branding` real con toast honesto; spec `028-branding-versioning` completa; t_6d3f5016 y t_d2b65bcf **done**; t_42e23b01 (titles estáticos) **todo** | **VERIFICADO**; obs menor abierta |
| 10 | Auth UX | VERIFICADO OK con gaps declarados (B4 cerrado; Google humano; mfa_enrolled) | **B4 re-verificado EN VIVO hoy** (favicon md5 exacto servido por el tema trust-agent, título limpio, 0 "Keycloak" en texto visible); t_13f8b623 **done** ✓; Google AC-AUTH-006: t_f372f47f **blocked** por credenciales del operador (decisión humana, fail-closed verificado); GAP-10.2 mfa_enrolled: t_edbcface **todo** | **VERIFICADO con gaps declarados** |

## Verificación de las afirmaciones estructurales del reporte

| Afirmación del cierre | Estado REAL | Veredicto |
|---|---|---|
| "10/10 puntos verificados con evidencia reproducible o convertidos en tarea" | Exacto: cada punto tiene log/script/spec/tarea verificable; las 7 tareas de gaps + Google existen en el board con los assignees correctos | **VERIFICADO** |
| "Las 7 gaps + t_f372f47f tienen a t_e8a28fce como padre: el gate final NO puede pasar hasta que todas converjan — garantía estructural" | **REFUTADO por SQL directo**: en `task_links` los 8 registros son `parent=t_e8a28fce, child=gap` → con la semántica del board (el hijo espera al padre), **el gate NO espera a los gaps; los gaps esperan al gate** (deadlock para dev y riesgo de que el gate corra sin los fixes, M-1 ALTA incluida). Ya flaggeado como CRÍTICO por la revisión maestra (comment 183 en t_e8a28fce) y **sigue sin corregir hoy** | **NO VERIFICADO — links invertidos, corrección pendiente del orchestrator** (`unlink t_e8a28fce <gap> && link <gap> t_e8a28fce` × 8) |
| Contabilidad de gaps (7 nuevas + Google + favicon) | Exacta contra el board: t_f75e6a18, t_bf7eff20, t_ac2d2611, t_8bd1fee8, t_fdc728d2, t_edbcface, t_42e23b01 (todo), t_f372f47f (blocked), t_13f8b623 (done) | **VERIFICADO** |
| Los 3 FAIL del run API inicial quedaron todos contabilizados | P4e → fix B2 re-verificado vivo 24/24 por esta tarea; P10e (banner MFA) → refutado con evidencia (el banner existe); P10f (Google) → gap humano declarado | **VERIFICADO** |

## Veredicto global del Pilar 2

**¿Completo y verificado e2e? — NO (aún).**

**Justificación basada solo en evidencia:**

- **Lo que SÍ está verificado (sólido)**: el núcleo funcional de los 10 puntos tiene evidencia reproducible real, y esta verificación la re-ejecutó en vivo hoy contra el stack actual: fix deny-all 24/24 PASS con tenant fresco, favicon del realm servido con md5 exacto, 41 tests pytest passed (departments 14 + roles 15 + auth_ux 12), fix de branding UI presente en código, logs y specs completos. El reporte de cierre es **honesto y exacto**: no promete más de lo que hay y su contabilidad de gaps cuadra contra el board.
- **Lo que impide declararlo completo**:
  1. **Punto 5**: gap **M-1 (ALTA, bloqueante)** — aprobar modelo a nivel usuario devuelve 500; root cause confirmado hoy en el código (`access.py::approve()` no resuelve el depto); t_f75e6a18 sin empezar. El nivel user de la matriz de aprobaciones (incluye BYOK user) es inoperante.
  2. **Punto 3**: gaps B-1 (cutoff=false con budget $0) y B-2 (spend sub-centavo truncado) con root causes aún en código; tareas sin empezar.
  3. **Punto 10**: AC-AUTH-006 (Google punta a punta) **exige decisión humana** (credenciales del operador); t_f372f47f blocked.
  4. **Punto 7**: la re-validación integral post-fixes sobre el camino vigente (gate) no ha corrido; t_0b6e8e2d (renovación device token, ALTO) sigue running.
  5. **Hallazgo estructural**: la "garantía estructural" del gate está **rota** (8 links invertidos verificados por SQL hoy) — tal como está, el gate final podría correr y "validar" el pilar sin los 7 fixes. Corrección pendiente del orchestrator.
- **Conclusión operativa**: el Pilar 2 está **verificado e2e en su núcleo con evidencia reproducible (nada simulado confirmado)**, pero **NO puede marcarse completo** hasta que converjan los 7 gaps (+ Google por decisión humana) y se corrija la dirección de los 8 links del gate. El estado "10/10 verificados o convertidos en tarea" del reporte de cierre es exacto; el estado "completo" no lo es, y el propio reporte tampoco lo reclama.

**Para t_50422874 (compilación final)**: el veredicto del Pilar 2 es **NO completo / reporte de cierre fiable**, con 2 dependencias críticas antes de cualquier "done" global: (a) corrección de los 8 links invertidos del gate t_e8a28fce, (b) convergencia de t_f75e6a18 (M-1) como mínimo del gap ALTA.

# Spec 013 — Cierre de gaps de spec 001 (hallazgos de la validación final)

| Campo | Valor |
|---|---|
| **Padre** | specs/001-platform/spec.md (REQ/AC citados por ítem), docs/validacion-final.md §3/§5 (t_bf72a163) |
| **Alcance** | Los 7 ítems inventariados por QA. Por ítem se indica la spec que extiende y la decisión de diseño. |
| **Fuente** | Task t_884bfcb4 (QA findings). Ninguno bloquea la demo; severidad MoSCoW heredada de spec 001. |
| **Reglas** | SDD por ítem, evidencia real (tests contra PostgreSQL vivo), audit al contrato spec 008 §5 (acciones nuevas sembradas en `catalog.audit_actions`, jamás bypass del FK). |

## 1. [MEDIUM] AC-BUD-010 — rollover de período con reloj simulado

**Extiende**: specs/005-governance (D-GOV-07, REQ-GOV-005…011) · specs/001-platform REQ-BUD-010.

**Decisión D-013-01**: el reloj es un seam de test, no un servicio. `app.governance.budgets.today()` es la ÚNICA fuente de la fecha actual para ventanas de presupuesto/counters (budgets.py y spend.py la usan); el test de rollover la monkeypatcha (reloj simulado, permitido por AC-BUD-010). No se añade "período de 1 min" de producción: el reset NO es un job que borra counters — cada ventana vive en su propia fila de `budget_counters` (PK `tenant_id, level, entity_id, period_start`), de modo que el contador del período nuevo NACE en 0 y el enforcement (bloqueo/umbrales) consulta la ventana vigente.

> **AC-013-010**: con `period=daily`, tras gastar ≥ presupuesto y quedar cortado (key bloqueada), avanzar el reloj un día y correr enforcement → la key se desbloquea (`budget_recovered_or_period_reset`), `GET /api/budgets/me` muestra `spent_cents=0` en la ventana nueva y existe una fila de counter por cada ventana (la vieja conserva su gasto).

## 2. [MEDIUM] REQ-CON-005 — desconexión proactiva ≤ 60 s

**Extiende**: specs/006-vault-connectors (REQ-VC-010…015) · specs/005-governance (REQ-POL-001…007).

**Decisión D-013-02**: el barrido es SÍNCRONO, en la MISMA transacción del cambio de política — no un job periódico. Cuando `policies.upsert`/`deactivate` toca `allowed_connectors` o `messaging_permissions` (org/dept/user), se re-evalúa la política efectiva de cada usuario afectado y toda instancia ACTIVA que la nueva política ya no permite pasa a `disconnected` (primitiva compartida `disconnect_connector_instances`, la misma de revocación de credencial), con notificación de inbox al dueño y `connector.disconnected` auditado (`reason: policy_updated`). Presupuesto ≤ 60 s: trivialmente cumplido (misma transacción, cero espera). El enforcement a tiempo de uso (catálogo filtrado + `_check_instance_governance`) se mantiene como defensa en profundidad.

> **AC-013-005**: deshabilitar `email` a nivel depto → las instancias activas de ese depto pasan a `disconnected`, sus dueños reciben notificación, `connector.disconnected` queda auditado; el depto vecino no se afecta. Barrido análogo a nivel org y user.

## 3. [LOW·S] REQ-BUD-012 — GET /api/budgets/history

**Extiende**: specs/005-governance (budgets) · specs/001-platform REQ-BUD-012.

**Decisión D-013-03**: el histórico se deriva de `budget_counters` (presupuesto vs usado por nivel y ventana, solo ventanas CERRADAS: `period_end < today`) + desglose por modelo desde `spend_ledger` (`cost_billed_cents` por `model_name` en la ventana). org_admin ve todo; dept_admin ve solo sus deptos (usuarios de su scope; `by_model` filtrado por sus deptos; sin bloqueo org). Sin tabla nueva: los counters ya retienen el histórico por ventana.

> **AC-013-012**: `GET /api/budgets/history` devuelve períodos cerrados con presupuesto vs usado por nivel y desglose por modelo; el `spent` de cada nivel cuadra con la suma del ledger en esa ventana (test de reconciliación).

## 4. [LOW·S] REQ-AUD-006 — retención/purga de audit

**Extiende**: specs/008-audit (§5 contrato) · specs/002-data-layer (schema físico).

**Decisión D-013-04**: la purga es un JOB OPS del rol `trust_migrator` (la app es INSERT-only por diseño — `REVOKE DELETE`), expuesto como `python -m app.audit.cli purge [--tenant <id>|--all]` (cron-able). La cadena NO se rompe: la purga deja un checkpoint por tenant (`audit_chain_checkpoint`: último seq/hash purgado); `audit_verify_chain` verifica desde el checkpoint y el trigger de INSERT continúa la cadena desde el checkpoint si el tenant quedó vacío. El trigger de inmutabilidad permite DELETE únicamente con `current_user = trust_migrator` Y el flag de transacción `app.audit_purge = 'on'` (fail-closed para cualquier otro rol/uso; UPDATE siempre prohibido). La retención vive en `tenants.retention_days` (ya existía, CHECK ≥ 365): `PUT /api/audit/retention` (org_admin) rechaza < 365 con 422 y audita `audit.retention_updated` (acción nueva sembrada en catálogo). La purga registra `audit.purged` con `{purged_rows, retention_days, cutoff}` (acción ya sembrada).

> **AC-013-006**: `PUT /api/audit/retention` con 300 → 422; con 400 → 200 y audit `audit.retention_updated`. El job purga eventos con `ts < now() - retention`, emite `audit.purged` con conteo, y la cadena verifica `ok` después de purgar y tras eventos nuevos (incluido el caso de purga total del tenant: el evento siguiente continúa desde el checkpoint).

## 5. [LOW·S] REQ-SEC-011 — WebAuthn/passkeys: NO implementado (decisión humana pendiente)

**Extiende**: specs/001-platform §10.3 (decisión abierta). El spec lo dejó explícitamente como decisión abierta ("confirmar si sube a Must para v1"). **No se implementa en esta fase**: subir WebAuthn a Must es una decisión de producto que excede al lane dev. Se escala al humano (card bloqueada `needs_input`); TOTP con `acr≥2` (spec 011) sigue siendo el segundo factor vigente.

## 6. [LOW] REQ-BRD-002/003 — branding runtime en el desktop

**Extiende**: specs/012-org-endpoints (REQ-ORG-110/111, GET /api/branding ya verificado) · specs/001-platform REQ-BRD-002/003.

**Decisión D-013-06**: el desktop consume `GET /api/branding` tras el login (server-side, con el bearer de la sesión) y lo inyecta en `Brand`/`BrandTagline`/Sidebar: `product_name`, `tagline`, `accent_color`, `logo_url`. Antes del login mantiene el branding estático del proveedor (AC-BRD-003: default antes de autenticarse; el endpoint exige sesión MFA). Sin redeploy ni re-empaquetado: es una lectura en runtime en cada render del dashboard.

> **AC-013-BRD**: cambiar el branding del tenant → el dashboard lo refleja tras recargar, sin reiniciar servicios; antes del login se muestra el default del proveedor. Evidencia: build del desktop en verde + smoke del contrato.

## 7. [LOW] REQ-ORG-001 — overview con departamentos sin presupuesto

**Extiende**: specs/012-org-endpoints (REQ-ORG-121, departments) · specs/001-platform AC-ORG-001.

**Decisión D-013-07**: se LISTAN los departamentos sin presupuesto en el overview (`budget_cents: null`, `spent_cents: 0`) en vez de crear un presupuesto 0 implícito. Motivo: una fila de presupuesto 0 cambia semántica de validación (Σ deptos ≤ org, force-below-spend, "sin presupuesto aún" en budgets de usuario) y mentiría el estado real. AC-ORG-001 queda así satisfecho: el depto recién creado es visible en la tabla del overview con su límite aún sin asignar. La consola tipa `budget_cents: number | null` (el `usd()` existente ya renderiza "—").

> **AC-013-ORG**: `POST /api/departments` → el depto aparece en `GET /api/budgets/overview` con `budget_cents: null` y `spent_cents: 0`; tras `PUT /api/budgets/dept/{id}` muestra el monto. Aislamiento entre tenants se mantiene.

## 8. Contrato de audit (spec 008 §5)

Acciones usadas por esta spec — todas sembradas en `catalog.audit_actions` (schema.sql §11 + migración 0006):

| Acción | Estado | Uso |
|---|---|---|
| `connector.disconnected` | existente | barrido REQ-CON-005, payload `{connector_slug, reason: "policy_updated", level, department_id?}` |
| `audit.retention_updated` | NUEVA (§11) | `PUT /api/audit/retention`, payload `{retention_days, previous_retention_days}` |
| `audit.purged` | existente (ya sembrada) | job de purga, payload `{purged_rows, retention_days, cutoff}` |
| `budget.updated` / `model.key_unblocked` | existentes | sin cambios |

## 9. Fuera de alcance

- WebAuthn (§5): bloqueado por decisión humana.
- Retención > 365 configurable por tenant: ya soportado por el esquema (`retention_days` sin techo); rango máximo sigue pendiente (spec 001 §10.3).
- Purga automática calendarizada: el job existe como CLI cron-able; el scheduler de producción es decisión de deploy.

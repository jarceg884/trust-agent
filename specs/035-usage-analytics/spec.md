# Spec 035 — Usage Analytics (mandato Miller)

## Objetivo

El admin debe poder ver los **requests y consumos por usuario** — no solo
presupuesto vs gastado, sino el detalle de uso por persona — sobre el
**ledger real** (`spend_ledger`, una fila por request). Nada simulado
(constitution I): cada cifra sale de `spend_ledger` vía SQL agregado.

## Contexto

- `spend_ledger` ya registra cada request: `request_id` (UNIQUE),
  `user_id`, `department_id`, `model_name`, `mode` (`central`/`byok`),
  `cost_base_cents`, `cost_billed_cents`, `tokens_in`, `tokens_out`,
  `occurred_at`.
- Ya existen: `budgets/overview` (budget vs spent por nivel),
  `budgets/history` (períodos cerrados + breakdown por modelo),
  `devices/presence` (conectados en vivo).
- Falta: la vista de uso por usuario sobre el ledger (agregado +
  drill-down + export).
- **Sin migración**: el esquema actual basta; los índices existentes
  (`spend_ledger_tenant_time_idx`, `spend_ledger_tenant_user_idx`)
  cubren los accesos por ventana y por usuario.

## Requisitos

### REQ-USG-001 — GET /api/usage (agregado por usuario)

Admin-scoped: `org_admin` ve todo el tenant; `dept_admin` solo sus
departamentos (`dept_scope`); cualquier otro rol → 403.

Filtros (query params, todos opcionales salvo indicación):
- `period` ∈ `today | 7d | 30d | custom` (default `30d`). Con
  `period=custom` son obligatorios `from` y `to` (fechas ISO `YYYY-MM-DD`,
  `to >= from`); ventana inclusiva sobre `occurred_at::date`.
- `user_id` (UUID válido o 422), `model` (texto exacto), `mode` ∈
  `central | byok` (cualquier otro valor → 422).

Respuesta:
```json
{
  "scope": "org" | "dept",
  "period": {"kind": "...", "from": "...", "to": "..."},
  "filters": {"user_id": null, "model": null, "mode": null},
  "totals": {"requests": N, "tokens_in": N, "tokens_out": N,
             "cost_base_cents": N, "cost_billed_cents": N},
  "users": [
    {"user_id": "...", "email": "...", "display_name": "...",
     "department_id": "...", "department_name": "...",
     "requests": N, "tokens_in": N, "tokens_out": N,
     "cost_base_cents": N, "cost_billed_cents": N,
     "central_requests": N, "byok_requests": N,
     "models": [{"model_name": "...", "mode": "...", "requests": N,
                 "cost_billed_cents": N}]}
  ]
}
```
- Solo usuarios con ≥1 request en la ventana (es usage, no directorio).
- Orden: `cost_billed_cents` DESC, luego email.
- Montos en centavos enteros (USD); la UI los formatea a USD.

### REQ-USG-002 — GET /api/usage/requests (drill-down paginado)

Mismos filtros de REQ-USG-001 + paginación `limit` (default 50, 1..200) y
`offset` (≥0). Respuesta:
```json
{"total": N, "limit": N, "offset": N,
 "requests": [{"request_id": "...", "occurred_at": "...ISO...",
   "user_id": "...", "email": "...", "display_name": "...",
   "department_id": "...", "model_name": "...", "mode": "...",
   "tokens_in": N, "tokens_out": N,
   "cost_base_cents": N, "cost_billed_cents": N}]}
```
Orden: `occurred_at` DESC. `total` = conteo sin paginar (para la UI).

### REQ-USG-003 — GET /api/usage/export (CSV, auditado)

- `level` ∈ `users | requests` (default `users`); mismos filtros de
  REQ-USG-001. Respuesta `text/csv` con `Content-Disposition:
  attachment; filename="usage-<level>-<from>_<to>.csv"`.
- Nivel `users`: una fila por usuario (user_id, email, display_name,
  department, requests, tokens_in, tokens_out, cost_billed_usd,
  cost_base_usd, models). Nivel `requests`: una fila por request
  (request_id, occurred_at, email, display_name, department, model_name,
  mode, tokens_in, tokens_out, cost_base_usd, cost_billed_usd).
- USD con 2 decimales (centavos/100), punto decimal, sin símbolo.
- **Audit**: cada export emite `usage.exported` con payload
  `{level, from, to, user_id, model, mode, rows}`.
- Tope de filas por export: 50 000 (defensa, mismo espíritu que
  `EXPORT_ROW_CAP` de audit).

### REQ-USG-004 — UI "Usage" en el Admin Console

Nueva sección `/usage` en el sidebar (entre Budgets y Model Access),
visible para admins (`/api/me` con rol org_admin/dept_admin):
- Selector de período: Today / Last 7 days / Last 30 days / Custom
  (date pickers `from`/`to`).
- Filtros: modelo (select con los modelos observados en la ventana) y
  modo (All / Central / BYOK).
- Tabla por usuario: presencia en vivo (punto verde/gris desde
  `GET /api/devices/presence`, con tooltip desktop/cli), usuario,
  departamento, requests, tokens in/out, costo USD, modelos (badges).
- Drill-down: diálogo por usuario con la lista paginada de requests
  individuales (timestamp, modelo+modo, tokens, costo) —
  `GET /api/usage/requests`.
- Export CSV: dos botones (by user / by request) que descargan el CSV
  del período/filtros actuales.
- Estándar UI Pro: lucide icons, skeletons (`TableSkeleton`), toasts,
  `ErrorState` con retry, `EmptyState` sin datos, cero estados
  inventados.

### REQ-USG-005 — Aislamiento multi-tenant

Todas las queries corren en la transacción tenant-scoped (`get_tx` +
RLS). Un admin de un tenant jamás ve filas de otro (verificable con
test de aislamiento). `dept_admin` no ve usuarios fuera de su scope
(patrón `scope_depts` de `budgets/history`).

### REQ-USG-006 — E2E con ledger real

Test E2E: 2 usuarios con modelos **distintos** hacen invocaciones
reales por el data plane (doble HTTP declarado D-GOV-15, semántica
documentada del gateway), el worker real (`spend_worker.run_once`)
ingiere a `spend_ledger`, y el admin ve en `/api/usage` los requests y
consumos de cada uno con los montos correctos (reconciliados contra
SQL directo al ledger). Incluye: scope dept_admin, filtros modelo/modo,
drill-down, export auditado y aislamiento entre tenants.

## Criterios de aceptación

- **AC-035-001**: `GET /api/usage` devuelve el agregado por usuario del
  período pedido, reconciliado con `SELECT ... FROM spend_ledger` (suma
  de `cost_billed_cents`, tokens y conteos exactos).
- **AC-035-002**: `dept_admin` solo ve usuarios de sus departamentos;
  un usuario sin rol admin recibe 403; un tenant no ve datos del otro.
- **AC-035-003**: `period=custom` sin `from`/`to` (o con `to < from`,
  o fechas malformadas) → 422, nunca 500.
- **AC-035-004**: `GET /api/usage/requests` pagina (`limit`/`offset`)
  y `total` cuadra con el conteo sin paginar.
- **AC-035-005**: `GET /api/usage/export?level=users|requests` devuelve
  CSV correcto (cabeceras + USD 2 decimales) y queda `usage.exported`
  en `audit_log` con el payload del filtro.
- **AC-035-006**: E2E: 2 usuarios × modelos distintos → el admin ve
  requests y montos correctos por usuario contra el ledger real.
- **AC-035-007**: La UI `/usage` renderiza la tabla con presencia,
  filtros, drill-down y export (build de Next sin errores de tipos).

## Fuera de alcance

- Re-proyección de `budget_counters` (ya cubierto por budgets).
- Alertas/umbral de uso (notificaciones ya cubiertas por spec 005).
- Export XLSX/PDF.

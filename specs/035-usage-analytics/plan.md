# Plan técnico — Spec 035 (Usage Analytics)

## Decisiones (D-035)

- **D-035-01 — Sin migración.** `spend_ledger` ya tiene todas las
  columnas; los índices `(tenant_id, occurred_at)` y
  `(tenant_id, user_id, occurred_at)` cubren ventana y drill-down.
  Anunciado en la card (hotspot t_2be18285): cero archivos en
  `db/migrations/versions/`.
- **D-035-02 — Servicio + rutas en governance.** Nueva hoja
  `platform/app/governance/usage.py` (queries puras sobre el ledger) y
  endpoints en `platform/app/governance/router.py` (mismo patrón que
  budgets: `get_tx` + RLS + `dept_scope`).
- **D-035-03 — Ventana inclusiva por fecha.** `occurred_at::date
  BETWEEN :from AND :to` (coincide con `budgets/history`), reloj UTC
  (`budgets.today()`), `period` resuelto server-side.
- **D-035-04 — Scope dept_admin por `department_id` del ledger.** El
  ledger ya lleva `department_id` por fila (lo resuelve el ingest);
  el scope filtra por `department_id = ANY(:depts)`. Un request con
  `department_id` NULL (usuario sin depto) solo lo ve `org_admin`.
- **D-035-05 — CSV server-side.** Mismo patrón que
  `audit/events/export`: `Response(media_type="text/csv")` con
  `Content-Disposition`; USD = centavos/100 con 2 decimales. Audit
  `usage.exported` en la MISMA transacción (`get_tx` commitea al
  retornar).
- **D-035-06 — UI sección nueva `/usage`** (no pestaña dentro de
  Users): el sidebar ya es la navegación canónica del console y una
  sección propia evita inflar el hotspot `users-page.tsx`. Presencia
  vía `GET /api/devices/presence` (ya existe, admin-scoped) uniendo
  por `user_id`.
- **D-035-07 — E2E con el doble declarado + worker real.** Patrón del
  paso 6/7 de `tests/e2e/test_control_plane_flow.py`: keys virtuales
  reales → chat real al doble HTTP → `spend_worker.run_once()` →
  `spend_ledger` real → asserts del admin contra SQL directo.

## Componentes

### Backend

1. `platform/app/governance/usage.py` (nuevo):
   - `resolve_window(period, from_, to)` → `(kind, date, date)`;
     valida y lanza `validation_failed` (422).
   - `_filters_sql(...)` → cláusulas compartidas por aggregate,
     drill-down y export (DRY: una sola definición de filtros).
   - `aggregate(conn, scope_depts, window, filters)`.
   - `requests_page(conn, scope_depts, window, filters, limit, offset)`.
   - `export_rows(conn, scope_depts, window, filters, level)` +
     `rows_to_csv(rows, level)`.
2. `platform/app/governance/router.py`:
   - `GET /api/usage`, `GET /api/usage/requests`,
     `GET /api/usage/export` (registradas ANTES de cualquier ruta con
     path param para evitar capturas).
   - Scoping: `org_admin` → `scope_depts=None`; `dept_admin` →
     `dept_scope(principal)`; resto → 403 (mismo patrón que
     `budgets/history`).
   - Export emite `usage.exported` vía `audit.emit`.

### Frontend

3. `platform/console/src/app/(console)/usage/page.tsx` +
   `usage-page.tsx` (nuevo): período, filtros, tabla con presencia,
   drill-down paginado, export CSV (descarga vía `fetch` al proxy BFF
   + blob), skeletons/toasts/estados.
4. `platform/console/src/components/shell/sidebar.tsx`: entrada
   "Usage" (icono lucide `Activity`/`ChartColumn`) entre Budgets y
   Model Access.
5. `platform/console/src/lib/types.ts`: tipos `UsageSummary`,
   `UsageRequestsPage`, `DevicePresence`.

### Tests

6. `platform/tests/test_usage_analytics.py` (nuevo):
   - fixture: ingest real vía `ingest_entries` (patrón
     `test_budget_history`) con 2 usuarios × 2 modelos × 2 modos,
     timestamps hoy/ayer/31 días atrás.
   - AC-035-001…005 (agregado, scope, 422, paginación, export+audit,
     aislamiento tenant B).
7. `platform/tests/e2e/test_usage_e2e.py` (nuevo): AC-035-006 —
   2 usuarios (worker + segundo usuario) con modelos distintos, chat
   real al doble, `run_once`, asserts de montos reconciliados.

## Riesgos

- **Hotspot `router.py` de governance** (442 líneas, ediciones
  concurrentes de otras lanes): se añaden SOLO 3 endpoints al final
  de la sección budgets; sin tocar código existente.
- `occurred_at::date` no usa el índice de rango timestamptz: aceptable
  a esta escala (desarrollo local); documentado en usage.py.

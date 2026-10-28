# Spec 021 — Tasks (Audit UX)

| Campo | Valor |
|---|---|
| **Estado** | En ejecución |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-26 |

## Tareas

- [x] T1 — `specs/021-audit-ux/` spec.md + plan.md + tasks.md (spec-first).
- [x] T2 — `audit-integrity.tsx`: pill de integridad con auto-verify 1×/sesión
      (org_admin), verify manual, estados ok/broken/forbidden/error (D-21-02).
- [x] T3 — `audit-timeline.tsx`: timeline ascendente por actor/sesión con rail
      visual, badges categorizados, payload resumido, coste y hash (D-21-04).
- [x] T4 — `audit-page.tsx` rework: filtros completos (actor, action, recurso,
      device, fechas, texto libre) con facets reales de los últimos 200 eventos,
      pestañas Table/Timeline, "View activity" por actor, export con total del
      rango, retiro del diálogo de verificación (D-21-03/05/06).
- [x] T5 — `npm run build` + `npm run lint` limpios (exit 0, 0 problems).
- [x] T6 — Rebuild del contenedor `trust-agent-console` (producción :3100,
      healthy, /audit 307→login sin sesión).
- [x] T7 — Evidencia real en `docs/audit-ux-evidencia.md` (AC-21-001…006,
      tabla de 10 checks con salidas reales; artefactos en
      `docs/evidencia/audit-ux/`).

## Desviaciones

- Ninguna semántica. (T4: el botón "Verify chain" deja de abrir un `Dialog` y
  vive junto al pill de integridad — decisión D-21-02: el estado es persistente
  y visible, que es el punto del feedback #9. T2: el estado "verifying" del
  auto-verify en mount no se muestra (regla react-hooks/set-state-in-effect);
  el pill neutral cubre el transitorio y el botón manual sí lo muestra.)


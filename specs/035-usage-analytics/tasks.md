# Tasks — Spec 035 (Usage Analytics)

- [x] T1 — Spec + plan (este documento).
- [x] T2 — `platform/app/governance/usage.py`: ventana de período,
      filtros compartidos, agregado por usuario, página de requests,
      filas de export + CSV.
- [x] T3 — Endpoints en `governance/router.py`: GET /api/usage,
      GET /api/usage/requests, GET /api/usage/export (audit
      `usage.exported`).
- [x] T4 — Tests `test_usage_analytics.py` (AC-035-001…005 + aislamiento).
- [x] T5 — E2E `test_usage_e2e.py` (AC-035-006): 2 usuarios, modelos
      distintos, worker real, montos reconciliados.
- [x] T6 — UI: sección `/usage` (tabla por usuario con presencia,
      filtros modelo/modo, período custom, drill-down paginado,
      export CSV), sidebar, tipos.
- [x] T7 — Verificación: pytest (6 nuevos + 47 regresión, 53 passed),
      `tsc --noEmit` + `next build` limpios, eslint limpio, paridad
      de esquema (15 passed), migración 0027 aplicada en la DB real,
      imágenes api/console reconstruidas y sonda viva PASS contra el
      stack (:8000) con sesión admin real.

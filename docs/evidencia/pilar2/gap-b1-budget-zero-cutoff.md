# GAP B-1 CONVERGIDO — borde amount=0 en budgets (t_ac2d2611)

**Bug original**: `docs/revision-pilar2-governance-e2e.md` §"BUG B-1" — `GET /api/budgets/me`
reportaba `cutoff: false` con budget explícito $0 y spend ≥ 1¢ mientras el enforcement SÍ
cortaba (key bloqueada, notificación, audit). AC-GOV-012 violado en el borde amount=0.

**Root cause**: `platform/app/governance/budgets.py` `_level_status` usaba
`budget["amount_cents"] > 0 and spent >= budget["amount_cents"]` — el guard `> 0`
(pensado para presupuesto ausente) excluía el 0 explícito. El enforcement
(`spend.py::enforce_tenant`) nunca tuvo ese guard → corte sí, estado no.

## Fix

| Archivo | Cambio |
|---|---|
| `platform/app/governance/budgets.py` | `_level_status`: cutoff = `spent >= amount_cents` cuando existe fila de budget (aunque sea 0); presupuesto ausente (sin fila) sigue sin corte. Alineado 1:1 con el enforcement. Cubre `/api/budgets/me` y `/api/me/budget` (ambas rutas → `status_for_user`). |
| `platform/console/src/app/(console)/budgets/budgets-page.tsx` | Mismo guard `> 0` replicado client-side en los badges Cutoff de depto y usuario → eliminado. La UI ya no puede mostrar "remaining −$0.01 sin Cutoff". |
| `specs/005-governance/spec.md` | **AC-GOV-012a** (nuevo): presupuesto explícito de 0¢ es un presupuesto vigente; la ruta de lectura aplica la MISMA regla que el enforcement; presupuesto ausente ≠ presupuesto 0 (distinción = existencia de la fila). |
| `platform/tests/test_budgets.py` | `test_my_budget_status_explicit_zero_budget_cutoff`: borde amount=0 contra DB real (counter 1¢ + PUT 0 force → `cutoff:true, cutoff_level:"user"` por ambas rutas; presupuesto ausente → `budget_cents:null, cutoff:false`), con restore. |

## Evidencia real (en vivo, tenant qa-gov-e2e, stack desplegado post-fix)

Repro completo steps 1-4 del reporte (`repro_b1_live.py`, 12/12 PASS):

```
=== GAP B-1 repro en vivo (post-fix) — tenant qa-gov-e2e ===
    [session] admin@qa-gov-e2e.test: sesion expirada (HTTP 401) -> login PKCE real
    [dance] 2º factor OTP real: 228345
    [session] w1@qa-gov-e2e.test: sesion expirada (HTTP 401) -> login PKCE real
    [dance] 2º factor OTP real: 480867
[PASS] step1 w1 tiene spend real >= 1c en ventana vigente — spent_cents=1
    [db] budget user w1 previo = 1000 cents
[PASS] step2 PUT /api/budgets/user/{w1} amount=0 force=true -> 200
[PASS] step2b GET /api/budgets/me (pre-worker): user.cutoff=true —
  {"user":{"budget_cents":0,"spent_cents":1,"remaining_cents":-1,"cutoff":true},
   "department":{...,"cutoff":false},"organization":{...}}
[PASS] step3 spend worker --once
[PASS] step3b key de w1 bloqueada por el worker — status=blocked
[PASS] step3c notificacion budget_cutoff al usuario w1
[PASS] step3d audit budget.cutoff emitido — n=6
[PASS] step4 GET /api/budgets/me: cutoff=true, cutoff_level='user' (AC-GOV-012)
[PASS] step4b sin estado contradictorio: budget=0, spent>=1, remaining<0, cutoff=true
  user={"level": "user", "budget_cents": 0, "spent_cents": 1,
        "remaining_cents": -1, "period": "monthly", "cutoff": true}
[PASS] cleanup budget w1 restaurado a 1000 cents
[PASS] cleanup spend worker --once (desbloqueo)
[PASS] cleanup key de w1 desbloqueada — status=active
=== RESULTADO: 12 PASS / 0 FAIL ===
```

El `Actual` del reporte (`"cutoff": false, "cutoff_level": null`) quedó convergido al
`Expected` (`cutoff: true, cutoff_level: "user"`), capturado en vivo con login PKCE
real + TOTP real (acr=2), spend worker real y DB real.

## UI verificada

Imagen `trust-agent/console:local` rebuildeda y desplegada (healthy). El chunk servido
`.next/static/chunks/3dqm9lcvoaw2b.js` contiene la lógica nueva
`null != e.budget_cents && e.spent_cents >= e.budget_cents` y **0 ocurrencias** del
guard viejo `budget_cents>0`.

## Tests

- `tests/test_budgets.py` — **11 passed** (incluye el nuevo de borde amount=0).
- Suites relacionadas (`test_spend_cutoff`, `test_budget_rollover`, `test_budget_history`,
  `test_governance_bundle`, `test_agent_profiles`, `test_governance`) — **51 passed**.
- Suite completa: 659 passed / 11 failed — los 11 fallos (provision, usage_analytics,
  model_access, dynamic_discovery, jobs) **pasan en aislamiento** y pertenecen a lanes
  hermanas recientes (estado compartido entre archivos de test, interferencia
  pre-existente); ninguno toca budgets/cutoff ni puede verse afectado por este cambio
  (solo altera el reporte de cutoff con fila de budget explícita de 0¢).

2026-10-27 — @dev (t_ac2d2611)

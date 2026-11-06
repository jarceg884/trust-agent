# Evidencia E2E — spec 042 REQ-PE-007/AC-007-2: Usage en vivo (GAP-42-C)

**Fecha**: 2026-10-29 · **Perfil**: ui (t_059bf0db) · **Nada simulado**
(constitution I): el consumo nuevo se genera con requests REALES por el
gateway LiteLLM (`:4000`, upstream MaaS) con la key virtual del worker
enrolado (perfil `trust-live-prac`, tenant `policies-ux`), que el
spend-worker persiste en `spend_ledger` (ciclo 2 s). Las cifras de la UI
salen de `GET /api/usage` sobre ese ledger — cero datos sintéticos. La
sesión admin del console es real (cookies de `/tmp/builder-state.json`,
login PKCE+TOTP del flujo 041).

## Qué se implementó

- `platform/console/src/hooks/use-api.ts` — opción `silentRefetch` en
  `useApi`: un `refetch()` de la MISMA ruta no vuelve a `loading` — la UI
  sigue mostrando el dato previo (`refreshing: true`) y al resolver solo
  cambian las cifras. Si el refetch silencioso falla, el último dato
  sobrevive (error expuesto junto al dato). Opt-in: el resto de páginas
  conserva el comportamiento anterior.
- `platform/console/src/app/(console)/usage/usage-page.tsx` —
  auto-refresco cada 15 s de summary + drill-down + presencia (patrón de
  la franja de presencia de Devices, spec 027 REQ-027-06), pausado con
  `document.hidden` y con refresco inmediato al volver a ser visible
  (`visibilitychange`). Badge "· live" en el header con el mismo lenguaje
  visual de Devices (punto verde con ping + contador de requests); si un
  refresco falla, el badge pasa a ámbar "· stale" sin tirar la tabla.
- `platform/console/src/app/(console)/usage/page.tsx` — descripción de la
  página actualizada ("live-updating every 15 s").

## Cómo reproducirlo

```bash
# 0) Stack vivo arriba (docker compose) y sesión admin del console en
#    /tmp/builder-state.json (cookies del login PKCE+TOTP del flujo 041).

# 1) Imagen del console con el cambio:
cd /root/trust-agent && docker compose build console && docker compose up -d --no-deps console

# 2) E2E en vivo (~2.5 min: incluye esperas reales de 15-20 s):
cd platform/console/scripts && \
  node e2e_usage_live.mjs /tmp/builder-state.json \
  /root/trust-agent/docs/evidencia/usage-live/
# → 15/15 checks PASS
```

Resultados máquina-verificables: `usage-live-results.json` (15/15 PASS,
`pickup_ledger_to_ui_seconds: 3.5`). Capturas: `usage-live-updated.png`
(badge "46 requests · live" con la tabla actualizada) y
`usage-drill-page2-live.png` (drill-down en página 2 tras un ciclo de
refresco, paginación intacta).

## AC-007-2 — request nuevo sin F5 ni clic, ≤ 15 s (E2E C3)

- Se dispararon **5 requests reales** por el gateway (glm-5.2, key del
  worker); el spend-worker los persistió en el ledger (verificado por
  API: total 41 → 46).
- **Ledger → UI = 3.5 s** (medido: primer momento en que el badge muestra
  el request nuevo vs. primer momento en que la API lo expone) — muy
  dentro del presupuesto de 15 s (ciclo de refresco) + 2 s (spend-worker).
- La fila del usuario subió en vivo: requests 41→46 y tokens_in
  incrementados en la tabla, **sin F5 ni clic** (el script no toca la
  página durante la medición).
- Cadencia verificada: los GET de `/api/usage` mientras la pestaña está
  visible guardan deltas de ~15 s (C3d).

## Pausa con la pestaña oculta (E2E C4)

- `document.hidden` → **0 fetches** de usage/presence en una ventana de
  20 s que cruza ≥1 borde del intervalo (C4b).
- Al volver a visible (`visibilitychange`), refresco inmediato: fetch
  dentro de los 3 s (C4c).
- **Nota honesta sobre el método**: Chromium headless NO oculta pestañas
  en segundo plano — verificado empíricamente con 2ª pestaña, headed bajo
  xvfb, `--headless=new` y CDP `Target.activateTarget`
  (`document.hidden` siempre `false`; `Page.setWebLifecycleState` no
  acepta `hidden`). El test conduce el contrato estándar de la web:
  override de `document.hidden`/`visibilityState` + evento
  `visibilitychange` real. El listener y el guard (`if (document.hidden)
  return`) son los de producción; solo la señal de oclusión de la pestaña
  es inyectada. En un navegador de escritorio real la pausa la dispara el
  propio cambio de pestaña.

## Sin flicker ni reset de filtros/paginación (E2E C5)

- Drill-down abierto en **página 2** (offset 25, "26–46 of 46"); tras un
  ciclo completo de refresco silencioso con el diálogo abierto: diálogo
  abierto, MISMA página, y todos los re-fetch del drill-down conservaron
  `offset=25` (C5c/C5d). El período de la página siguió "Last 30 days"
  (C5e). El skeleton solo aparece cuando cambia la ruta (filtros), no en
  el ciclo en vivo — `silentRefetch` sirve el dato previo mientras la
  request vuela.

## Paridad UI == API (E2E C2)

- El badge muestra exactamente `totals.requests` de `GET /api/usage`
  (badge=41, api=41 al arrancar) — mismas cifras del ledger, sin
  transformaciones client-side.

## Ruido pre-existing excluido del check de consola (C6)

- `favicon.ico` → 404 y prefetches RSC del sidebar abortados (`?_rsc=`,
  net::ERR_ABORTED): presentes en todas las páginas del console antes de
  este cambio, ajenos a la feature. 0 errores JS y 0 requests de API
  fallidos durante todo el flujo.

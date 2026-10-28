---
name: nextjs-console-patterns
description: "Patrones y trampas del Admin Console (Next.js 16 App Router + React 19 + Tailwind v4) de trust-agent: regla react-hooks/set-state-in-effect, cookies del BFF para browser automation, rebuild del contenedor de producción. Use when tocar platform/console/ o automatizar la UI real."
version: 1.0.0
author: trust-agent ui
metadata:
  hermes:
    tags: [nextjs, react19, tailwind, console, eslint, browser-automation, trust-agent]
---

# Admin Console (Next.js 16 + React 19) — patrones y trampas verificadas

Hallazgos verificados en vivo contra `platform/console/` (spec 021, t_c14d6703,
2026-10-26). Aplican a toda tarea que toque el console.

## 1. `react-hooks/set-state-in-effect` (error de lint, no warning)

El eslint del console (React Compiler rules) **prohíbe llamar setState
síncronamente dentro de un `useEffect`**, incluso de forma transitiva: si el
efecto llama a una función (useCallback o no) cuyo cuerpo hace `setState` antes
del primer `await`, el linter lo señala en la línea de la llamada.

- ❌ Falla: `useEffect(() => { void verify(); }, [...])` donde `verify` es
  `async` y empieza con `setState({kind:"verifying"})`.
- ❌ Falla igual con `async/await` aunque el setState esté después del `await`
  (el análisis es sintáctico sobre la función llamada).
- ✅ Pasa: setState SOLO dentro de callbacks `.then()/.catch()` de una promesa
  creada en el efecto — exactamente el patrón de `src/hooks/use-api.ts`.
  Si hace falta un estado transitorio "verifying", muéstralo solo desde event
  handlers (onClick), no desde el mount.

## 2. Inputs controlados de React desde automatización de navegador

Asignar `input.value = x` + `dispatchEvent(new Event("input"))` NO actualiza el
estado de React (value tracker). Dos caminos verificados:

- Inputs de texto: usar el helper `fill_input(selector, text)` del browser
  harness (usa el setter nativo).
- Inputs `type="date"`: `fill_input` tipea carácter a carácter y el navegador lo
  interpreta como fecha parcial (produce basura como `261025-02-02`). Usar el
  setter nativo a mano:

```js
const el = document.getElementById("f-since");
Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")
  .set.call(el, "2026-10-27");
el.dispatchEvent(new Event("input", {bubbles: true}));
```

## 3. Sesión real del console para browser automation (sin pasar por el login UI)

El BFF del console guarda la sesión del control plane en cookies del origen
:3100: `ta_console_session` (httpOnly, valor = cookie `trust_session` del
control plane) y `ta_console_csrf` (legible). Para usar la UI real con una
sesión admin real:

1. Baile PKCE S256 + TOTP contra Keycloak (reutilizar
   `platform/scripts/e2e_final_live.py::pkce_login` — importable; ojo que usa
   las globales `SLUG`/`REDIRECT_URI` del módulo: se pueden sobreescribir tras
   el import). Requiere el venv: `/root/trust-agent/platform/.venv/bin/python`
   (psycopg no está en el python del sistema).
2. `POST http://127.0.0.1:8000/api/auth/session` con `{"id_token":…,
   "kind":"admin"}` → 201; la cookie viene en el header `Set-Cookie:
   trust_session=…` (el body trae `session_token: null`) y `csrf_token` en el
   body. Exige `mfa_verified: true` (acr≥2) para las rutas gobernadas.
3. Inyectar en el navegador vía CDP `Network.setCookie`:
   `ta_console_session` (httpOnly=true) y `ta_console_csrf` (httpOnly=false)
   para `http://127.0.0.1:3100/`. El proxy reenvía `ta_console_session` como
   `trust_session` y `ta_console_csrf` como `X-CSRF-Token` en mutaciones.

## 4. El contenedor del console corre BUILD DE PRODUCCIÓN

`trust-agent-console` corre `npx next start` SIN volumen del código fuente:
cambiar `src/` no se refleja en :3100 hasta reconstruir la imagen y recrear el
contenedor (~1 min). `npm run build` local es necesario pero NO suficiente para
la evidencia en vivo.

- **`docker build` DIRECTO es lo confiable** (mandato QA, t_ccf901f4):
  `docker build -t trust-agent/<svc>:local platform/<svc>` desde
  `/root/trust-agent` — `docker compose build` se reportó como no-op silencioso
  en este host aunque a veces escribió imágenes (t_7cd4f40e); no apostar.
  Cambios en `platform/app/**` (API/workers) requieren además
  `docker build -t trust-agent/platform:local -f platform/Dockerfile .` y
  recrear `api jobs-worker spend-worker`.
- Recrear: `docker compose up -d --no-deps <svcs>` — en foreground lo rechaza
  el runner de terminales; usar background=true y poll. Verificar que el
  contenedor quedó en la imagen nueva (`docker inspect <c> --format '{{.Image}}'`
  vs `docker images <img> --format '{{.ID}}'`).
- Verificar copy desplegado sin login: grep sobre el chunk servido
  (`curl -s :3100/_next/static/chunks/<chunk>.js | grep -c "<copy>"`) y para el
  API `docker exec trust-agent-api python -c "import app.x.y as m, inspect;
  print('<copy>' in inspect.getsource(m))"` (el fuente vive en
  `/opt/trust-agent/platform/app`; el runtime importa de site-packages).
- **Tests del platform SIN .env**: `cd /root/trust-agent/platform &&
  .venv/bin/python -m pytest tests/<file> -q` — los tests usan DSNs hardcodeados
  (127.0.0.1:55432, `trust_app_dev`, db `trust_agent_platform_test`), no
  necesitan variables del `.env`. Sourcing `.env` dentro de un comando activa
  el approval wall del runner (timeout sin usuario en runs headless) — evitarlo.

## 5. Contratos reales del control plane (sorpresas encontradas)

- `PUT /api/budgets/dept/{id}` espera `amount_cents` (int), no `amount_usd`.
- Políticas: `approved_models_only`, `scoped_credentials`, … invariables → 422
  al intentar setearlas. OJO: `require_mfa` YA ES ORG-TUNABLE (`invariable=f`,
  default `false`) desde la migración `0014_auth_ux_policies` (spec 015) — una
  nota anterior aquí decía invariable; verificar contra `catalog.policy_keys`,
  no contra listas hardcodeadas. Las settables están en `catalog.policy_keys`
  (`invariable = f`).
- Path params no-UUID en rutas con `CAST(:x AS uuid)` → 500 (bug reportado,
  t_378503a1), no 422.

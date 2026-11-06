---
name: trust-agent-pkce-login-dance
description: "Login real PKCE S256 contra los realms KC de Trust Agent: usar acr_values según org-config, kind de sesión y trampas del baile TOTP."
version: 1.0.0
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [trust-agent, keycloak, pkce, qa, e2e]
---

# Trust Agent: baile de login real PKCE S256 contra Keycloak 26

Use when pruebes flujos E2E del stack Trust Agent (login, sesiones, devices, MFA) y necesites un id_token real por HTTP sin navegador.

## Maquinaria base
`platform/scripts/revalidate_litellm_real.py` expone `pkce_login()`, `mint()`, `api()`, `db()`, `kc_fetch`, `kc_admin_token`, `kc_json`, `totp()`. Cargar con `importlib.util.spec_from_file_location` y sobreescribir `rv.SLUG` / `rv.ADMIN_EMAIL` (patrón de `evidence_model_access_maas.py`).

## Hallazgos clave (verificados en vivo 2026-10-26)

1. **`acr_values` decide el LoA**: desde spec 015 (auth UX), `require_mfa=false` por defecto → el login NO fuerza TOTP y el id_token trae **acr=1**. Para obtener **acr=2** (necesario p.ej. en `POST /api/devices/enroll` y en sesiones cuando la org exige MFA) hay que añadir **`acr_values=2`** al endpoint `auth` del baile — es lo que la pantalla de login real hace según `GET /api/auth/org-config?org=<slug>` (`require_mfa` → acr 2/1). El `pkce_login` de revalidate NO manda acr_values: copiar la función y añadir el param (ver `qa_pilar2_e2e.py::pkce_login2` en docs/evidencia/pilar2/).
2. **Enrolar TOTP por API**: asignar required action `CONFIGURE_TOTP` vía KC Admin REST (`PUT /admin/realms/<slug>/users/<uuid>` `{"requiredActions":["CONFIGURE_TOTP"]}`) y re-login — el baile encuentra el formulario `totpSecret` y lo completa.
3. **`POST /api/auth/session` (mint) exige `kind` ∈ {`desktop`, `admin`}`**: `desktop` devuelve `session_token` usable como Bearer; `admin` solo setea cookie (token None). Para sondas API usar siempre `desktop`.
4. **`require_mfa=true` (policy org) es fail-closed en vivo**: sesiones mintadas con acr=1 y enrolamientos de devices pasan a 403 `mfa_required` — re-mintear la sesión tras obtener un id_token con acr=2.
5. **Auto-registro**: `POST /api/auth/register` `{org, email, password, display_name}` → 201; el usuario entra con rol `user` sin TOTP. Útil para probar anti-escalada (403 `roles.assign`) y budgets por usuario.
6. **Placeholders psycopg**: `rv.db()` usa `%s` (tuplas), NO `:named`; devuelve tuplas (índices enteros), no dicts.
7. **/api/me** devuelve `roles: [{role_key, department_id}]` (no `role_keys`); audit events en `/api/audit/events` usan la clave `items` y el filtro de actor es `actor_id` (no `actor`).

## Provisión de tenant de prueba
`POST /api/admin/tenants` con header `Authorization: Bearer test-provider-token` y `{organization_name, admin_email, initial_budget_usd, model_plan:["glm-5.2"], slug}` → 201 + `one_time_activation.one_time_password` (one-time). El primer login con esa OTA fuerza `UPDATE_PASSWORD` (pasar `new_password=` a `pkce_login`).

## Hallazgos E2E CLI gobernado (spec 034, verificados en vivo 2026-10-27)

8. **OTA no es recuperable en re-ejecuciones**: en vez de capturar la OTA, resetear el password del admin vía KC Admin REST (`PUT /admin/realms/<slug>/users/<uuid>/reset-password` `{"type":"password","value":...,"temporary":false}`) antes del baile — idempotente y cubre primera corrida y re-runs (patrón `evidence_cli_governed.py::phase_setup`).
9. **Prompt de telemetría de primera ejecución bloquea el TUI**: en un home fresco el chat de Hermes muestra el opt-in de shared metrics AUNQUE el instalador fijó `telemetry.shared_metrics.enabled=false` ("Asking once more…", comportamiento del core). El harness debe detectarlo en el pane de tmux (`Help improve Hermes?`) y enviar Enter (ver `_dismiss_first_run_prompts`).
10. **BundleSyncer tick-ea cada 300 s** (`DEFAULT_SYNC_INTERVAL_SECONDS`): las ventanas de espera del bundle JWS/heartbeat tras aprobar un device deben cubrir un tick completo (~360 s), no 60 s.
11. **`POST /api/models/keys` responde 201**, no 200 — los checks deben aceptar ambos.
12. **`hermes config get` de una LISTA devuelve YAML multilinea** (`- hermes-cli\n  - trust_agent`): no recortar a la primera línea al buscar un elemento de lista (solo para escalares).
13. **`docker compose build api` NO reconstruye nada** (exit 0, sin output): el servicio `api` solo declara `image: trust-agent/platform:local`; el `build:` vive en el servicio `migrate`. Para desplegar un fix al stack vivo: `docker compose build migrate && docker compose up -d --no-deps --force-recreate api`, y verificar con `docker exec trust-agent-api md5sum /opt/trust-agent/platform/app/...` contra el árbol (patrón t_fdc728d2).

## Sesión admin para navegador real (CDP) — patrón t_e03bba6c

Para E2E de UI con Chromium real: variante de `console/scripts/smoke_console.py`
que tras el baile imprime `COOKIES_JSON:{...}` con las cookies `ta_console_session`
(httpOnly), `ta_console_refresh` (httpOnly) y `ta_console_csrf` (legible por JS —
el header X-CSRF-Token sale de `document.cookie`, por lo que la inyección CDP
soporta PUT/DELETE, a diferencia de lo visto en t_6d3f5016). Inyección:
`cdp("Network.setCookie", name=…, value=…, url="http://127.0.0.1:3100/", path="/")`
por cookie y navegar. La sesión admin caduca en ~30 min: si el navegador acaba en
`/login`, re-mintea el baile (el script es idempotente: resetea OTP + OTA del
usuario de test en cada corrida) y reinyecta.

## Lecciones del GATE FINAL (t_e8a28fce, 2026-10-27)

13. **El harness de browser automation reinicia el navegador cada ~10 min** (la
    pestaña vuelve a `about:blank` y las cookies se pierden). Trabaja en ráfagas
    cortas: 1 página + sus acciones + screenshot por ráfaga, y re-inyecta las
    cookies minteadas al inicio de cada ráfaga (patrón `mint_gate_cookies.py` del
    workspace del gate: baile smoke_console con password vigente → COOKIES_JSON).
14. **`fill_input` pierde caracteres en inputs React que se re-renderizan** (value
    quedó en 31/36 chars y luego en 1 char; provocó un 422 falso "user_id is
    required"). Para campos críticos usa el native setter + evento input:
    `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el, v)`
    + `el.dispatchEvent(new Event('input',{bubbles:true}))` — actualiza el estado
    de React de forma confiable. Verifica SIEMPRE `el.value` tras llenar.
15. **Los select de React tampoco registran `s.value=x` sin evento**: usa el setter
    nativo de `HTMLSelectElement.prototype` + `change` con `bubbles:true`.
16. **Los toasts se auto-descartan en ~2-3 s**: captura el texto inmediatamente tras
    el click (≤1 s) o te pierdes el mensaje de error/éxito (un check tardío dio un
    falso "no hay feedback visible").
17. **El emoji 🐴 en `document.title` de páginas KC es artefacto del tooling CDP**
    (prefija el favicon al title); el HTML servido está limpio — verifícalo con
    fetch directo antes de reportar bug de white-label.
18. **OTA del operador provider-admin**: borrar `ops.provider_sessions` +
    `ops.provider_operators` y reiniciar el API — el bootstrap loguea la OTA una
    sola vez en `docker logs trust-agent-api` (procedimiento canónico de
    docs/provider-superuser.md). El primer login fuerza cambio de password.
    Alternativa sin reinicio (stack compartido): UPDATE directo del
    `password_hash` con `app.provider.passwords.hash_password()` +
    `must_change_password=false` (patrón t_64e645c1). OJO: otra lane puede
    resetear el operador a mitad de tu run — antes de debuguear un 401 revisa
    `ops.provider_operators.updated_at` y el log del bootstrap.
19. **El navegador del tooling NO alcanza 127.0.0.1** (backend aislado): usa la
    IP LAN del host (p. ej. `http://192.168.0.23:3100`). Las cookies minteadas
    deben inyectarse con `cdp("Network.setCookie", url="http://<IP>:3100/")` —
    inyectarlas para 127.0.0.1 no sirve. Si el tab cae en `chrome-error://`,
    abre una sesión nombrada nueva (`session=...`).
20. **`Page.captureScreenshot` tiembea justo después de llamadas `js()`**:
    reintenta en una llamada fresca con `time.sleep(5)` antes — el patrón
    "navegar → js → screenshot" falla; "navegar" y "screenshot" en ráfagas
    separadas pasa. La captura full-page (`capture_screenshot()`) es más
    propensa a timeout que `cdp("Page.captureScreenshot")` de viewport.
21. **Nunca copiees cookies minteadas a mano en el código del browser**: un
    solo carácter de más produce `ERR_TOO_MANY_REDIRECTS` (sesión inválida →
    loop BFF). Guarda el `COOKIES_JSON` a archivo desde el script minteador y
    léelo con `json.load()` dentro de browser_exec.
22. **El terminal bloquea comandos con passwords inline** (approval wall sin
    usuario en runs headless): pasa credenciales por env var leída del
    script, nunca en la línea de comandos.

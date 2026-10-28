# Spec 022 — Hermes real bridge: enrolamiento + enforcement en el Hermes REAL instalado

| Campo | Valor |
|---|---|
| **Estado** | ✅ CONVERGIDA (implementada y validada con evidencia real 2026-10-26 — docs/hermes-real-bridge.md; todos los AC-HRB-001..009 con evidencia en vivo o unitaria). **Amend v1.1.0 (2026-10-27, gap t_0b6e8e2d)**: D-HRB-03 reescrita + REQ-HRB-010 — renovación deslizante del device token por heartbeat y recuperación con re-autenticación (el TTL de 15 min sin renovación rompía sesiones > 15 min; la recuperación documentada anterior moría en 409/410). |
| **Versión** | 1.1.0 |
| **Fecha** | 2026-10-26 |
| **Autor** | dev (tarea t_c875626f, mandato Miller) |
| **Padre** | specs/019-governance-over-hermes (§9 Incrementos 2 y validación real; REQ-GOH-005/009/010/016/020) |
| **ADRs** | ADR-016, ADR-017, ADR-018 (docs/arquitectura.md §4) |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

---

## 1. Problema (mandato de Miller, tarea t_c875626f)

La spec 019 decidió el mecanismo (governance bundle JWS + plugin + gates remotos) y su
**Incremento 1** (tarea t_f99e12ab: endpoint `GET /api/me/governance-bundle` + plugin v2
núcleo de lectura) está en curso. Lo que falta para el mandato — y que ESTA spec cubre — es
que el **Hermes REAL instalado en este servidor** (`/usr/local/lib/hermes-agent`, código
fuente oficial) pueda:

1. **Enrolarse** a una organización Trust Agent desde el propio Hermes (comando del plugin,
   API existente spec 007: enroll → approve → exchange → device token).
2. **Aplicar el enforcement real** (Incremento 2 de spec 019): veto `pre_tool_call` por
   `allowed_tools`, fail-closed offline/desligado (REQ-GOH-005/009/010).
3. **Validarse contra el código fuente real**: plugin activo en un perfil Hermes real,
   suite de tests del repo Hermes pasando con el plugin presente, y una sesión real
   enrolada demostrando el enforcement EN VIVO.

Regla dura heredada: **cero parches al core de Hermes** (constitution IV, lección C6,
AC-GOH-016). Todo por `plugin.yaml` + `register(ctx)` + config por perfil.

## 2. Alcance y delimitación (sin duplicación con el Incremento 1)

| Componente | Spec / tarea dueña | Estado |
|---|---|---|
| Endpoint `GET /api/me/governance-bundle` (JWS RS256) + JWKS + policy keys | spec 019 Inc. 1 (t_f99e12ab) | implementado por t_f99e12ab (`app/governance/bundle.py`, `jws_keys.py`) |
| Plugin v2 núcleo: sync periódico, `governance_status` desde bundle, `pre_llm_call` contexto normal, system prompt section | spec 019 Inc. 1 (t_f99e12ab) | en curso (mismo paquete `trust-agent-governance`) |
| **Comando de enrolamiento `/trust-enroll` + persistencia segura del device token** | **esta spec** (`enroll.py`, archivo NUEVO) | esta corrida |
| **Veto `pre_tool_call` + fail-closed + aviso de modo restringido** | **esta spec** (`enforcement.py`, archivo NUEVO) — es el Incremento 2 de spec 019 | esta corrida |
| **Activación en perfil Hermes real + suite de tests + sesión enrolada con evidencia** | **esta spec** | esta corrida |
| Empaquetado/link de descarga / instalador white-label | spec 023 (t_526030f3, hija de t_c875626f) | fuera de alcance aquí |
| Attestation + detección (Inc. 3), revocación integral de conectores (Inc. 4) | spec 019 §9 | fuera de alcance aquí |

**Contrato de acoplamiento** entre el núcleo v2 (t_f99e12ab) y esta spec — ambos módulos
conviven en el paquete `trust-agent-governance`:

- **Caché del bundle**: `$HERMES_HOME/trust-agent/governance-bundle.jws` = JWS compacto
  (string), exactamente como lo devuelve el endpoint (REQ-GOH-004). Quien sincronice
  (núcleo v2 o `enforcement.refresh()`), escribe ese archivo; ambos verifican de forma
  independiente (defensa en profundidad, ver D-HRB-04).
- **Device token**: env `TRUST_DEVICE_TOKEN` (compatibilidad v1/v2) o el archivo canónico
  `$HERMES_HOME/trust-agent/device-token.json` que escribe `/trust-enroll` (D-HRB-03).
  El archivo gana si ambos existen (el enrolamiento es la fuente).
- **Clave pública pineada**: `trust_agent_policy_pubkey.pem` junto al plugin (dato público).
- **`pre_llm_call`**: el núcleo v2 inyecta el contexto de gobernanza en modo normal;
  `enforcement.py` registra el SUYO que solo retorna contenido en modo restringido
  (el core concatena los contextos de todos los hooks — verificado en
  `agent/turn_context.py::_collect_pre_llm_call_context`). Sin duplicación.
- **Propiedad de archivos** (hotspot declarado en las tarjetas t_c875626f y t_f99e12ab):
  t_f99e12ab es dueño de `__init__.py`, `plugin.yaml` y su cliente del bundle; esta spec
  SOLO añade archivos nuevos (`enroll.py`, `enforcement.py`) y cablea en `__init__.py`
  al final, sobre la versión final del Incremento 1.

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| **D-HRB-01** | El enrolamiento es un **comando del plugin** (`/trust-enroll`, `ctx.register_command`) — no un proceso externo ni un parche. El `id_token` OIDC (con MFA, acr≥2) NUNCA se teclea en el chat: llega por **archivo local** (argumento del comando o env `TRUST_ENROLL_ID_TOKEN_FILE`) producido por el flujo OIDC del desktop/CLI. El plugin jamás maneja credenciales de usuario (passwords/TOTP viven fuera). | Constitution IV, spec 007, spec 019 REQ-GOH-020 |
| **D-HRB-02** | Keypair del dispositivo: **RSA-2048**, privada PEM PKCS#8 con permisos `0600` en `$HERMES_HOME/trust-agent/device_key.pem`, pública SPKI en `device_key.pub.pem`; `fingerprint` = sha256 hex del PEM público. Se reutiliza entre re-enrolamientos (upsert por fingerprint, spec 007: reintentos no crean filas extra). | Tarea t_c875626f §1, spec 007 |
| **D-HRB-03** | Device token persistido en `$HERMES_HOME/trust-agent/device-token.json` (`0600`; `{device_id, token, enrolled_at, org_code, tenant_slug}` — la renovación añade `renewed_at`). **Renovación (amend v1.1, gap t_0b6e8e2d — el TTL de 15 min sin camino de renovación rompía toda sesión > 15 min y la recuperación documentada moría en 409/410)**: (a) **sliding renewal por heartbeat** — `POST /api/me/device-heartbeat` (camino plugin, Bearer=device token) responde además `device_token` + `expires_in` con un token FRESCO emitido en la MISMA transacción que comprobó la fila `active` (spec 027 §4 amend); el plugin lo persiste en el mismo ciclo. Mientras el agente esté vivo y el device `active`, la sesión no caduca; la **revocación sigue siendo fail-closed inmediato** (el gate corre antes de emitir: revocado → 401 → restricted, AC-HRB-004c intacto). El token pasa a ser una credencial rodante: su robo permite renovarla hasta la revocación (mitigación: archivo `0600` + revocación inmediata + audit de re-emisión). (b) **Recuperación con re-autenticación** — `POST /api/devices/enroll` con `id_token` OIDC fresco (acr≥2 verificado por JWKS) para un device `active` del MISMO usuario responde **200** con `{device_id, status:"active", device_token, expires_in}` directo (sin re-aprobación admin: el device ya estaba aprobado y el usuario re-probó identidad con MFA; audit `device.token_reissued`). El 409 se conserva SOLO para fingerprint propiedad de otro usuario. Un device `revoked`/`rejected` sigue el flujo histórico (reset a pending + aprobación admin — fail-closed: un device revocado NO obtiene token sin admin). (c) **Auto-recuperación del plugin** — el `BundleSyncer` detecta expiración local del token (claim `exp` sin verificar: solo enruta la máquina de estados, el server verifica todo), intenta UN re-enroll con el `id-token.txt` fresco de `trust-login` y entra en `restricted(token_expired)` (razón honesta, distinta de `revoked`) si no puede; el watcher de auto-enroll ya no corta por presencia del archivo sin chequear expiración. La recuperación documentada pasa a ser: "re-ejecutar `trust-login` (SSO+MFA) → el plugin re-emite el token en el siguiente ciclo". | app/devices/tokens.py, heartbeat.py, devices/service.py (amend), spec 027 §4 (amend), ADR-017 |
| **D-HRB-04** | `enforcement.py` verifica el caché JWS **por sí mismo** (clave pineada, RS256 únicamente — HS256 rechazado, `aud == device_id` local, `exp` no vencido, `jti` anti-replay con caché local en `$HERMES_HOME/trust-agent/jti-cache.json`): defensa en profundidad y testable de forma aislada, sin depender del módulo del núcleo v2. Si el caché está ausente/expirado, `enforcement.refresh()` intenta UN sync contra `GET /api/me/governance-bundle` (Bearer = device token, `X-Device-Token` = device token — camino plugin ya implementado en `bundle.py::require_bundle_auth`); 401/403 → invalida caché y pasa a restringido inmediato (REQ-GOH-010); error de red → opera con el caché si está dentro de su `exp` (gracia offline, REQ-GOH-009/018). | ADR-016/017, REQ-GOH-003/009/010 |
| **D-HRB-05** | **Modos operativos** (máquina de estados spec 019 §5.4, evaluada por llamada): `normal` (bundle vigente) / `restricted` (sin bundle, caché vencida sin sync, o 401/403). En `restricted`: `pre_tool_call` bloquea TODA tool externa con el mensaje de modo restringido; `unbind_mode=lock` añade la instrucción de no operar vía `pre_llm_call` (REQ-GOH-010). En `normal`: tool ∉ `allowed_tools` (y no es tool interna del plugin) → `{"action":"block","message":"BLOCKED by organization policy: tool '<name>' is not allowed. Allowed tools: <lista>."}` (REQ-GOH-005, mensaje exacto). `governance_status` y las tools/commands del propio plugin siempre permitidos. El conteo de vetos por tool se persiste para la futura attestation (REQ-GOH-017). | spec 019 §5.4, REQ-GOH-005/009/010/017 |
| **D-HRB-06** | La validación usa un **perfil Hermes REAL dedicado** (`trust-e2e`) creado con el CLI real (`hermes profile create`/equivalente) en este servidor. PROHIBIDO instalar el plugin con fail-closed en el home default (`/root/.hermes`) o en el perfil `dev` de la factory: rompería las demás lanes. El perfil apunta su `model.base_url` al gateway LiteLLM del tenant con la virtual key del usuario (una sola, sin `fallback_model`). | Constitution VII, spec 019 L1, D-GOV-15 |
| **D-HRB-07** | La suite de Hermes se corre con el runner canónico del repo (`scripts/run_tests.sh`) con el plugin presente en el home de pruebas; la verificación de "cero parches" es `git -C /usr/local/lib/hermes-agent status --porcelain` limitado a la modificación preexistente `agent/error_classifier.py` (de otra lane, anterior a esta tarea, no tocada por nosotros). | AC-GOH-016, regla 5 CONTEXTO-TAREAS |
| **D-HRB-08** | El veto de MODELOS no vive en el plugin (no hay hook de veto de modelo): el gate duro es LiteLLM con la virtual key (L3). El plugin informa los modelos aprobados (contexto/tool) y el perfil no declara `fallback_model`. Intentar un modelo no aprobado → rechazo del gateway, que el agente ve como error del proveedor. | spec 019 §2.1 L3, D-GOV-15, pitfall verificado (no existe hook de veto de modelo) |
| **D-HRB-09** | **Heartbeat de presencia (seam spec 027 §4, mandato Miller)**: cada ciclo del `BundleSyncer` hace también `POST /api/me/device-heartbeat` con `{applied_policy_version, uptime_seconds, mode}` (misma auth que el bundle). `policy_update_available=true` → re-baja del bundle JWS en el MISMO ciclo (propagación en vivo sin reiniciar ni re-enrolar, D-027-03). El intervalo recomendado del servidor se adopta SOLO si el operador no fijó `TRUST_SYNC_INTERVAL` (D-027-02). El heartbeat es best-effort: un fallo de red NO cambia el estado de gobernanza; un 401/403 SÍ (revocación → fail-closed inmediato, REQ-GOH-010). El mecanismo es de esta lane; la vista de presencia y el endpoint son de t_f4f99970 (spec 027, ya implementados y verificados). | spec 027 §4 (contrato normativo), comentario 2026-10-26 en esta card, REQ-GOH-010 |

## 4. Requisitos (formato EARS) — serie REQ-HRB

**REQ-HRB-001** (M) [D-HRB-01/02] — Cuando el usuario ejecuta `/trust-enroll <org_code> [id_token_file]` en una sesión de Hermes con el plugin activo, el comando genera (o reutiliza) el par RSA-2048 local, calcula el fingerprint, llama `POST /api/devices/enroll` con `{org_code, id_token, public_key, fingerprint, device_name, platform}`, recibe el enrollment token one-time y lo canjea con `POST /api/devices/enroll/exchange` (polling 202→200) hasta obtener el device token, que persiste en `$HERMES_HOME/trust-agent/device-token.json` con permisos `0600`; el comando responde en el chat con el estado final (device_id, tenant, modo).
> **AC-HRB-001**: enrolamiento real contra el control plane levantado → fila `devices` en estado `active` en la DB del tenant, archivo `device-token.json` con permisos `0600`, y `governance_status` (o el verificador del plugin) reporta modo `normal` tras el primer sync. Enrolamiento con enrollment token ya consumido → 410 y mensaje de error claro, sin crash de la sesión.

**REQ-HRB-002** (M) [D-HRB-01] — Cuando el comando necesita el `id_token`, lo lee EXCLUSIVAMENTE del archivo indicado (argumento o `TRUST_ENROLL_ID_TOKEN_FILE`); sin archivo → mensaje de uso sin solicitar credenciales por el chat. El plugin no acepta ni almacena passwords/códigos MFA.
> **AC-HRB-002**: `/trust-enroll` sin archivo de id_token → mensaje de uso que explica cómo producirlo (flujo OIDC del desktop); grep del código del plugin sin manejo de passwords/TOTP.

**REQ-HRB-003** (M) [D-HRB-05, REQ-GOH-005] — Cuando el agente intenta ejecutar una tool y existe bundle vigente en modo `normal`, el hook `pre_tool_call` de `enforcement.py` permite solo: tools en `allowed_tools`, tools internas del propio plugin (`governance_status`) y nada más; toda otra tool retorna `{"action": "block", "message": "BLOCKED by organization policy: tool '<name>' is not allowed. Allowed tools: <lista>."}`; el veto se cuenta por tool en `$HERMES_HOME/trust-agent/veto-counts.json`.
> **AC-HRB-003**: con `allowed_tools=["web_search","governance_status"]`, una llamada real a `terminal` → bloqueada con el mensaje exacto de policy y conteo incrementado; `web_search` → procede; con `allowed_tools=[]` → toda tool externa bloqueada y `governance_status` siempre responde.

**REQ-HRB-004** (M) [D-HRB-04/05, REQ-GOH-009/010] — Cuando no hay bundle válido (sin caché, caché vencida con sync fallido, o sync con 401/403), el plugin opera en modo `restricted`: `pre_tool_call` bloquea TODA tool externa con mensaje de modo restringido (mencionando la causa: `no valid governance bundle` / `governance bundle expired, network sync unavailable` / `device not authorized by the organization`), y `pre_llm_call` inyecta el aviso de modo restringido (con la instrucción de no operar si `unbind_mode=lock`). Al recuperar un bundle válido, el modo vuelve a `normal` en la siguiente evaluación.
> **AC-HRB-004**: (a) arranque sin caché → toda tool externa bloqueada con mensaje restricted; (b) caché vencida + control plane caído → ídem con mensaje de expiración; (c) revocación real del dispositivo (`POST /api/devices/{id}/revoke`) → el siguiente sync recibe 401 y el modo pasa a restricted aunque el bundle estuviera dentro de su TTL; (d) restaurada la validez → las tools permitidas vuelven a operar.

**REQ-HRB-005** (M) [D-HRB-04, REQ-GOH-003] — Cuando `enforcement.py` verifica un bundle, rechaza: firma inválida/manipulada (payload alterado), algoritmo distinto de RS256, `aud != device_id` local, `exp` vencido y `jti` repetido; ante cualquier rechazo mantiene el anterior vigente (o restricted si no hay anterior) y lo registra en el log local del plugin.
> **AC-HRB-005**: tests unitarios del verificador con los 5 casos de rechazo + caso válido (pasan en pytest con evidencia real).

**REQ-HRB-006** (M) [D-HRB-06] — Cuando se activa el plugin en el perfil Hermes real `trust-e2e`, el paquete `trust-agent-governance` (núcleo v2 + `enroll.py` + `enforcement.py`) carga sin errores desde `$HERMES_HOME/plugins/`, el `config.yaml` del perfil apunta al gateway LiteLLM del tenant con la virtual key del usuario y SIN `fallback_model`, y el arranque de una sesión real del perfil muestra el plugin registrado en el log.
> **AC-HRB-006**: `hermes` (o `hermes chat -q`) con el perfil `trust-e2e` arranca sin errores de plugin; log/estado demuestra el plugin cargado; `config.yaml` verificado.

**REQ-HRB-007** (M) [D-HRB-07] — Cuando se corre la suite de tests del repo Hermes real (`scripts/run_tests.sh`) con el plugin presente, la suite pasa igual que sin el plugin (sin nuevas fallas atribuibles al plugin) y `git status` del repo no muestra escrituras nuestras.
> **AC-HRB-007**: salida real de `scripts/run_tests.sh` (o subconjunto documentado si la suite completa excede el presupuesto de la corrida, dejándolo explícito) con el plugin presente; `git -C /usr/local/lib/hermes-agent status --porcelain` sin cambios nuevos respecto del baseline documentado (`agent/error_classifier.py` preexistente).

**REQ-HRB-008** (M) [Constitution I] — Cuando la validación termina, la evidencia real (comandos, salidas, estados de DB, transcript de la sesión) queda en `/root/trust-agent/docs/hermes-real-bridge.md` con la tabla requisito→AC→evidencia.
> **AC-HRB-008**: el documento existe, en español, con salidas reales pegadas (no descripciones), y cada AC-HRB-001..007 tiene su evidencia enlazada o inline.

**REQ-HRB-009** (M) [D-HRB-09, spec 027 §4] — Cuando el `BundleSyncer` completa un ciclo de sync con device token presente, el plugin envía `POST /api/me/device-heartbeat` con la `policy_version` APLICADA, el uptime del agente y el modo operativo; si la respuesta indica `policy_update_available`, re-baja y aplica el bundle nuevo en el mismo ciclo sin reiniciar ni re-enrolar; un 401/403 del heartbeat pasa a restricted(revoked) invalidando la caché; un error de red del heartbeat no cambia el estado de gobernanza.
> **AC-HRB-009**: (a) tests unitarios del contrato (body, piggyback, re-baja en el mismo ciclo, 401→revoked, red→best-effort, autoajuste de intervalo) pasan; (b) en vivo: la fila `devices` refleja `last_heartbeat_at`/`applied_policy_version` tras un ciclo real del plugin, y un cambio de policy del admin se aplica en el siguiente ciclo sin reinicio (transición visible en `plugin.log`: `heartbeat_ok` → `policy_update_pull` → `bundle_synced` con la versión nueva).

**REQ-HRB-010** (M) [D-HRB-03 amend v1.1, gap t_0b6e8e2d] — (a) Cuando el plugin envía un heartbeat autenticado con su device token (camino plugin) y el dispositivo sigue `active`, el control plane responde además un `device_token` fresco (`expires_in` = TTL vigente) emitido en la misma transacción que ejecutó el DeviceGate, y el plugin lo persiste en `device-token.json` (`renewed_at`) en el mismo ciclo de sync; una sesión enrolada con el agente vivo NO caduca nunca por TTL. (b) Cuando el device token ha expirado (agente apagado > TTL), el `BundleSyncer` lo detecta localmente (`exp`), intenta UN re-enroll con el `id-token.txt` vigente de `trust-login` y, si el server responde 200 con token directo (device `active` + mismo owner + id_token MFA fresco), la sesión vuelve a `normal` en ese ciclo; si no puede (sin id_token fresco), entra en `restricted` con razón `token_expired` (distinta de `revoked`). (c) Cuando el enroll llega para un device `active` de otro usuario, el server responde 409 (sin oracle de propiedad); para un device `revoked`/`rejected` conserva el flujo pending+aprobación admin.
> **AC-HRB-010**: (1) live: sesión enrolada mantiene modo NORMAL con `heartbeat_ok` sostenidos más allá del TTL original de emisión (plugin.log), y `device-token.json` muestra `renewed_at` avanzando; (2) live: revocación admin a mitad de sesión → siguiente ciclo 401 → `restricted(revoked)` inmediato (AC-HRB-004c intacto); (3) live: token expirado + `trust-login` fresco → re-emisión directa 200 (audit `device.token_reissued` en la DB del tenant) → `normal` sin intervención admin; (4) tests: heartbeat camino plugin renueva / camino sesión no; enroll active mismo usuario → 200 + token; otro usuario → 409; revoked → pending; plugin: expiración → recuperación o `token_expired`, nunca `revoked` por expiración.

## 5. Contrato del comando `/trust-enroll`

```
/trust-enroll <org_code> [id_token_file]
```

- `<org_code>`: slug público del tenant (validado server-side contra el claim del token).
- `[id_token_file]`: ruta al archivo con el id_token OIDC (acr≥2). Default: `$TRUST_ENROLL_ID_TOKEN_FILE` o `$HERMES_HOME/trust-agent/id-token.txt`.
- Flujo: load/generate keypair → `POST /api/devices/enroll` (202 + enrollment_token one-time) → polling `POST /api/devices/enroll/exchange` (202 mientras `pending`; 200 con `{device_id, device_token}` al aprobar; 410 si rechazado/consumido) → persistir `device-token.json` (0600) → respuesta final.
- El polling es acotado (default 120 s, env `TRUST_ENROLL_TIMEOUT`); si el admin aún no aprobó, responde con el estado `pending` y el instruction de re-ejecutar `/trust-enroll` (el upsert por fingerprint hace seguro el reintento).
- Errores del control plane (4xx/5xx) → mensaje claro en el chat; jamás crash de la sesión.
- API base: env `TRUST_AGENT_API` (default `http://127.0.0.1:8000`).

## 6. Protocolo de validación real (resumen ejecutable)

1. **Infra**: generar la clave de firma del bundle → `TRUST_POLICY_JWS_PRIVATE_KEY_B64` en `/root/trust-agent/.env`; rebuild de la imagen `trust-agent/platform:local` y restart de `api`/workers; verificar `GET /.well-known/policy-jwks.json` en vivo.
2. **Tenant de prueba**: usar/aprovisionar un tenant real con usuario + budget + policy `allowed_tools` restrictiva + modelo aprobado con virtual key (APIs existentes).
3. **Perfil Hermes real**: crear perfil `trust-e2e`; instalar el paquete del plugin en `$HERMES_HOME/plugins/trust-agent-governance/`; `config.yaml` → gateway LiteLLM del tenant + virtual key, sin fallback; clave pública pineada junto al plugin.
4. **Enrolamiento en vivo**: sesión real (`hermes` en tmux con el perfil) → `/trust-enroll` → aprobación admin (API real) → device token → sync → `governance_status` modo normal.
5. **Enforcement en vivo**: en la misma sesión: tool permitida procede; tool no permitida → bloqueo con mensaje de policy; transcript capturado.
6. **Fail-closed en vivo**: revocar el dispositivo (API real) → siguiente evaluación → restricted; (opcional) control plane caído + caché vencida → restricted.
7. **Suite de Hermes**: `scripts/run_tests.sh` con el plugin presente; registrar salida y baseline git.
8. **Evidencia**: `docs/hermes-real-bridge.md`.

## 7. Trazabilidad

| Req | Padre | ADR | Componente |
|---|---|---|---|
| REQ-HRB-001/002 | spec 007, REQ-GOH-020 | ADR-016 | plugin `enroll.py` |
| REQ-HRB-003 | REQ-GOH-005 (Inc. 2) | ADR-016 | plugin `enforcement.py` |
| REQ-HRB-004 | REQ-GOH-009/010 (Inc. 2) | ADR-017 | plugin `enforcement.py` |
| REQ-HRB-005 | REQ-GOH-003 | ADR-016 | verificador de `enforcement.py` |
| REQ-HRB-006 | REQ-GOH-004, D-GOH-09 | ADR-016 | perfil real `trust-e2e` |
| REQ-HRB-007 | REQ-GOH-016, constitution IV | ADR-018 | suite Hermes |
| REQ-HRB-008 | Constitution I | — | docs |

## 8. Fuera de alcance

- Instalador/link de descarga white-label (spec 023, t_526030f3).
- Attestation al control plane + job de overdue (spec 019 Inc. 3).
- Revocación integral de conectores + bloqueo de virtual key (spec 019 Inc. 4).
- Renovación headless del device token — **INCORPORADA en v1.1.0** (REQ-HRB-010: sliding renewal por heartbeat + re-emisión directa con id_token MFA fresco; antes "camino desktop", retirado con el demo :9090 en t_b8769ecb).
- UI de enrolamiento (el comando es la superficie headless; la UI es del desktop).

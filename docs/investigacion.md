# Investigación técnica — Trust Agent (fase base)

> Fecha: 2026-10-23 · Autor: @dev (tarea t_31934ca5)
> Todo lo marcado **[probado]** se ejecutó contra sistemas reales en este servidor
> (Keycloak 26.2 local en 127.0.0.1:8080, PostgreSQL 17 en contenedor efímero,
> código fuente Hermes v0.22.1+126 en `/usr/local/lib/hermes-agent`). El resto está
> citado contra docs oficiales. Skills derivados en `/root/trust-agent/skills/`.

## Resumen ejecutivo

1. **Hermes se extiende sin tocar core** mediante plugins (`~/.hermes/plugins/` +
   `register(ctx)`) con kinds tipados (tools/hooks, model-providers, memory,
   dashboard-auth). El ecosistema `register_provider` de `dashboard_auth` ya define
   el contrato exacto que Trust Agent necesita: `DashboardAuthProvider` (ABC con
   `start_login/complete_login/verify_session/refresh_session/revoke_session`),
   helpers PKCE+JWKS reutilizables (`_shared.py`) y un provider OIDC self-hosted
   ya construido (`SelfHostedOIDCProvider`) que apunta a cualquier IdP vía
   discovery. **Conclusión: la integración de identidad se hace registrando un
   provider propio como plugin; cero parches al core (regla C6 satisfecha por
   diseño).** Los perfiles por departamento se materializan con `hermes profile
   install` (distributions) y packs de skills.
2. **LiteLLM cubre el plano de control de modelos completo**: virtual keys con
   `max_budget` + `budget_duration` (reset por ventana), teams con
   `max_budget_in_team` (jerarquía depto→usuario), roles nativos incluyendo
   `org_admin`, spend endpoints granulares y refresh de pricing en caliente
   (`/reload/model_cost_map`). **Pitfall crítico: sin PostgreSQL los budgets
   fallan abiertos** — la plataforma siempre correrá LiteLLM con DB.
3. **Keycloak Admin REST + OIDC quedaron probados e2e en local**: realm de
   prueba `trust-agent-test` creado por API, cliente público PKCE S256, flujo
   authorization-code completo por HTTP puro, verificación JWKS del ID token
   con PyJWT (iss/aud pineados), caso negativo de verifier inválido y refresh
   con rotación. El Keycloak local queda disponible para las siguientes tareas.
4. **PostgreSQL RLS verificado con prueba real de aislamiento**: patrón
   `tenant_id` + `current_setting('app.tenant_id', true)` + `SET LOCAL` por
   transacción + `FORCE ROW LEVEL SECURITY`. Los cuatro casos de test
   (lectura aislada, default-deny sin tenant, insert cross-tenant rechazado,
   update que reasigna tenant rechazado) pasaron en PostgreSQL 17. Se
   documentaron 2 pitfalls que golpearon en vivo (GRANT de secuencias, bypass
   del dueño).

---

## 1. Hermes Agent — plugins y extensión

**Skill:** `skills/hermes-agent/hermes-plugins-extension/SKILL.md`

### Qué se verificó
- Código fuente local (git install): `plugins/AGENTS.md` (política "Plugins never
  touch core"), `plugins/dashboard_auth/{_shared.py, self_hosted/, basic/, nous/, drain/}`,
  `hermes_cli/dashboard_auth/base.py` (ABC), `plugins/model-providers/*` (patrón
  `register_provider(ProviderProfile(...))`).
- Docs oficiales: `/docs/user-guide/features/plugins`, `/docs/developer-guide/plugins`,
  `/docs/developer-guide/model-provider-plugin`, índice `llms.txt`.
- CLI local: `hermes profile --help` (16 subcomandos, incl. `export/import/install`
  de distributions), `hermes skills --help` (22 subcomandos), `hermes plugins --help`
  (catálogo, validate, pack).

### Decisiones de diseño para Trust Agent
- **Auth del dashboard/desktop**: plugin `trust-agent-auth` que registra un
  `DashboardAuthProvider` (subclase de `JwtOAuthProvider` usando los helpers de
  `plugins/dashboard_auth/_shared.py`) contra el Keycloak de la plataforma; el
  claim `org_id`/`groups` mapea al tenant. Alternativa sin código propio:
  configurar el provider `self_hosted` bundled (`dashboard.oauth.self_hosted.*`
  o `HERMES_DASHBOARD_OIDC_*`).
- **Modelos**: LiteLLM delante de Hermes como endpoint OpenAI-compatible; si se
  necesita un provider nativo, plugin en `$HERMES_HOME/plugins/model-providers/`
  (last-writer-wins, sin tocar core).
- **Perfiles por departamento**: profile distributions (`hermes profile install`)
  + `hermes plugins pack` / skills.

### Pitfalls
1. El gateway no carga plugins de `./.hermes/plugins/` del cwd — van en
   `$HERMES_HOME/plugins/`.
2. Model-providers se descubren **lazy** y `PluginManager` no los importa (doble
   instanciación); el registro es a nivel de módulo con last-writer-wins.
3. Imports internos de `hermes_cli` **no son API** (cambiaron en PR #102117):
   construir solo sobre `ctx` y los ABCs documentados.
4. La descripción que ve el modelo vive en `schema["description"]`; el
   `description=` de `register_tool` es metadata separada.
5. Compatibilidad es contrato de comportamiento (hooks crecen por keyword
   fields con inspección de firma), no un número de versión.

## 2. LiteLLM proxy — keys, budgets, teams, spend, pricing

**Skill:** `skills/llm-gateway/litellm-proxy-governance/SKILL.md`

### Qué se verificó
Docs oficiales docs.litellm.ai (virtual_keys, users/budgets, team_budgets,
cost_tracking, sync_models_github, custom_model_cost_map, error_reference).

### Mapeo a Trust Agent
| Concepto Trust Agent | Mecanismo LiteLLM |
|---|---|
| Presupuesto org | `max_budget` global o team padre |
| Presupuesto depto | `/team/new` con `max_budget` + `budget_duration` |
| Presupuesto usuario | `max_budget_in_team` vía `/team/member_add` |
| Key por usuario/dispositivo | `/key/generate` con `user_id` + `team_id` |
| Modelos aprobados | `models` en key/team; `access_groups` en config |
| Pricing sync | `POST /reload/model_cost_map` + `/schedule/model_cost_map_reload?hours=6` |
| Corte de presupuesto | 422/429 `budget_exceeded` al superar `max_budget` |
| Roles admin de tenant | `org_admin` nativo (crea teams/users solo en su org) |

### Pitfalls
1. **Budgets sin DB fallan abiertos** — sin `DATABASE_URL` no hay virtual keys y
   el budget global se salta con solo un warning. Siempre PostgreSQL.
2. Key con `team_id` → **solo se aplican budgets del team/miembro**, no el
   personal del dueño (v1.95.0).
3. El campo `user` del body es auto-declarado → spoofing de spend; mitigar con
   `overwrite_user_with_key_hash: true` y seteando `user_id` siempre.
4. `/spend/logs` legacy trunca a 10k filas → usar `/spend/logs/v2`.
5. Huawei MaaS: si el modelo no está en el cost map, spend = $0 con WARNING y
   contador Prometheus `litellm_zero_cost_requests_total` — setear
   `model_info` con precios MaaS o `LITELLM_MODEL_COST_MAP_URL` propio.
6. `temp_budget_expiry` es datetime ISO, no duration string.
7. `/spend/keys` y `/spend/users` están scoped por rol sin opt-out (para
   automation full-table se necesita key `proxy_admin`/`proxy_admin_viewer`).

## 3. Keycloak — Admin REST + OIDC authorization-code/PKCE + JWKS

**Skill:** `skills/identity/keycloak-admin-oidc/SKILL.md` · **[probado]**

### Qué se probó (evidencia real, Keycloak 26.2 local)
- Token admin vía `admin-cli` (766 bytes) → crear realm `trust-agent-test`
  (201), cliente público `trust-agent-web` con PKCE S256 (201), usuario `demo`
  (201), reset-password (204).
- Discovery y JWKS del realm (2 claves: RS256 + RSA-OAEP).
- **Flujo e2e authorization-code+PKCE por HTTP puro** (sin navegador):
  authorize → login form → 302 con code → token exchange con `code_verifier` →
  `id_token` (1106 chars) + access (1422) + refresh.
- **Verificación JWKS con PyJWT**: firma RS256, `iss`/`aud` pineados, claims
  requeridos `exp/iat/aud/iss/sub` → claims correctos (sub, email).
- **Casos negativos**: verifier incorrecto → 400 `invalid_grant` "PKCE
  verification failed"; sin `scope=openid` → respuesta sin `id_token`.
- `refresh_token` grant con rotación (200) y `userinfo` con access token (200).

### Artefactos que quedan
- Realm `trust-agent-test` + cliente + usuario demo en el Keycloak local
  (credenciales admin: `docker inspect keycloak`, env del contenedor).
- Script e2e reutilizable `/root/trust-agent/scripts/test_keycloak_pkce.py`.

### Pitfalls
1. **`scope=openid` es obligatorio para recibir `id_token`** (el access token
   llega igual — engañoso).
2. El POST del login necesita TODAS las cookies del GET authorize
   (`AUTH_SESSION_ID`, `KC_RESTART`); no colapsar headers repetidos en un dict.
3. PKCE por cliente: attribute `pkce.code.challenge.method: "S256"`; ROPC off
   (`directAccessGrantsEnabled: false`) para usuarios finales.
4. `redirect_uri` match exacto contra `redirectUris`.
5. Access token vive ~5 min → refrescar por request-path o verificar el ID token
   con leeway de reloj (60s, mismo default que Hermes).
6. HS256 jamás en `algorithms` de verificación (algorithm confusion).

## 4. PostgreSQL — RLS multi-tenant

**Skill:** `skills/database/postgresql-rls-multitenant/SKILL.md` · **[probado]**

### Qué se probó (evidencia real, PostgreSQL 17 en contenedor)
Esquema `documents(tenant_id)` + rol `app_user` + política
`USING/WITH CHECK (tenant_id = current_setting('app.tenant_id', true))`:

| Caso | Resultado |
|---|---|
| SELECT con `SET LOCAL app.tenant_id='acme'` | solo filas de acme ✓ |
| SELECT sin tenant seteado | 0 filas (default deny) ✓ |
| INSERT dentro del tenant | OK ✓ |
| INSERT cross-tenant | `ERROR: new row violates row-level security policy` ✓ |
| UPDATE reasignando tenant_id | `ERROR: new row violates row-level security policy` ✓ |

### Pitfalls (los dos primeros los golpeamos en vivo)
1. **`GRANT` sobre tabla no da `USAGE` sobre la secuencia del `bigserial`** →
   `permission denied for sequence documents_id_seq`. Fix: `GRANT USAGE, SELECT
   ON ALL SEQUENCES IN SCHEMA public TO app_user;`
2. **El dueño de la tabla salta RLS** → sin `FORCE ROW LEVEL SECURITY` la app
   conectada como dueño no tiene aislamiento. App = rol sin BYPSSRLS y no dueño.
3. `SET` de sesión + connection pool = fuga de tenant entre requests → siempre
   `SET LOCAL` / `set_config(..., true)` dentro de la transacción.
4. RLS no cubre `TRUNCATE` → revocar al rol de app.
5. `current_setting` sin `missing_ok` lanza error en vez de NULL.
6. Índice compuesto `(tenant_id, ...)` para que la política no haga seq scan
   por fila.
7. Migraciones/admin: rol dedicado con `BYPASSRLS` auditado o `SET LOCAL`
   explícito (con FORCE RLS el dueño también queda filtrado).

## Cómo verificarlo

```bash
# Skills
ls /root/trust-agent/skills/*/   # 4 SKILL.md con fuentes
# Keycloak local (realm de prueba vivo)
curl -s http://127.0.0.1:8080/realms/trust-agent-test/.well-known/openid-configuration | head -c 200
# Script e2e PKCE
python3 /root/trust-agent/scripts/test_keycloak_pkce.py
# RLS: reproducible con docker run postgres:17 + SQL del skill §1
```

## Próximos pasos sugeridos (para el orchestrator)

1. Spec del schema PostgreSQL multi-tenant (RLS como defensa en profundidad
   según constitution III) — el skill `postgresql-rls-multitenant` es la base.
2. Spec de identidad: 1 realm vs 1 client por tenant en Keycloak + el plugin
   `trust-agent-auth` para Hermes (skill `keycloak-admin-oidc` §1 + skill
   `hermes-plugins-extension` §3).
3. Spec del plano LiteLLM (jerarquía org→depto→usuario del skill
   `litellm-proxy-governance` §3) con pricing MaaS custom.

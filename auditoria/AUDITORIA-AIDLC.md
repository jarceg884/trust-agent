# Auditoría completa del proyecto AIDLC (referencia para la plataforma)

> 3 auditorías paralelas (admin-panel, parches Hermes, infra K8s) sobre `/root/aidlc-audit/aidlc-bot-project/project/`.
> Veredicto unánime: **confirma D1 — plataforma nueva desde cero**. AIDLC es un prototipo single-tenant funcional con patrones de integración valiosos (Keycloak, LiteLLM, provisioning con rollback) pero NO sirve como base de código: sin tenant_id en ninguna capa, sin base de datos, secretos en claro, sesiones/jobs en memoria.

## 1. Qué es AIDLC hoy

Monolito single-tenant en namespace K8s `aidlc-bot` (Huawei CCE): FastAPI admin-panel (api.py 1019L + core.py 435L + provision.py 404L, puerto 9400) que orquesta Keycloak 26 (SSO OIDC + MFA TOTP, realm único `aidlc`) y LiteLLM (gateway de modelos hacia Huawei MaaS, virtual keys + spend por usuario), mem0 para memorias, bot Hermes con parches aplicados por postStart (reescritura completa de archivos core), SSH server TOTP-only (asyncssh, 2222), y state-sync a OBS cada 6h. Estado en JSONs planos bajo `/opt/data` (PVC): governance.json, ssh-credentials.json, user-provisioning.log. **Sin base de datos. Sin concepto de organización/departamento/tenant.** Todo 1 réplica, Recreate, PVC RWO — sin HA posible.

### Endpoints del panel (referencia funcional)
Auth: `/healthz`, `/auth/login` (OIDC), `/auth/login-direct` (ROPC+TOTP), `/auth/callback`, `/auth/logout`, `/api/me`.
Gobernanza: `/api/overview`, `/api/usage`, `/api/users` (POST, job async), `/api/jobs/{jid}`, `/api/users/{u}/toggle|reset-password|mfa/regen|mfa/disable|regen-vkey|budget|models|soul`, `/api/grants[/bulk]`, `/api/soul/bulk`, `/api/bots` (+key/keys/all/create/delete), `/api/admins` (+promote/demote), `/api/ssh/sessions`, SPA catch-all.

## 2. Hallazgos críticos y de seguridad (merge de las 3 auditorías)

### Críticos
| # | Ubicación | Hallazgo |
|---|-----------|----------|
| C1 | api.py `reset-password` | La nueva password se devuelve en el cuerpo JSON de la respuesta — expuesta a navegador/proxies/logs. Debe ser entrega one-time. |
| C2 | api.py `_SESSIONS` | Sesiones en dict en memoria: sin expiración server-side, cookie sin flag `Secure`, token predecible (base64(username:hex)), se pierden al reiniciar. |
| C3 | Global | **Single-tenant por diseño**: sin tenant_id, un realm KC, cualquier platform-admin ve TODO. Bloqueador directo para multi-tenant (D2). |
| C4 | provision.py/core.py/ssh-server | Secretos en texto claro: virtual key LiteLLM en config.yaml del perfil, TOTP secret en ssh-credentials.json, respuestas de API incluyen password+totp_secret. Sin cifrado en reposo. |
| C5 | core.py `kc_setup_totp` | Enrolamiento MFA por **scraping del HTML de Keycloak** (regex + decodificar QR con cv2) con verificación TLS desactivada. Frágil ante upgrades de KC; regeneración resetea password como efecto secundario. |
| C6 | patches/self_hosted/__init__.py:119 | `httpx.Client(verify=False)` en el POST de credenciales al token endpoint de Keycloak (ROPC): un MITM roba password+MFA+client_secret. |

### Altos
- **JWT sin verificar firma**: `parse_jwt_payload` decodifica base64 sin validar firma/exp/aud/iss (api.py) — falta JWKS.
- **Superadmin con fallback hardcodeado** (`jose-arce`) en core.py; ROPC sin client_secret.
- **postStart `|| true` + imagen `:latest` sin pin**: los parches se copian ANTES de verificar sintaxis, sin rollback; un release upstream deja el auth roto y el pod arranca degradado. Los parches ya están 76-173 líneas por detrás del runtime actual y al aplicarlos **regresan hardening del core** (validación de redirect_uri loopback, X-Forwarded-Prefix, clock-skew leeway).
- **Panel y SSH server como procesos nohup** dentro del pod hermes, supervisados con `ps aux | grep`: mueren silenciosamente; jobs `_JOBS` y sesiones en memoria se pierden en restart.
- **governance.json leído-escrito sin lock** (carreras entre threads) y audit log de texto plano manipulable — insuficiente para gobernanza enterprise.
- **mem0 usa LITELLM_MASTER_KEY**: su gasto pasa por encima de presupuestos y virtual keys — no atribuible a usuario/departamento.
- **Imágenes no fijadas** (hermes:latest, litellm:main-latest) + mem0 con `pip install` en cada arranque: no reproducible, riesgo supply-chain.

### Medios
- Sin CSRF tokens explícitos (solo SameSite=Lax); sin rate limiting en login (ROPC fuerza bruta).
- Throttle anti-brute-force process-local y keyado por IP tras ingress (spoofeable o DoS compartido).
- Heurística que extrae OTP de 6 dígitos del final del password y muta la credencial enviada (self_hosted:92-104).
- ROPC deprecado en OAuth 2.1 — bypasea MFA resistente a phishing (WebAuthn/passkeys).
- Presupuestos solo por usuario (max_budget de virtual key LiteLLM): **sin jerarquía org→depto→usuario ni corte con notificación**.
- Constantes hardcodeadas: IPs públicas, dominios nip.io, namespace, realm, MODELS_ALL, superadmin.
- Sin NetworkPolicy, sin PodSecurityContext/runAsNonRoot.
- Provisionador ejecuta subprocess con credenciales admin KC + master key LiteLLM en el mismo proceso, sin sandbox.

## 3. Parches de Hermes — qué son realmente

`apply-hermes-patches.sh` (postStart) **sobrescribe archivos core** (no monkeypatch): `login_page.py` y `routes.py` → `/opt/hermes/hermes_cli/dashboard_auth/`, `self_hosted/__init__.py` → `/opt/hermes/plugins/dashboard_auth/self_hosted/`. La funcionalidad real: campo `totp` en el login (ROPC+TOTP contra Keycloak) en el provider self_hosted (~60 líneas de delta real sobre ~1200 líneas de copia del core). `model_switch_providers.py` (72KB) es una **copia muerta y stale** que ningún script aplica — su único delta útil (probe de `/v1/models` con api_key para LiteLLM) debería upstreamearse.

**Lección clave para la plataforma**: el mecanismo de parches por sobrescritura es la fragilidad central — la integración con Hermes debe hacerse por **plugins oficiales que subclasen** (el ecosistema `register_provider` ya lo soporta), nunca reescribiendo core. El delta `totp` debería ir upstream al core de Hermes.

## 4. Patrones REUTILIZABLES (como referencia, no como código)

1. **LiteLLM como plano de control de modelos**: virtual key por usuario con `max_budget`/`budget_duration`, teams por proyecto, bloqueo de keys gemelas, modelos en BD, pricing sync runtime (`maas-pricing-sync.py`).
2. **Cliente Keycloak Admin API** + flujo OIDC con MFA TOTP y QR.
3. **Secuencia de provisioning con rollback parcial** en orden inverso + auditoría por paso.
4. **State-sync a OBS con manifests hasheados** y restore documentado; patrón secret-struct (solo nombres de claves en el bucket).
5. **SSH server TOTP-only sin shell libre** (exec restringido a comandos fijos).
6. **Gobernanza own/invoke/view** de agentes (governance.json) — el modelo conceptual es bueno, la implementación (JSON plano sin lock) no.
7. Pitfalls documentados de LiteLLM y Keycloak 26 (VERIFY_PROFILE roto, etc.).

## 5. Variables de entorno/secrets que la plataforma requiere (referencia)

Identidad: KC_ADMIN_PASSWORD, KC_DB_PASSWORD, KEYCLOAK_ADMIN*, KC_HOSTNAME_URL. Modelos: LITELLM_MASTER_KEY, DATABASE_URL (Postgres LiteLLM), MAAS_API_KEY, LITELLM_UI_USER/PW. Panel: PANEL_SUPERADMIN. Backup: HUWEI_AK/SK, OBS_BUCKET, OBS_ENDPOINT. Hermes: HERMES_HOME, HERMES_DASHBOARD*, OIDC_ISSUER/CLIENT_ID/PUBLIC_URL, SSL_CERT_FILE. mem0: POSTGRES_*, JWT_SECRET, MEM0_*.

## 6. Implicaciones directas para el diseño de la plataforma (D2/D5)

1. **PostgreSQL es el corazón**: orgs, departments, users, roles, budgets (jerárquicos org→depto→usuario con corte), policies, devices, connectors, vault (cifrado), audit_log (inmutable, hash chain), model_access, api_keys. `tenant_id` en TODO, RLS (Row Level Security) de PostgreSQL como defensa en profundidad.
2. **Sesiones y jobs en PostgreSQL** (o Redis), nunca en memoria.
3. **Auth: authorization code + PKCE** con verificación JWKS de firma/exp/aud/iss — nada de ROPC para usuarios finales (solo bootstrap interno si acaso). MFA resistente a phishing como opción.
4. **Integración Hermes = plugins oficiales** (subclasado), cero sobrescritura de core.
5. **Enrolamiento de dispositivo de primera clase** (no scraping): device tokens, approval flow del admin, revocación.
6. **Vault cifrado** (envelope encryption: DEW/KMS o equivalente) con entrega one-time de secretos y scoped credentials.
7. **Audit log inmutable y queryable** (DB + hash chain por fila).
8. **Cada servicio = proceso/Deployment propio** con healthchecks; nada de nohup dentro de otro pod.
9. **Presupuestos jerárquicos** con agregación en tiempo casi real desde LiteLLM spend + corte y notificación.
10. **Catálogos (modelos, conectores, skills por departamento) = datos por tenant**, no constantes en código.

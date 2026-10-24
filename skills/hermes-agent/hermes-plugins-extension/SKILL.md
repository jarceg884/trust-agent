---
name: hermes-plugins-extension
description: "Extender Hermes Agent sin tocar core: plugins (plugin.yaml + register(ctx)), ecosistema register_provider de dashboard-auth, model-providers, capacidades CLI de profiles/skills. Use when integrar Trust Agent con Hermes o construir plugins/providers."
version: 1.0.0
author: trust-agent dev
metadata:
  hermes:
    tags: [hermes, plugins, extension, dashboard-auth, providers, profiles, skills, register_provider]
---

# Hermes Agent — Extensión vía plugins (sin tocar core)

Verificado contra Hermes Agent **v0.22.1+126.g3b043e1** (instalación git local en
`/usr/local/lib/hermes-agent`) y la documentación oficial
(https://hermes-agent.nousresearch.com/docs/). Regla dura del propio repo
(`plugins/AGENTS.md`): **"Plugins never touch core"** — un plugin NO debe modificar
`run_agent.py`, `cli.py`, `gateway/run.py`, `hermes_cli/main.py`. Si falta una
capacidad, se amplía la superficie genérica del plugin (hook/ctx method), nunca se
hardcodea lógica de plugin en core. Esto alinea con la regla C6 del proyecto
(cero parches al core de Hermes).

## 1. Anatomía de un plugin general

```
~/.hermes/plugins/my-plugin/        # ($HERMES_HOME/plugins/ con profiles)
├── plugin.yaml                     # manifest: name, version, description
├── __init__.py                     # register(ctx) — registra tools/hooks/comandos
├── schemas.py                      # tool schemas (lo que el LLM ve)
└── tools.py                        # handlers
```

`register(ctx)` expone (verificado en `hermes_cli/plugins.py` y docs):

| Método de ctx | Uso |
|---|---|
| `ctx.register_tool(name=, toolset=, schema=, handler=)` | Registrar una tool llamable por el modelo |
| `ctx.register_hook("pre_tool_call" / "post_tool_call" / "pre_llm_call" / "post_llm_call" / "on_session_start" / "on_session_end", fn)` | Hooks de ciclo de vida |
| `ctx.register_cli_command(...)` | Subcomando argparse cableado en `hermes` al arranque (sin tocar `main.py`) |
| `ctx.register_dashboard_auth_provider(provider)` | Registrar un `DashboardAuthProvider` (ver §3) |
| `ctx.register_locale_dir(...)` | Language packs |

Ejemplo mínimo (docs oficiales, `/docs/user-guide/features/plugins`):

```python
# ~/.hermes/plugins/hello-world/__init__.py
import json

def register(ctx):
    schema = {
        "name": "hello_world",
        "description": "Returns a friendly greeting for the given name.",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "required": ["name"]},
    }
    def handle_hello(params, **kwargs):
        return json.dumps({"success": True,
                           "greeting": f"Hello, {params.get('name', 'World')}!"})
    ctx.register_tool(name="hello_world", toolset="hello_world",
                      schema=schema, handler=handle_hello)
```

La descripción que ve el modelo vive en `schema["description"]` (si se pasa también
`description=` a `register_tool`, es metadata del registry: mantenerlas sincronizadas).

**Pitfall:** el gateway NO carga plugins de `./.hermes/plugins/` del directorio de
trabajo (descubre plugins una vez por proceso). Los plugins de usuario van en
`$HERMES_HOME/plugins/` (o `~/.hermes/plugins/`).

## 2. Kinds de plugin y sus sistemas de descubrimiento

Tabla canónica (fuente: `plugins/AGENTS.md` del repo, líneas 70-79):

| Kind | Dónde | Descubrimiento | Notas |
|---|---|---|---|
| General | `plugins/<name>/`, `~/.hermes/plugins/`, pip entry points | `PluginManager` (`hermes_cli/plugins.py`), **later-wins** | tools, hooks, CLI subcommands, locales |
| Model provider | `plugins/model-providers/<name>/` | `providers/__init__.py::_discover_providers()`, **lazy**; bundled → `$HERMES_HOME/plugins/model-providers/` → legacy `providers/<name>.py` | `__init__.py` llama `providers.register_provider(ProviderProfile(...))` al importar; **last-writer-wins** (un plugin de usuario sobrescribe un perfil bundled) |
| Memory provider | `plugins/memory/<name>/` | bundled-first (¡orden inverso al general!) | implementa `MemoryProvider` ABC (`agent/memory_provider.py`); activación vía `memory.provider` |
| Context engine / image-gen / video-gen / web-search / secret-source | `plugins/context_engine/`, `plugins/image_gen/`, ... | ABC + orquestador + directorio por plugin | ver developer-guide correspondiente |
| Platform adapters | `plugins/platforms/<name>/adapter.py` | gateway | reglas token-lock en `gateway/AGENTS.md` |

**Model provider mínimo** (patrón verificado en `plugins/model-providers/arcee/__init__.py`):

```python
from providers import register_provider
# ... construir ProviderProfile con auth, modelos, api modes
register_provider(mi_provider)   # a nivel de módulo, se ejecuta al cargar (lazy)
```

## 3. El ecosistema `register_provider` de dashboard-auth

Ubicación en el código: `plugins/dashboard_auth/` con providers bundled:
`basic` (usuario/password), `nous` (Portal), `drain` (token de servicio),
`self_hosted` (OIDC genérico: Keycloak, Authentik, Zitadel...). El contrato ABC está
en `hermes_cli/dashboard_auth/base.py` (verificado leyendo el archivo):

```python
class DashboardAuthProvider(ABC):
    name: str = ""            # id estable en minúsculas (obligatorio)
    display_name: str = ""    # obligatorio
    supports_password: bool = False   # complete_password_login()
    supports_token: bool = False      # verify_token() -> TokenPrincipal (service-to-service)
    supports_session: bool = True     # False para credenciales solo-token (drain)

    @abstractmethod
    def start_login(self, *, redirect_uri: str) -> LoginStart: ...
    @abstractmethod
    def complete_login(self, *, code: str, state: str, code_verifier: str, redirect_uri: str) -> Session: ...
    @abstractmethod
    def verify_session(self, *, access_token: str) -> Optional[Session]: ...
    @abstractmethod
    def refresh_session(self, *, refresh_token: str) -> Session: ...
    @abstractmethod
    def revoke_session(self, *, refresh_token: str) -> None: ...   # best-effort, nunca raise
```

Semántica de fallos (del docstring del ABC):
- `ProviderError` → IDP inalcanzable → middleware responde **503**.
- `InvalidCodeError` → code/state/token inválido → **400** (o `verify_session` → `None` → middleware intenta refresh).
- `RefreshExpiredError` → ese provider rechaza el refresh (en multi-provider se prueban los demás).
- `InvalidCredentialsError` → 401 genérico (sin oracle de usuario).

`Session` (dataclass frozen): `user_id, email, display_name, org_id, provider,
expires_at, access_token, refresh_token, issuer`. **`org_id` ya existe** — clave
para mapear tenant de Trust Agent.

### Helpers compartidos (`plugins/dashboard_auth/_shared.py`)

- `register_provider(ctx, logger, tag, provider_cls, settings)` — construye
  `provider_cls(**settings())` y hace `ctx.register_dashboard_auth_provider(provider)`.
  `settings()` puede lanzar `SkipRegistration(reason)` para declinar (p.ej. sin config).
- `JwtOAuthProvider` — base para authorization-code+PKCE cuyo token de sesión es un
  JWT verificado localmente contra JWKS. Subclases implementan `_jwks_uri()`,
  `_claims_for()`, `_grant()`, `_refresh_request()`, `_session()`.
- `pkce_login_start(authorize_url, client_id=, scope=, redirect_uri=)` — genera
  verifier (64 bytes b64url ≈86 chars), state, `code_challenge` S256; el middleware
  espera `cookie_payload["hermes_session_pkce"] = "state=…;verifier=…"`.
- `verify_jwt(token, jwks_client, algorithms=, audience=, issuer=, label=, leeway=60)` —
  PyJWT con `options={"require": ["exp","iat","aud","iss","sub"]}`; clasifica errores
  JWKS (transporte → 503; token ajeno/expirado → InvalidCodeError).
- `validate_redirect_uri()` — exige path que termine en `/auth/callback`.
- JWKS cacheado 300s (`PyJWKClient(cache_keys=True, lifespan=300)`).

### Provider self-hosted OIDC (el relevante para Trust Agent + Keycloak)

`plugins/dashboard_auth/self_hosted/__init__.py` — `SelfHostedOIDCProvider`:
RP OIDC genérico que descubre endpoints de `{issuer}/.well-known/openid-configuration`,
verifica el **ID token** contra el `jwks_uri` descubierto con `iss`/`aud` pineados.
Algoritmos permitidos: RS256/ES256/RS384/RS512/ES384/ES512 (**HS256 excluido** —
algorithm-confusion footgun). Clientes públicos y confidenciales (secret *encima*
de PKCE, nunca en lugar de PKCE).

Config (env gana sobre config.yaml; valor vacío en env = no seteado):

```yaml
dashboard:
  oauth:
    self_hosted:
      issuer: https://auth.example.com/realms/trust-agent
      client_id: trust-agent-dashboard
      scopes: "openid profile email"     # default
      client_secret: ""                  # vacío = cliente público
      id_token_leeway: 60                # segundos de tolerancia de reloj (0 = estricto)
```

```bash
export HERMES_DASHBOARD_OIDC_ISSUER=...
export HERMES_DASHBOARD_OIDC_CLIENT_ID=...
export HERMES_DASHBOARD_OIDC_CLIENT_SECRET=...   # opcional
export HERMES_DASHBOARD_OIDC_SCOPES=...          # opcional
```

Sin issuer+client_id el provider declina con `SkipRegistration` (mensaje exacto en
`LAST_SKIP_REASON`). `org_id` de la sesión: acepta claims `org_id`/`organization`,
si no une `groups` con comas.

**Para Trust Agent**: la puerta de entrada natural es registrar un provider propio
(subclase de `JwtOAuthProvider` o composición con los helpers de `_shared.py`) en un
plugin `~/.hermes/plugins/trust-agent-auth/` que apunte al Keycloak de la plataforma
y mapee `org_id` → tenant. Cero parches al core.

## 4. Catálogo de plugins y CLI

```bash
hermes plugins install <catalog-name|git-url|owner/repo>
hermes plugins search <q>          # catálogo curado (plugin-catalog/<name>.yaml, pin SHA obligatorio)
hermes plugins validate <dir>      # gate de admisión al catálogo (CI)
hermes plugins list / enable / disable / remove / capabilities / doctor
hermes plugins pack                # sets declarativos shareables (hermes-pack.yaml)
```

Aislamiento: `plugins.isolation: host` corre cada plugin no-bundled en un proceso
host por profile (framed JSON protocol); el modo se lee por overlay gestionado
(`/etc/hermes/config.yaml` le gana al config del profile) — la parte aislada no
puede optar por salir si el operador lo pinea.

## 5. Capacidades CLI de profiles y skills (verificado con `hermes --help` local)

```bash
hermes profile list|use|create|delete|show|rename|describe
hermes profile export / import           # archivar/restaurar un profile completo
hermes profile install <git-url|dir>     # profile distributions: compartir un agente entero
hermes profile update                    # re-pull de la distribución (datos de usuario preservados)
hermes skills trust|untrust              # confiar skills repo-locales (./.hermes/skills)
hermes skills search|browse|install|inspect|uninstall
hermes skills check|update|audit         # hub skills
hermes skills list-modified|diff|reset   # bundled skills editados por el usuario
hermes skills publish|snapshot|tap|config
```

Implicación para Trust Agent: los "perfiles Hermes pre-diseñados por departamento"
(DECISIONES.md §3) se materializan como **profile distributions** (`hermes profile
install`) o packs de skills, sin tocar nada del core.

## 6. Contrato de compatibilidad (resumen)

- Compatibilidad es un **contrato de comportamiento**, no un `PLUGIN_API_VERSION`.
- Payloads de hooks crecen como **keyword fields**; callbacks se inspeccionan por firma.
- Nunca se remueven/renombra métodos de `PluginContext`; nuevos parámetros opcionales.
- Campos manifest desconocidos se ignoran.
- Imports internos (`hermes_cli_*` siblings tras PR #102117) **no son API**: un
  plugin viejo que importe paths internos muere con `ImportError` visible en
  `hermes plugins list`. Construir sobre `ctx` y los ABCs documentados.

## Fuentes

- Docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins
- Docs: https://hermes-agent.nousresearch.com/docs/developer-guide/plugins (Build a Hermes Plugin)
- Docs: https://hermes-agent.nousresearch.com/docs/developer-guide/model-provider-plugin
- Docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard
- Índice completo: https://hermes-agent.nousresearch.com/docs/llms.txt
- Código local verificado: `/usr/local/lib/hermes-agent/plugins/AGENTS.md`,
  `plugins/dashboard_auth/{_shared.py,self_hosted/__init__.py,basic,nous,drain}`,
  `hermes_cli/dashboard_auth/base.py`, `plugins/model-providers/*`,
  `hermes_cli/plugins.py`

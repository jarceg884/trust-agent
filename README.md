# Trust Agent — Plataforma SaaS de gobernanza de IA sobre Hermes Agent

Piloto funcional **desplegable donde sea**: un `docker compose up` levanta la
plataforma completa en cualquier host Linux con Docker — tu laptop, tu VM en
cualquier nube (Huawei Cloud ECS, AWS, etc.) o un servidor on-prem. Sin
dependencias del proyecto de origen: solo este repo + un `.env`.

> ¿Qué es? Un SaaS multi-tenant donde un **proveedor** aprovisiona clientes;
> cada cliente gobierna su organización (usuarios, roles, presupuestos
> jerárquicos, políticas, modelos vía LiteLLM, dispositivos enrolados, audit
> log inmutable) y sus usuarios finales corren agentes de Hermes Agent
> **sujetos a esa gobernanza** (veto de políticas, corte por presupuesto,
> revocación de dispositivos). Documentación de producto: `docs/`.

---

## Requisitos

| Requisito | Versión probada | Nota |
|---|---|---|
| Linux x86_64 (host) | Ubuntu 24.04 | El stack usa `network_mode: host` (Linux only) |
| Docker Engine | 27.x | |
| Docker Compose v2 | 2.40 | `docker compose version` |
| OpenSSL | cualquiera | `scripts/setup.sh` genera los secretos |
| RAM libre | ~4 GB | Keycloak + PostgreSQL + Next.js + FastAPI |
| Disco libre | ~10 GB | imágenes + volúmenes de datos |

No se necesita Python, Node ni ninguna otra herramienta en el host: todo
corre dentro de contenedores.

## Instalación de UN comando

```bash
git clone https://github.com/jarceg884/trust-agent.git
cd trust-agent

# Modo dev local (todo en 127.0.0.1, zero config):
scripts/setup.sh          # o: make setup

# Modo servidor (IP/dominio público propio):
TRUST_PUBLIC_BASE_URL=http://TU-IP-O-DOMINIO scripts/setup.sh
```

`setup.sh` es idempotente y hace TODO: crea `.env` desde la plantilla,
**genera los secretos** que falten (nada de "change-me" en producción),
deriva las URLs públicas, construye las imágenes, levanta el stack, corre
las migraciones (servicio `migrate`, automático), seedea el **super-user
del proveedor**, captura su contraseña one-time del log del API y corre el
smoke de verificación. Al final imprime:

```
PROVIDER OPERATOR ONE-TIME PASSWORD (shown ONLY once, at bootstrap):
username=provider-admin password=XXXXX — sign in and change it immediately
```

**Guarda esa contraseña**: es la entrada al Provider Console (el panel con
que aprovisionas clientes) y se muestra solo esa vez. El primer login
obliga a cambiarla.

## Instalación paso a paso (equivalente)

```bash
# 1) Entorno: copia la plantilla COMPLETA y edita lo que necesites.
cp .env.example .env
#    Genera los secretos fail-closed (sin ellos el API NO arranca):
python3 -c "import secrets;print(secrets.token_urlsafe(32))"          # TRUST_DEVICE_TOKEN_SECRET
python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"  # TRUST_VAULT_KEK_B64
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 | base64 -w0  # TRUST_POLICY_JWS_PRIVATE_KEY_B64
#    Y define: POSTGRES_PASSWORD, TRUST_APP_PASSWORD, TRUST_MIGRATOR_PASSWORD,
#    KC_ADMIN_PASSWORD, TRUST_PROVIDER_TOKEN (todas con valores fuertes).

# 2) Imágenes (primera vez ~3-5 min; luego cacheado).
docker compose build

# 3) Levanta todo. El orden lo resuelve compose:
#    db healthy → migrate (alembic, automático) → idp healthy → api → workers → consoles.
docker compose up -d

# 4) El super-user del proveedor se seedea AL ARRANQUE del API. Su contraseña
#    one-time se loguea EXACTAMENTE una vez:
docker logs <prefijo>-api 2>&1 | grep "ONE-TIME"

# 5) Verificación (ver sección siguiente).
scripts/smoke.sh
```

## Verificar que levantó bien

`scripts/smoke.sh` (o `make smoke`) corre checks REALES contra el stack
vivo — nada de "debería funcionar":

```
== Smoke Trust Agent ==
PASS  healthz del API (status=ok, database=up)
PASS  IdP: discovery OIDC del realm master (200)
PASS  Admin Console: /login responde (200)
PASS  Provider Console: /login responde (200)
PASS  LiteLLM double: rechaza sin master key (401)
PASS  Login del super-user del proveedor (200 ok=true)
== Resultado: 6 PASS / 0 FAIL ==
```

El último check (login real del super-user) corre automáticamente cuando
`setup.sh` acaba de bootstrapear; suelto, admite `SMOKE_PROVIDER_PASSWORD=… scripts/smoke.sh`.

A mano, si prefieres:

```bash
docker compose ps                        # todos Up (healthy)
curl -s http://127.0.0.1:8000/healthz    # {"status":"ok","database":"up"}
curl -s -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:8080/realms/master/.well-known/openid-configuration   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3100/login     # 200
```

## Qué levanta el compose

| Servicio | Puerto default | Qué es |
|---|---|---|
| `db` | 55432 (solo 127.0.0.1) | PostgreSQL 17 con roles canónicos (`trust_app` sin BYPASSRLS / `trust_migrator`) y DB `trust_agent` |
| `idp` | 8080 | Keycloak 26.2 (`start-dev`); el provisioning crea el realm por tenant vía Admin REST |
| `migrate` | — | One-shot: `alembic upgrade head` (automático antes del API) |
| `api` | 8000 | Control plane FastAPI (healthz en `/healthz`) |
| `jobs-worker` / `spend-worker` / `attestation-worker` | — | Cola de jobs, ingest de spend + corte de presupuesto, detección de agentes sin attestation |
| `litellm-double` | 39989 | **Doble de tests** de la Admin API de LiteLLM (solo dev — ver §Topologías) |
| `console` | 3100 | Admin Console Next.js (el panel del cliente) |
| `provider-console` | 3200 | Provider Console Next.js (tu panel de operador) |
| `litellm` + `litellm-db` | 4000 / 55433 | **Perfil `litellm`** (producción): proxy LiteLLM real + su PostgreSQL dedicado |

**Nota de red**: `api`, workers, `migrate`, consoles y `litellm-double`
corren con `network_mode: host` (Linux). Motivo: los tokens OIDC llevan el
issuer con la URL pública del IdP (la que ve el navegador) y el API verifica
la firma contra JWKS **en esa misma URL** — host networking mantiene una
sola verdad de URLs sin bifurcar issuer interno/externo.

---

## Los dos modos de despliegue

### Modo (a) — Dev local (default)

Todo en `127.0.0.1` con los puertos canónicos. **No toques nada**: los
defaults del `.env.example` son exactamente este modo. Es el que valida el
smoke tras `scripts/setup.sh` sin argumentos.

### Modo (b) — Servidor con IP/dominio propio

El stack es el mismo; cambian **solo variables del `.env`** (setup.sh las
deriva solo de `TRUST_PUBLIC_BASE_URL`):

| Variable | Dev (default) | Servidor propio |
|---|---|---|
| `TRUST_PUBLIC_BASE_URL` | `http://127.0.0.1` | `http://TU-IP` o `http://trust.tudominio.com` |
| `TRUST_IDP_PUBLIC_URL` | `http://127.0.0.1:8080` | `http://TU-IP:8080` (issuer OIDC) |
| `TRUST_IDP_BASE_URLS` | `…127.0.0.1:8080,127.0.0.1:8908` | `http://TU-IP:8080` (sin el IdP de test) |
| `TRUST_CONSOLE_PUBLIC_URL` | `http://127.0.0.1:3100` | `http://TU-IP:3100` |
| `TRUST_DOWNLOAD_BASE_URL` | `http://127.0.0.1:8000` | `http://TU-IP:8000` |
| `TRUST_OIDC_REDIRECT_URIS` | callbacks 127.0.0.1/localhost:3100 | `http://TU-IP:3100/auth/callback,http://127.0.0.1:8765/callback` |
| `TRUST_IDP_BIND` | `127.0.0.1` | `0.0.0.0` (el navegador del cliente debe llegar al IdP) |
| `TRUST_API_HOST` | `127.0.0.1` | `0.0.0.0` |
| `TRUST_COOKIE_SECURE` | `false` | `false` por ahora (http); `true` con HTTPS |
| `TRUST_LITELLM_PUBLIC_BASE_URL` | vacío | `http://TU-IP:4000` (URL pública del gateway para desktops) |
| `TRUST_PUBLIC_API_BASE_URL` | vacío (origen del request) | `http://TU-IP:8000`, o `http://TU-IP:3100` con el edge público |

### Un solo puerto público: el edge de la consola

Si el firewall solo publica el puerto de la Admin Console (3100), la consola
hace de edge del desktop: reenvía a loopback **solo** lo que un empleado y su
desktop gobernado necesitan — `GET /download/{slug}/**` (página, MSIX con
Range, certificado, `desktop-config`, CLI), la API del dispositivo
(`enroll`, `enroll/exchange`, `token/refresh`, `attestation`,
`/api/me/governance-bundle`, `device-heartbeat`, `update-check`) y la
inferencia de LiteLLM bajo `/llm` (`chat/completions`, `completions`,
`embeddings`, `models`). Todo lo demás responde 404: ni el admin API ni la UI
de LiteLLM, ni la gestión de dispositivos quedan expuestos. La allowlist vive en
`platform/console/src/lib/edge-routes.ts` (tests: `npm run test:edge`).

```bash
TRUST_DOWNLOAD_BASE_URL=http://TU-IP:3100
TRUST_PUBLIC_API_BASE_URL=http://TU-IP:3100
TRUST_LITELLM_PUBLIC_BASE_URL=http://TU-IP:3100/llm
```

El empleado activa con `Portal URL = http://TU-IP:3100`. La IP del cliente que
ven los throttles y la auditoría es el peer TCP (`edge-client-address.cjs`,
precargado por la imagen); detrás de un proxy inverso declara sus IPs en
`TRUST_EDGE_TRUSTED_PROXIES`. Es HTTP sin TLS: tokens de activación y claves de
modelo viajan en claro — para producción, dominio + TLS delante.

Las URLs **internas** (`TRUST_API_BASE_URL`, `TRUST_KEYCLOAK_ADMIN_URL`)
siguen apuntando a `127.0.0.1` — con host networking el stack siempre se
habla por loopback, aunque el mundo entre por la IP pública. La DB nunca
se publica fuera del host.

### Servidor con HTTPS / proxy inverso (avanzado)

Tras un proxy TLS (nginx/Caddy) cada servicio puede publicarse en su propio
dominio, y setup.sh NO deriva nada automáticamente. Fija a mano en `.env`:
`TRUST_IDP_PUBLIC_URL=https://idp.tudominio.com`,
`TRUST_CONSOLE_PUBLIC_URL=https://admin.tudominio.com`,
`TRUST_DOWNLOAD_BASE_URL=https://api.tudominio.com`,
`TRUST_IDP_BASE_URLS=https://idp.tudominio.com`,
`TRUST_OIDC_REDIRECT_URIS=https://admin.tudominio.com/auth/callback,http://127.0.0.1:8765/callback`,
`TRUST_COOKIE_SECURE=true`, y en el servicio `idp` agrega
`KC_HOSTNAME=idp.tudominio.com`. Los binds pueden quedarse en `127.0.0.1`
(el proxy es el único que entra). Recomendado para producción real.

### Instalador de escritorio universal (multi-organización)

El repo de plataforma **no guarda el binario MSIX de ~744 MB en git**. El
instalador se construye una vez con un bundle neutro y sirve para cualquier
organización; el branding, portal, modelo y políticas se resuelven en la
activación del empleado.

La receta de build vive en
`https://github.com/jarceg884/trust-agent-build` y se ejecuta con el workflow
`Build Trust Agent Windows MSIX` (`workflow_dispatch`). Por defecto produce
`Trust Agent` genérico, sin `org_code` ni credenciales.

Después de construir, publica el MSIX en el host de la plataforma:

```bash
python3 scripts/bake_generic_desktop_bundle.py
# Build del desktop/MSIX (ver docs/generic-installer.md)
python3 scripts/publish_universal_installer.py \
  --msix /ruta/TrustAgentDesktop-0.0.0-win-x64.msix \
  --version 0.6.0
```

Eso deja `installer-artifacts/_universal/<version>/` y la API lo sirve
automáticamente para **cualquier tenant activo sin build propio**:

```text
GET /download/{slug}                      # página de descarga (Windows)
GET /download/{slug}/desktop-config       # branding/modelo público del tenant
GET /download/{slug}/installer/windows    # MSIX universal
GET /download/{slug}/certificate          # certificado de firma (.cer)
```

Flujo de activación: el admin del cliente crea un token one-time en
`Devices → Enroll device`; el empleado abre el MSIX e introduce
`Portal URL + Organization + Organization token`. El desktop valida el tenant
y escribe su `config.yaml`, `SOUL.md`, skin y modelo **antes** de consumir el
token. Para desktops remotos, `TRUST_LITELLM_PUBLIC_BASE_URL` debe ser la URL
del gateway alcanzable desde la máquina del empleado (p. ej.
`http://TU-IP:4000`, o `http://TU-IP:3100/llm` con el edge de la consola). Si
se deja vacía y el base URL interno es loopback, la API deriva el host de la
petición.

Detalle completo: `docs/generic-installer.md`.

---

## Topologías del plano de control de modelos (LiteLLM)

El control plane gobierna el acceso a modelos y el spend a través de un
proxy LiteLLM (Admin API). Hay dos topologías:

**1. Dev/tests (default): `litellm-double`.** Un doble HTTP declarado de la
Admin API (`platform/tests/litellm_fake.py`). Costo cero, sin credenciales —
pero NO sirve para gobernar spend real: es solo para el stack de pruebas.

**2. Producción: proxy LiteLLM REAL (perfil `litellm`).** El compose trae el
proxy real (imagen pineada `v1.106.3`) con el catálogo y precios reales de
`litellm-real/config.yaml` (Huawei Cloud MaaS en el piloto) y su PostgreSQL
dedicado (los budgets del proxy sin DB fallan abiertos — inaceptable):

```bash
# 1) En .env: pon tu endpoint y key del proveedor de modelos (MaaS central
#    del proveedor del piloto, o el que sea en topología BYOK del cliente):
#      TRUST_MAAS_ENDPOINT=https://tu-endpoint-maas/v1
#      TRUST_MAAS_API_KEY=<tu-key>          # el VALOR vive SOLO en .env
#      TRUST_LITELLM_BASE_URL=http://127.0.0.1:4000
#      TRUST_LITELLM_MASTER_KEY=<LITELLM_MASTER_KEY de tu .env>
#    (setup.sh ya generó LITELLM_MASTER_KEY y LITELLM_DB_PASSWORD.)
# 2) Levanta el perfil y recrea api/workers para que tomen la nueva URL:
docker compose --profile litellm up -d
docker compose up -d api jobs-worker spend-worker attestation-worker
# 3) Verifica:
curl -s http://127.0.0.1:4000/health/liveliness   # {"status":"healthy"...}
```

- **MaaS central del proveedor**: el proveedor del SaaS pone su key MaaS;
  los clientes consumen modelos centrales con presupuestos del control plane.
- **BYOK total del cliente**: el cliente pone SU endpoint y SU key en las
  mismas variables (cualquier proveedor OpenAI-compatible); el catálogo de
  `litellm-real/config.yaml` se edita a su upstream.

---

## Backup y restore de los datos del cliente

Los datos viven en TU PostgreSQL (volumen `trust_db_data`); la identidad
(Keycloak: usuarios, MFA, realms) en `kc_data`. Respaldo básico de la DB:

```bash
scripts/backup.sh                 # → trust-agent-backup-<fecha>.dump (pg_dump -Fc)
scripts/backup.sh /ruta/mi.dump   # ruta explícita
make backup                       # equivalente

scripts/restore.sh trust-agent-backup-<fecha>.dump   # PELIGRO: reemplaza TODO
```

`backup.sh` verifica que el dump sea restaurable (`pg_restore --list`).
Sugerencia de cron diario: `0 3 * * * cd /opt/trust-agent && scripts/backup.sh /backups/$(date +\%F).dump`.
Para respaldar también la identidad, respalda el volumen `kc_data`
(`docker run --rm -v trust-agent_kc_data:/data -v /backups:/out alpine tar czf /out/kc_data.tgz -C /data .`).

## Operación

```bash
docker compose ps                  # estado
docker compose logs -f api         # logs del control plane
docker compose restart api         # reinicio (sesiones/jobs viven en PostgreSQL)
docker compose down                # apagar (los volúmenes persisten)
docker compose down -v             # apagar + BORRAR datos (DB y Keycloak)
docker compose build --no-cache    # reconstruir tras cambiar código
make smoke                         # verificación en cualquier momento
```

## Troubleshooting

| Síntoma | Causa/fix |
|---|---|
| `api` no arranca: `TRUST_DEVICE_TOKEN_SECRET is missing or still a placeholder` | Secretos fail-closed: `scripts/setup.sh` los genera, o pon valores reales en `.env` |
| Build de una console: `Cannot find module '@tailwindcss/turbopack'` | Ya resuelto en el Dockerfile (`npm ci --include=dev`); si persiste, `docker compose build --no-cache console` |
| `idp` nunca pasa a healthy | Primera vez tarda ~40-60 s; revisa `docker logs <prefijo>-idp` |
| Puerto ocupado (`bind: address already in use`) | `ss -tlnp | grep -E ':(8000|3100|3200|8080|55432|39989)'` y libera el puerto, o cambia el puerto en `.env` (`TRUST_API_PORT`, etc.) |
| `migrate` falla | `docker logs <prefijo>-migrate`; verifica `POSTGRES_PASSWORD` en `.env` |
| Login del cliente rompe tras cambiar IP/dominio | Los realms existentes conservan redirect URIs viejas: actualiza `TRUST_OIDC_REDIRECT_URIS` y `TRUST_IDP_BASE_URLS` en `.env` y `docker compose up -d api` (el heal `ensure_web_client_redirect_uris` las reconcilia) |
| Dos stacks del piloto en el mismo host (p. ej. pruebas) | `TRUST_CONTAINER_PREFIX=otro docker compose -p otro up -d` + puertos distintos en `.env` |

## Mapa del repo

```
docker-compose.yml          # el stack completo (12 servicios, 2 perfiles)
.env.example                # plantilla de entorno COMPLETA (secretos + URLs + puertos)
Makefile                    # setup / smoke / backup / restore / litellm-up
scripts/                    # setup.sh, smoke.sh, backup.sh, restore.sh (operador)
db/                         # schema.sql + Alembic + bootstrap de roles
platform/                   # control plane FastAPI
platform/console/           # Admin Console Next.js (panel del cliente)
platform/provider-console/  # Provider Console Next.js (panel del operador)
platform/hermes/            # plugin de gobernanza + profile-template del bundle de descarga
litellm-real/               # config del proxy LiteLLM real (catálogo + precios MaaS)
specs/                      # specs SDD del producto (specify → plan → tasks → implement → converge)
docs/                       # arquitectura, validación, evidencia real de cada capacidad
skills/                     # hallazgos capturados como skills reutilizables
```

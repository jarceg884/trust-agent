# Trust Agent — Plataforma SaaS de gobernanza de IA sobre Hermes Agent

Empaquetado **local single-node** (constitution VII): un solo `docker compose up`
levanta la plataforma completa para validación local — nada de infra cloud.

> Contexto del proyecto: `CONTEXTO-TAREAS.md` · Vision/decisiones: `DECISIONES.md` ·
> Constitution: `.specify/memory/constitution.md` · Arquitectura: `docs/arquitectura.md`

## Qué levanta el compose

| Servicio | Contenedor | Puerto (127.0.0.1) | Qué es |
|---|---|---|---|
| `db` | `trust-agent-db` | **55432** | PostgreSQL 17 con roles canónicos (`trust_app` sin BYPASSRLS / `trust_migrator`) y DB `trust_agent` |
| `idp` | `trust-agent-idp` | **8080** | Keycloak 26.2 (`start-dev`); el provisioning crea el realm por tenant vía Admin REST |
| `migrate` | `trust-agent-migrate` | — | One-shot: `alembic upgrade head` por la vía canónica (`db/migrations`, D-CP-11) |
| `api` | `trust-agent-api` | **8000** | Control plane FastAPI (specs 003–008, 011, 012) |
| `jobs-worker` | `trust-agent-jobs-worker` | — | Cola de jobs en PostgreSQL (invites de usuarios, spec 012) |
| `spend-worker` | `trust-agent-spend-worker` | — | Ingest de spend + enforcement de corte (spec 005) |
| `litellm-double` | `trust-agent-litellm-double` | **39989** | **Doble HTTP DECLARADO** de la LiteLLM Admin API (D-GOV-15, `platform/tests/litellm_fake.py`) |
| `console` | `trust-agent-console` | **3100** | Admin Console Next.js 16 (spec 009) |

Los puertos son los **canónicos del runbook** (`platform/README.md`, `db/README.md`):
los scripts E2E y smokes funcionan sin retocar nada.

**Nota de red**: `api`, `migrate`, workers, `litellm-double` y `console` corren con
`network_mode: host` (Linux). Motivo: los tokens OIDC llevan issuer
`http://127.0.0.1:8080/realms/<org>` (la URL que ve el navegador) y el API verifica
la firma contra JWKS **en esa misma URL** — con red bridge habría que bifurcar URLs
de issuer (frágil). Host networking mantiene una sola verdad de puertos.

## Arranque paso a paso

```bash
cd /root/trust-agent

# 1) Entorno: copia la plantilla y rellena los secretos.
cp .env.example .env
#    Genera los dos secretos fail-closed (sin ellos el API NO arranca):
python3 -c "import secrets;print(secrets.token_urlsafe(32))"         # → TRUST_DEVICE_TOKEN_SECRET
python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"  # → TRUST_VAULT_KEK_B64
#    Y define: KC_ADMIN_PASSWORD, TRUST_PROVIDER_TOKEN.

# 2) Imágenes (primera vez ~3-5 min; luego cacheado).
docker compose build

# 3) Levanta todo. El orden lo resuelve compose:
#    db healthy → migrate (alembic) → idp healthy → api → workers/console.
docker compose up -d

# 4) Espera a que todos estén sanos (el IdP tarda ~40-60 s la primera vez).
docker compose ps
#    NAME                       STATUS
#    trust-agent-api            Up (healthy)
#    trust-agent-console        Up (healthy)
#    trust-agent-db             Up (healthy)
#    trust-agent-idp            Up (healthy)
#    trust-agent-jobs-worker    Up
#    trust-agent-litellm-double Up (healthy)
#    trust-agent-spend-worker   Up
```

## Verificación de salud

```bash
# Control plane (fail-closed: si la DB no responde, no dice "ok")
curl -s http://127.0.0.1:8000/healthz
# {"status":"ok","database":"up"}

# Keycloak: discovery del realm master
curl -s -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:8080/realms/master/.well-known/openid-configuration
# 200

# Console: página de login
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3100/login
# 200

# Doble de LiteLLM: rechaza sin master key (401 = vivo y cerrado)
curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:39989/key/info?key=x'
# 401

# Migraciones aplicadas (vía canónica)
docker logs trust-agent-migrate
# ... alembic ... (exit 0)

# Workers vivos (poll cada ~2-15 s)
docker logs --tail 5 trust-agent-jobs-worker
docker logs --tail 5 trust-agent-spend-worker
```

## Flujo demo de punta a punta (provision → admin → usuario)

Evidencia real de este empaquetado: [`docs/smoke-local.md`](docs/smoke-local.md).

```bash
cd /root/trust-agent/platform

# E2E global en vivo (79 checks): proveedor crea tenant → admin PKCE+TOTP real
# (acr=2) → usuario obrero → dispositivo → agente compartido → modelo aprobado
# → [reinicia el API: docker compose restart api] → spend/corte/audit/revocación.
.venv/bin/python scripts/e2e_final_live.py --phase a --state /tmp/e2e_state.json
docker compose restart api        # desde /root/trust-agent
.venv/bin/python scripts/e2e_final_live.py --phase b --state /tmp/e2e_state.json

# Smoke del Admin Console (login PKCE+TOTP REAL por el console del compose):
# primero aprovisiona un tenant y pasa su one-time password.
curl -s -X POST http://127.0.0.1:8000/api/admin/tenants \
  -H "Authorization: Bearer $TRUST_PROVIDER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"organization_name":"Console Smoke Corp","admin_email":"admin@console-smoke2.test",
       "initial_budget_usd":500.0,"model_plan":"standard","slug":"console-smoke2"}'
.venv/bin/python console/scripts/smoke_console.py <one_time_password>
```

El navegador también sirve: `http://127.0.0.1:3100/login` → org code del tenant
demo (`final-e2e` o `console-smoke2`) → login Keycloak con MFA.

## Operación

```bash
docker compose ps                  # estado
docker compose logs -f api         # logs del control plane
docker compose restart api         # reinicio (las sesiones/jobs viven en PostgreSQL)
docker compose down                # apagar (los volúmenes persisten)
docker compose down -v             # apagar + BORRAR datos (DB y Keycloak)
docker compose build --no-cache    # reconstruir tras cambiar código
```

- **Datos**: volúmenes `trust-agent_trust_db_data` (PostgreSQL) y
  `trust-agent_kc_data` (Keycloak). `down` sin `-v` los conserva.
- **Cambios de código**: `docker compose build api console && docker compose up -d`.
- **LiteLLM real** (opcional): el compose usa el doble declarado (D-GOV-15) —
  costo cero y sin credenciales MaaS. Para un proxy real: levántalo aparte y
  cambia `TRUST_LITELLM_BASE_URL` / `TRUST_LITELLM_MASTER_KEY` en los servicios
  `api` y `spend-worker` del `docker-compose.yml`.

## Rollback al runbook ad-hoc (pre-compose)

El entorno anterior (procesos uvicorn/next en el host + contenedores `trust-db`
y `keycloak`) quedó preservado:

```bash
docker compose down          # apagar el stack empaquetado
docker start trust-db keycloak
# y arrancar API/console a mano según platform/README.md y platform/console/README.md
```

## Troubleshooting

| Síntoma | Causa/fix |
|---|---|
| `api` no arranca: `TRUST_DEVICE_TOKEN_SECRET is missing or still a placeholder` | Secretos fail-closed: genera uno real en `.env` (constitution IV) |
| Build de la console: `Cannot find module '@tailwindcss/turbopack'` | `npm ci` con `NODE_ENV=production` se salta devDeps — el Dockerfile ya usa `npm ci --include=dev` |
| `idp` nunca pasa a healthy | Primera vez tarda ~40-60 s; revisa `docker logs trust-agent-idp` |
| Puerto ocupado (`bind: address already in use`) | Algo del entorno ad-hoc sigue vivo: `ss -tlnp | grep -E ':(8000|3100|8080|55432|39989)'` y detén el proceso |
| `migrate` falla | `docker logs trust-agent-migrate`; verifica `POSTGRES_PASSWORD` en `.env` |

## Mapa del repo

```
docker-compose.yml          # este empaquetado (8 servicios)
.env.example                # plantilla de entorno (secretos fail-closed)
db/                         # schema.sql + Alembic + tests (spec 002)
platform/                   # control plane FastAPI (specs 003-008, 011, 012)
platform/console/           # Admin Console Next.js (spec 009)
platform/desktop-client/    # Desktop white-label (spec 010)
platform/scripts/           # e2e_final_live.py, run_litellm_double.py, smokes
specs/                      # 12 specs SDD (specify → plan → tasks → implement → converge)
docs/                       # arquitectura, validación, smoke-local (evidencia)
skills/                     # hallazgos capturados como skills reutilizables
```

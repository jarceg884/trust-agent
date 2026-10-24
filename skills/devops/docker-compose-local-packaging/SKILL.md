---
name: docker-compose-local-packaging
description: Empaquetar un stack multi-servicio con OIDC (Keycloak) + API que verifica JWKS en compose local single-node; pitfalls de red, healthchecks y builds.
category: devops
---

# Empaquetado docker-compose local single-node (stack con OIDC + JWKS)

Use when: haya que empaquetar en docker-compose un stack donde un API verifica
tokens OIDC contra un IdP al que también accede el navegador (Keycloak + FastAPI
+ Next.js + PostgreSQL), para validación local en un solo host Linux.

## Regla central: una sola verdad de URLs de issuer

Los tokens OIDC llevan el issuer **que ve el navegador** (p. ej.
`http://127.0.0.1:8080/realms/<org>`) y el API verifica la firma contra JWKS
**en esa URL exacta** (`{issuer}/protocol/openid-connect/certs`). Con red bridge
el contenedor del API no puede resolver `127.0.0.1:8080` del host, y duplicar
bases de issuer (host.docker.internal + 127.0.0.1) es frágil.

**Solución**: `network_mode: host` para API/workers/console en Linux local. Los
puertos publicados (`127.0.0.1:PORT:PORT`) del IdP/DB quedan accesibles igual.
Los puertos deben ser los **canónicos del runbook** para que los scripts E2E
funcionen sin retocar.

## Pitfalls reales (todos costaron una corrida de build/up)

1. **`NODE_ENV=production` antes de `npm ci`** excluye devDependencies →
   `next build` falla con `Cannot find module '@tailwindcss/turbopack'`.
   Usar `npm ci --include=dev` y fijar `NODE_ENV=production` SOLO tras el build.
2. **Healthcheck HTTP a mano con /dev/tcp**: si envías `HTTP/1.0`, el server
   responde `HTTP/1.0 200 OK` — un grep de `HTTP/1.1 200` nunca matchea.
   Grep `' 200 '`.
3. **Keycloak 26 `start-dev`** tarda 40-60 s la primera vez: healthcheck con
   `start_period: 40s`. Admin bootstrap: `KC_BOOTSTRAP_ADMIN_USERNAME/PASSWORD`
   (no KEYCLOAK_ADMIN_*). La imagen no trae curl/wget: usar bash `/dev/tcp`.
4. **Migraciones como servicio one-shot** (`restart: "no"`) y el API con
   `depends_on: {condition: service_completed_successfully}` — jamás arranca
   contra un schema a medias.
5. **Dobles de test que bindian puerto aleatorio** (port 0): pinea el puerto
   monkeypatcheando la clase del server (ver
   `platform/scripts/run_litellm_double.py`).
6. **Postgres init**: `POSTGRES_DB` crea la DB; los roles van por script en
   `docker-entrypoint-initdb.d/` (solo corre la PRIMERA vez del volumen).

## Verificación de salud mínima (todas deben dar salida real)

```bash
curl -s http://127.0.0.1:8000/healthz        # {"status":"ok","database":"up"}
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/realms/master/.well-known/openid-configuration  # 200
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/login       # 200
docker logs <migrate>                       # exit 0
```

Referencia viva: `/root/trust-agent/docker-compose.yml` + `/root/trust-agent/README.md`
+ evidencia en `/root/trust-agent/docs/smoke-local.md` (t_d36b3f31).

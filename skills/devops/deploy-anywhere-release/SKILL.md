---
name: deploy-anywhere-release
description: Use when packaging a compose stack for third-party deployment or cutting a pilot release (portability, secret audit, fresh-clone evidence). Covers env-driven compose, gitleaks 8.24 quirks, and evidence-run pitfalls.
category: devops
---

# Release "desplegable donde sea" (empaquetado para terceros)

Lecciones del release v0.1.0-pilot de trust-agent (t_b62edff2). Todo validado
con corridas reales fresh-clone, no con "debería funcionar".

## Portabilidad del compose

- **Nada hardcodeado al host de dev**: cada IP/puerto/password/nombre de
  contenedor va como `${VAR:-default}` donde default = valor de dev
  IDÉNTICO al runbook original. Así el stack existente no cambia de
  comportamiento y el cliente solo edita .env.
- `network_mode: host` (Linux) evita bifurcar issuer OIDC interno/externo:
  el API verifica JWKS en la MISMA URL pública que ve el navegador. Las URLs
  server-side (BFF→API, Admin REST del IdP) quedan en loopback.
- Derivación de URLs: `TRUST_PUBLIC_BASE_URL` + un script de setup que
  reescribe las `*_PUBLIC_URL` en .env. compose NO puede derivar listas
  (redirect URIs) por interpolación — hazlo en el script.
- Dos stacks del mismo compose en un host: `COMPOSE_PROJECT_NAME=x` overridea
  el `name:` del YAML (verificado en compose 2.40) + `TRUST_CONTAINER_PREFIX`
  para container_name + puertos distintos en .env.
- Parameteriza TAMBIÉN los healthchecks (leen el puerto de os.environ/process.env)
  y los CMD de los Dockerfiles (`sh -c "… --port ${PORT:-default}"`).

## gitleaks 8.24.3 (comportamiento real, verificado)

- Los `[[allowlists]]` del config NO se aplican — ni con `paths` ni con
  `regexes`, ni con `[extend] useDefault = true` ni sin él (issue #1844).
- Lo que SÍ funciona: marcador inline `# gitleaks:allow` en la línea del
  hallazgo (con justificación al lado). Es además más seguro: solo pasa ese
  valor exacto en esa línea.
- En strings TOML literales (`'''…'''`) NO hay procesamiento de escapes:
  `\\.` llega al regex como backslash literal + comodín. Usa `\.` a secas.
- `protect --staged` escanea el índice (lo que va al commit) — úsalo ANTES
  de commit; `detect` escanea el historial; `detect --no-git --source <dir>`
  escanea el árbol (simula un clone con `git archive HEAD | tar -x`).
- Secretos reales en archivos de evidencia: redacta a `os.environ[...]` (el
  script sigue siendo reproducible pasando el env) y verifica con
  `git log -S"<valor>" --all` que NUNCA entraron al historial.

## Bugs de bash que el fresh-clone cazó (patrón a evitar)

- `run "rand_hex 24"` con `"$2"` ejecuta un comando llamado literalmente
  "rand_hex 24" → output vacío → secret vacío. Pasa el VALOR evaluado en el
  callsite: `gen KEY "$(rand_hex 24)"`.
- Todo generador de secretos necesita un guard fail-fast DESPUÉS de generar
  y ANTES de arrancar contenedores: si algo quedó vacío/placeholder, aborta
  con mensaje accionable — que falle el setup, no el Keycloak a mitad de up.
- El smoke de un setup de un comando debe ESPERAR a todos los servicios que
  verifica (las consoles Next.js arrancan después del API; correr el smoke
  con el primer healthz = FAILs falsos).
- Los scripts de operador no pueden asumir client-tools en el host (pg_dump
  sí va en el contenedor db; pg_restore también — verifica el dump con
  `docker exec -i <db> pg_restore --list < dump`).

## Proceso de release que funcionó

1. Commit del estado validado + cambios de portabilidad en rama → PR → CI
   (compose config + gitleaks pineado a la MISMA versión de la auditoría
   local) → merge → tag → GitHub release.
2. Evidencia fresh-clone DESPUÉS del tag, contra el repo público, simulando
   al cliente (solo repo + .env.example). Cada bug cazado se fixea y se
   documenta; si el bug rompe la promesa del tag (p. ej. setup.sh), mueve el
   tag al fix y documéntalo en las notas del release — un tag joven se puede
   mover una vez documentado; un tag que falla en falso no vale nada.
3. La evidencia va al repo (docs/evidencia/...) vía PR propio, con la OTA y
   demás credenciales de la corrida REDACTADAS.

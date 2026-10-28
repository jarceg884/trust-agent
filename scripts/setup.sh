#!/usr/bin/env bash
# setup.sh — instalación de UN comando del Trust Agent (t_b62edff2, mandato
# Miller: "desplegable donde sea" por un operador externo).
#
# Qué hace (idempotente — re-ejecutar no rompe nada):
#   1. Crea .env desde .env.example si no existe.
#   2. Genera secretos fuertes donde quede un placeholder "change-me".
#      NUNCA toca valores que el operador ya haya puesto.
#   3. Deriva las URLs públicas desde TRUST_PUBLIC_BASE_URL (modo servidor).
#   4. docker compose build && docker compose up -d
#   5. Espera salud de todos los servicios.
#   6. Captura la contraseña one-time del super-user del proveedor (se loguea
#      EXACTAMENTE una vez en el arranque del API) y la imprime.
#   7. Corre el smoke de verificación (scripts/smoke.sh).
#
# Uso:
#   scripts/setup.sh                                   # dev local (127.0.0.1)
#   TRUST_PUBLIC_BASE_URL=http://TU-IP scripts/setup.sh  # servidor propio
set -euo pipefail
cd "$(dirname "$0")/.."

PREFIX="${TRUST_CONTAINER_PREFIX:-trust-agent}"

# --- helpers -----------------------------------------------------------------
env_exists() { grep -q "^$1=" .env 2>/dev/null; }
env_get()    { sed -n "s|^$1=||p" .env | head -1; }
env_set()    { sed -i "s|^$1=.*|$1=$2|" .env; }
is_placeholder() {
  case "$1" in
    ""|"change-me"*) return 0 ;;
    *) return 1 ;;
  esac
}
rand_hex()   { openssl rand -hex "$1"; }
rand_b64()   { openssl rand -base64 "$1" | tr -d '\n'; }

# --- 1) .env -----------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[setup] .env creado desde .env.example"
else
  echo "[setup] .env existente — no se sobreescribe"
fi

# --- 2) secretos donde haya placeholder --------------------------------------
gen_if_placeholder() { # $1=key  $2=generador
  local current; current="$(env_get "$1")"
  if is_placeholder "$current"; then
    env_set "$1" "$("$2")"
    echo "[setup] $1 generado (aleatorio)"
  fi
}
gen_if_placeholder POSTGRES_PASSWORD                "rand_hex 24"
gen_if_placeholder TRUST_APP_PASSWORD               "rand_hex 24"
gen_if_placeholder TRUST_MIGRATOR_PASSWORD          "rand_hex 24"
gen_if_placeholder KC_ADMIN_PASSWORD                "rand_hex 24"
gen_if_placeholder TRUST_DEVICE_TOKEN_SECRET        "rand_hex 32"
gen_if_placeholder TRUST_VAULT_KEK_B64              "rand_b64 32"
gen_if_placeholder TRUST_PROVIDER_TOKEN             "rand_hex 32"
gen_if_placeholder LITELLM_DB_PASSWORD              "rand_hex 24"
gen_if_placeholder LITELLM_MASTER_KEY               "rand_hex 32"
# Clave RSA-2048 PKCS#8 (PEM base64) que firma el governance bundle.
if is_placeholder "$(env_get TRUST_POLICY_JWS_PRIVATE_KEY_B64)"; then
  env_set TRUST_POLICY_JWS_PRIVATE_KEY_B64 "$(openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 2>/dev/null | base64 -w0)"
  echo "[setup] TRUST_POLICY_JWS_PRIVATE_KEY_B64 generado (RSA-2048)"
fi

# --- 3) derivación de URLs públicas (modo servidor) ---------------------------
BASE="$(env_get TRUST_PUBLIC_BASE_URL)"
BASE="${BASE:-http://127.0.0.1}"
if [ "$BASE" != "http://127.0.0.1" ] && [ "$BASE" != "http://localhost" ]; then
  if [[ "$BASE" == https:* ]]; then
    cat >&2 <<EOF
[setup] TRUST_PUBLIC_BASE_URL=https detectado: detrás de un proxy TLS las URLs
        públicas NO se derivan automáticamente (cada servicio puede ir en un
        dominio distinto). Fija a mano en .env: TRUST_IDP_PUBLIC_URL,
        TRUST_CONSOLE_PUBLIC_URL, TRUST_DOWNLOAD_BASE_URL,
        TRUST_IDP_BASE_URLS, TRUST_OIDC_REDIRECT_URIS y
        TRUST_COOKIE_SECURE=true — ver README §"Servidor con HTTPS/proxy".
EOF
  else
    IDP_PORT="$(env_get TRUST_IDP_PORT)";      IDP_PORT="${IDP_PORT:-8080}"
    API_PORT="$(env_get TRUST_API_PORT)";      API_PORT="${API_PORT:-8000}"
    CONSOLE_PORT="$(env_get TRUST_CONSOLE_PORT)"; CONSOLE_PORT="${CONSOLE_PORT:-3100}"
    env_set TRUST_IDP_PUBLIC_URL      "$BASE:$IDP_PORT"
    env_set TRUST_CONSOLE_PUBLIC_URL  "$BASE:$CONSOLE_PORT"
    env_set TRUST_DOWNLOAD_BASE_URL   "$BASE:$API_PORT"
    env_set TRUST_IDP_BASE_URLS       "$BASE:$IDP_PORT"
    env_set TRUST_OIDC_REDIRECT_URIS  "$BASE:$CONSOLE_PORT/auth/callback,http://127.0.0.1:8765/callback"
    # Server-side (host networking): siempre loopback.
    env_set TRUST_API_BASE_URL        "http://127.0.0.1:$API_PORT"
    env_set TRUST_KEYCLOAK_ADMIN_URL  "http://127.0.0.1:$IDP_PORT"
    # Publica IdP y API en todas las interfaces (el navegador del cliente
    # debe llegar por la URL pública). La DB SIGUE solo en 127.0.0.1.
    env_set TRUST_IDP_BIND            "0.0.0.0"
    env_set TRUST_API_HOST            "0.0.0.0"
    echo "[setup] URLs públicas derivadas de TRUST_PUBLIC_BASE_URL=$BASE"
  fi
fi

# --- 4) build + up -------------------------------------------------------------
echo "[setup] docker compose build (primera vez ~3-5 min)..."
docker compose build
echo "[setup] docker compose up -d ..."
docker compose up -d

# --- 5) espera de salud --------------------------------------------------------
API_PORT="$(env_get TRUST_API_PORT)"; API_PORT="${API_PORT:-8000}"
echo "[setup] esperando salud del control plane (hasta 5 min, el IdP tarda la primera vez)..."
healthy=0
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null "http://127.0.0.1:$API_PORT/healthz"; then healthy=1; break; fi
  sleep 5
done
if [ "$healthy" != 1 ]; then
  echo "[setup] ERROR: el API no respondió healthz en 5 min." >&2
  echo "[setup] Diagnóstico: docker compose ps && docker compose logs --tail 50 api migrate" >&2
  exit 1
fi
# migrate debe haber terminado bien (compose ya lo garantiza con
# service_completed_successfully; el healthcheck del api es la prueba real).

# --- 6) contraseña one-time del super-user del proveedor -----------------------
OTA="$(docker logs "$PREFIX-api" 2>&1 | grep -m1 'PROVIDER OPERATOR ONE-TIME PASSWORD' || true)"
echo
echo "=============================================="
if [ -n "$OTA" ]; then
  echo "$OTA"
  echo "  → Cámbiala en el primer login del Provider Console."
  echo "  → (Si vas a correr el smoke ahora, se usa automáticamente.)"
else
  echo "[setup] El super-user del proveedor ya existía (bootstrap previo):"
  echo "        usa tu contraseña actual del Provider Console."
fi
echo "=============================================="

# --- 7) smoke ------------------------------------------------------------------
echo "[setup] corriendo smoke de verificación..."
SMOKE_PROVIDER_PASSWORD="$(echo "$OTA" | sed -n 's/.*password=\([^ ]*\).*/\1/p')"
if [ -n "$SMOKE_PROVIDER_PASSWORD" ]; then
  export SMOKE_PROVIDER_PASSWORD
fi
scripts/smoke.sh
echo
echo "[setup] Listo. Provider Console: http://127.0.0.1:$(env_get TRUST_PROVIDER_CONSOLE_PORT || echo 3200)/login"
echo "[setup]                Admin Console: http://127.0.0.1:$(env_get TRUST_CONSOLE_PORT || echo 3100)/login"

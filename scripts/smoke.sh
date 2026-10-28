#!/usr/bin/env bash
# smoke.sh — verificación de que el stack levantó bien (t_b62edff2).
# Seis checks reales, nada simulado; sale con código != 0 si algo falla.
#
# Uso: scripts/smoke.sh
#   SMOKE_PROVIDER_PASSWORD=... scripts/smoke.sh   # habilita el check 6
set -euo pipefail
cd "$(dirname "$0")/.."

# Puertos desde .env (defaults = canónicos del runbook).
port_of() { sed -n "s|^$1=||p" .env 2>/dev/null | head -1; }
API_PORT="$(port_of TRUST_API_PORT)";            API_PORT="${API_PORT:-8000}"
IDP_PUBLIC="$(port_of TRUST_IDP_PUBLIC_URL)";    IDP_PUBLIC="${IDP_PUBLIC:-http://127.0.0.1:8080}"
CONSOLE_PUBLIC="$(port_of TRUST_CONSOLE_PUBLIC_URL)"; CONSOLE_PUBLIC="${CONSOLE_PUBLIC:-http://127.0.0.1:3100}"
PCONSOLE_PORT="$(port_of TRUST_PROVIDER_CONSOLE_PORT)"; PCONSOLE_PORT="${PCONSOLE_PORT:-3200}"
LITELLM_URL="$(port_of TRUST_LITELLM_BASE_URL)"; LITELLM_URL="${LITELLM_URL:-http://127.0.0.1:39989}"

PASS=0; FAIL=0
check() { # $1=nombre  $2=comando
  if eval "$2" >/dev/null 2>&1; then
    echo "PASS  $1"; PASS=$((PASS+1))
  else
    echo "FAIL  $1"; FAIL=$((FAIL+1))
  fi
}

echo "== Smoke Trust Agent =="

# 1) Control plane vivo y con DB arriba (fail-closed: sin DB no dice ok).
check "healthz del API (status=ok, database=up)" \
  "curl -sf http://127.0.0.1:$API_PORT/healthz | grep -q '\"status\":\"ok\"'"

# 2) IdP: discovery OIDC del realm master (Keycloak sirviendo).
check "IdP: discovery OIDC del realm master (200)" \
  "curl -sf -o /dev/null '$IDP_PUBLIC/realms/master/.well-known/openid-configuration'"

# 3) Admin Console: página de login.
check "Admin Console: /login responde (200)" \
  "curl -sf -o /dev/null '$CONSOLE_PUBLIC/login'"

# 4) Provider Console: página de login.
check "Provider Console: /login responde (200)" \
  "curl -sf -o /dev/null http://127.0.0.1:$PCONSOLE_PORT/login"

# 5) Plano de control de modelos:
#    - doble de tests (:39989): debe rechazar sin master key (401 = vivo y cerrado)
#    - proxy real (perfil litellm): /health/liveliness debe dar 200
if [[ "$LITELLM_URL" == *:39989* ]]; then
  check "LiteLLM double: rechaza sin master key (401)" \
    "test \"\$(curl -s -o /dev/null -w '%{http_code}' '$LITELLM_URL/key/info?key=x')\" = 401"
else
  check "LiteLLM real: /health/liveliness (200)" \
    "curl -sf -o /dev/null '$LITELLM_URL/health/liveliness'"
fi

# 6) Login REAL del super-user del proveedor (DB + API + BFF del Provider
#    Console de punta a punta). Requiere la contraseña one-time del
#    bootstrap (setup.sh la pasa automáticamente) o la vigente.
if [ -n "${SMOKE_PROVIDER_PASSWORD:-}" ]; then
  USER="$(sed -n 's|^TRUST_PROVIDER_OPERATOR_USERNAME=||p' .env 2>/dev/null | head -1)"; USER="${USER:-provider-admin}"
  check "Login del super-user del proveedor (200 ok=true)" \
    "curl -sf -X POST http://127.0.0.1:$PCONSOLE_PORT/auth/provider/login \
       -H 'Content-Type: application/json' \
       -d '{\"username\":\"$USER\",\"password\":\"$SMOKE_PROVIDER_PASSWORD\"}' | grep -q '\"ok\":true'"
else
  echo "SKIP  Login del super-user del proveedor (sin SMOKE_PROVIDER_PASSWORD;"
  echo "      setup.sh lo pasa solo en el primer arranque)"
fi

echo "== Resultado: $PASS PASS / $FAIL FAIL =="
[ "$FAIL" -eq 0 ] || exit 1

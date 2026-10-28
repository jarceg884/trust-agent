#!/usr/bin/env bash
# Arranca el proxy LiteLLM REAL del Trust Agent (t_c4766c4d).
# - venv propio (litellm 1.106.3 PINNEADA — ver requirements.txt)
# - PostgreSQL dedicado trust-agent-litellm-db :55433 (budgets; sin DB
#   fallan abiertos)
# - escucha SOLO en 127.0.0.1:4000
# Uso: ./start.sh [--foreground]
set -euo pipefail
cd "$(dirname "$0")"
if ! docker ps --format '{{.Names}}' | grep -q '^trust-agent-litellm-db$'; then
  echo "[start] levantando PostgreSQL dedicado del proxy (55433)..."
  docker start trust-agent-litellm-db
  sleep 3
fi
set -a; . ./.env; set +a
if [ "${1:-}" = "--foreground" ]; then
  exec ./venv/bin/litellm --config config.yaml --host 127.0.0.1 --port 4000
fi
echo "[start] litellm real en http://127.0.0.1:4000 (logs: litellm.log)"
exec ./venv/bin/litellm --config config.yaml --host 127.0.0.1 --port 4000 >> litellm.log 2>&1

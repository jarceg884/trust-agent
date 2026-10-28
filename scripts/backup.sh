#!/usr/bin/env bash
# backup.sh — respaldo de la DB del Trust Agent (t_b62edff2, recuadro Miller:
# un operador externo lo pide primero). Formato custom de pg_dump (-Fc):
# comprimido y restaurable con pg_restore (scripts/restore.sh).
#
# Uso:
#   scripts/backup.sh                    # → trust-agent-backup-YYYYMMDD-HHMMSS.dump
#   scripts/backup.sh /ruta/mi.dump      # ruta explícita
set -euo pipefail
cd "$(dirname "$0")/.."

PREFIX="${TRUST_CONTAINER_PREFIX:-trust-agent}"
OUT="${1:-trust-agent-backup-$(date +%Y%m%d-%H%M%S).dump}"

# -Fc: formato custom (comprimido, pg_restore). El dump sale del contenedor
# por stdout — nunca queda dentro del volumen.
docker exec "$PREFIX-db" pg_dump -U postgres -d trust_agent -Fc > "$OUT"
SIZE="$(du -h "$OUT" | cut -f1)"
echo "[backup] $OUT ($SIZE)"
echo "[backup] Verificación de integridad del dump:"
# pg_restore corre DENTRO del contenedor postgres (hallazgo de la evidencia
# fresh-clone: el host del cliente no necesita client-tools de PostgreSQL).
docker exec -i "$PREFIX-db" pg_restore --list < "$OUT" >/dev/null \
  && echo "[backup] OK: catálogo legible (restore seguro con scripts/restore.sh)"

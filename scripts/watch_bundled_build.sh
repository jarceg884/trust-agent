#!/usr/bin/env bash
# Publica el instalador FULL (bundled) de Windows cuando termina el workflow
# de GitHub Actions, actualiza el manifiesto del tenant demo-miller y avisa
# por WhatsApp y Telegram. Corre desacoplado del desktop: sobrevive aunque el
# usuario cierre su laptop.
set -uo pipefail

REPO="jarceg884/trust-agent-build"
RUN_ID="${RUN_ID:?RUN_ID required}"
VERSION="${VERSION:-v0.24.8}"
ART_DIR="/root/trust-agent/installer-artifacts/demo-miller/0.6.0"
LOG="/root/.hermes/cache/scratch/bundled_publish.log"
mkdir -p "$ART_DIR"

exec >>"$LOG" 2>&1
echo "===== $(date -Is) watching run $RUN_ID ====="

notify() {
  local text="$1"
  curl -sS -X POST http://127.0.0.1:3005/send \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"chatId":"50670219002@s.whatsapp.net","message":sys.argv[1]}))' "$text")" || true
  local token
  token=$(grep '^TELEGRAM_BOT_TOKEN=' /root/.hermes/.env | cut -d= -f2-)
  curl -sS -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    -d chat_id=2005116918 --data-urlencode "text=$text" || true
}

for i in $(seq 1 300); do
  read -r status conclusion <<<"$(gh run view "$RUN_ID" --repo "$REPO" --json status,conclusion --jq '[.status,.conclusion] | @tsv' 2>/dev/null || echo 'unknown unknown')"
  echo "$(date -Is) status=$status conclusion=$conclusion"
  case "$status" in
    completed) break ;;
  esac
  sleep 60
done

if [[ "${status:-}" != "completed" || "${conclusion:-}" != "success" ]]; then
  echo "BUILD FAILED: status=$status conclusion=$conclusion"
  notify "Trust Agent: el build FULL de Windows falló (run $RUN_ID, estado ${conclusion:-unknown}). Sigo revisando; te aviso cuando haya una versión funcional."
  exit 1
fi

WORK="/root/.hermes/cache/scratch/bundled-artifact-$RUN_ID"
rm -rf "$WORK"; mkdir -p "$WORK"
gh run download "$RUN_ID" --repo "$REPO" -n trust-agent-windows -D "$WORK"
EXE=$(find "$WORK" -type f -iname '*.exe' | head -1 || true)
if [[ -z "$EXE" || ! -f "$EXE" ]]; then
  echo "No .exe in artifact"
  notify "Trust Agent: el build FULL terminó pero el artefacto no trae .exe. Revisando."
  exit 1
fi

DEST="$ART_DIR/trust-agent-desktop-demo-miller-$VERSION-bundled-win-x64.exe"
cp -f "$EXE" "$DEST"
SHA=$(sha256sum "$DEST" | awk '{print $1}')
SIZE=$(stat -c%s "$DEST")
BUILT=$(date -u +%Y-%m-%dT%H:%M:%S+0000)
python3 - "$DEST" "$SHA" "$SIZE" "$BUILT" "$VERSION" <<'PY'
import json, sys
from pathlib import Path
dest, sha, size, built, version = sys.argv[1:6]
p = Path('/root/trust-agent/installer-artifacts/demo-miller/0.6.0/installer-manifest.json')
d = json.loads(p.read_text())
d['built_at'] = built
d['git_rev'] = f'trust-agent-desktop-official-{version}-bundled'
d['desktop_app'] = ('official NousResearch/hermes-agent apps/desktop, built as trust variant with '
                    'FULL bundled agent payload — governed activation + enrollment, tenant branding/logo, '
                    'policy bundle signing, tool veto, audit, Windows-only downloads.')
d.setdefault('platforms', {})['windows'] = {'filename': Path(dest).name, 'sha256': sha, 'size': int(size)}
p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + '\n')
print('manifest updated', p)
PY

# Verificación real de la descarga pública (HTTP status).
CODE=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/download/demo-miller/installer/windows)
echo "download HTTP $CODE sha=$SHA size=$SIZE"
PAGE_CODE=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/download/demo-miller)
echo "page HTTP $PAGE_CODE"

if [[ "$CODE" != "200" || "$PAGE_CODE" != "200" ]]; then
  notify "Trust Agent: el instalador FULL se construyó pero la descarga no responde 200 (installer=$CODE page=$PAGE_CODE). Revisando."
  exit 1
fi

notify "✅ Trust Agent FULL para Windows listo y publicado ($VERSION bundled). Descárgalo en http://192.168.0.23:8000/download/demo-miller — incluye gobernanza, enrolamiento obligatorio, branding y logo del tenant. SHA256: $SHA"
echo "DONE $(date -Is) $DEST"

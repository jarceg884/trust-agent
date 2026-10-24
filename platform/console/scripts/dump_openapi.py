#!/usr/bin/env python3
"""Vuelca el OpenAPI del control plane a platform/console/openapi.json.

Referencia de los tipos de src/lib/types.ts (spec 009 D-AC-03). Requiere el
venv de platform/ y env mínimos de settings (no conecta a la DB: solo importa
la app para app.openapi()).

Uso:
    cd /root/trust-agent/platform
    TRUST_DATABASE_URL="postgresql+psycopg://x:x@127.0.0.1:1/x" \
    TRUST_DEVICE_TOKEN_SECRET="dump-only" \
    TRUST_VAULT_KEK_B64="$(python3 -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())')" \
    .venv/bin/python console/scripts/dump_openapi.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from app.main import app  # noqa: E402

spec = app.openapi()
out = pathlib.Path(__file__).resolve().parent.parent / "openapi.json"
out.write_text(json.dumps(spec, indent=2) + "\n")
print(f"paths: {len(spec.get('paths', {}))} · schemas: {len(spec.get('components', {}).get('schemas', {}))} · {out}")

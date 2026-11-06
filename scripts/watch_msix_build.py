#!/usr/bin/env python3
"""Watch the Trust Agent MSIX build and notify WhatsApp when it finishes.

The desktop chat is not guaranteed to stay open; this process runs on the
server and sends the completion message independently. It also downloads the
MSIX and certificate into the local artifact directory so they are ready for
the next step.
"""
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

RUN_ID = "42079589539"
REPO = "jarceg884/trust-agent-build"
ARTIFACT_ROOT = Path("/root/trust-agent/installer-artifacts/demo-miller/0.6.0/msix")
STATUS_FILE = ARTIFACT_ROOT / "msix-build-status.json"
WHATSAPP_URL = "http://127.0.0.1:3005/send"
CHAT_ID = "50670219002@s.whatsapp.net"


def gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        cwd="/root",
        text=True,
        capture_output=True,
        timeout=120,
    )


def send_whatsapp(message: str) -> None:
    payload = json.dumps({"chatId": CHAT_ID, "message": message}).encode()
    req = urllib.request.Request(
        WHATSAPP_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            print("whatsapp", response.status, response.read().decode()[:200], flush=True)
    except Exception as exc:
        print("whatsapp error", exc, flush=True)


def write_status(status: dict) -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    print("watching", RUN_ID, flush=True)
    while True:
        probe = gh("run", "view", RUN_ID, "--repo", REPO, "--json", "status,conclusion")
        if probe.returncode != 0:
            print("gh probe failed:", probe.stderr[-500:], flush=True)
            time.sleep(30)
            continue
        try:
            state = json.loads(probe.stdout)
        except json.JSONDecodeError:
            print("bad gh json", probe.stdout[:500], flush=True)
            time.sleep(30)
            continue
        status = state.get("status")
        conclusion = state.get("conclusion") or ""
        print("state", status, conclusion, flush=True)
        if status == "completed":
            message = ""
            if conclusion == "success":
                # Download both artifacts. The MSIX can be large; gh handles it.
                downloads = [
                    ("trust-agent-windows-msix", "msix-files"),
                    ("trust-agent-msix-cert", "cert-files"),
                ]
                ok = True
                for artifact, folder in downloads:
                    dest = ARTIFACT_ROOT / folder
                    if dest.exists():
                        shutil.rmtree(dest, ignore_errors=True)
                    dest.mkdir(parents=True, exist_ok=True)
                    got = gh("run", "download", RUN_ID, "--repo", REPO, "-n", artifact, "-D", str(dest))
                    print("download", artifact, got.returncode, got.stderr[-300:], flush=True)
                    ok = ok and got.returncode == 0
                if ok:
                    write_status({"run_id": RUN_ID, "status": "success", "conclusion": conclusion})
                    message = (
                        "✅ MSIX de Shawm Agent listo. Build 42076471249 completado con éxito. "
                        "El .msix y el .cer ya están descargados en el servidor; te los paso en el chat."
                    )
                else:
                    write_status({"run_id": RUN_ID, "status": "success_download_failed", "conclusion": conclusion})
                    message = (
                        "✅ Build MSIX de Shawm Agent completado, pero falló la descarga local "
                        "de los artefactos. Reviso el servidor."
                    )
            else:
                write_status({"run_id": RUN_ID, "status": "failed", "conclusion": conclusion})
                message = (
                    f"❌ MSIX de Shawm Agent falló (build {RUN_ID}, {conclusion}). "
                    "No instalar todavía; estoy revisando los logs."
                )
            send_whatsapp(message)
            return
        time.sleep(60)


if __name__ == "__main__":
    main()

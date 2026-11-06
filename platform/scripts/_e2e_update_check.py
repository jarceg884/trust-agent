import json
import subprocess
from sqlalchemy import text
from app.db import get_engine, set_tenant
from app.config import get_settings
from app.devices.tokens import issue_device_token

TENANT = "e406274e-dcbd-408f-abc1-4953fc3f22b3"
ADMIN = "04405b1b-591c-4469-8a01-f303ec2598d1"
engine = get_engine()
settings = get_settings()
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    row = conn.execute(
        text("SELECT id FROM devices WHERE tenant_id=CAST(:t AS uuid) ORDER BY enrolled_at DESC LIMIT 1"),
        {"t": TENANT},
    ).mappings().first()
    if row is None:
        row = conn.execute(
            text(
                "INSERT INTO devices (tenant_id, user_id, fingerprint, device_name, platform, status, approved_at) "
                "VALUES (CAST(:t AS uuid), CAST(:u AS uuid), 'e2e-update-check-fp', 'E2E Update Check', 'Linux', 'active', now()) "
                "RETURNING id"
            ),
            {"t": TENANT, "u": ADMIN},
        ).mappings().first()
    else:
        conn.execute(
            text("UPDATE devices SET status='active', approved_at=now() WHERE id=CAST(:id AS uuid)"),
            {"id": str(row["id"])},
        )
    device_id = str(row["id"])

token = issue_device_token(settings, device_id=device_id, user_id=ADMIN, tenant_id=TENANT)
print("DEVICE_ID=" + device_id)
print("TOKEN=" + token)

# --- real HTTP calls against the update-check endpoint ---------------------
import httpx

base = "http://127.0.0.1:8000"
headers = {"Authorization": f"Bearer {token}", "X-Device-Token": token}

r1 = httpx.get(f"{base}/api/me/update-check", params={"current_git_rev": "deadbee", "platform": "windows"}, headers=headers, timeout=10)
print("no-manifest-yet status=", r1.status_code, r1.json())

# Now write a REAL installer-manifest.json for this tenant so "published" path is exercised.
import pathlib
artifacts_dir = pathlib.Path(settings.installer_artifacts_dir) / "demo-miller"
plugin_dir = pathlib.Path(settings.hermes_plugin_dir)
import re
version = re.search(r"^version:\s*(\S+)", (plugin_dir / "plugin.yaml").read_text(), re.M).group(1)
version_dir = artifacts_dir / version
version_dir.mkdir(parents=True, exist_ok=True)
manifest = {
    "slug": "demo-miller", "plugin_version": version, "built_at": "2026-11-04T00:00:00Z",
    "git_rev": "2ee433a",
    "platforms": {"windows": {"filename": "fake.exe", "sha256": "abc123", "size": 42}},
}
(version_dir / "installer-manifest.json").write_text(json.dumps(manifest))
(version_dir / "fake.exe").write_text("fake binary content")

r2 = httpx.get(f"{base}/api/me/update-check", params={"current_git_rev": "deadbee", "platform": "windows"}, headers=headers, timeout=10)
print("published-diff status=", r2.status_code, r2.json())

r3 = httpx.get(f"{base}/api/me/update-check", params={"current_git_rev": "2ee433a", "platform": "windows"}, headers=headers, timeout=10)
print("published-same status=", r3.status_code, r3.json())

ok = (
    r1.status_code == 200 and r1.json()["update_available"] is False
    and r2.status_code == 200 and r2.json()["update_available"] is True
    and r2.json()["download_url"] is not None
    and r3.status_code == 200 and r3.json()["update_available"] is False
)
print("ALL_PASS=" + str(ok))

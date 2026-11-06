import json
from sqlalchemy import text
from app.db import get_engine, set_tenant
from app.auth.service import Principal, RoleAssignment
from app.devices import service as devices_service
from app.devices.heartbeat import HeartbeatBody, record_heartbeat
from app.governance.bundle import compile_bundle_payload
from app.org.router import delete_user
from app.policy_bundles.router import effective_bundle_for_user

TENANT = "e406274e-dcbd-408f-abc1-4953fc3f22b3"
ADMIN = "04405b1b-591c-4469-8a01-f303ec2598d1"
p = Principal(
    tenant_id=TENANT, tenant_slug="demo-miller", tenant_name="Miller Demo Corp",
    user_id=ADMIN, email="admin@demo-miller.local", display_name="Admin",
    department_id=None, roles=[RoleAssignment(role_key="org_admin")],
    permissions={"policies.manage", "users.manage", "devices.manage"}, mfa_verified=True,
)

engine = get_engine()
results = {}

# 1) Policy Studio bundle
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    conn.execute(text("""
        INSERT INTO policy_bundles (tenant_id, name, description, desktop, credentials,
          global_prompt, profiles, department_id, created_by)
        VALUES (CAST(:t AS uuid), 'E2E · Operaciones', 'E2E validation',
          CAST(:desktop AS jsonb), CAST(:creds AS jsonb), :prompt,
          CAST(:profiles AS jsonb), NULL, CAST(:by AS uuid))
        ON CONFLICT (tenant_id, name) DO UPDATE SET global_prompt=EXCLUDED.global_prompt,
          desktop=EXCLUDED.desktop, updated_at=now()
    """), {
        "t": TENANT,
        "desktop": json.dumps({"browser": False, "voice": False, "model_main": True}),
        "creds": json.dumps({"allow_credential_vault": True}),
        "prompt": "E2E ORG PROMPT — la organización manda.",
        "profiles": json.dumps([{"name": "Operaciones", "model": "glm-5.2"}]),
        "by": ADMIN,
    })
    eb = effective_bundle_for_user(conn, ADMIN) or {}
    results["policy_bundle_prompt_ok"] = eb.get("global_prompt") == "E2E ORG PROMPT — la organización manda."
    results["policy_bundle_ui_ok"] = eb.get("desktop", {}).get("browser") is False

# 2) Enrollment: preauthorize commits, then exchange in its own transaction
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    pre = devices_service.preauthorize(conn, tenant_id=TENANT, user_id=ADMIN, actor=p,
                                       device_name="E2E Device")
out = devices_service.exchange(engine, pre["enrollment_token"],
                               fingerprint="E2E-FP-001", device_name="E2E Device",
                               platform="Linux")
results["enrollment_active"] = out.get("status") == "active"
results["enrollment_has_device_token"] = bool(out.get("device_token"))
results["enrollment_has_model_key"] = bool(out.get("model_key"))
DEVICE = out["device_id"]

# 3) Gateway heartbeat -> presence
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    hb = record_heartbeat(conn, principal=p, device_id=DEVICE,
                          body=HeartbeatBody(applied_policy_version=1, uptime_seconds=60, mode="normal"))
    results["heartbeat_ok"] = bool(hb)
    hbrow = conn.execute(text(
        "SELECT last_heartbeat_at IS NOT NULL AS has_hb FROM devices WHERE id=CAST(:d AS uuid)"),
        {"d": DEVICE}).mappings().first()
    results["gateway_connected_to_admin"] = bool(hbrow and hbrow["has_hb"])

# 4) Bundle payload (compiler output)
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    payload = compile_bundle_payload(conn, p, device_id=DEVICE)
    results["bundle_global_prompt"] = payload.get("global_prompt") == "E2E ORG PROMPT — la organización manda."
    results["bundle_ui_features"] = payload.get("ui_features", {}).get("browser") is False

# 5) Delete device
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    devices_service.delete(conn, principal=p, device_id=DEVICE)
    results["device_delete_ok"] = conn.execute(
        text("SELECT count(*) FROM devices WHERE id=CAST(:d AS uuid)"), {"d": DEVICE}).scalar() == 0

# 6) Delete user (throwaway)
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    uid = conn.execute(text("""
        INSERT INTO users (tenant_id, email, display_name, status)
        VALUES (CAST(:t AS uuid), 'e2e-delete@demo-miller.local', 'E2E Delete', 'active')
        RETURNING id
    """), {"t": TENANT}).scalar()
    delete_user(str(uid), principal=p, conn=conn)
    results["user_delete_ok"] = conn.execute(
        text("SELECT count(*) FROM users WHERE id=CAST(:u AS uuid)"), {"u": str(uid)}).scalar() == 0

for key, value in results.items():
    print(f"{key}={'PASS' if value else 'FAIL'}")
print("ALL_PASS=" + str(all(results.values())))

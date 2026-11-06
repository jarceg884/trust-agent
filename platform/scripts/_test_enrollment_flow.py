from app.db import get_engine, set_tenant
from app.auth.service import Principal, RoleAssignment
from app.devices import service

TENANT = "e406274e-dcbd-408f-abc1-4953fc3f22b3"
ADMIN = "04405b1b-591c-4469-8a01-f303ec2598d1"
p = Principal(
    tenant_id=TENANT,
    tenant_slug="demo-miller",
    tenant_name="Miller Demo Corp",
    user_id=ADMIN,
    email="admin@demo-miller.local",
    display_name="Admin",
    department_id=None,
    roles=[RoleAssignment(role_key="org_admin")],
    permissions={"devices.manage"},
    mfa_verified=True,
)

engine = get_engine()
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    res = service.preauthorize(
        conn, tenant_id=TENANT, user_id=ADMIN, actor=p, device_name="Prueba Helios-flow"
    )

print("PREAUTH_DEVICE:", res["device_id"])
print("PREAUTH_TOKEN_PREFIX:", res["enrollment_token"][:20] + "...")
print("PREAUTH_STATUS:", res["status"])

out = service.exchange(
    engine,
    res["enrollment_token"],
    fingerprint="TESTHELIOS-0001",
    public_key=None,
    device_name="Prueba Helios-flow",
    platform="Windows 11",
)
print("EXCHANGE_STATUS:", out.get("status"))
print("EXCHANGE_DEVICE:", out.get("device_id"))
print("EXCHANGE_HAS_DEVICE_TOKEN:", bool(out.get("device_token")))
print("EXCHANGE_MODEL_KEY_PRESENT:", bool(out.get("model_key")))
print("EXCHANGE_ALLOWED_MODELS:", out.get("allowed_models"))

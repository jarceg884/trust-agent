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
        conn, tenant_id=TENANT, user_id=ADMIN, actor=p, device_name="Laptop Miller"
    )

print("TOKEN=" + res["enrollment_token"])
print("DEVICE=" + res["device_id"])

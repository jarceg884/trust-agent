import json
from sqlalchemy import text
from app.db import get_engine, set_tenant
from app.auth.service import Principal, RoleAssignment
from app.policy_bundles.router import BundleBody, create_bundle, update_bundle, effective_bundle_for_user
from app.governance.bundle import compile_bundle_payload
from app.devices import service as devices_service

TENANT = "e406274e-dcbd-408f-abc1-4953fc3f22b3"
ADMIN = "04405b1b-591c-4469-8a01-f303ec2598d1"
p = Principal(tenant_id=TENANT, tenant_slug="demo-miller", tenant_name="Miller Demo Corp",
              user_id=ADMIN, email="admin@demo-miller.local", display_name="Admin",
              department_id=None, roles=[RoleAssignment(role_key="org_admin")],
              permissions={"policies.manage"}, mfa_verified=True)

engine = get_engine()
results = {}
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    # wipe policies from earlier demos
    conn.execute(text("DELETE FROM policy_bundles"))
    body = BundleBody(
        name="Admin · Full",
        description="Política personal del admin (demo E2E)",
        desktop={"browser": True, "terminal": True, "model_main": False, "vault": True},
        credentials={"allow_credential_vault": True},
        global_prompt="PROMPT ORG E2E — organización primero.",
        profiles=[],
        models=["deepseek-v4.1-flash-1017", "glm-5.2"],
        default_model="deepseek-v4.1-flash-1017",
        agents=[],
        department_id=None,
        user_id=ADMIN,
    )
    created = create_bundle(body, principal=p, conn=conn)
    results["created_user_scope"] = created["user_id"] == ADMIN and created["department_id"] is None
    results["models_saved"] = created["models"] == ["deepseek-v4.1-flash-1017", "glm-5.2"]
    results["default_model_saved"] = created["default_model"] == "deepseek-v4.1-flash-1017"

    effective = effective_bundle_for_user(conn, ADMIN) or {}
    results["effective_user_policy"] = effective.get("name") == "Admin · Full"

    payload = compile_bundle_payload(conn, p, device_id="00000000-0000-0000-0000-000000000001")
    results["bundle_policy_models"] = payload.get("policy_models") == ["deepseek-v4.1-flash-1017", "glm-5.2"]
    results["bundle_default_model"] = payload.get("policy_default_model") == "deepseek-v4.1-flash-1017"
    results["bundle_ui_features"] = payload.get("ui_features", {}).get("browser") is True

    body.name = "Admin · Full v2"
    updated = update_bundle(created["id"], body, principal=p, conn=conn)
    results["update_ok"] = updated["name"] == "Admin · Full v2"

    # department + user both -> validation error
    try:
        bad = BundleBody(name="Bad", department_id="11111111-1111-1111-1111-111111111111", user_id=ADMIN)
        create_bundle(bad, principal=p, conn=conn)
        results["scope_rejects_both"] = False
    except Exception:
        results["scope_rejects_both"] = True

    # per-user enrollment token
    token = devices_service.preauthorize(conn, tenant_id=TENANT, user_id=ADMIN, actor=p, device_name="User token E2E")
    results["user_token_issued"] = bool(token.get("enrollment_token"))

for k, v in results.items():
    print(f"{k}={'PASS' if v else 'FAIL'}")
print("ALL_PASS=" + str(all(results.values())))

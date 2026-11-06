import json
from sqlalchemy import text
from app.db import get_engine, set_tenant
from app.auth.service import Principal, RoleAssignment
from app.policy_bundles.router import BundleBody, create_bundle
from app.governance.bundle import compile_bundle_payload

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
    conn.execute(text("DELETE FROM policy_bundles"))
    body = BundleBody(
        name="Admin · Contracts",
        description="E2E contracts",
        desktop={"cli": False, "cli_terminal": False, "browser": True},
        credentials={"vault_secret_ids": ["secret-a", "secret-b"]},
        contracts=[{"name": "Uso aceptable", "text": "No reveles secretos."}, {"name": "Escalación", "text": "Pregunta al admin."}],
        global_prompt="ignored fallback",
        models=["deepseek-v4.1-flash-1017"], default_model="deepseek-v4.1-flash-1017",
        agents=[], department_id=None, user_id=ADMIN,
    )
    created = create_bundle(body, principal=p, conn=conn)
    results["contracts_saved"] = len(created["contracts"]) == 2
    results["vault_secret_saved"] = created["credentials"]["vault_secret_ids"] == ["secret-a", "secret-b"]
    payload = compile_bundle_payload(conn, p, device_id="00000000-0000-0000-0000-000000000001")
    results["payload_contracts"] = len(payload.get("policy_contracts", [])) == 2
    results["cli_disables_tools"] = payload.get("allowed_tools") == []
for k, v in results.items():
    print(f"{k}={'PASS' if v else 'FAIL'}")
print("ALL_PASS=" + str(all(results.values())))

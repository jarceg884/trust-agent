import json
from sqlalchemy import text
from app.db import get_engine, set_tenant
from app.auth.service import Principal, RoleAssignment
from app.policy_bundles.router import BundleBody, create_bundle
from app.governance.bundle import compile_bundle_payload
from app.vault import service as vault_service

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

    # --- Vault folders -----------------------------------------------------
    conn.execute(text("DELETE FROM vault_credentials WHERE user_id = CAST(:u AS uuid)"), {"u": ADMIN})
    conn.execute(text("DELETE FROM vault_folders WHERE user_id = CAST(:u AS uuid)"), {"u": ADMIN})
    folder = vault_service.create_folder(conn, p, name="Produccion")
    sub = vault_service.create_folder(conn, p, name="DB", parent_folder_id=folder["id"])
    results["folder_nested"] = sub["parent_folder_id"] == folder["id"]

    secret = vault_service.create_credential(
        conn, p, name="E2E Secret", credential_type="api_key",
        secret_value="s3cr3t", folder_id=folder["id"],
    )
    results["secret_in_folder"] = secret["metadata"]["folder_id"] == folder["id"]

    moved = vault_service.move_credential(conn, p, secret["credential_id"], folder_id=sub["id"])
    results["secret_moved"] = moved["folder_id"] == sub["id"]

    try:
        vault_service.delete_folder(conn, p, folder["id"])
        results["delete_nonempty_blocked"] = False
    except Exception:
        results["delete_nonempty_blocked"] = True

    moved_back = vault_service.move_credential(conn, p, secret["credential_id"], folder_id=None)
    results["secret_back_to_root"] = moved_back["folder_id"] is None
    vault_service.delete_folder(conn, p, sub["id"])
    vault_service.delete_folder(conn, p, folder["id"])
    results["folders_deleted_when_empty"] = len(vault_service.list_folders(conn, p)) == 0

    # --- Policy with MULTIPLE contracts + per-secret vault access ---------
    conn.execute(text("DELETE FROM policy_bundles"))
    secret2 = vault_service.create_credential(
        conn, p, name="E2E Secret 2", credential_type="api_key", secret_value="k2",
    )
    body = BundleBody(
        name="Admin · Contracts v2",
        description="E2E",
        desktop={"cli": False, "browser": True},
        credentials={"vault_secret_ids": [secret["credential_id"]]},  # NOT secret2
        contracts=[
            {"name": "Uso aceptable", "text": "No reveles secretos."},
            {"name": "Escalación", "text": "Pregunta al admin."},
            {"name": "Tono", "text": "Formal y conciso."},
        ],
        global_prompt="ignored",
        models=["deepseek-v4.1-flash-1017"], default_model="deepseek-v4.1-flash-1017",
        agents=[], department_id=None, user_id=ADMIN,
    )
    created = create_bundle(body, principal=p, conn=conn)
    results["three_contracts_saved"] = len(created["contracts"]) == 3
    results["vault_scoped_to_one_secret"] = created["credentials"]["vault_secret_ids"] == [secret["credential_id"]]

    payload = compile_bundle_payload(conn, p, device_id="00000000-0000-0000-0000-000000000001")
    results["payload_three_contracts"] = len(payload.get("policy_contracts", [])) == 3
    results["cli_disables_tools"] = payload.get("allowed_tools") == []

for k, v in results.items():
    print(f"{k}={'PASS' if v else 'FAIL'}")
print("ALL_PASS=" + str(all(results.values())))

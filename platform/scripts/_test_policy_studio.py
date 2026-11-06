import json
from sqlalchemy import text
from app.db import get_engine, set_tenant
from app.auth.service import Principal, RoleAssignment
from app.policy_bundles.router import effective_bundle_for_user
from app.governance.bundle import compile_bundle_payload

TENANT = "e406274e-dcbd-408f-abc1-4953fc3f22b3"
ADMIN = "04405b1b-591c-4469-8a01-f303ec2598d1"

p = Principal(
    tenant_id=TENANT, tenant_slug="demo-miller", tenant_name="Miller Demo Corp",
    user_id=ADMIN, email="admin@demo-miller.local", display_name="Admin",
    department_id=None, roles=[RoleAssignment(role_key="org_admin")],
    permissions={"policies.manage"}, mfa_verified=True,
)

engine = get_engine()
with engine.begin() as conn:
    set_tenant(conn, TENANT)
    conn.execute(text("""
        INSERT INTO policy_bundles
          (tenant_id, name, description, desktop, credentials, global_prompt, profiles, department_id, created_by)
        VALUES (CAST(:t AS uuid), 'Operaciones · Estándar', 'Demo', CAST(:desktop AS jsonb),
                CAST(:creds AS jsonb), :prompt, CAST(:profiles AS jsonb), NULL, CAST(:by AS uuid))
        ON CONFLICT (tenant_id, name) DO UPDATE SET global_prompt = EXCLUDED.global_prompt,
          desktop = EXCLUDED.desktop, updated_at = now()
        """),
        {
            "t": TENANT,
            "desktop": json.dumps({"browser": False, "voice": False, "model_main": True, "terminal": False}),
            "creds": json.dumps({"allow_credential_vault": True, "allow_payment_autofill": False}),
            "prompt": "REGLA DE LA ORGANIZACIÓN: responde en español y nunca reveles credenciales.",
            "profiles": json.dumps([{"name": "Operaciones", "model": "glm-5.2"}]),
            "by": ADMIN,
        },
    )
    eb = effective_bundle_for_user(conn, ADMIN)
    print("EFFECTIVE_BUNDLE_PROMPT:", (eb or {}).get("global_prompt"))
    print("EFFECTIVE_DESKTOP:", (eb or {}).get("desktop"))

    payload = compile_bundle_payload(conn, p, device_id="00000000-0000-0000-0000-000000000001")
    print("PAYLOAD_GLOBAL_PROMPT:", payload.get("global_prompt"))
    print("PAYLOAD_UI_FEATURES:", payload.get("ui_features"))
    print("PAYLOAD_POLICY_PROFILES:", payload.get("policy_profiles"))

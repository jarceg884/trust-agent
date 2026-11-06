import json
from sqlalchemy import text

from app.db import get_migrator_engine

TENANT = "e406274e-dcbd-408f-abc1-4953fc3f22b3"
ADMIN = "04405b1b-591c-4469-8a01-f303ec2598d1"
TARGET = "3f23a02f-f140-4748-bb8c-d65eacf2947a"
DESKTOP = {
    "model_main": False,
    "model_fallback": False,
    "model_moa": False,
    "chat_model_switch": False,
    "providers": False,
    "gateways": False,
    "vault": False,
    "browser": False,
    "voice": False,
    "terminal": False,
    "plugins": False,
    "memory": True,
    "sessions": True,
    "workspace": True,
    "capabilities": False,
    "messaging": False,
    "artifacts": False,
    "cron": False,
}
with get_migrator_engine().begin() as conn:
    existing = conn.execute(
        text("SELECT id FROM policy_bundles WHERE tenant_id=CAST(:t AS uuid) AND user_id=CAST(:u AS uuid)"),
        {"t": TENANT, "u": TARGET},
    ).mappings().first()
    if existing:
        print("EXISTING", existing["id"])
    else:
        row = conn.execute(
            text(
                "INSERT INTO policy_bundles "
                "(tenant_id,name,description,desktop,credentials,global_prompt,profiles,contracts,models,default_model,agents,department_id,user_id,created_by) "
                "VALUES (CAST(:t AS uuid), :name, :desc, CAST(:desktop AS jsonb), CAST(:creds AS jsonb), :prompt, CAST(:profiles AS jsonb), CAST(:contracts AS jsonb), CAST(:models AS jsonb), :default_model, CAST(:agents AS jsonb), NULL, CAST(:u AS uuid), CAST(:by AS uuid)) "
                "RETURNING id"
            ),
            {
                "t": TENANT,
                "name": "Demo Miller · Usuario gobernado",
                "desc": "Política de usuario de prueba: sin providers, modelos, gateways ni suscripciones editables.",
                "desktop": json.dumps(DESKTOP),
                "creds": json.dumps({"allow_credential_vault": False, "allow_payment_autofill": False}),
                "prompt": "Eres el asistente de la organización. No reveles información interna ni credenciales.",
                "profiles": json.dumps([]),
                "contracts": json.dumps([]),
                "models": json.dumps(["deepseek-v4.1-flash-1017"]),
                "default_model": "deepseek-v4.1-flash-1017",
                "agents": json.dumps([]),
                "u": TARGET,
                "by": ADMIN,
            },
        ).mappings().first()
        print("CREATED", row["id"])
print("DONE")

"""Migración 0042: default de vault_allowed_credential_types = los 4 tipos
reales soportados por el CHECK de vault_credentials.credential_type
(api_key/password/token/oauth2) — el default previo (api_key/oauth2) dejaba
"password"/"token" bloqueados en todo tenant nuevo sin razón funcional.
"""
from alembic import op
import sqlalchemy as sa

revision = "0042_vault_types_default"
down_revision = "0041_vault_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE catalog.policy_keys "
        "SET default_value = '[\"api_key\",\"password\",\"token\",\"oauth2\"]' "
        "WHERE key = 'vault_allowed_credential_types'"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "UPDATE catalog.policy_keys "
        "SET default_value = '[\"api_key\",\"oauth2\"]' "
        "WHERE key = 'vault_allowed_credential_types'"
    ))

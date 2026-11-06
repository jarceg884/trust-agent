"""Migración 0043: precio de cache-hit en el catálogo de modelos.

LiteLLM ya factura con ``cache_read_input_token_cost`` (el cost map real lo
trae para GLM/DeepSeek MaaS), pero ``catalog.model_catalog`` solo guardaba
input/output — la consola no podía mostrar el precio de caché y el admin veía
una tabla incompleta. Columna en centavos por millón, mismo criterio que las
otras dos (numeric(12,3), NULL = modelo sin cache-hit documentado).
"""
from alembic import op
import sqlalchemy as sa

revision = "0043_model_cache_price"
down_revision = "0042_vault_types_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE catalog.model_catalog "
        "ADD COLUMN IF NOT EXISTS cache_read_price_cents_per_m numeric(12,3)"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE catalog.model_catalog "
        "DROP COLUMN IF EXISTS cache_read_price_cents_per_m"
    ))

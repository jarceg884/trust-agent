"""Migración 0028: nullable_catalog_prices

Spec 018 §5 (D-018-07, GAP M-2 / t_bf7eff20). El refresh central da de alta
los deployments NUEVOS que aparecen en el GET {MAAS_ENDPOINT}/models vivo con
precios NULL: coste desconocido hasta que una migración los sella contra la
doc oficial (price-maas-0002). El CHECK >= 0 se conserva — NULL es
"desconocido", no "gratis" (mismo contrato visual "—" que los límites NULL
del Model List).

Hoy las columnas son NOT NULL y el alta automática revienta con
NotNullViolation (verificado en test). DROP NOT NULL en ambas columnas de
precio; paridad con db/schema.sql §3.3.

(Nota de serialización: el guard apiló esta revisión sobre
0027_usage_export_audit de una lane paralela; el archivo
0027_nullable_catalog_prices creado a mano fue retirado — única cabeza.)
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401

revision = "0028_nullable_catalog_prices"
down_revision = "0027_usage_export_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE catalog.model_catalog
          ALTER COLUMN input_price_cents_per_m DROP NOT NULL,
          ALTER COLUMN output_price_cents_per_m DROP NOT NULL
        """
    )


def downgrade() -> None:
    # Reverso exacto: ninguna fila central con precio NULL puede existir
    # antes del downgrade (el alta automática es la única fuente de NULL).
    op.execute(
        """
        UPDATE catalog.model_catalog
        SET input_price_cents_per_m = 0, output_price_cents_per_m = 0
        WHERE input_price_cents_per_m IS NULL OR output_price_cents_per_m IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE catalog.model_catalog
          ALTER COLUMN input_price_cents_per_m SET NOT NULL,
          ALTER COLUMN output_price_cents_per_m SET NOT NULL
        """
    )

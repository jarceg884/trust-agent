"""Migración 0029: sub_cent_spend_precision

Spec 005 §2 D-GOV-17 / REQ-GOV-016 (gap B-2, t_8bd1fee8): con precios reales
de MaaS una llamada chica cuesta fracciones de centavo (ej. 174 tokens
deepseek-v4-flash = $0.000045 = 0.0045¢) y `round(usd*100)` + columnas bigint
truncaban 9/10 filas del ledger a 0¢ — los presupuestos sub-contaban gasto
real y el corte llegaba tarde o nunca.

Ledger y counters pasan a `numeric(12,3)`: centavos con precisión de
milli-centavo (0.001¢), misma convención que los precios del catálogo de la
spec 018 (migración 0018, `numeric(12,3)`). Los nombres `*_cents` se
conservan — la unidad sigue siendo el centavo, solo se admite fracción.
`budgets.amount_cents` y `budget_counters.budget_cents` QUEDAN bigint: los
presupuestos los fija un admin en centavos enteros.

`USING col::numeric(12,3)` conserva los valores enteros existentes exactos.
Paridad con db/schema.sql §5.6/§5.7 (la exige db/tests/test_schema_parity.py).
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401

revision = "0029_sub_cent_spend_precision"
down_revision = "0028_nullable_catalog_prices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE spend_ledger
          ALTER COLUMN cost_base_cents TYPE numeric(12,3)
            USING cost_base_cents::numeric(12,3),
          ALTER COLUMN cost_billed_cents TYPE numeric(12,3)
            USING cost_billed_cents::numeric(12,3)
        """
    )
    op.execute(
        """
        ALTER TABLE budget_counters
          ALTER COLUMN spent_cents TYPE numeric(12,3)
            USING spent_cents::numeric(12,3)
        """
    )


def downgrade() -> None:
    # Reverso exacto: ninguna fila puede tener fracción sub-centavo antes del
    # downgrade (el ingest anterior a 0029 solo escribía enteros).
    op.execute(
        """
        ALTER TABLE budget_counters
          ALTER COLUMN spent_cents TYPE bigint
            USING spent_cents::bigint
        """
    )
    op.execute(
        """
        ALTER TABLE spend_ledger
          ALTER COLUMN cost_base_cents TYPE bigint
            USING cost_base_cents::bigint,
          ALTER COLUMN cost_billed_cents TYPE bigint
            USING cost_billed_cents::bigint
        """
    )

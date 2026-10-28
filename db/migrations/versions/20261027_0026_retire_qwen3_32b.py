"""Migración 0026: retire_qwen3_32b

Spec 018 §5 (D-018-07, GAP M-2 / t_bf7eff20). Verificación en vivo
2026-10-27T20:55Z: GET {MAAS_ENDPOINT}/models devuelve 6 modelos SIN
qwen3-32b (chat/completions → 404 ModelArts.81009 "Invalid model.") y CON
el deployment nuevo deepseek-v4.1-flash-1017.

1. qwen3-32b → status='stale' (retiro fail-closed: fuera de allowed-for-me,
   verify 403, plan standard deja de aprobarlo — provisioning resuelve
   WHERE status='available'). Las filas de model_access existentes se
   CONSERVAN (historia de aprobaciones; el gate por status las vuelve
   inefectivas). Si MaaS lo re-despliega, el refresh central lo restaura.
2. Alta de deepseek-v4.1-flash-1017 con metadatos oficiales documentados:
   display DeepSeek-V4.1-Flash-1017, contexto 1M, máx. output 384K
   (Model List intl, actualizado 2026-10-27 GMT+8), precios peor caso P1
   30/120 ¢/M (price-maas-0002, actualizado 2026-10-27 GMT+8).

Paridad con db/schema.sql (seed del catálogo central).
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401

revision = "0026_retire_qwen3_32b"
down_revision = "0025_device_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Retiro de qwen3-32b (stale, no DELETE: preserva FK de model_access
    #    y el ciclo de vida de spec 032 §2 — puede reaparecer upstream).
    op.execute(
        """
        UPDATE catalog.model_catalog
        SET status = 'stale', updated_at = now()
        WHERE model_name = 'qwen3-32b' AND source = 'central'
        """
    )
    # 2. Alta del deployment nuevo aparecido upstream el 2026-10-27.
    #    ON CONFLICT DO UPDATE: idempotente y sella metadatos oficiales si
    #    el refresh central ya lo hubiera insertado con precios NULL.
    op.execute(
        """
        INSERT INTO catalog.model_catalog
          (model_name, display_name, provider, context_tokens,
           max_output_tokens, input_price_cents_per_m,
           output_price_cents_per_m, status, source)
        VALUES
          ('deepseek-v4.1-flash-1017', 'DeepSeek-V4.1-Flash-1017',
           'huawei_maas', 1000000, 384000, 30.0, 120.0, 'available',
           'central')
        ON CONFLICT (model_name) WHERE source = 'central' DO UPDATE SET
          display_name = EXCLUDED.display_name,
          provider = EXCLUDED.provider,
          context_tokens = EXCLUDED.context_tokens,
          max_output_tokens = EXCLUDED.max_output_tokens,
          input_price_cents_per_m = EXCLUDED.input_price_cents_per_m,
          output_price_cents_per_m = EXCLUDED.output_price_cents_per_m,
          status = 'available',
          updated_at = now()
        """
    )


def downgrade() -> None:
    # Reverso exacto: eliminar el alta y restaurar qwen3-32b a available.
    op.execute(
        """
        DELETE FROM catalog.model_catalog
        WHERE model_name = 'deepseek-v4.1-flash-1017' AND source = 'central'
        """
    )
    op.execute(
        """
        UPDATE catalog.model_catalog
        SET status = 'available', updated_at = now()
        WHERE model_name = 'qwen3-32b' AND source = 'central'
        """
    )

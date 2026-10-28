"""model_catalog: glm-5.2 con pricing REAL de Huawei MaaS (t_c4766c4d).

Revision ID: 0008_model_catalog_glm52
Revises: 0007_spend_rejections
Create Date: 2026-10-24

La re-validación contra un proxy LiteLLM REAL (riesgo 2 de
docs/validacion-final.md §6) usa el modelo glm-5.2 de Huawei Cloud MaaS
ap-southeast-1 (verificado en vivo: GET /openai/v1/models lo lista y
responde chat/completions). El catálogo global solo tenía
qwen2.5-72b-instruct y deepseek-v3, que NO existen en el endpoint MaaS
actual — un tenant que los apruebe no podría ejercer el acceso contra un
gateway real.

Pricing REAL (support.huaweicloud.com/intl/en-us/price-maas/
price-maas-0002.html, USD por millón de tokens, Periodo 1
08:00-20:59 GMT+8): input $1.40 (140¢/M), output $4.40 (440¢/M).
MaaS cobra además cache-hit a $0.26/M y un Periodo 2 (off-peak) más
barato — el catálogo fija el precio de Periodo 1 (peor caso); el costo
base del ledger lo calcula LiteLLM con su cost map (ver
skills/llm-gateway/litellm-proxy-governance §5).

Espejo idéntico del seed de db/schema.sql §3.3 (paridad AC-DB-040).
"""

from alembic import op

revision = "0008_model_catalog_glm52"
down_revision = "0007_spend_rejections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO catalog.model_catalog
          (model_name, display_name, provider,
           input_price_cents_per_m, output_price_cents_per_m)
        VALUES
          ('glm-5.2', 'GLM 5.2 (Huawei MaaS)', 'huawei_maas', 140, 440)
        ON CONFLICT (model_name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.model_catalog WHERE model_name = 'glm-5.2'"
    )

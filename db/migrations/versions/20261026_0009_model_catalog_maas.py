"""model_catalog: catálogo REAL de Huawei MaaS intl + contexto (t_e0943bd8, spec 018).

Revision ID: 0009_model_catalog_maas
Revises: 0008_model_catalog_glm52
Create Date: 2026-10-26

Feedback Miller #6: el catálogo tenía nombres genéricos (qwen2.5-72b-instruct,
deepseek-v3) que NO existen como deployments del endpoint MaaS internacional.
Esta migración lo reconstruye sobre los modelos REALES desplegados en
https://api-ap-southeast-1.modelarts-maas.com/openai/v1 (verificado en vivo
con la key "Inforum", GET /models HTTP 200, 2026-10-26): glm-5.3, glm-5.2,
glm-5.1, deepseek-v4.1-flash, deepseek-v4-flash, qwen3-32b.

Fuentes oficiales (USD por millón de tokens, peor caso P1/tier≥32K/reasoning):
- support.huaweicloud.com/intl/en-us/price-maas/price-maas-0002.html
- support.huaweicloud.com/intl/en-us/price-modelarts/price-modelarts-0047.html (Qwen3-32B)
Límites de contexto: model-list-maas/model_list_0001.html (K=1,000, M=1,000,000).
qwen3-32b está desplegado y cobra pero NO figura en el Model List intl: sus
columnas de límite quedan NULL (la UI renderiza "—").

Precios fraccionales oficiales (107.8, 377.4, 13.5, 269.5 ¢/M): las columnas
de precio migran de bigint a numeric(12,3).

Los modelos retirados se eliminan junto con sus filas de model_access (un
tenant que los aprobara no podría ejercer el acceso contra el gateway real).
El histórico de spend_ledger referencia nombres como texto y se conserva.

Espejo idéntico del seed de db/schema.sql §3.3 (paridad AC-DB-040).
"""

from alembic import op

revision = "0009_model_catalog_maas"
down_revision = "0008_model_catalog_glm52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Límites de contexto documentados + precios fraccionales.
    op.execute(
        """
        ALTER TABLE catalog.model_catalog
          ADD COLUMN context_tokens bigint,
          ADD COLUMN max_output_tokens bigint
        """
    )
    op.execute(
        """
        ALTER TABLE catalog.model_catalog
          ALTER COLUMN input_price_cents_per_m TYPE numeric(12,3),
          ALTER COLUMN output_price_cents_per_m TYPE numeric(12,3)
        """
    )
    # 2. Retiro de los modelos genéricos (no existen upstream).
    op.execute(
        """
        DELETE FROM model_access WHERE model_id IN (
          SELECT id FROM catalog.model_catalog
          WHERE model_name IN ('qwen2.5-72b-instruct', 'deepseek-v3')
        )
        """
    )
    op.execute(
        "DELETE FROM catalog.model_catalog "
        "WHERE model_name IN ('qwen2.5-72b-instruct', 'deepseek-v3')"
    )
    # 3. Catálogo real MaaS intl (precios ¢/M, peor caso).
    op.execute(
        """
        INSERT INTO catalog.model_catalog
          (model_name, display_name, provider, context_tokens,
           max_output_tokens, input_price_cents_per_m, output_price_cents_per_m)
        VALUES
          ('glm-5.3',            'GLM-5.3',            'huawei_maas', 1000000, 128000, 140.0, 440.0),
          ('glm-5.2',            'GLM-5.2',            'huawei_maas', 1000000, 128000, 140.0, 440.0),
          ('glm-5.1',            'GLM-5.1',            'huawei_maas',  198000, 128000, 107.8, 377.4),
          ('deepseek-v4.1-flash','DeepSeek-V4.1-Flash','huawei_maas', 1000000, 384000,  30.0, 120.0),
          ('deepseek-v4-flash',  'DeepSeek-V4-Flash',  'huawei_maas', 1000000, 384000,  13.5,  27.0),
          ('qwen3-32b',          'Qwen3-32B',          'huawei_maas',    NULL,   NULL,  27.0, 269.5)
        ON CONFLICT (model_name) DO UPDATE SET
          display_name = EXCLUDED.display_name,
          provider = EXCLUDED.provider,
          context_tokens = EXCLUDED.context_tokens,
          max_output_tokens = EXCLUDED.max_output_tokens,
          input_price_cents_per_m = EXCLUDED.input_price_cents_per_m,
          output_price_cents_per_m = EXCLUDED.output_price_cents_per_m,
          updated_at = now()
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.model_catalog WHERE model_name IN "
        "('glm-5.3','glm-5.2','glm-5.1','deepseek-v4.1-flash',"
        "'deepseek-v4-flash','qwen3-32b')"
    )
    op.execute(
        """
        INSERT INTO catalog.model_catalog
          (model_name, display_name, provider,
           input_price_cents_per_m, output_price_cents_per_m)
        VALUES
          ('qwen2.5-72b-instruct', 'Qwen2.5 72B Instruct', 'huawei_maas', 400, 1200),
          ('deepseek-v3',          'DeepSeek V3',          'huawei_maas', 270, 1090),
          ('glm-5.2',              'GLM 5.2 (Huawei MaaS)', 'huawei_maas', 140, 440)
        ON CONFLICT (model_name) DO NOTHING
        """
    )
    op.execute(
        """
        ALTER TABLE catalog.model_catalog
          ALTER COLUMN input_price_cents_per_m TYPE bigint
            USING input_price_cents_per_m::bigint,
          ALTER COLUMN output_price_cents_per_m TYPE bigint
            USING output_price_cents_per_m::bigint
        """
    )
    op.execute(
        """
        ALTER TABLE catalog.model_catalog
          DROP COLUMN context_tokens,
          DROP COLUMN max_output_tokens
        """
    )

---
name: huawei-maas-models
description: "Catálogo REAL de Huawei Cloud MaaS internacional: modelos desplegados, precios USD/M tokens, límites de contexto, rate limits, endpoint OpenAI-compatible y verificación en vivo. Use when cargar catálogos de modelos, fijar pricing de LiteLLM contra MaaS o validar deployments."
version: 1.1.0
author: trust-agent dev
metadata:
  hermes:
    tags: [huawei, maas, modelarts, models, pricing, usd, llm-gateway, litellm, catalog]
---

# Huawei Cloud MaaS (intl) — Catálogo real de modelos, precios USD y límites

Investigado contra la documentación oficial intl (oct-2026) y **VERIFICADO EN VIVO**
contra el endpoint `https://api-ap-southeast-1.modelarts-maas.com/openai/v1`
(key "Inforum", `GET /models` HTTP 200 — t_e0943bd8 2026-10-26; re-verificado
t_bf7eff20 2026-10-27).

## 1. Fuentes oficiales

- Model List (intl): https://support.huaweicloud.com/intl/en-us/model-list-maas/model_list_0001.html (actualizada 2026-10-27)
- Billing texto (intl, USD): https://support.huaweicloud.com/intl/en-us/price-maas/price-maas-0002.html (actualizada 2026-10-27)
- API chat: `POST {endpoint}/chat/completions` (OpenAI-compatible, Bearer API key)

## 2. Modelos desplegados (verificación en vivo 2026-10-27)

`deepseek-v4-flash`, `deepseek-v4.1-flash`, `deepseek-v4.1-flash-1017`, `glm-5.1`, `glm-5.2`, `glm-5.3`

**`qwen3-32b` fue RETIRADO upstream** (2026-10-27: ausente de `GET /models`,
`chat/completions` → 404 `ModelArts.81009 "Invalid model."`). La deriva es
BIDIRECCIONAL: el mismo día apareció `deepseek-v4.1-flash-1017` (verificado:
listado en `/models` pero la key "Inforum" obtiene 403 `ModelArts.81004`
"you do not have access to it" — el acceso por key se habilita en la consola
MaaS; listado ≠ invocable).

```bash
# Verificar en vivo (no imprime la key)
set -a; source /root/trust-agent/litellm-real/.env; set +a
curl -sS "$MAAS_ENDPOINT/models" -H "Authorization: Bearer $MAAS_API_KEY"
```

## 3. Límites y rate limits (doc oficial Model List, 2026-10-27)

| Model ID | Contexto | Máx input | Máx output | Máx CoT | Rate limit |
|---|---|---|---|---|---|
| glm-5.3 | 1M | 1M | 128K | 128K | TPM 2,000,000 · RPM 100 |
| glm-5.2 | 1M | 1M | 128K | 64K | TPM 1,000,000 · RPM 100 |
| glm-5.1 | 198K | 192K | 128K | 96K | TPM 1,000,000 · RPM 100 |
| deepseek-v4.1-flash-1017 | 1M | 1M | 384K | 96K | TPM 2,000,000 · RPM 100 |
| deepseek-v4.1-flash | 1M | 1M | 384K | 96K | TPM 1,000,000 · RPM 100 |
| deepseek-v4-flash | 1M | 1M | 384K | 96K | TPM 60,000 · RPM 15 |

Notación de la doc: K=1,000 · M=1,000,000.

## 4. Precios USD por millón de tokens (doc oficial Billing, 2026-10-27)

Diferencial por horario (GMT+8): **Period 1** 08:00–20:59, **Period 2** 21:00–07:59.
Fórmula con cache: `(input − cached) × input + cached × cache_hit + output × output`.

| Modelo | Cache hit | Input | Output | Nota |
|---|---|---|---|---|
| GLM-5.3 | $0.26 | $1.40 | $4.40 | sin diferencial horario |
| GLM-5.2 P1 / P2 | $0.26 / $0.182 | $1.40 / $0.98 | $4.40 / $3.08 | |
| GLM-5.1 <32K P1 / P2 | $0.175 / $0.123 | $0.809 / $0.566 | $3.235 / $2.265 | pricing por longitud |
| GLM-5.1 ≥32K P1 / P2 | $0.27 / $0.189 | $1.078 / $0.755 | $3.774 / $2.642 | |
| DeepSeek-V4.1-Flash-1017 P1 / P2 | $0.03 / $0.015 | $0.30 / $0.15 | $1.20 / $0.60 | texto e imagen |
| DeepSeek-V4.1-Flash P1 / P2 | $0.03 / $0.015 | $0.30 / $0.15 | $1.20 / $0.60 | texto e imagen |
| DeepSeek-V4-Flash | — | $0.135 | $0.27 | sin diferencial |

**Convención del proyecto (peor caso)**: fijar siempre Period 1 / tier ≥32K / patrón
reasoning — nunca subestimar costo. Ejemplo oficial: GLM-5.2 P1 con 1.5M input
(0.5M cache hit) + 3M output = $14.73.

## 5. Lecciones

- **El catálogo de MaaS deriva en AMBAS direcciones y sin aviso** (GAP M-2,
  t_bf7eff20): `qwen3-32b` desapareció y `deepseek-v4.1-flash-1017` apareció
  el MISMO día. Nunca mantener un catálogo contra una verificación puntual:
  el `GET /models` vivo es la fuente autoritativa del CONJUNTO (en Trust
  Agent lo sincroniza el refresh central, spec 018 §5); la doc oficial es
  la fuente de metadatos (precios/límites).
- **Estar listado en `/models` NO implica ser invocable con una key dada**:
  `deepseek-v4.1-flash-1017` lista pero responde 403 `ModelArts.81004`
  hasta habilitar el acceso en la consola MaaS. Distinguir 404
  `ModelArts.81009` (no existe) de 403 `ModelArts.81004` (sin entitlement).
- Nombres de deployment en minúscula con guiones (`glm-5.2`); display oficial
  con mayúsculas y guion (`GLM-5.2`). LiteLLM `model_name` = deployment ID.
- Precios fraccionales (ej. $0.135/M = 13.5¢/M): el catálogo debe usar
  `numeric`, no enteros de centavos.
- El endpoint intl de MaaS es OpenAI-compatible: LiteLLM lo consume con
  `model: openai/<deployment>` y `api_base: $MAAS_ENDPOINT`.

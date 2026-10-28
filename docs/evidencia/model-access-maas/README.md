# Evidencia — spec 018: Model Access sobre MaaS real + policy (feedback Miller #6)

Cadena verificada en vivo (nada simulado), script `platform/scripts/evidence_model_access_maas.py`
(21 checks, reutiliza la maquinaria de `revalidate_litellm_real.py`: login REAL PKCE S256 + TOTP
contra Keycloak 26):

- **E1** `GET {proxy}/v1/models` (LiteLLM real 127.0.0.1:4000, key master) == los 6 deployments
  reales de Huawei MaaS intl (`api-ap-southeast-1.modelarts-maas.com/openai/v1`).
- **P1/P2** tenant real + login REAL del admin (authorization-code + PKCE S256 + TOTP, `acr=2`).
- **E2** `GET /api/models` → exactamente los 6 modelos oficiales de MaaS con contexto y precios
  USD/M de la documentación oficial (glm-5.3/5.2/5.1, deepseek-v4.1-flash, deepseek-v4-flash,
  qwen3-32b); los genéricos de LiteLLM (qwen2.5-72b-instruct, deepseek-v3) retirados.
- **E3/E4** `GET /api/models/access` incluye sección `policy` (approved_models_only,
  allowed_models) y `GET /api/policies?level=org` expone `model_approvals` con modo/estado.
- **E5** aprobación org de deepseek-v4-flash → aparece en `model_approvals` y en el acceso efectivo.
- **E6** `PUT /api/policies/org allowed_models=['glm-5.2']` → el conjunto efectivo se restringe
  (deny-overrides org→depto→usuario→policy); `verify` de un modelo aprobado fuera de policy → 403.
- **E7** invocación REAL glm-5.2 con key virtual del tenant → proxy LiteLLM → MaaS real responde
  200; el mismo request con deepseek-v4-flash (fuera de policy) → 403.

## Corridas

| Archivo | Fecha (CST) | Modo | Resultado |
|---------|-------------|------|-----------|
| `01-models-maas-vs-proxy.txt` | 2026-10-26 10:05 | capturas parciales | proxy ↔ MaaS ok |
| `02-invoke-glm52-real.txt` | 2026-10-26 10:06 | captura | invocación real glm-5.2 ok |
| `03-api-policy-live.txt` | 2026-10-26 10:09 | capturas | /api/models + /api/policies ok |
| `04-evidence-r15-reuse.log` | 2026-10-26 12:19 | `--slug model-access-maas-retry144 --password …` (reuse) | **21 PASS / 0 FAIL** |

## Nota de la corrida 04 (re-verificación, intento 15)

La re-verificación con slug fresco quedó **bloqueada por una regresión ajena** (lane auth UX,
t_1844fb06): desde el rebuild de la imagen API (~11:43 CST) todo realm recién provisionado tiene
el browser flow roto ("Password LoA" sin username-password form → authorize 400 sin formulario;
repro y root cause en el comentario #65 de esa card). Por eso la corrida 04 usa el modo `--reuse`
contra el tenant `model-access-maas-retry144` (realm pre-rebuild con flow sano, provisionado por
el run 144 de esta misma lane): DB restaurada al estado post-provisioning (aprobación
deepseek-v4-flash y policy allowed_models eliminadas para que E5/E6 las re-creen) y TOTP del admin
re-enrolado vía KC Admin API (`CONFIGURE_TOTP`). El login sigue siendo REAL (PKCE S256 + TOTP
nuevo, acr=2) y la invocación E7 atraviesa proxy LiteLLM real → MaaS real.

Cuando la lane auth UX repare el flow, re-validar con slug fresco:
`.venv/bin/python scripts/evidence_model_access_maas.py --slug <slug-nuevo>`

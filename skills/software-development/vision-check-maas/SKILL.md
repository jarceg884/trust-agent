---
name: vision-check-maas
description: Verificación visual de capturas con el modelo vision del catálogo MaaS (deepseek-v4.1-flash). Úsala cuando necesites "ver" imágenes — crítica de UI, verificación de screenshots de evidencia, QA visual.
---

# Verificación visual con MaaS

El único modelo del catálogo MaaS (api-ap-southeast-1.modelarts-maas.com/openai/v1) que acepta `image_url` es **deepseek-v4.1-flash**. Verificado 2026-10-26:

| Modelo | ¿Acepta imagen? |
|---|---|
| deepseek-v4.1-flash | ✅ SÍ (multimodal) |
| deepseek-v4-flash | ❌ 81001 (content inválido) |
| glm-5.3 / glm-5.2 / glm-5.1 | ❌ 81001 / unsupported content type |
| qwen3-32b | ❌ 81009 Invalid model (en este endpoint) |

## Uso (Python)

```python
import base64, json, urllib.request
b64 = base64.b64encode(open("captura.png","rb").read()).decode()
body = {"model": "deepseek-v4.1-flash", "messages": [{"role":"user","content":[
    {"type":"text","text":"<tu pregunta sobre la imagen>"},
    {"type":"image_url","image_url":{"url":f"data:image/png;base64,{b64}"}}]}],
    "max_tokens": 500}
# POST a https://api-ap-southeast-1.modelarts-maas.com/openai/v1/chat/completions
# con Authorization: Bearer $HERMES_CUSTOM_API_AP_SOUTHEAST_1_MODELARTS_MAAS_COM_API_KEY
```

- La key vive en el .env del perfil (HERMES_CUSTOM_API_AP_SOUTHEAST_1_MODELARTS_MAAS_COM_API_KEY) o en litellm-real/.env (MAAS_API_KEY).
- Cuidado con el rate limit compartido (2M tokens/min): imágenes grandes consumen mucho — una captura de 180KB ≈ ~50K tokens de imagen. No mandes decenas de imágenes en ráfaga cuando la factory esté consumiendo.
- El tool `vision_analyze` del agente usa el modelo principal (glm-5.3) y FALLA con estas imágenes — para ver capturas del proyecto usa SIEMPRE este método directo.

## Lecciones (t_3cf085b9, pulido UI v2)

- **El juez es no determinístico** (±1–1.5 puntos entre corridas idénticas): la
  misma captura de Hermes Download puntuó 5, 6, 7 y 8 según la corrida. Para
  un gate de aceptación: fija el rubric EXACTO en el prompt (checks
  numerados PASS/FAIL), corre la verificación sobre la captura FINAL del
  build final y reporta esa corrida; no hagas cherry-picking del mejor score.
- **Verifica colores COMPUTADOS de elementos reales, no CSS vars**: en la r3
  del pulido, `@theme inline` de Tailwind v4 inlitaba los valores en las
  utilidades y un override de `:root` no cambiaba NADA renderizado — leer
  `getComputedStyle(document.documentElement).getPropertyValue('--color-accent')`
  dio un FALSO positivo. El chequeo honesto es
  `getComputedStyle(element).backgroundColor` sobre el elemento visible
  (botón, barra activa, avatar).
- **Prompt-frame importa**: decirle al juez que es un "dev/local deployment"
  con criterio explícito ("raw 127.0.0.1 IP is the worst case; a named host
  is acceptable for a dev deployment") evita que falle un punto por una
  limitación que la tarea ya declaró como pendiente de despliegue.

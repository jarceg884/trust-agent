"""Huawei Cloud MaaS real pricing (spec 005, REQ-MDL-004 — job `pricing_sync`).

Source of truth: the published MaaS billing docs (NOT a vendor API — Huawei
does not expose one for pricing). Convención PEOR CASO ya establecida en
``litellm-real/config.yaml`` (mismo criterio, un solo lugar ahora):
Period 1 (08:00-20:59 GMT+8, el tramo más caro) cuando hay diferencial
horario; tier >=32K cuando hay pricing por longitud de contexto.

  https://support.huaweicloud.com/intl/en-us/price-maas/price-maas-0002.html
  (verificado contra la tabla real, "Updated on 2026-10-27 GMT+08:00")

Esta tabla es la ÚNICA fuente de pricing para el catálogo central: se sirve
como LiteLLM "model cost map" real (``GET /api/models/cost-map`` ==
``LITELLM_MODEL_COST_MAP_URL``, recargado vía ``POST /reload/model_cost_map``
— arquitectura.md §Pricing) — "todo el pricing vive en LiteLLM" (mandato
Miller), nunca en una tabla paralela mantenida a mano por fuera de él.

Cuando Huawei publica un modelo NUEVO: `discovery.refresh_central` (spec 032,
D-018-07) ya lo registra automáticamente en el gateway en cuanto aparece en
`GET {MAAS_ENDPOINT}/models` — lo único que faltaba era el precio. Si el
modelo nuevo ya tiene fila aquí, su precio se aplica solo en el siguiente
reload del cost map (hasta 6h, o inmediato con `sync_now()`). Si NO está aquí
todavía (familia de modelo nunca vista), LiteLLM lo deja sin costo conocido
(igual que hoy) hasta que esta tabla se actualice a mano con el precio
publicado — no hay forma de inventar un precio que Huawei no publicó
(constitution I).
"""
from __future__ import annotations

from typing import Any

import httpx

from ..config import get_settings
from ..errors import service_unavailable

# model_name (tal cual lo devuelve GET /models del upstream, lowercase) ->
# pricing en USD por token, Periodo 1 (peor caso). cache_read = tarifa de
# input con cache-hit; None = el modelo no soporta cache-hit (sin descuento).
_USD_PER_MILLION = {
    "glm-5.3":                   {"input": 1.40,  "output": 4.40,  "cache_read": 0.26},
    "glm-5.2":                   {"input": 1.40,  "output": 4.40,  "cache_read": 0.26},
    # GLM-5.1: pricing por longitud de contexto — peor caso = tier >=32K.
    "glm-5.1":                   {"input": 1.078, "output": 3.774, "cache_read": 0.27},
    "deepseek-v4.1-flash":       {"input": 0.30,  "output": 1.20,  "cache_read": 0.03},
    "deepseek-v4.1-flash-1017":  {"input": 0.30,  "output": 1.20,  "cache_read": 0.03},
    "deepseek-v4-flash":         {"input": 0.135, "output": 0.27,  "cache_read": None},
}

# Contexto documentado (Model List intl) — usado por LiteLLM para enforcement
# de ventana cuando el deployment no lo trae por su cuenta.
_MAX_TOKENS = {
    "glm-5.3": 1_000_000,
    "glm-5.2": 1_000_000,
    "glm-5.1": 198_000,
    "deepseek-v4.1-flash": 1_000_000,
    "deepseek-v4.1-flash-1017": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
}


def normalize_model_name(raw: str) -> str:
    """Upstream ids ya vienen en minúsculas con guiones (verificado GET
    /models en vivo); normaliza por si acaso para que el match nunca falle
    por mayúsculas."""
    return (raw or "").strip().lower()


def price_for(model_name: str) -> dict[str, Any] | None:
    """-> {input_cost_per_token, output_cost_per_token, cache_read_input_token_cost}
    en USD/token (no USD/millón), listas para litellm_params/model_info.
    None si el modelo no está en la tabla (pricing desconocido — honesto,
    nunca inventado)."""
    row = _USD_PER_MILLION.get(normalize_model_name(model_name))
    if row is None:
        return None
    out: dict[str, Any] = {
        "input_cost_per_token": row["input"] / 1_000_000.0,
        "output_cost_per_token": row["output"] / 1_000_000.0,
    }
    if row["cache_read"] is not None:
        out["cache_read_input_token_cost"] = row["cache_read"] / 1_000_000.0
    return out


def build_cost_map() -> dict[str, dict[str, Any]]:
    """Cost map completo en el formato que LiteLLM espera para
    ``LITELLM_MODEL_COST_MAP_URL``.

    LiteLLM exige un mapa con >=50 entradas (guardia de integridad propia,
    v1.106.3: "Fetched model cost map has only N models (minimum=50) ...
    Falling back to local backup" — un mapa de solo nuestros 6 modelos MaaS
    se rechaza siempre) — así que la base es el propio cost map bundleado de
    LiteLLM (``litellm.model_cost`` real, volcado una vez desde el proxy
    corriendo: ``litellm_base_cost_map.json``, 4241 modelos) y encima se
    sobre-escriben SOLO los nombres de Huawei MaaS con el pricing real
    documentado (huawei_pricing._USD_PER_MILLION) — la fuente de verdad para
    esos 6 sigue siendo esta tabla, nunca el bundle genérico de LiteLLM (que
    no conoce GLM/DeepSeek-MaaS en absoluto)."""
    import json
    from pathlib import Path

    base_path = Path(__file__).parent / "litellm_base_cost_map.json"
    try:
        out: dict[str, dict[str, Any]] = json.loads(base_path.read_text())
    except (OSError, ValueError):
        out = {}
    for name, row in _USD_PER_MILLION.items():
        entry: dict[str, Any] = {
            "input_cost_per_token": row["input"] / 1_000_000.0,
            "output_cost_per_token": row["output"] / 1_000_000.0,
            "litellm_provider": "openai",
            "mode": "chat",
        }
        if name in _MAX_TOKENS:
            entry["max_tokens"] = _MAX_TOKENS[name]
            entry["max_input_tokens"] = _MAX_TOKENS[name]
        if row["cache_read"] is not None:
            entry["cache_read_input_token_cost"] = row["cache_read"] / 1_000_000.0
        out[name] = entry
    return out


def sync_catalog_prices() -> dict:
    """Sincroniza ``catalog.model_catalog`` con la tabla REAL de pricing.

    La consola (Models/Usage) y la facturación leen precios de
    ``catalog.model_catalog`` en centavos/millón. Hasta ahora esa tabla se
    sembraba a mano y quedaba desfasada de la fuente de verdad
    (``_USD_PER_MILLION``) — el admin veía precios viejos aunque LiteLLM
    cobrara con el precio correcto (mandato Miller: "los precios deben ser
    dinámicos"). Esta función es el puente: se llama tras cada reload del
    cost map (6h y `sync_now`), así ambos lados cuentan la misma historia.

    Precio en centavos/millón = USD/millón × 100 (1 USD = 100¢).
    Best-effort: un fallo de DB no rompe el reload del proxy.
    """
    from sqlalchemy import text

    from ..db import get_engine

    updated = 0
    try:
        engine = get_engine()
        with engine.begin() as conn:
            for name, row in _USD_PER_MILLION.items():
                result = conn.execute(
                    text(
                        "UPDATE catalog.model_catalog SET "
                        "input_price_cents_per_m = :inp, "
                        "output_price_cents_per_m = :outp, "
                        "cache_read_price_cents_per_m = :cache, "
                        "updated_at = now() "
                        "WHERE lower(model_name) = :name"
                    ),
                    {
                        "inp": round(row["input"] * 100, 3),
                        "outp": round(row["output"] * 100, 3),
                        "cache": (
                            round(row["cache_read"] * 100, 3)
                            if row["cache_read"] is not None else None
                        ),
                        "name": name,
                    },
                )
                updated += result.rowcount or 0
    except Exception:  # noqa: BLE001 - pricing del proxy no depende de la DB
        return {"updated": updated, "error": "catalog sync skipped"}
    return {"updated": updated}


def sync_now() -> dict:
    """Fuerza un reload INMEDIATO del cost map en el proxy real (en vez de
    esperar el schedule de 6h) — ``POST /reload/model_cost_map``, contrato
    verificado contra litellm v1.106.3. También sincroniza el catálogo de la
    consola con la misma tabla para que la UI muestre el precio real."""
    settings = get_settings()
    base = (settings.litellm_base_url or "").rstrip("/")
    key = settings.litellm_master_key or ""
    if not base or not key:
        raise service_unavailable(
            "LiteLLM proxy is not configured "
            "(TRUST_LITELLM_BASE_URL / TRUST_LITELLM_MASTER_KEY)."
        )
    try:
        resp = httpx.post(
            f"{base}/reload/model_cost_map",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise service_unavailable(f"LiteLLM proxy unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise service_unavailable(
            f"LiteLLM /reload/model_cost_map failed: {resp.status_code} "
            f"{resp.text[:200]}"
        )
    catalog = sync_catalog_prices()
    return {
        "reloaded": True,
        "models": len(_USD_PER_MILLION),
        "catalog": catalog,
    }

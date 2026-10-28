"""Capa de presentación legible de policies (spec 036, D-36-01) — READ-ONLY.

Carga el contrato canónico ``policy_catalog_readable.json`` (copia byte-a-byte
de ``specs/036-policies-ux/policy-catalog-readable.json``) como package data y
lo expone para el bloque aditivo ``presentation`` de
``GET /api/policies/matrix``.

- La UI JAMÁS hardcodea nombres/frases: consume este bloque (D-36-01).
- Cero mutaciones, cero cambios de semántica del engine: este módulo solo
  lee un JSON estático (REQ-036-006).
- La cobertura 1:1 con ``catalog.policy_keys`` la exige el test de plataforma
  ``tests/test_policies_presentation.py`` (AC-036-001); aquí no se filtra ni
  se interpola nada para no enmascarar drift.
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONTRACT_PATH = Path(__file__).with_name("policy_catalog_readable.json")


@lru_cache(maxsize=1)
def _load_contract() -> dict[str, Any]:
    with _CONTRACT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def presentation_blocks() -> dict[str, dict[str, Any]]:
    """key → bloque de presentación legible (nombre/desc es+en, control,
    icono, categoría, plantillas de frase). Se devuelve TAL CUAL: el test
    de cobertura 1:1 (AC-036-001) es el que detecta faltantes/sobrantes."""
    return dict(_load_contract()["keys"])


def presentation_categories() -> list[dict[str, Any]]:
    """Las 9 categorías legibles (id, icono, nombre es+en)."""
    return list(_load_contract()["categories"])

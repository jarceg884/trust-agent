"""Contrato del Policy Builder de políticas nombradas (spec 041, D-41-02) — READ-ONLY.

Carga el contrato canónico ``named_policy_builder.json`` (copia byte-a-byte
de ``specs/041-named-policies/named-policy-builder.json``, test sha256) como
package data y lo expone para el bloque aditivo ``builder`` de
``GET /api/named-policies``.

- La UI JAMÁS hardcodea grupos, capacidades, defaults ni plantillas: consume
  este bloque (D-41-02). La verdad de validación/combinación sigue en el
  control plane (422/deny-overrides).
- Cero mutaciones, cero cambios de semántica del engine: este módulo solo
  lee un JSON estático (mismo patrón que policy_presentation.py de la
  spec 036).
- La cobertura 1:1 con ``catalog.policy_keys`` (44/44 keys no invariables,
  ítems de ``allowed_tools`` == ``known_items``, ``org_only`` ==
  ``ORG_ONLY_KEYS``) la exige el test de plataforma
  ``tests/test_named_policies.py`` (AC-041-003); aquí no se filtra ni se
  interpola nada para no enmascarar drift.
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONTRACT_PATH = Path(__file__).with_name("named_policy_builder.json")


@lru_cache(maxsize=1)
def _load_contract() -> dict[str, Any]:
    with _CONTRACT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def builder_groups() -> list[dict[str, Any]]:
    """Los 6 grupos del sidebar (id, icono, nombre/descripción es+en)."""
    return list(_load_contract()["groups"])


def builder_items() -> list[dict[str, Any]]:
    """Las 47 capacidades (ítems) mapeadas 1:1 sobre las keys del catálogo."""
    return list(_load_contract()["items"])


def builder_templates() -> list[dict[str, Any]]:
    """Las 3 plantillas pre-cargadas (slug, nombre es+en, definition)."""
    return list(_load_contract()["templates"])


def builder_block() -> dict[str, Any]:
    """El bloque ``builder`` completo, TAL CUAL del contrato (D-41-02).

    La única traducción: ``$schema_version`` (nombre canónico del contrato)
    se expone como ``schema_version`` en la respuesta de la API."""
    contract = _load_contract()
    return {
        "schema_version": contract["$schema_version"],
        "groups": contract["groups"],
        "items": contract["items"],
        "templates": contract["templates"],
    }


def template_definition(slug: str) -> dict[str, Any] | None:
    """Definition de una plantilla builtin por slug (o None si no existe)."""
    for tpl in _load_contract()["templates"]:
        if tpl["slug"] == slug:
            return dict(tpl["definition"])
    return None

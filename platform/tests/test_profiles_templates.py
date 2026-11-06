"""AC-ONB-020/021 — Profile template registry (REQ-PRF-001, D-ONB-06).

Spec 038 amendments (D-38-01/03): the registry grows 4 -> 12 templates with
localized display (name_es/description_es) and LOSES its implicit-seed role
— `resolve_template_keys(None)` resolves to an EMPTY list (an organization
is born with the departments it DECLARES or none at all).
"""
import pytest

from app.profiles.templates import (
    DEFAULT_TEMPLATE_KEYS,
    TEMPLATES,
    catalog_payload,
    get_template,
    resolve_template_keys,
)

NEW_KEYS_038 = {
    "engineering", "sales", "marketing", "human_resources", "legal",
    "product", "data_analytics", "security_compliance",
}
ORIGINAL_KEYS = {
    "it_operations", "cloud_operations", "finance", "customer_operations",
}


def test_registry_has_twelve_templates():
    """AC-ONB-020 (amended by spec 038 D-38-03): 12 pre-designed templates —
    the 4 original plus the 8 new keys — each declaring skills, suggested
    connectors, quick actions, base policies, a suggested monthly budget
    (USD) and the es+en display fields."""
    assert len(TEMPLATES) == 12
    assert set(TEMPLATES) == ORIGINAL_KEYS | NEW_KEYS_038
    # Suggestion list mirrors the whole catalog (no implicit-seed role).
    assert set(DEFAULT_TEMPLATE_KEYS) == set(TEMPLATES)
    for key, template in TEMPLATES.items():
        assert template.key == key
        assert template.name
        assert template.description
        assert template.name_es, key  # spec 036 es+en precedent
        assert template.description_es, key
        assert len(template.skills) >= 3, key
        assert template.suggested_connectors
        assert len(template.quick_actions) >= 2, key
        assert isinstance(template.base_policies, dict)
        assert template.suggested_monthly_budget_usd > 0


def test_resolve_no_selection_is_empty():
    """AC-ONB-021 (amended by spec 038 D-38-01): no selection -> EMPTY list.
    The registry never seeds a tenant implicitly anymore."""
    assert resolve_template_keys(None) == []
    assert resolve_template_keys([]) == []


def test_resolve_explicit_selection():
    assert resolve_template_keys(["finance"]) == ["finance"]
    assert resolve_template_keys(["cloud_operations", "it_operations"]) == [
        "cloud_operations", "it_operations"
    ]
    # New catalog keys validate like any other (spec 038 D-38-03).
    assert resolve_template_keys(["engineering", "human_resources"]) == [
        "engineering", "human_resources"
    ]


def test_resolve_rejects_unknown_and_duplicates():
    with pytest.raises(ValueError, match="Unknown profile template"):
        resolve_template_keys(["hr"])
    with pytest.raises(ValueError, match="Duplicate"):
        resolve_template_keys(["finance", "finance"])
    with pytest.raises(ValueError, match="Unknown profile template"):
        get_template("nope")


def test_catalog_payload_readonly():
    """REQ-DO-001: the /api/org/profile-templates payload — every template
    with es+en display fields, ordered by key, no tenant data."""
    payload = catalog_payload()
    assert len(payload) == 12
    assert [t["key"] for t in payload] == sorted(t["key"] for t in payload)
    for t in payload:
        assert set(t) == {
            "key", "name", "name_es", "description", "description_es",
            "suggested_monthly_budget_usd",
        }
        assert t["name_es"]

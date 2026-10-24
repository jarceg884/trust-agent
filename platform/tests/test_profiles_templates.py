"""AC-ONB-020/021 — Profile template registry (REQ-PRF-001, D-ONB-06)."""
import pytest

from app.profiles.templates import (
    DEFAULT_TEMPLATE_KEYS,
    TEMPLATES,
    get_template,
    resolve_template_keys,
)


def test_registry_has_four_standard_templates():
    """AC-ONB-020: >= 4 pre-designed templates, each declaring skills,
    suggested connectors, quick actions, base policies and a suggested
    monthly budget (USD)."""
    assert len(TEMPLATES) >= 4
    assert set(DEFAULT_TEMPLATE_KEYS) == {
        "it_operations", "cloud_operations", "finance", "customer_operations"
    }
    for key, template in TEMPLATES.items():
        assert template.key == key
        assert template.name
        assert template.description
        assert len(template.skills) >= 3, key
        assert template.suggested_connectors
        assert len(template.quick_actions) >= 2, key
        assert isinstance(template.base_policies, dict)
        assert template.suggested_monthly_budget_usd > 0


def test_resolve_default_selection():
    """AC-ONB-021: no selection -> the four standard templates, in order."""
    assert resolve_template_keys(None) == DEFAULT_TEMPLATE_KEYS


def test_resolve_explicit_selection():
    assert resolve_template_keys(["finance"]) == ["finance"]
    assert resolve_template_keys(["cloud_operations", "it_operations"]) == [
        "cloud_operations", "it_operations"
    ]


def test_resolve_rejects_unknown_and_duplicates():
    with pytest.raises(ValueError, match="Unknown profile template"):
        resolve_template_keys(["hr"])
    with pytest.raises(ValueError, match="Duplicate"):
        resolve_template_keys(["finance", "finance"])
    with pytest.raises(ValueError, match="Unknown profile template"):
        get_template("nope")

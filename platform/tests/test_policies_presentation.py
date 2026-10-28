"""Spec 036 — capa de presentación legible de policies (AC-036-001, REQ-036-007).

- Cobertura 1:1: las keys del bloque ``presentation`` de matrix == las keys de
  ``catalog.policy_keys`` (47/47, sin faltantes ni sobrantes) — falla en CI si
  el contrato y el catálogo de la DB divergen (D-36-01).
- sha256: el JSON de plataforma es idéntico al contrato canónico de la spec.
- Contrato coherente con el estado real: org_only == ORG_ONLY_KEYS,
  invariable == DB, campos obligatorios por kind de control.
- matrix aditivo: ``categories`` (9), ``org_effective`` y
  ``departments[].effective`` computados con el combinator (D-36-04).
- dept_admin también recibe presentation (el panel legible es la vista por
  defecto para él también, D-36-02).
"""
import hashlib
from pathlib import Path

from conftest import DEPT_A1, bearer_headers, mint

PLATFORM_JSON = (
    Path(__file__).resolve().parents[1]
    / "app" / "governance" / "policy_catalog_readable.json"
)


def _spec_contract() -> Path | None:
    """Contrato canónico de la spec (si el checkout trae specs/)."""
    for base in PLATFORM_JSON.resolve().parents:
        candidate = base / "specs" / "036-policies-ux" / (
            "policy-catalog-readable.json"
        )
        if candidate.is_file():
            return candidate
    return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _matrix(client, tokens):
    resp = client.get("/api/policies/matrix", headers=bearer_headers(tokens))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_platform_json_is_byte_identical_to_spec_contract():
    """AC-036-001: sha256 del JSON de plataforma == contrato de la spec."""
    assert PLATFORM_JSON.is_file(), (
        "platform/app/governance/policy_catalog_readable.json falta (D-36-01)"
    )
    contract = _spec_contract()
    if contract is None:  # checkout desplegado sin specs/ — no aplicable
        return
    assert _sha256(PLATFORM_JSON) == _sha256(contract), (
        "El JSON de presentación de plataforma diverge del contrato canónico "
        "de specs/036-policies-ux — recópialo byte-a-byte (D-36-01)."
    )


def test_presentation_coverage_1_1_with_catalog(client, idp):
    """AC-036-001: presentation ↔ catalog.policy_keys, 47/47 exactas."""
    alice = mint(client, idp, "alice")
    body = _matrix(client, alice)

    catalog_keys = {e["key"] for e in body["catalog"]}
    presentation_keys = set(body["presentation"])
    assert len(catalog_keys) >= 47
    missing = catalog_keys - presentation_keys
    extra = presentation_keys - catalog_keys
    assert not missing, f"keys sin presentación: {sorted(missing)}"
    assert not extra, f"keys de presentación fuera del catálogo: {sorted(extra)}"
    assert len(presentation_keys) == 47

    # 9 categorías legibles y cada key cae en una de ellas.
    categories = {c["id"] for c in body["categories"]}
    assert len(categories) == 9
    for key, block in body["presentation"].items():
        assert block["category"] in categories, key
        # Campos obligatorios del contrato (bilingüe completo, D-36-07).
        for locale in ("es", "en"):
            assert block["name"][locale].strip(), f"{key}.name.{locale}"
            assert block["description"][locale].strip(), f"{key}.desc.{locale}"
        assert block["icon"], f"{key}.icon"
        kind = block["control"]["kind"]
        assert kind in {
            "toggle-allow", "toggle-require", "select", "number",
            "multiselect-allow", "multiselect-deny", "domains", "messaging",
            "locked",
        }, f"{key}: kind desconocido {kind}"
        # Plantilla de frase: statement (on/off) para booleans, template con
        # {value} para el resto (D-36-04).
        if kind in ("toggle-allow", "toggle-require"):
            assert block["statement"]["on"]["en"], f"{key}.statement.on"
            assert block["statement"]["off"]["en"], f"{key}.statement.off"
        elif kind == "locked":
            assert block["statement"]["on"]["en"], f"{key}.statement.on"
        else:
            assert "{value}" in block["statement_template"]["en"], (
                f"{key}.statement_template debe llevar {{value}}"
            )


def test_presentation_flags_match_engine_state(client, idp):
    """El contrato espeja el estado real: org_only == ORG_ONLY_KEYS,
    invariable == catalog.policy_keys (validación del product-owner,
    convertida en regression test)."""
    from app.governance.combinator import ORG_ONLY_KEYS

    alice = mint(client, idp, "alice")
    body = _matrix(client, alice)
    catalog = {e["key"]: e for e in body["catalog"]}
    for key, block in body["presentation"].items():
        assert block.get("org_only", False) == (key in ORG_ONLY_KEYS), key
        assert block.get("invariable", False) == catalog[key]["invariable"], key
        # Los enums del control legible == enums reales del combinator.
        if key == "approval_mode":
            values = [o["value"] for o in block["control"]["options"]]
            assert set(values) == {"off", "smart", "manual"}
        if key == "unbind_mode":
            values = [o["value"] for o in block["control"]["options"]]
            assert set(values) == {"lock", "readonly"}
        if key == "artifact_sharing":
            values = [o["value"] for o in block["control"]["options"]]
            assert set(values) == {"private", "shared"}
        if key == "shared_agent_grants_mode":
            values = [o["value"] for o in block["control"]["options"]]
            assert set(values) == {"explicit", "open"}


def test_matrix_effective_fields_use_combinator(client, idp):
    """D-36-04: org_effective y departments[].effective llegan computados
    por el backend (combine), listos para la vista por destinatario."""
    alice = mint(client, idp, "alice")
    body = _matrix(client, alice)

    # org: allowed_connectors seed org [vmware, email] → efectiva idéntica
    # (el combinator devuelve la intersección ordenada).
    assert body["org_effective"]["allowed_connectors"] == ["email", "vmware"]
    assert body["org_effective"]["require_mfa"] is True

    dept = next(d for d in body["departments"]
                if d["department_id"] == DEPT_A1)
    # dept A1 estrecha a [vmware] → la efectiva del depto es la intersección.
    assert dept["effective"]["allowed_connectors"] == ["vmware"]
    # Una key sin override de depto cae al valor org (deny-overrides).
    assert dept["effective"]["require_mfa"] is True


def test_dept_admin_also_gets_presentation(client, idp):
    """D-36-02: el panel legible es la vista por defecto también para
    dept_admin — necesita el contrato completo para renderizar sus deptos."""
    bob = mint(client, idp, "bob")  # dept_admin (A2)
    body = _matrix(client, bob)
    assert len(body["presentation"]) == 47
    assert len(body["categories"]) == 9
    assert body["org_effective"]["require_mfa"] is True
    for dept in body["departments"]:
        assert "effective" in dept


def test_matrix_tolerates_key_without_presentation(client, idp, monkeypatch):
    """REQ-036-007 (mitad backend del fallback): si el catálogo tuviera una
    key sin bloque de presentación, matrix DEBE servirla igual (el bloque
    ``presentation`` es aditivo) — la UI renderiza el control genérico por
    value_type marcada "Untranslated" en vez de romperse. El test de
    cobertura 1:1 (AC-036-001) es el que impide que esto ocurra en CI;
    este test garantiza la degradación grácil si ocurre en producción."""
    from app.governance import policy_presentation

    original = policy_presentation.presentation_blocks()

    def _dropped() -> dict:
        blocks = dict(original)
        blocks.pop("allowed_tools", None)
        return blocks

    monkeypatch.setattr(policy_presentation, "presentation_blocks", _dropped)
    # policies_matrix importa el símbolo directamente → parchear también allí.
    from app.governance import policies_matrix

    monkeypatch.setattr(policies_matrix, "presentation_blocks", _dropped)

    alice = mint(client, idp, "alice")
    body = _matrix(client, alice)
    catalog_keys = {e["key"] for e in body["catalog"]}
    assert "allowed_tools" in catalog_keys  # el catálogo la sigue sirviendo
    assert "allowed_tools" not in body["presentation"]  # sin bloque: fallback
    assert len(body["presentation"]) == 46

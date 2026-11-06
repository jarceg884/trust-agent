"""AC-CP-054 — global catalog reads (models, connectors)."""
from tests.conftest import bearer_headers, mint


def test_connector_catalog_lists_six(client, idp):
    tokens = mint(client, idp, "alice")
    resp = client.get("/api/connectors", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    slugs = {c["slug"] for c in resp.json()["connectors"]}
    assert slugs == {"vmware", "email", "whatsapp", "telegram", "slack", "teams"}


def test_model_catalog_lists_models_with_pricing(client, idp):
    """AC-CP-054 + AC-018-01: catálogo global = los deployments REALES de
    MaaS intl, con pricing oficial USD/M y límites documentados."""
    tokens = mint(client, idp, "alice")
    resp = client.get("/api/models", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    models = resp.json()["models"]
    for m in models:
        assert "input_price_cents_per_m" in m
        assert "output_price_cents_per_m" in m
        assert "context_tokens" in m
        assert "max_output_tokens" in m
    # La fuente CENTRAL es la protegida por el AC: otras filas byok de
    # conexiones llm sembradas por otros tests de la misma sesión pueden
    # coexistir (mismo criterio que test_model_access).
    central = {m["model_name"]: m for m in models
               if m.get("source", "central") == "central"}
    names = set(central)
    # AC-018-01 (enmienda 2026-10-27, spec 018 §5): los deployments vivos de
    # {MAAS_ENDPOINT}/models + qwen3-32b retirado (stale, conservado como
    # historia); los genéricos ya no existen.
    assert names == {
        "glm-5.3", "glm-5.2", "glm-5.1", "deepseek-v4.1-flash-1017",
        "deepseek-v4.1-flash", "deepseek-v4-flash", "qwen3-32b",
    }
    assert "qwen2.5-72b-instruct" not in names
    assert "deepseek-v3" not in names
    by_name = central
    # Precios oficiales (peor caso P1/tier≥32K/reasoning), ¢/M USD.
    assert float(by_name["glm-5.2"]["input_price_cents_per_m"]) == 140
    assert float(by_name["glm-5.2"]["output_price_cents_per_m"]) == 440
    assert float(by_name["glm-5.1"]["input_price_cents_per_m"]) == 107.8
    assert float(by_name["deepseek-v4-flash"]["input_price_cents_per_m"]) == 13.5
    # deepseek-v4.1-flash-1017 (alta 2026-10-27): P1 30/120 ¢/M, 1M/384K
    # (price-maas-0002 + Model List intl, ambos actualizados 2026-10-27).
    assert float(by_name["deepseek-v4.1-flash-1017"]["input_price_cents_per_m"]) == 30
    assert float(by_name["deepseek-v4.1-flash-1017"]["output_price_cents_per_m"]) == 120
    assert by_name["deepseek-v4.1-flash-1017"]["context_tokens"] == 1_000_000
    assert by_name["deepseek-v4.1-flash-1017"]["max_output_tokens"] == 384_000
    # qwen3-32b RETIRADO upstream (404 ModelArts.81009 en vivo): stale,
    # precios/limites historicos conservados (migración 0026).
    assert by_name["qwen3-32b"]["status"] == "stale"
    assert float(by_name["qwen3-32b"]["output_price_cents_per_m"]) == 269.5
    # Límites documentados (Model List intl); qwen3-32b sin documentar → NULL.
    assert by_name["glm-5.2"]["context_tokens"] == 1_000_000
    assert by_name["glm-5.1"]["context_tokens"] == 198_000
    assert by_name["deepseek-v4.1-flash"]["max_output_tokens"] == 384_000
    assert by_name["qwen3-32b"]["context_tokens"] is None


def test_catalogs_require_authentication(client):
    assert client.get("/api/models").status_code == 401
    assert client.get("/api/connectors").status_code == 401


def test_connector_catalog_policy_state(client, idp):
    """Spec 037 (REQ-037-009): cada entrada del catálogo lleva
    ``allowed_for_caller`` con la regla deny-overrides completa. Alice
    (org ∩ dept A1) tiene efectivo ['vmware'] → solo vmware True, y el
    conjunto True coincide con el catálogo ``/available``."""
    tokens = mint(client, idp, "alice")
    resp = client.get("/api/connectors", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    entries = resp.json()["connectors"]
    assert entries, "catálogo vacío"
    for c in entries:
        assert isinstance(c["allowed_for_caller"], bool), c
    allowed = {c["slug"] for c in entries if c["allowed_for_caller"]}
    assert allowed == {"vmware"}, allowed  # email la estrecha el depto A1

    available = client.get("/api/connectors/available",
                           headers=bearer_headers(tokens)).json()["connectors"]
    assert {c["slug"] for c in available} == allowed

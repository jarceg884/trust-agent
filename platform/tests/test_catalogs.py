"""AC-CP-054 — global catalog reads (models, connectors)."""
from tests.conftest import bearer_headers, mint


def test_connector_catalog_lists_six(client, idp):
    tokens = mint(client, idp, "alice")
    resp = client.get("/api/connectors", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    slugs = {c["slug"] for c in resp.json()["connectors"]}
    assert slugs == {"vmware", "email", "whatsapp", "telegram", "slack", "teams"}


def test_model_catalog_lists_models_with_pricing(client, idp):
    tokens = mint(client, idp, "alice")
    resp = client.get("/api/models", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    models = resp.json()["models"]
    assert len(models) >= 2
    for m in models:
        assert "input_price_cents_per_m" in m
        assert "output_price_cents_per_m" in m
    names = {m["model_name"] for m in models}
    assert {"qwen2.5-72b-instruct", "deepseek-v3"} <= names


def test_catalogs_require_authentication(client):
    assert client.get("/api/models").status_code == 401
    assert client.get("/api/connectors").status_code == 401

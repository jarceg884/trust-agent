"""LiteLLM proxy Admin API client (spec 005, D-GOV-02/12/15).

Real HTTP client against the LiteLLM proxy management API
(https://docs.litellm.ai/docs/proxy/virtual_keys). Key governance facts,
verified against the official docs and production integrations:

- ``/key/generate`` returns the plaintext key EXACTLY once plus ``token``
  (the sha256 of the key). We persist ONLY that hash
  (``api_keys.litellm_key_hash``) — never the key itself.
- ``/key/block`` / ``/key/unblock`` / ``/key/info`` accept the sha256 hash
  as the ``key`` argument, so budget cutoff works without ever storing the
  raw key.
- ``max_budget`` is expressed in USD (float); the platform keeps integer
  cents and converts at this boundary only.
- Spend logs carry the request id, the key hash and the
  ``spend_logs_metadata`` we stamp at key creation ({tenant_id, user_id}).

Fail-closed: without ``TRUST_LITELLM_BASE_URL`` every method raises 503 —
governance never fails open (a budget that cannot be enforced is refused).
"""
from dataclasses import dataclass
import json
from typing import Any

import httpx

from ..config import get_settings
from ..errors import service_unavailable

_PERIOD_TO_DURATION = {"monthly": "30d", "weekly": "7d", "daily": "24h"}


def budget_duration_for(period: str) -> str:
    """Map a platform budget period to a LiteLLM budget_duration."""
    return _PERIOD_TO_DURATION.get(period, "30d")


@dataclass
class SpendLog:
    """One normalized spend-log row from /spend/logs/v2."""

    request_id: str
    key_hash: str | None
    model: str
    spend_usd: float
    tokens_in: int
    tokens_out: int
    occurred_at: str  # ISO-8601
    tenant_id: str | None  # from spend_logs_metadata (stamped at issuance)
    user_id: str | None


class LiteLLMClient:
    """Thin admin client; no state, one HTTP call per method."""

    def __init__(self, base_url: str, master_key: str, timeout: float = 10.0):
        if not base_url or not master_key:
            raise service_unavailable(
                "LiteLLM proxy is not configured (TRUST_LITELLM_BASE_URL / "
                "TRUST_LITELLM_MASTER_KEY); model governance refuses to "
                "operate fail-open."
            )
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"}
        self._timeout = timeout

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = httpx.request(
                method, f"{self._base}{path}",
                headers={**self._headers,
                         **kwargs.pop("headers", {})},
                timeout=self._timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise service_unavailable(f"LiteLLM proxy unreachable: {exc}") from exc
        if resp.status_code >= 500:
            raise service_unavailable(
                f"LiteLLM proxy error ({resp.status_code}) on {path}."
            )
        return resp

    # --- Virtual keys -------------------------------------------------------

    def generate_key(
        self,
        *,
        alias: str,
        user_id: str,
        models: list[str],
        max_budget_usd: float | None,
        budget_duration: str | None,
        metadata: dict[str, Any],
    ) -> dict:
        """POST /key/generate -> {key (one-time), token (sha256), ...}."""
        body: dict[str, Any] = {
            "key_alias": alias,
            "user_id": user_id,
            "models": models,
            "metadata": metadata,
            "spend_logs_metadata": metadata,
        }
        if max_budget_usd is not None:
            body["max_budget"] = max_budget_usd
        if budget_duration is not None:
            body["budget_duration"] = budget_duration
        resp = self._request("POST", "/key/generate", json=body)
        if resp.status_code != 200:
            raise service_unavailable(
                f"LiteLLM /key/generate failed: {resp.status_code} {resp.text[:200]}"
            )
        return resp.json()

    def block_key(self, key_hash: str) -> dict:
        """POST /key/block with the key HASH (D-GOV-02)."""
        resp = self._request("POST", "/key/block", json={"key": key_hash})
        if resp.status_code == 404:
            return {"blocked": False, "missing": True}
        if resp.status_code != 200:
            raise service_unavailable(
                f"LiteLLM /key/block failed: {resp.status_code} {resp.text[:200]}"
            )
        return resp.json()

    def unblock_key(self, key_hash: str) -> dict:
        """POST /key/unblock with the key HASH."""
        resp = self._request("POST", "/key/unblock", json={"key": key_hash})
        if resp.status_code == 404:
            return {"blocked": True, "missing": True}
        if resp.status_code != 200:
            raise service_unavailable(
                f"LiteLLM /key/unblock failed: {resp.status_code} {resp.text[:200]}"
            )
        return resp.json()

    def key_info(self, key_hash: str) -> dict:
        resp = self._request("GET", "/key/info", params={"key": key_hash})
        if resp.status_code != 200:
            raise service_unavailable(
                f"LiteLLM /key/info failed: {resp.status_code} {resp.text[:200]}"
            )
        return resp.json()

    def update_key_budget(self, key_hash: str, max_budget_usd: float) -> dict:
        """POST /key/update with the key HASH to re-sync its native
        ``max_budget`` (D-GOV-16, re-validación t_c4766c4d).

        Without this, a budget RAISE unblocks the key at the platform
        level but the gateway keeps refusing with its native
        ``max_budget`` stamped at issuance ("Budget has been exceeded")
        until the budget_duration window resets — the recovery loop
        breaks against a real proxy. ``None`` is never sent: /key/update
        treats an explicit null as CLEAR (skill pitfall 6).
        """
        resp = self._request(
            "POST", "/key/update",
            json={"key": key_hash, "max_budget": max_budget_usd},
        )
        if resp.status_code != 200:
            raise service_unavailable(
                f"LiteLLM /key/update failed: {resp.status_code} "
                f"{resp.text[:200]}"
            )
        return resp.json()

    # --- Model registration (spec 032, dynamic discovery) -------------------

    def add_model(
        self,
        *,
        model_name: str,
        upstream_model: str,
        api_base: str,
        api_key: str,
        input_cost_per_token: float | None = None,
        output_cost_per_token: float | None = None,
    ) -> dict:
        """POST /model/new — register a BYOK model in the gateway.

        Contract verified against the REAL proxy v1.106.3 (spec 032): the
        response carries ``model_id`` (the uuid /model/delete needs — NOT
        the model name) and requires ``general_settings.store_model_in_db:
        true``. The connection key travels in the request body only; it is
        never logged (D-GOV-12, same rule as /key/generate).
        """
        body: dict[str, Any] = {
            "model_name": model_name,
            "litellm_params": {
                "model": upstream_model,
                "api_base": api_base,
                "api_key": api_key,
            },
        }
        # Declared BYOK pricing (¢/M → USD/token). Verified against the REAL
        # proxy v1.106.3: model_info pricing alone is NOT applied to billing
        # (spend=0 observed live) — the docs' recommended per-deployment
        # override lives in litellm_params; model_info keeps the metadata
        # passthrough (visible in /model/info). Both are sent.
        if input_cost_per_token is not None:
            body["litellm_params"]["input_cost_per_token"] = input_cost_per_token
        if output_cost_per_token is not None:
            body["litellm_params"]["output_cost_per_token"] = output_cost_per_token
        model_info: dict[str, Any] = {}
        if input_cost_per_token is not None:
            model_info["input_cost_per_token"] = input_cost_per_token
        if output_cost_per_token is not None:
            model_info["output_cost_per_token"] = output_cost_per_token
        if model_info:
            body["model_info"] = model_info
        resp = self._request("POST", "/model/new", json=body)
        if resp.status_code != 200:
            raise service_unavailable(
                f"LiteLLM /model/new failed: {resp.status_code} {resp.text[:200]}"
            )
        return resp.json()

    def delete_model(self, model_id: str) -> dict:
        """POST /model/delete {id} — the uuid returned by /model/new."""
        resp = self._request("POST", "/model/delete", json={"id": model_id})
        if resp.status_code == 404:
            return {"deleted": False, "missing": True}
        if resp.status_code != 200:
            raise service_unavailable(
                f"LiteLLM /model/delete failed: {resp.status_code} "
                f"{resp.text[:200]}"
            )
        return resp.json()

    def db_model_ids(self) -> dict[str, str]:
        """GET /model/info -> {model_name: model_id} for DB-registered models.

        Used for idempotent registration (skip models already present) and to
        resolve the id of legacy rows without ``gateway_model_id``.
        """
        resp = self._request("GET", "/model/info")
        if resp.status_code != 200:
            raise service_unavailable(
                f"LiteLLM /model/info failed: {resp.status_code} "
                f"{resp.text[:200]}"
            )
        out: dict[str, str] = {}
        for row in resp.json().get("data", []):
            info = row.get("model_info") or {}
            if info.get("db_model"):
                model_id = info.get("id")
                if model_id:
                    out[row.get("model_name", "")] = str(model_id)
        return out

    def list_gateway_models(self) -> list[str]:
        """GET /v1/models (master key) — the models the gateway can route."""
        resp = self._request("GET", "/v1/models")
        if resp.status_code != 200:
            raise service_unavailable(
                f"LiteLLM /v1/models failed: {resp.status_code} "
                f"{resp.text[:200]}"
            )
        return [m.get("id", "") for m in resp.json().get("data", [])]

    # --- Spend --------------------------------------------------------------

    def spend_logs(
        self, *, start_date: str, end_date: str, page_size: int = 100
    ) -> list[SpendLog]:
        """GET /spend/logs/v2 (paginated; the legacy /spend/logs truncates)."""
        logs: list[SpendLog] = []
        page = 1
        while True:
            resp = self._request(
                "GET", "/spend/logs/v2",
                params={
                    "start_date": start_date,
                    "end_date": end_date,
                    "page": page,
                    "page_size": page_size,
                },
            )
            if resp.status_code != 200:
                raise service_unavailable(
                    f"LiteLLM /spend/logs/v2 failed: {resp.status_code} "
                    f"{resp.text[:200]}"
                )
            rows = resp.json().get("data", [])
            if not rows:
                break
            logs.extend(_parse_log(r) for r in rows)
            if len(rows) < page_size:
                break
            page += 1
        return logs

    def health(self) -> bool:
        try:
            resp = self._request("GET", "/health/liveliness")
            return resp.status_code == 200
        except Exception:
            return False


def _parse_log(row: dict) -> SpendLog:
    """Normalize one /spend/logs/v2 row.

    Field names verified against a REAL LiteLLM proxy (re-validación
    t_c4766c4d): the key hash travels in ``api_key`` (there is NO
    ``user_api_key_hash`` field) and the PUBLIC model name in
    ``model_group`` (``model`` carries the internal litellm_params name,
    e.g. ``openai/glm-5.2``; ``model_group`` is empty for failed requests
    of non-routed models, so ``model`` is the fallback). The declared test
    double (tests/litellm_fake.py) keeps its historical field names — the
    fallback chain supports both. ``spend_logs_metadata`` is NOT stamped
    by the real proxy (observed ``"spend_logs_metadata": null``), so
    tenant/user resolution must go through the key-hash lookup.
    """
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata or "{}")
    return SpendLog(
        request_id=str(row.get("request_id") or ""),
        key_hash=(row.get("user_api_key_hash") or row.get("api_key")
                  or row.get("token")),
        model=row.get("model_group") or row.get("model", ""),
        spend_usd=float(row.get("spend") or 0.0),
        tokens_in=int(row.get("prompt_tokens") or row.get("tokens") or 0),
        tokens_out=int(
            row.get("completion_tokens") or 0
        ),
        occurred_at=str(row.get("startTime") or row.get("request_date") or ""),
        tenant_id=metadata.get("tenant_id"),
        user_id=metadata.get("user_id"),
    )


def get_litellm() -> LiteLLMClient:
    """Client from settings (fail-closed when unconfigured)."""
    settings = get_settings()
    return LiteLLMClient(settings.litellm_base_url, settings.litellm_master_key)

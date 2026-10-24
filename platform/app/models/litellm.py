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
    metadata = row.get("metadata") or {}
    return SpendLog(
        request_id=str(row.get("request_id") or ""),
        key_hash=row.get("user_api_key_hash") or row.get("token"),
        model=row.get("model", ""),
        spend_usd=float(row.get("spend") or 0.0),
        tokens_in=int(row.get("tokens") or row.get("prompt_tokens") or 0),
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

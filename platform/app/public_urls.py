"""Public origins the control plane advertises to employees and their desktops."""
from __future__ import annotations

from fastapi import Request

from .config import get_settings


def public_api_base(request: Request) -> str:
    """Control-plane origin advertised to employees (``api_base`` / download URLs).

    ``TRUST_PUBLIC_API_BASE_URL`` pins it (the console's public edge or a TLS domain): a
    request that reached the API through that edge would otherwise advertise the edge's
    loopback upstream (``http://127.0.0.1:8000``), which no desktop can reach. Empty keeps
    the origin of the request itself, so direct access works unchanged.
    """
    configured = (get_settings().public_api_base_url or "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")

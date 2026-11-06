"""Provider model-provider routes (spec 036 + 043, mandato Miller).

``/api/admin/model-providers`` — the provider registers model sources
(OpenAI-compatible endpoint + key in vault envelope), fetches their models
with a REAL ``GET {base_url}/models`` and enables them per tenant ("qué
accesos le doy a este cliente"). Operator auth (spec 025) + data-driven
permission ``providers.manage`` (D-ONB-02). The API key NEVER appears in
any response, log or audit payload.

Spec 043: test connection (real GET /models with timer) + BYOK gating per
tenant (switch on/off; when off, the option is HIDDEN from the client's
Admin Console — total hiding, not grayed).
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..auth.service import Principal, get_provider_principal
from ..db import get_engine, get_migrator_engine
from ..errors import permission_denied, require_uuid
from . import providers

router = APIRouter(prefix="/api/admin/model-providers")


def _require_manage(principal: Principal) -> None:
    if "providers.manage" not in principal.permissions:
        raise permission_denied("Missing permission: providers.manage")


def _operator(request: Request) -> dict:
    """Operator identity for ops.provider_audit (spec 025 D-PS-07, patrón
    t_4f4b0eb1): get_provider_principal lo deja en request.state — nada de
    parsear display_name; el) break-glass token no carga identidad."""
    op = getattr(request.state, "provider_operator", None) or {}
    return {
        "operator_id": op.get("id"),
        "username": op.get("username"),
    }


class CreateBody(BaseModel):
    name: str
    base_url: str
    api_key: str
    display_name: str | None = None
    input_price_cents_per_m: float | None = None
    output_price_cents_per_m: float | None = None


class EnableBody(BaseModel):
    tenant: str  # slug or uuid


class StatusBody(BaseModel):
    status: str  # active | disabled


class ByokPolicyBody(BaseModel):
    byok_allowed: bool


# --- Static routes (MUST come before /{provider_id} to avoid capture) -------

@router.get("")
def list_model_providers(
    request: Request,
    principal: Principal = Depends(get_provider_principal),
    engine: Engine = Depends(get_engine),
    migrator_engine: Engine = Depends(get_migrator_engine),
):
    _require_manage(principal)
    # Cross-tenant read (BYPASSRLS migrator): the model count lives in
    # catalog.model_catalog under per-tenant RLS (AC-032-07) and the
    # provider plane has no tenant context.
    return {"providers": providers.list_providers(engine, migrator_engine)}


@router.post("", status_code=201)
def create_model_provider(
    body: CreateBody,
    request: Request,
    principal: Principal = Depends(get_provider_principal),
    engine: Engine = Depends(get_engine),
):
    _require_manage(principal)
    return providers.create_provider(
        engine,
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key,
        display_name=body.display_name,
        input_price_cents_per_m=body.input_price_cents_per_m,
        output_price_cents_per_m=body.output_price_cents_per_m,
        operator=_operator(request),
    )


# --- BYOK gating per tenant (spec 043, D-043-02) ---------------------------
# MUST be declared before /{provider_id} routes or FastAPI captures them.

@router.get("/byok-policy")
def get_byok_policy(
    request: Request,
    principal: Principal = Depends(get_provider_principal),
    migrator_engine: Engine = Depends(get_migrator_engine),
):
    """List all tenants with their BYOK allowed flag (cross-tenant)."""
    _require_manage(principal)
    return {"tenants": providers.list_byok_policy(migrator_engine)}


@router.put("/byok-policy/{tenant_id}")
def put_byok_policy(
    tenant_id: str,
    body: ByokPolicyBody,
    request: Request,
    principal: Principal = Depends(get_provider_principal),
    engine: Engine = Depends(get_engine),
    migrator_engine: Engine = Depends(get_migrator_engine),
):
    """Set BYOK allowed/disallowed for a tenant + audit."""
    _require_manage(principal)
    return providers.set_byok_policy(
        engine, migrator_engine, tenant_id, body.byok_allowed,
        operator=_operator(request),
    )


# --- Parameterized routes (/{provider_id}) ----------------------------------

@router.get("/{provider_id}")
def get_model_provider(
    provider_id: str,
    request: Request,
    principal: Principal = Depends(get_provider_principal),
    engine: Engine = Depends(get_engine),
    migrator_engine: Engine = Depends(get_migrator_engine),
):
    _require_manage(principal)
    provider_id = require_uuid(provider_id, "provider_id")
    # Cross-tenant read for the catalog rows (BYPASSRLS migrator, same
    # reason as the listing): RLS would hide every tenant's model row.
    detail = providers.get_provider(engine, provider_id, migrator_engine)
    # Resolve tenant slugs cross-tenant (migrator read, D-ONB-10).
    with migrator_engine.connect() as conn:
        rows = {
            str(r["id"]): r["slug"]
            for r in conn.execute(
                text("SELECT id, slug FROM tenants")
            ).mappings()
        }
    for t in detail["tenants"]:
        t["slug"] = rows.get(t["tenant_id"])
    return detail


@router.post("/{provider_id}/refresh")
def refresh_model_provider(
    provider_id: str,
    request: Request,
    principal: Principal = Depends(get_provider_principal),
    engine: Engine = Depends(get_engine),
):
    _require_manage(principal)
    provider_id = require_uuid(provider_id, "provider_id")
    return providers.refresh_provider(
        engine, provider_id, operator=_operator(request)
    )


@router.post("/{provider_id}/tenants", status_code=201)
def enable_for_tenant(
    provider_id: str,
    body: EnableBody,
    request: Request,
    principal: Principal = Depends(get_provider_principal),
    engine: Engine = Depends(get_engine),
    migrator_engine: Engine = Depends(get_migrator_engine),
):
    _require_manage(principal)
    provider_id = require_uuid(provider_id, "provider_id")
    return providers.enable_for_tenant(
        engine, migrator_engine, provider_id, body.tenant,
        operator=_operator(request),
    )


@router.delete("/{provider_id}/tenants/{tenant_id}")
def disable_for_tenant(
    provider_id: str,
    tenant_id: str,
    request: Request,
    principal: Principal = Depends(get_provider_principal),
    engine: Engine = Depends(get_engine),
    migrator_engine: Engine = Depends(get_migrator_engine),
):
    _require_manage(principal)
    provider_id = require_uuid(provider_id, "provider_id")
    tenant_id = require_uuid(tenant_id, "tenant_id")
    return providers.disable_for_tenant(
        engine, migrator_engine, provider_id, tenant_id,
        operator=_operator(request),
    )


@router.post("/{provider_id}/status")
def set_status(
    provider_id: str,
    body: StatusBody,
    request: Request,
    principal: Principal = Depends(get_provider_principal),
    engine: Engine = Depends(get_engine),
    migrator_engine: Engine = Depends(get_migrator_engine),
):
    _require_manage(principal)
    provider_id = require_uuid(provider_id, "provider_id")
    return providers.set_provider_status(
        engine, migrator_engine, provider_id, body.status,
        operator=_operator(request),
    )


# --- Test connection (spec 043, D-043-01) -----------------------------------

@router.post("/{provider_id}/test-connection")
def test_connection(
    provider_id: str,
    request: Request,
    principal: Principal = Depends(get_provider_principal),
    engine: Engine = Depends(get_engine),
):
    """REAL GET {base_url}/models with timer — returns models, latency or
    a clear error (never 5xx for upstream failures). Key never in response."""
    _require_manage(principal)
    provider_id = require_uuid(provider_id, "provider_id")
    return providers.test_provider_connection(engine, provider_id)

"""Provider admin routes: tenant provisioning (spec 004, REQ-ONB-030…032)."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..auth.service import Principal, get_provider_principal
from ..config import get_settings
from ..db import get_engine, get_migrator_engine
from ..errors import not_found, permission_denied
from .healthcheck import healthcheck
from .keycloak_admin import KeycloakAdminClient
from .provider_service import list_tenants, set_tenant_status
from .provision import (
    ProvisionRequest,
    find_tenant_by_slug,
    provision,
    tenant_state,
)

router = APIRouter(prefix="/api/admin/tenants")


class ProvisionBody(BaseModel):
    organization_name: str
    admin_email: str
    initial_budget_usd: float
    model_plan: str | list[str] = "standard"
    branding: dict | None = None
    departments: list[str] | None = None
    slug: str | None = None
    admin_display_name: str | None = None


def _require_provision_permission(principal: Principal) -> None:
    """Data-driven check against catalog.role_permissions (D-ONB-02):
    removing the platform_admin -> tenants.provision row must deny here."""
    if "tenants.provision" not in principal.permissions:
        raise permission_denied("Missing permission: tenants.provision")


def _kc() -> KeycloakAdminClient:
    return KeycloakAdminClient(get_settings())


@router.post("")
def provision_tenant(
    body: ProvisionBody,
    principal: Principal = Depends(get_provider_principal),
):
    """Provision a complete tenant (REQ-ONB-001).

    201 on creation; 200 when the slug already exists (idempotent re-run,
    no credentials re-delivered). Provider token + tenants.provision.
    """
    _require_provision_permission(principal)
    result = provision(
        get_engine(),
        get_migrator_engine(),
        _kc(),
        ProvisionRequest(
            organization_name=body.organization_name,
            admin_email=body.admin_email,
            initial_budget_usd=body.initial_budget_usd,
            model_plan=body.model_plan,
            branding=body.branding,
            departments=body.departments,
            slug=body.slug,
            admin_display_name=body.admin_display_name,
        ),
    )
    return JSONResponse(
        status_code=201 if result["created"] else 200, content=result
    )


@router.get("")
def list_all_tenants(
    principal: Principal = Depends(get_provider_principal),
):
    """Lista de tenants con stats (spec 014 REQ-PC-001, D-PC-04).

    Filas vía migrator (cross-tenant, solo el proveedor); stats por tenant
    leídos como trust_app dentro del scope de cada uno.
    """
    _require_provision_permission(principal)
    return list_tenants(get_engine(), get_migrator_engine())


@router.post("/{slug}/suspend")
def suspend_tenant(
    slug: str, principal: Principal = Depends(get_provider_principal)
):
    """Suspende un tenant (spec 014 REQ-PC-002, D-PC-05).

    Transaccional + auditado (tenant.suspended, actor provider); idempotente.
    El core niega cada request del tenant con 403 tenant_suspended.
    """
    _require_provision_permission(principal)
    return set_tenant_status(
        get_engine(), get_migrator_engine(), slug, "suspended"
    )


@router.post("/{slug}/reactivate")
def reactivate_tenant(
    slug: str, principal: Principal = Depends(get_provider_principal)
):
    """Reactiva un tenant suspendido (spec 014 REQ-PC-003, D-PC-05)."""
    _require_provision_permission(principal)
    return set_tenant_status(
        get_engine(), get_migrator_engine(), slug, "active"
    )


@router.get("/{slug}")
def get_tenant(slug: str, principal: Principal = Depends(get_provider_principal)):
    """Tenant state (cross-tenant slug lookup via the migrator connection,
    state read as trust_app inside the tenant scope — D-ONB-10)."""
    _require_provision_permission(principal)
    existing = find_tenant_by_slug(get_migrator_engine(), slug)
    if existing is None:
        raise not_found("Tenant not found.")
    return {"tenant": tenant_state(get_engine(), str(existing["id"]))}


@router.get("/{slug}/healthcheck")
def tenant_healthcheck(
    slug: str, principal: Principal = Depends(get_provider_principal)
):
    """Automated post-provisioning verification (REQ-ONB-032)."""
    _require_provision_permission(principal)
    return healthcheck(get_engine(), get_migrator_engine(), _kc(), slug)

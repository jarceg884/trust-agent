"""Connector domain (spec 006): catalog, governance view and instances."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth.service import Principal, get_principal, get_tx, require_mfa
from . import service

router = APIRouter(prefix="/api/connectors", dependencies=[Depends(require_mfa)])


class InstanceCreate(BaseModel):
    connector_slug: str = Field(min_length=1, max_length=60)
    credential_id: str | None = None
    mode: str | None = Field(default=None, max_length=20)
    config: dict = Field(default_factory=dict)


@router.get("")
def list_connectors(conn: Connection = Depends(get_tx)):
    """Global connector catalog (REQ-CON-001: initial set of 6)."""
    rows = conn.execute(
        text(
            "SELECT slug, name, category, capabilities, required_scopes, status "
            "FROM catalog.connector_catalog ORDER BY slug"
        )
    ).mappings()
    return {"connectors": [dict(r) for r in rows]}


@router.get("/available")
def list_available(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Catalog filtered by the principal's effective policy (REQ-CON-003)."""
    return {"connectors": service.list_available(conn, principal)}


@router.post("/instances", status_code=201)
def create_instance(
    payload: InstanceCreate,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Connect a connector instance (governance-gated, REQ-CON-002/004)."""
    meta, reactivated = service.create_instance(
        conn, principal,
        connector_slug=payload.connector_slug,
        credential_id=payload.credential_id,
        mode=payload.mode,
        config=payload.config,
    )
    from fastapi.responses import JSONResponse

    return JSONResponse(meta, status_code=200 if reactivated else 201)


@router.get("/instances")
def list_instances(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return {"instances": service.list_instances(conn, principal)}


@router.get("/instances/{instance_id}")
def get_instance(
    instance_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return service.get_instance(conn, principal, instance_id)


@router.delete("/instances/{instance_id}")
def disconnect_instance(
    instance_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Disconnect an instance (idempotent; audited, REQ-CON-005)."""
    return service.disconnect_instance(conn, principal, instance_id)

"""Connector domain (spec 006): catalog, governance view and instances."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..errors import optional_uuid, require_uuid
from . import service

router = APIRouter(prefix="/api/connectors", dependencies=[Depends(require_mfa)])


class InstanceCreate(BaseModel):
    connector_slug: str = Field(min_length=1, max_length=60)
    credential_id: str | None = None
    mode: str | None = Field(default=None, max_length=20)
    config: dict = Field(default_factory=dict)


@router.get("")
def list_connectors(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Global connector catalog (REQ-CON-001) + per-entry policy state of
    the caller (spec 037 REQ-037-009: ``allowed_for_caller``)."""
    return {"connectors": service.catalog_with_policy(conn, principal)}


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
    credential_id = optional_uuid(payload.credential_id, "credential_id")
    meta, reactivated = service.create_instance(
        conn, principal,
        connector_slug=payload.connector_slug,
        credential_id=credential_id,
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
    instance_id = require_uuid(instance_id, "instance_id")  # t_378503a1
    return service.get_instance(conn, principal, instance_id)


@router.delete("/instances/{instance_id}")
def disconnect_instance(
    instance_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Disconnect an instance (idempotent; audited, REQ-CON-005)."""
    instance_id = require_uuid(instance_id, "instance_id")  # t_378503a1
    return service.disconnect_instance(conn, principal, instance_id)

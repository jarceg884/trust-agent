"""Vault API (arquitectura §5.5, spec 006).

Every route requires a verified-MFA session (MfaGate, D-CP-07). Secrets are
delivered ONE time in the response that generates them (create/rotate); reads
return metadata only (REQ-VLT-007).
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from ..auth.service import Principal, get_principal, get_tx, require_mfa
from . import service

router = APIRouter(
    prefix="/api/vault/credentials", dependencies=[Depends(require_mfa)]
)


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    credential_type: str = Field(min_length=1, max_length=40)
    secret_value: str = Field(min_length=1, max_length=65536)
    connector_slug: str | None = None
    scope: str | None = Field(default=None, max_length=200)


class CredentialRotate(BaseModel):
    new_secret_value: str | None = Field(default=None, min_length=1,
                                         max_length=65536)


@router.post("", status_code=201)
def create_credential(
    payload: CredentialCreate,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Register an encrypted credential -> 201 with the value ONE time."""
    return service.create_credential(
        conn, principal,
        name=payload.name,
        credential_type=payload.credential_type,
        secret_value=payload.secret_value,
        connector_slug=payload.connector_slug,
        scope=payload.scope,
    )


@router.get("")
def list_credentials(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Metadata only, scoped by role visibility (REQ-VLT-007)."""
    return {"credentials": service.list_credentials(conn, principal)}


@router.get("/{credential_id}")
def get_credential(
    credential_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return service.get_credential(conn, principal, credential_id)


@router.post("/{credential_id}/rotate")
def rotate_credential(
    credential_id: str,
    payload: CredentialRotate | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Rotate -> new value ONE time; the previous value dies (D-VC-05)."""
    return service.rotate_credential(
        conn, principal, credential_id,
        new_secret_value=payload.new_secret_value if payload else None,
    )


@router.post("/{credential_id}/revoke")
def revoke_credential(
    credential_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Revoke + immediate cutoff of dependent instances (REQ-VLT-005)."""
    return service.revoke_credential(conn, principal, credential_id)


@router.post("/{credential_id}/use")
def use_credential(
    credential_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Runtime injection channel (REQ-VLT-003): decrypted value to the
    credential owner's connector runtime, in memory, audited."""
    return service.use_credential(conn, principal, credential_id)

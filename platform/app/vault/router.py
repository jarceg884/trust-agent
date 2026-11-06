"""Vault API (arquitectura §5.5, spec 006).

Every route requires a verified-MFA session (MfaGate, D-CP-07). Secrets are
delivered ONE time in the response that generates them (create/rotate); reads
return metadata only (REQ-VLT-007).
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection

from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..errors import require_uuid
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
    folder_id: str | None = None


class CredentialRotate(BaseModel):
    new_secret_value: str | None = Field(default=None, min_length=1,
                                         max_length=65536)


class CredentialMove(BaseModel):
    folder_id: str | None = None


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_folder_id: str | None = None


class FolderRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


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
        folder_id=payload.folder_id,
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
    credential_id = require_uuid(credential_id, "credential_id")  # t_378503a1
    return service.get_credential(conn, principal, credential_id)


@router.post("/{credential_id}/rotate")
def rotate_credential(
    credential_id: str,
    payload: CredentialRotate | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Rotate -> new value ONE time; the previous value dies (D-VC-05)."""
    credential_id = require_uuid(credential_id, "credential_id")  # t_378503a1
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
    credential_id = require_uuid(credential_id, "credential_id")  # t_378503a1
    return service.revoke_credential(conn, principal, credential_id)


@router.post("/{credential_id}/use")
def use_credential(
    credential_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Runtime injection channel (REQ-VLT-003): decrypted value to the
    credential owner's connector runtime, in memory, audited."""
    credential_id = require_uuid(credential_id, "credential_id")  # t_378503a1
    return service.use_credential(conn, principal, credential_id)


@router.post("/{credential_id}/move")
def move_credential(
    credential_id: str,
    payload: CredentialMove,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Move a secret into a folder (or back to root with folder_id=null).

    Owner-only, like rotate — organizing the tree is a vault-tree edit."""
    credential_id = require_uuid(credential_id, "credential_id")
    return service.move_credential(
        conn, principal, credential_id, folder_id=payload.folder_id,
    )


# --- Folders (Infisical-style tree) -----------------------------------------

folders_router = APIRouter(
    prefix="/api/vault/folders", dependencies=[Depends(require_mfa)]
)


@folders_router.get("")
def list_folders_route(
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return {"folders": service.list_folders(conn, principal)}


@folders_router.post("", status_code=201)
def create_folder_route(
    payload: FolderCreate,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    return service.create_folder(
        conn, principal, name=payload.name,
        parent_folder_id=payload.parent_folder_id,
    )


@folders_router.patch("/{folder_id}")
def rename_folder_route(
    folder_id: str,
    payload: FolderRename,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    folder_id = require_uuid(folder_id, "folder_id")
    return service.rename_folder(conn, principal, folder_id, name=payload.name)


@folders_router.delete("/{folder_id}")
def delete_folder_route(
    folder_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    """Deletes only an EMPTY folder (409 otherwise) — non-destructive by
    default, matching the vault's no-silent-data-loss contract."""
    folder_id = require_uuid(folder_id, "folder_id")
    return service.delete_folder(conn, principal, folder_id)

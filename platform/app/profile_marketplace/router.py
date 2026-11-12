"""Admin Console → Agentes: marketplace de perfiles de Hermes (spec 045).

Mounted at ``/api/marketplace/profiles``: ``/api/agent-profiles`` belongs to spec 020's default
templates (kept read-compatible while the console migrates, D-045-08).

Viewing the marketplace (and assigning its profiles in a policy) is for org and department
admins; importing, editing, archiving and downloading packages is org_admin only — a profile's
content runs on every device its policy reaches.

Import body: the raw ``.tar.gz`` produced by ``hermes profile export`` (``application/gzip`` or
``application/octet-stream``), read with a hard size cap before anything touches the database.
"""
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.engine import Connection

from ..auth.service import Principal, get_principal, get_tx, require_mfa
from ..errors import permission_denied, validation_failed
from . import service
from .sanitize import DEFAULT_LIMITS

router = APIRouter(prefix="/api/marketplace/profiles", dependencies=[Depends(require_mfa)])


def _require_admin(principal: Principal) -> None:
    if not {"org_admin", "dept_admin"} & set(principal.role_keys):
        raise permission_denied("Solo los administradores ven el marketplace de perfiles.")


def _require_org_admin(principal: Principal) -> None:
    if "org_admin" not in principal.role_keys:
        raise permission_denied(
            "Solo el administrador de la organización importa, edita o archiva perfiles."
        )


async def _upload_body(request: Request) -> bytes:
    limit = DEFAULT_LIMITS.max_compressed
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise validation_failed("El archivo supera el tamaño máximo permitido.", {"rule": "too_large"})
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise validation_failed("El archivo supera el tamaño máximo permitido.", {"rule": "too_large"})
        chunks.append(chunk)
    if not total:
        raise validation_failed("Adjunta el .tar.gz exportado con `hermes profile export`.", {"rule": "empty"})
    return b"".join(chunks)


class ProfilePatch(BaseModel):
    name: str | None = None
    description: str | None = None


class ArchiveBody(BaseModel):
    confirm: bool = False


@router.get("")
def list_profiles(
    include_archived: bool = False,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_admin(principal)
    service.ensure_factory_profiles(conn, principal.tenant_id)
    return {"profiles": service.list_profiles(conn, include_archived)}


@router.post("/import", status_code=201)
def import_profile(
    response: Response,
    slug: str | None = None,
    data: bytes = Depends(_upload_body),
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_org_admin(principal)
    result = service.import_package(conn, principal, data, slug_override=slug or None)
    if result["unchanged"]:
        response.status_code = 200
    return result


@router.get("/{profile_id}")
def get_profile(
    profile_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_admin(principal)
    return service.get_profile(conn, profile_id)


@router.put("/{profile_id}")  # PUT: the console's BFF proxy forwards GET/POST/PUT/DELETE
def update_profile(
    profile_id: str,
    body: ProfilePatch,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_org_admin(principal)
    return service.update_profile(conn, principal, profile_id, body.name, body.description)


@router.post("/{profile_id}/archive")
def archive_profile(
    profile_id: str,
    body: ArchiveBody | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_org_admin(principal)
    return service.archive_profile(conn, principal, profile_id, bool(body and body.confirm))


@router.post("/{profile_id}/restore")
def restore_profile(
    profile_id: str,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_org_admin(principal)
    return service.restore_profile(conn, principal, profile_id)


@router.get("/{profile_id}/package")
def download_package(
    profile_id: str,
    version: int | None = None,
    principal: Principal = Depends(get_principal),
    conn: Connection = Depends(get_tx),
):
    _require_org_admin(principal)
    path, sha256, slug, resolved = service.package_path(conn, profile_id, version)
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=f"{slug}-v{resolved}.tar.gz",
        headers={"ETag": f'"{sha256}"', "X-Content-SHA256": sha256, "Cache-Control": "no-store"},
    )

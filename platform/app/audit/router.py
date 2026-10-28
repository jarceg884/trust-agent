"""Audit endpoints: filterable query API + chain verifier (spec 008).

- GET  /api/audit/events         — query within the caller's role scope
                                   (permission audit.view, REQ-AU-001/002)
- GET  /api/audit/events/export  — CSV/JSON export of the filtered range
                                   (REQ-AU-003)
- POST /api/admin/audit/verify   — chain verification (spec 003 contract),
                                   now emitting audit.verified (REQ-AU-011)
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..auth.rbac import dept_scope, require_permission, require_role
from ..auth.service import Principal, get_tx, require_mfa
from ..errors import optional_uuid, validation_failed
from . import service

router = APIRouter(
    prefix="/api/audit",
    dependencies=[Depends(require_mfa), Depends(require_permission("audit.view"))],
)


def _filters(
    actor_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    target_id: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None),
    department_id: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    q: Optional[str] = Query(None),
) -> dict:
    # t_378503a1: UUID filters are format-checked (422, not a SQL 500).
    # target_id stays free-form: audit targets are not always UUIDs
    # (e.g. budget level names like "org"/"dept").
    return {
        "actor_id": optional_uuid(actor_id, "actor_id"),
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "device_id": optional_uuid(device_id, "device_id"),
        "department_id": optional_uuid(department_id, "department_id"),
        "since": since,
        "until": until,
        "q": q,
    }


@router.get("/events")
def events(
    principal: Principal = Depends(require_permission("audit.view")),
    conn: Connection = Depends(get_tx),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    filters: dict = Depends(_filters),
):
    """Filterable audit query within the role scope (REQ-AU-001/002)."""
    return service.query_events(
        conn,
        role_keys=principal.role_keys,
        departments=dept_scope(principal),
        user_id=principal.user_id,
        limit=limit,
        offset=offset,
        **filters,
    )


@router.get("/events/export")
def export(
    principal: Principal = Depends(require_permission("audit.view")),
    conn: Connection = Depends(get_tx),
    format: str = Query("csv", pattern="^(csv|json)$"),
    filters: dict = Depends(_filters),
):
    """CSV/JSON export of the filtered range within the role scope (REQ-AU-003)."""
    items = service.export_rows(
        conn,
        role_keys=principal.role_keys,
        departments=dept_scope(principal),
        user_id=principal.user_id,
        **filters,
    )
    if format == "json":
        return JSONResponse(content=items)
    csv_text = service.rows_to_csv(items)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-events.csv"'},
    )


# --- Chain verifier (spec 003 contract surface, unchanged path/role) ---------

admin_router = APIRouter(
    prefix="/api/admin/audit",
    dependencies=[Depends(require_role("org_admin"))],
)


@admin_router.post("/verify")
def verify_chain(
    principal: Principal = Depends(require_role("org_admin")),
    conn: Connection = Depends(get_tx),
):
    """Verify the tenant's audit hash chain -> ok | broken + seq (REQ-AUD-007).

    Every run is itself audited as audit.verified (D-AU-05, REQ-AU-011).
    """
    result = service.verify_chain(conn, principal.tenant_id)
    service.emit_verification(
        conn, tenant_id=principal.tenant_id, result=result, source="api"
    )
    return result


# --- Retention (REQ-AUD-006, spec 013 D-013-04) --------------------------------

RETENTION_FLOOR_DAYS = 365


class RetentionBody(BaseModel):
    retention_days: int


def _current_retention(conn: Connection) -> int:
    row = conn.execute(
        text("SELECT retention_days FROM tenants")
    ).mappings().first()
    return int(row["retention_days"])


@admin_router.get("/retention")
def get_retention(
    principal: Principal = Depends(require_role("org_admin")),
    conn: Connection = Depends(get_tx),
):
    """Current audit retention of the tenant (minimum: 365 days)."""
    return {
        "retention_days": _current_retention(conn),
        "minimum_days": RETENTION_FLOOR_DAYS,
    }


@admin_router.put("/retention")
def put_retention(
    body: RetentionBody,
    principal: Principal = Depends(require_role("org_admin")),
    conn: Connection = Depends(get_tx),
):
    """Set the tenant's audit retention. The 365-day floor is INVARIABLE:
    anything below it is rejected with 422 (REQ-AUD-006). The change is
    audited as audit.retention_updated."""
    if body.retention_days < RETENTION_FLOOR_DAYS:
        raise validation_failed(
            f"Audit retention must be at least {RETENTION_FLOOR_DAYS} days "
            f"(invariable floor, REQ-AUD-006); got {body.retention_days}."
        )
    previous = _current_retention(conn)
    conn.execute(
        text("UPDATE tenants SET retention_days = :d, updated_at = now()"),
        {"d": body.retention_days},
    )
    service.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role="org_admin",
        actor_id=principal.user_id,
        action="audit.retention_updated",
        target_type="tenant",
        target_id=principal.tenant_id,
        payload={
            "retention_days": body.retention_days,
            "previous_retention_days": previous,
        },
    )
    return {
        "retention_days": body.retention_days,
        "minimum_days": RETENTION_FLOOR_DAYS,
        "previous_retention_days": previous,
    }

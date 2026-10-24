"""Job status endpoint (REQ-ORG-002: status queryable by job id)."""
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Connection

from ..auth.service import get_tx, require_mfa
from .service import get_job

router = APIRouter(prefix="/api/jobs", dependencies=[Depends(require_mfa)])


@router.get("/{job_id}")
def job_status(job_id: str, conn: Connection = Depends(get_tx)):
    # Tenant scope via RLS: a job from another tenant simply does not exist
    # -> 404 without revealing existence (D-CP-09).
    return get_job(conn, job_id)

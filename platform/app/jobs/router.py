"""Job status endpoint (REQ-ORG-002: status queryable by job id)."""
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Connection

from ..auth.service import get_tx, require_mfa
from ..errors import require_uuid
from .service import get_job

router = APIRouter(prefix="/api/jobs", dependencies=[Depends(require_mfa)])


@router.get("/{job_id}")
def job_status(job_id: str, conn: Connection = Depends(get_tx)):
    job_id = require_uuid(job_id, "job_id")  # t_378503a1: 422, not 500
    # Tenant scope via RLS: a job from another tenant simply does not exist
    # -> 404 without revealing existence (D-CP-09).
    return get_job(conn, job_id)

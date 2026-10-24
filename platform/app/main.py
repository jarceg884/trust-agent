"""Trust Agent control plane — FastAPI application factory (arq. §3.1)."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from . import errors
from .audit.router import admin_router as audit_admin_router
from .audit.router import router as audit_router
from .auth.router import me_router, router as auth_router
from .config import get_settings, validate_device_token_secret
from .connectors.router import router as connectors_router
from .db import get_engine
from .devices.router import router as devices_router
from .errors import ApiError, new_request_id, request_id_var
from .governance.router import router as governance_router
from .jobs.router import router as jobs_router
from .models.router import router as models_router
from .org.router import router as org_router
from .tenants.router import router as tenants_router
from .vault.router import router as vault_router


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Wrap framework HTTP errors in the standard envelope (arq. §5.2)."""
    reason = {
        401: "invalid_token", 403: "permission_denied", 404: "not_found",
        405: "method_not_allowed", 422: "validation_failed",
        429: "rate_limited",
    }.get(exc.status_code, "error")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "error", "reason": reason,
                           "message": str(exc.detail),
                           "request_id": request_id_var.get()}},
    )


def create_app() -> FastAPI:
    # Fail-closed boot (constitution IV): get_settings() refuses to start
    # without TRUST_DATABASE_URL (REQ-CP-002) and the device-token signing
    # key must be a real secret — never empty or a "change-me" placeholder
    # (QA t_993c71d5).
    validate_device_token_secret(get_settings())

    app = FastAPI(
        title="Trust Agent Control Plane",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = new_request_id()
        request_id_var.set(request_id)
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    app.add_exception_handler(ApiError, errors.api_error_handler)
    app.add_exception_handler(
        RequestValidationError, errors.validation_error_handler
    )
    app.add_exception_handler(
        StarletteHTTPException, http_exception_handler
    )
    app.add_exception_handler(Exception, errors.unhandled_error_handler)

    @app.get("/healthz")
    def healthz():
        """Liveness + database check as trust_app (REQ-SEC-009)."""
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            raise errors.service_unavailable("Database is not reachable.")
        return {"status": "ok", "database": "up"}

    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(jobs_router)
    app.include_router(audit_router)
    app.include_router(audit_admin_router)
    app.include_router(models_router)
    app.include_router(connectors_router)
    app.include_router(devices_router)
    app.include_router(vault_router)
    app.include_router(tenants_router)
    app.include_router(governance_router)
    app.include_router(org_router)
    return app


app = create_app()

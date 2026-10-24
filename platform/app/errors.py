"""Error envelope (arquitectura §5.2) — the single API error contract.

Every failure returns:
    {"error": {"code": ..., "reason": ..., "message": ..., "request_id": ...}}

`reason` values are a stable API contract. Cross-tenant forged IDs answer
404 (never reveal existence); business validation answers 422 with detail.
"""
import uuid
from contextvars import ContextVar

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_410_GONE,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_503_SERVICE_UNAVAILABLE,
)

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:20]


class ApiError(Exception):
    """Domain error carrying the stable reason code and HTTP status."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        reason: str,
        message: str,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.reason = reason
        self.message = message
        self.retry_after = retry_after


def invalid_token(message: str = "Invalid or expired token.") -> ApiError:
    # No oracle about which claim failed (REQ-SEC-002).
    return ApiError(
        status_code=HTTP_401_UNAUTHORIZED, code="unauthorized",
        reason="invalid_token", message=message,
    )


def mfa_required() -> ApiError:
    return ApiError(
        status_code=HTTP_403_FORBIDDEN, code="forbidden",
        reason="mfa_required",
        message="Multi-factor authentication is required for this organization.",
    )


def permission_denied(message: str = "You do not have permission to perform this action.") -> ApiError:
    return ApiError(
        status_code=HTTP_403_FORBIDDEN, code="forbidden",
        reason="permission_denied", message=message,
    )


def tenant_suspended() -> ApiError:
    return ApiError(
        status_code=HTTP_403_FORBIDDEN, code="forbidden",
        reason="tenant_suspended",
        message="This organization is currently suspended.",
    )


def device_not_authorized() -> ApiError:
    return ApiError(
        status_code=HTTP_401_UNAUTHORIZED, code="unauthorized",
        reason="device_not_authorized",
        message="The device is not authorized to access this resource.",
    )


def csrf_required() -> ApiError:
    return ApiError(
        status_code=HTTP_403_FORBIDDEN, code="forbidden",
        reason="csrf_required",
        message="Missing or invalid CSRF token for a cookie-authenticated mutation.",
    )


def not_found(message: str = "Resource not found.") -> ApiError:
    # Cross-tenant and cross-scope lookups land here (D-CP-09).
    return ApiError(
        status_code=HTTP_404_NOT_FOUND, code="not_found",
        reason="not_found", message=message,
    )


def model_not_approved(message: str = "This model is not approved for your organization.") -> ApiError:
    # REQ-MDL-001: only catalog-approved models effective for the user.
    return ApiError(
        status_code=HTTP_403_FORBIDDEN, code="forbidden",
        reason="model_not_approved", message=message,
    )


def credential_type_not_allowed(message: str = "Credential type not allowed by policy.") -> ApiError:
    # Governance gate on vault_allowed_credential_types (REQ-VLT-004, arq. §5.2).
    return ApiError(
        status_code=HTTP_422_UNPROCESSABLE_ENTITY, code="invalid_request",
        reason="credential_type_not_allowed", message=message,
    )


def connector_not_allowed(message: str = "Connector not allowed by policy.") -> ApiError:
    # Governance gate on allowed_connectors / messaging_permissions (REQ-CON-002/004).
    return ApiError(
        status_code=HTTP_403_FORBIDDEN, code="forbidden",
        reason="connector_not_allowed", message=message,
    )


def messaging_mode_not_allowed(message: str = "Messaging mode exceeds the policy mode.") -> ApiError:
    # Extension of the §5.2 contract, documented in spec 006 (D-VC-03).
    return ApiError(
        status_code=HTTP_422_UNPROCESSABLE_ENTITY, code="invalid_request",
        reason="messaging_mode_not_allowed", message=message,
    )


def validation_failed(message: str) -> ApiError:
    return ApiError(
        status_code=HTTP_422_UNPROCESSABLE_ENTITY, code="invalid_request",
        reason="validation_failed", message=message,
    )


def gone(message: str = "This resource was already consumed.") -> ApiError:
    return ApiError(
        status_code=HTTP_410_GONE, code="gone", reason="gone", message=message,
    )


def conflict(message: str) -> ApiError:
    return ApiError(
        status_code=HTTP_409_CONFLICT, code="conflict", reason="conflict",
        message=message,
    )


def rate_limited(message: str, retry_after: int | None = None) -> ApiError:
    return ApiError(
        status_code=HTTP_429_TOO_MANY_REQUESTS, code="too_many_requests",
        reason="rate_limited", message=message, retry_after=retry_after,
    )


def service_unavailable(message: str) -> ApiError:
    return ApiError(
        status_code=HTTP_503_SERVICE_UNAVAILABLE, code="unavailable",
        reason="service_unavailable", message=message,
    )


def _envelope(code: str, reason: str, message: str) -> dict:
    return {"error": {"code": code, "reason": reason, "message": message,
                      "request_id": request_id_var.get()}}


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    headers = (
        {"Retry-After": str(exc.retry_after)}
        if getattr(exc, "retry_after", None) is not None else None
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.reason, exc.message),
        headers=headers,
    )


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_422_UNPROCESSABLE_ENTITY,
        content=_envelope(
            "invalid_request", "validation_failed",
            "Request validation failed: " + "; ".join(
                f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}" for e in exc.errors()
            ),
        ),
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=_envelope("internal_error", "internal_error",
                          "An unexpected error occurred."),
    )

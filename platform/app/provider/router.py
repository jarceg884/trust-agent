"""Router del super-user del proveedor (spec 025, mandato Miller).

Login usuario+contraseña(+TOTP), logout, me, cambio de password (required
action del primer login) y MFA opcional (enroll/confirm/disable). Todas las
rutas son del plano proveedor: sin tenant, sin RLS (D-PS-01).

La sesión vive en PostgreSQL (``ops.provider_sessions``) y el token
``pst1…`` viaja al navegador SOLO dentro de la cookie httpOnly que fija el
BFF del Provider Console — este router devuelve el token una vez en el
login para que el BFF lo guarde; ningún GET lo vuelve a exponer (D-PS-04).
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..db import get_engine
from . import operator

router = APIRouter(prefix="/api/provider", tags=["provider"])


def _session_password_changed(
    session: dict = Depends(operator.get_operator_session),
) -> dict:
    """Sesión de operador + gate del required action (D-PS-08): 403 hasta
    completar el cambio de password del primer login."""
    return operator.require_password_changed(session)


def _client_ip(request: Request) -> str | None:
    """Solo IPs parseables (la columna ip es inet) — mismo patrón que
    auth/router.py: el host de TestClient ("testclient") no lo es."""
    import ipaddress

    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _client_meta(request: Request) -> dict:
    return {
        "ip": _client_ip(request),
        "user_agent": request.headers.get("user-agent"),
    }


class LoginBody(BaseModel):
    username: str
    password: str
    totp_code: str | None = None


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


class TotpBody(BaseModel):
    code: str


class MfaDisableBody(BaseModel):
    password: str
    code: str


@router.post("/auth/login")
def login(body: LoginBody, request: Request):
    """Login del operador (REQ-PS-003/004/008): throttle persistido,
    sesión en PostgreSQL, audit en ``ops.provider_audit``."""
    meta = _client_meta(request)
    return operator.login(
        get_engine(),
        username=body.username,
        password=body.password,
        totp_code=body.totp_code,
        ip=meta["ip"],
        user_agent=meta["user_agent"],
    )


@router.post("/auth/logout")
def logout(request: Request, session: dict = Depends(operator.get_operator_session)):
    """Revoca la sesión actual (el BFF borra la cookie)."""
    token = request.state.provider_session_token
    revoked = operator.revoke_session(get_engine(), token)
    return {"ok": True, "revoked": revoked}


@router.get("/auth/me")
def me(session: dict = Depends(operator.get_operator_session)):
    """Estado del operador (funciona con must_change_password=true — la
    UI lo usa para el gate del required action)."""
    return {
        "username": session["username"],
        "must_change_password": bool(session["must_change_password"]),
        "mfa_enrolled": bool(session["totp_active"]),
        "mfa_verified": bool(session["mfa_verified"]),
    }


@router.post("/auth/password")
def change_password(
    body: PasswordBody,
    request: Request,
    session: dict = Depends(operator.get_operator_session),
):
    """Cambio de password (REQ-PS-006): limpia el flag del primer login,
    revoca las demás sesiones y audita ``provider.password_changed``."""
    meta = _client_meta(request)
    return operator.change_password(
        get_engine(),
        session=session,
        current_password=body.current_password,
        new_password=body.new_password,
        ip=meta["ip"],
        user_agent=meta["user_agent"],
    )


@router.post("/mfa/enroll-start")
def mfa_enroll_start(session: dict = Depends(_session_password_changed)):
    """Genera el secret TOTP pendiente y lo devuelve ONE-TIME (REQ-PS-007):
    secreto base32 + ``otpauth://`` para el QR/entrada manual."""
    return operator.mfa_enroll_start(get_engine(), session=session)


@router.post("/mfa/enroll-confirm")
def mfa_enroll_confirm(
    body: TotpBody,
    request: Request,
    session: dict = Depends(_session_password_changed),
):
    """Verifica un código real ANTES de activar (REQ-PS-007)."""
    meta = _client_meta(request)
    return operator.mfa_enroll_confirm(
        get_engine(), session=session, code=body.code,
        ip=meta["ip"], user_agent=meta["user_agent"],
    )


@router.post("/mfa/disable")
def mfa_disable(
    body: MfaDisableBody,
    request: Request,
    session: dict = Depends(_session_password_changed),
):
    """Desactiva MFA exigiendo password + código TOTP (REQ-PS-009)."""
    meta = _client_meta(request)
    return operator.mfa_disable(
        get_engine(), session=session, password=body.password, code=body.code,
        ip=meta["ip"], user_agent=meta["user_agent"],
    )

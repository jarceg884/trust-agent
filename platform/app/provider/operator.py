"""Servicio del super-user del proveedor (spec 025, mandato Miller).

Identidad (``ops.provider_operators``), sesiones (``ops.provider_sessions``)
y audit (``ops.provider_audit``) — todo en PostgreSQL, plano proveedor sin
RLS (D-PS-01), NADA en memoria de proceso (D-PS-04).

Token de sesión: ``pst1.<session_id>.<secret>`` — opaco, aleatorio, con
solo ``sha256(secret)`` en BD (``jti``). Mismo contrato que las sesiones de
tenant (``sts1.…``) pero sin hint de tenant: no hay tenant.

Transacciones: los fallos de login se registran (throttle + audit) en una
transacción PROPIA que se compromete ANTES de lanzar el 401 — el
``engine.begin()`` del request no existe todavía y un rollback no debe
perder el contador ni el evento (lección del patrón en auth/router.py).
"""
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ..auth import throttle
from ..config import get_settings
from ..db import get_engine
from ..errors import (
    ApiError,
    invalid_token,
    mfa_code_required,
    password_change_required,
    service_unavailable,
    validation_failed,
)
from ..vault.crypto import decrypt_secret, encrypt_secret, load_kek
from . import audit, passwords, totp

logger = logging.getLogger("trust.provider")

SESSION_PREFIX = "pst1."
MIN_PASSWORD_LEN = 12

# Claves de throttle (misma ops.login_throttle, D-PS-09): la cuenta se
# identifica por el username hasheado — el plano proveedor no tiene tenant.
def account_key(username: str) -> str:
    return f"acct:provider:{hashlib.sha256(username.encode()).hexdigest()}"


# ---------------------------------------------------------------------------
# Bootstrap (D-PS-03): seed idempotente con password one-time
# ---------------------------------------------------------------------------

def ensure_provider_operator(engine: Engine) -> str | None:
    """Crea el super-user inicial si no existe ninguno.

    Idempotente y a prueba de carreras: advisory lock de transacción +
    re-check del conteo. Devuelve el password one-time SOLO en la rama que
    realmente insertó (el llamador lo loguea EXACTAMENTE una vez); si el
    operador ya existía devuelve None — re-arrancar no vuelve a generar ni
    a loguear nada.
    """
    settings = get_settings()
    username = settings.provider_operator_username
    with engine.begin() as conn:
        conn.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('provider-operator-seed', 0))"
            )
        )
        count = conn.execute(
            text("SELECT count(*) FROM ops.provider_operators")
        ).scalar()
        if count:
            return None
        password = (
            settings.provider_operator_initial_password.strip()
            or secrets.token_urlsafe(18)
        )
        conn.execute(
            text(
                "INSERT INTO ops.provider_operators (username, password_hash) "
                "VALUES (:username, :password_hash)"
            ),
            {
                "username": username,
                "password_hash": passwords.hash_password(password),
            },
        )
        return password


def bootstrap_provider_operator() -> None:
    """Hook de arranque del API: seedea el operador y loguea la OTA una vez.

    Falla suave si la tabla aún no existe (imagen sin migrar): se registra
    el error y el login fail-closea después — nunca bloquea el arranque.
    """
    try:
        one_time = ensure_provider_operator(get_engine())
    except Exception as exc:  # pragma: no cover - arranque degradado
        logger.error(
            "Provider operator bootstrap failed (login will fail closed "
            "until fixed): %s", exc,
        )
        return
    if one_time is not None:
        logger.warning(
            "PROVIDER OPERATOR ONE-TIME PASSWORD (shown ONLY once, at "
            "bootstrap): username=%s password=%s — sign in and change it "
            "immediately (first login forces the change).",
            get_settings().provider_operator_username, one_time,
        )


# ---------------------------------------------------------------------------
# Sesiones (D-PS-04)
# ---------------------------------------------------------------------------

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def mint_operator_session(
    conn: Connection,
    *,
    operator_id: str,
    mfa_verified: bool,
    ip: str | None,
    user_agent: str | None,
) -> tuple[str, datetime]:
    """INSERT la sesión y devuelve (token pst1…, expires_at)."""
    settings = get_settings()
    session_id = str(uuid.uuid4())
    secret = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.session_ttl_minutes
    )
    conn.execute(
        text(
            "INSERT INTO ops.provider_sessions "
            " (id, operator_id, jti, status, mfa_verified, ip, user_agent, "
            "  expires_at) "
            "VALUES (:id, CAST(:operator_id AS uuid), :jti, 'active', :mfa, "
            " CAST(:ip AS inet), :user_agent, :expires_at)"
        ),
        {
            "id": session_id,
            "operator_id": operator_id,
            "jti": _hash(secret),
            "mfa": mfa_verified,
            "ip": ip,
            "user_agent": user_agent,
            "expires_at": expires_at,
        },
    )
    return f"{SESSION_PREFIX}{session_id}.{secret}", expires_at


def parse_session_token(token: str) -> tuple[str, str]:
    """-> (session_id, secret) o raise invalid_token (formato estricto)."""
    if not token.startswith(SESSION_PREFIX):
        raise invalid_token("Provider session token required.")
    parts = token[len(SESSION_PREFIX):].split(".")
    if len(parts) != 2:
        raise invalid_token()
    session_id, secret = parts
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise invalid_token()
    if not secret:
        raise invalid_token()
    return session_id, secret


def lookup_operator_session(conn: Connection, token: str) -> dict | None:
    """Sesión activa del operador o None. Actualiza last_seen_at."""
    session_id, secret = parse_session_token(token)
    row = conn.execute(
        text(
            "SELECT s.id, s.operator_id, s.status, s.mfa_verified, "
            "       s.expires_at, o.username, o.status AS operator_status, "
            "       o.must_change_password, o.totp_active "
            "FROM ops.provider_sessions s "
            "JOIN ops.provider_operators o ON o.id = s.operator_id "
            "WHERE s.id = CAST(:sid AS uuid) AND s.jti = :jti"
        ),
        {"sid": session_id, "jti": _hash(secret)},
    ).mappings().first()
    now = datetime.now(timezone.utc)
    if row is None or row["status"] != "active" or row["expires_at"] <= now:
        return None
    if row["operator_status"] != "active":
        return None
    conn.execute(
        text(
            "UPDATE ops.provider_sessions SET last_seen_at = now() "
            "WHERE id = CAST(:sid AS uuid)"
        ),
        {"sid": session_id},
    )
    return dict(row)


def revoke_session(engine: Engine, token: str) -> bool:
    with engine.begin() as conn:
        session_id, _ = parse_session_token(token)
        result = conn.execute(
            text(
                "UPDATE ops.provider_sessions SET status = 'revoked' "
                "WHERE id = CAST(:sid AS uuid) AND status = 'active'"
            ),
            {"sid": session_id},
        )
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# TOTP en reposo: envelope AES-256-GCM con la KEK del vault (D-PS-05)
# ---------------------------------------------------------------------------

def _kek():
    settings = get_settings()
    if not settings.vault_kek_b64:
        raise service_unavailable(
            "TRUST_VAULT_KEK_B64 is not configured; provider MFA is "
            "unavailable (fail-closed)."
        )
    return load_kek(settings.vault_kek_b64)


def _store_totp_secret(
    conn: Connection, operator_id: str, secret_b32: str
) -> None:
    kek = _kek()
    settings = get_settings()
    ct, dek_ct, nonce, key_id = encrypt_secret(
        secret_b32.encode(), kek, settings.vault_key_id
    )
    conn.execute(
        text(
            "UPDATE ops.provider_operators "
            "SET totp_secret_enc = :ct, totp_dek_enc = :dek, "
            "    totp_nonce = :nonce, totp_key_id = :key_id "
            "WHERE id = CAST(:oid AS uuid)"
        ),
        {
            "ct": ct,
            "dek": dek_ct,
            "nonce": nonce,
            "key_id": key_id,
            "oid": operator_id,
        },
    )


def _load_totp_secret(row: dict) -> str:
    if not row["totp_secret_enc"]:
        raise service_unavailable(
            "Provider MFA state is inconsistent (no TOTP secret stored)."
        )
    kek = _kek()
    return decrypt_secret(
        row["totp_secret_enc"], row["totp_dek_enc"], row["totp_nonce"], kek
    ).decode()


# ---------------------------------------------------------------------------
# Login (REQ-PS-003/004/006/008)
# ---------------------------------------------------------------------------

def operator_exists(engine: Engine) -> bool:
    """¿Hay algún operador seedeado? (fail-closed doble, REQ-PS-010)."""
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("SELECT count(*) FROM ops.provider_operators")
            ).scalar()
        )


def login(
    engine: Engine,
    *,
    username: str,
    password: str,
    totp_code: str | None,
    ip: str | None,
    user_agent: str | None,
) -> dict:
    """Login usuario+contraseña(+TOTP si activo) → sesión en PostgreSQL.

    Fail-closed si no hay operador seedeado (REQ-PS-010). Throttle
    persistido (D-PS-09): password malo o código TOTP malo cuentan como
    fallo; el código ausente es un prompt de UX (401 mfa_code_required) y
    no cuenta. Sin oracle: mismo 401 para usuario inexistente, password
    malo, operador disabled y TOTP malo.
    """
    if not operator_exists(engine):
        raise service_unavailable(
            "Provider operations are disabled: no provider operator is "
            "seeded. Restart the API to bootstrap one (fail-closed)."
        )
    username = username.strip()
    acct = account_key(username)
    ip_k = throttle.ip_key(ip)
    keys = [acct, ip_k]

    with engine.connect() as conn:
        throttle.check(conn, keys)
        op = conn.execute(
            text(
                "SELECT id, username, password_hash, status, "
                "       must_change_password, totp_active, totp_secret_enc, "
                "       totp_dek_enc, totp_nonce "
                "FROM ops.provider_operators WHERE username = :username"
            ),
            {"username": username},
        ).mappings().first()

    denied_reason = None
    if op is None:
        denied_reason = "bad_credentials"
    elif op["status"] != "active":
        denied_reason = "operator_disabled"
    elif not passwords.verify_password(op["password_hash"], password):
        denied_reason = "bad_credentials"
    elif op["totp_active"]:
        if not totp_code:
            # Prompt de UX, no un fallo de credencial (D-PS-09).
            raise mfa_code_required()
        try:
            secret = _load_totp_secret(dict(op))
        except ApiError:
            # KEK sin configurar / estado inconsistente → 503 fail-closed
            # (D-PS-05), NO un 401 de credencial.
            raise
        if not totp.verify(secret, totp_code):
            denied_reason = "bad_totp"

    if denied_reason is not None:
        # Transacción PROPIA: el contador y el evento sobreviven al 401.
        with engine.begin() as conn:
            throttle.register_failure(conn, keys)
            audit.emit(
                conn,
                action="provider.login_denied",
                operator_id=str(op["id"]) if op else None,
                username=username,
                ip=ip,
                user_agent=user_agent,
                payload={"reason": denied_reason},
            )
        raise invalid_token("Invalid username or password.")

    # Éxito: limpia el contador de la cuenta, minta sesión, audita.
    with engine.begin() as conn:
        throttle.register_success(conn, acct)
        token, expires_at = mint_operator_session(
            conn,
            operator_id=str(op["id"]),
            mfa_verified=bool(op["totp_active"]),
            ip=ip,
            user_agent=user_agent,
        )
        audit.emit(
            conn,
            action="provider.login",
            operator_id=str(op["id"]),
            username=username,
            ip=ip,
            user_agent=user_agent,
            payload={
                "mfa": bool(op["totp_active"]),
                "forced_password_change": bool(op["must_change_password"]),
            },
        )
    return {
        "operator": {
            "username": op["username"],
            "must_change_password": bool(op["must_change_password"]),
            "mfa_enrolled": bool(op["totp_active"]),
        },
        "session_token": token,
        "expires_at": expires_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Password change (REQ-PS-006, required action del primer login)
# ---------------------------------------------------------------------------

def change_password(
    engine: Engine,
    *,
    session: dict,
    current_password: str,
    new_password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    if len(new_password) < MIN_PASSWORD_LEN:
        raise validation_failed(
            f"New password must be at least {MIN_PASSWORD_LEN} characters."
        )
    with engine.connect() as conn:
        op = conn.execute(
            text(
                "SELECT id, username, password_hash, must_change_password "
                "FROM ops.provider_operators WHERE id = CAST(:oid AS uuid)"
            ),
            {"oid": session["operator_id"]},
        ).mappings().first()
    if op is None or not passwords.verify_password(
        op["password_hash"], current_password
    ):
        raise invalid_token("Current password is incorrect.")

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ops.provider_operators "
                "SET password_hash = :h, must_change_password = false "
                "WHERE id = CAST(:oid AS uuid)"
            ),
            {"h": passwords.hash_password(new_password), "oid": op["id"]},
        )
        # Las demás sesiones del operador quedan revocadas (D-PS-08).
        conn.execute(
            text(
                "UPDATE ops.provider_sessions SET status = 'revoked' "
                "WHERE operator_id = CAST(:oid AS uuid) "
                "  AND id <> CAST(:sid AS uuid) AND status = 'active'"
            ),
            {"oid": op["id"], "sid": session["id"]},
        )
        audit.emit(
            conn,
            action="provider.password_changed",
            operator_id=str(op["id"]),
            username=op["username"],
            ip=ip,
            user_agent=user_agent,
            payload={"was_first_login": bool(op["must_change_password"])},
        )
    return {"ok": True, "must_change_password": False}


# ---------------------------------------------------------------------------
# MFA (REQ-PS-007/008/009): opcional adentro, verificado antes de activar
# ---------------------------------------------------------------------------

def mfa_enroll_start(engine: Engine, *, session: dict) -> dict:
    """Genera un secret TOTP PENDIENTE (no activo) y lo guarda cifrado.

    El secret se entrega one-time en la respuesta (secreto + otpauth://)
    para el QR/entrada manual; ningún GET lo vuelve a exponer. Reiniciar el
    enrolamiento rota el secret pendiente.
    """
    secret = totp.generate_secret()
    with engine.begin() as conn:
        _store_totp_secret(conn, session["operator_id"], secret)
    return {
        "secret": secret,
        "otpauth_url": totp.otpauth_url(secret, session["username"]),
    }


def mfa_enroll_confirm(
    engine: Engine,
    *,
    session: dict,
    code: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """Verifica un código del secret pendiente ANTES de activar (D-PS-05)."""
    with engine.connect() as conn:
        op = conn.execute(
            text(
                "SELECT id, username, totp_secret_enc, totp_dek_enc, "
                "       totp_nonce, totp_active "
                "FROM ops.provider_operators WHERE id = CAST(:oid AS uuid)"
            ),
            {"oid": session["operator_id"]},
        ).mappings().first()
    if op is None or not op["totp_secret_enc"]:
        raise validation_failed("Start MFA enrollment first.")
    secret = _load_totp_secret(dict(op))
    if not totp.verify(secret, code):
        raise validation_failed("Invalid TOTP code. MFA was NOT activated.")
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ops.provider_operators "
                "SET totp_active = true, totp_enrolled_at = now() "
                "WHERE id = CAST(:oid AS uuid)"
            ),
            {"oid": session["operator_id"]},
        )
        audit.emit(
            conn,
            action="provider.mfa_enrolled",
            operator_id=str(session["operator_id"]),
            username=session["username"],
            ip=ip,
            user_agent=user_agent,
            payload={},
        )
    return {"ok": True, "mfa_enrolled": True}


def mfa_disable(
    engine: Engine,
    *,
    session: dict,
    password: str,
    code: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict:
    """Desactiva MFA exigiendo password + código TOTP válidos (REQ-PS-009)."""
    with engine.connect() as conn:
        op = conn.execute(
            text(
                "SELECT id, username, password_hash, totp_active, "
                "       totp_secret_enc, totp_dek_enc, totp_nonce "
                "FROM ops.provider_operators WHERE id = CAST(:oid AS uuid)"
            ),
            {"oid": session["operator_id"]},
        ).mappings().first()
    if op is None or not op["totp_active"]:
        raise validation_failed("MFA is not enrolled.")
    if not passwords.verify_password(op["password_hash"], password):
        raise invalid_token("Password is incorrect.")
    secret = _load_totp_secret(dict(op))
    if not totp.verify(secret, code):
        raise validation_failed("Invalid TOTP code.")
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ops.provider_operators "
                "SET totp_active = false, totp_enrolled_at = NULL, "
                "    totp_secret_enc = NULL, totp_dek_enc = NULL, "
                "    totp_nonce = NULL, totp_key_id = NULL "
                "WHERE id = CAST(:oid AS uuid)"
            ),
            {"oid": session["operator_id"]},
        )
        audit.emit(
            conn,
            action="provider.mfa_disabled",
            operator_id=str(session["operator_id"]),
            username=session["username"],
            ip=ip,
            user_agent=user_agent,
            payload={},
        )
    return {"ok": True, "mfa_enrolled": False}


# ---------------------------------------------------------------------------
# Dependencias de sesión para el router
# ---------------------------------------------------------------------------

def get_operator_session(request: Request) -> dict:
    """Sesión de operador del header Authorization (la inyecta el BFF)."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise invalid_token("Provider session required.")
    token = auth[len("Bearer "):].strip()
    if not token.startswith(SESSION_PREFIX):
        raise invalid_token("Provider session token required.")
    engine = get_engine()
    with engine.begin() as conn:
        session = lookup_operator_session(conn, token)
    if session is None:
        raise invalid_token("Invalid or expired provider session.")
    request.state.provider_session = session
    request.state.provider_session_token = token
    return session


def require_password_changed(session: dict) -> dict:
    """Gate de la required action: 403 hasta completar el cambio (D-PS-08)."""
    if session.get("must_change_password"):
        raise password_change_required()
    return session

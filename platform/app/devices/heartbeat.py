"""Heartbeat de presencia + propagación de policy en vivo (spec 027).

El plugin ``trust-agent-governance`` (lane t_c875626f) reporta un heartbeat
en cada ciclo de sync: qué versión de policy está APLICANDO, su uptime y su
modo operativo. El control plane:

  1. Registra presencia por dispositivo (``devices.last_heartbeat_at`` —
     señal de agente vivo, distinta del ``last_seen_at`` del DeviceGate).
  2. Compara la versión aplicada contra la vigente (misma función que firma
     el bundle) y responde ``policy_update_available`` para que el device
     re-baje el governance-bundle JWS en el MISMO ciclo — sin reiniciar ni
     re-enrolar (mandato Miller). Ventana de consistencia peor caso =
     intervalo de heartbeat (spec 027 §6).
  3. Audita sin flooding (D-027-05): ``device.heartbeat`` muestreado (≤1 por
     dispositivo por ``heartbeat_audit_interval_seconds``) y
     ``policy.propagated`` en cada transición confirmada.

Auth idéntica al governance-bundle (spec 019 §5.5): Bearer device token
(camino plugin, MFA heredada) o sesión+MFA, ambos con X-Device-Token; la
fila ``active`` se comprueba en la transacción (revocación → 401 inmediato,
REQ-GOH-010). Este módulo NO edita ``governance/bundle.py`` (hotspot de
t_150e8931): importa ``require_bundle_auth`` y ``_policy_version``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit_service
from ..auth.service import Principal
from ..config import get_settings
from ..db import get_engine, set_tenant
from ..governance.bundle import _policy_version, require_bundle_auth
from .gate import check_device_access
from .tokens import issue_device_token

router = APIRouter(prefix="/api/me")


class HeartbeatBody(BaseModel):
    """Contrato §4 (spec 027). `applied_policy_version` null = sin bundle."""

    applied_policy_version: int | None = None
    uptime_seconds: int = Field(default=0, ge=0)
    mode: str = Field(default="normal", pattern="^(normal|restricted)$")


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"


def record_heartbeat(
    conn: Connection,
    *,
    principal: Principal,
    device_id: str,
    body: HeartbeatBody,
) -> dict:
    """Persiste presencia + señal de actualización + audit muestreado."""
    settings = get_settings()

    # DeviceGate en la MISMA transacción: revocado → 401 inmediato. El
    # heartbeat ES la señal de presencia, así que no tocamos last_seen_at
    # (esa es del gate, D-DEV-09).
    check_device_access(conn, device_id, principal, touch=False)

    row = conn.execute(
        text(
            "SELECT applied_policy_version, last_heartbeat_audit_at "
            "FROM devices WHERE id = CAST(:did AS uuid)"
        ),
        {"did": device_id},
    ).mappings().first()
    prev_applied = row["applied_policy_version"] if row else None
    last_audit_at = row["last_heartbeat_audit_at"] if row else None

    conn.execute(
        text(
            "UPDATE devices SET last_heartbeat_at = now(), "
            "applied_policy_version = :version, "
            "agent_uptime_seconds = :uptime, updated_at = now() "
            "WHERE id = CAST(:did AS uuid)"
        ),
        {
            "did": device_id,
            "version": body.applied_policy_version,
            "uptime": body.uptime_seconds,
        },
    )

    current_version = _policy_version(
        conn, department_id=principal.department_id, user_id=principal.user_id
    )

    # -- Audit (D-027-05) ----------------------------------------------------
    # policy.propagated: transición CONFIRMADA — el device reporta la versión
    # vigente y la registrada previa era distinta. Primer reporte (prev null)
    # o reporte aún desactualizado: solo línea base / señal de update.
    if (
        body.applied_policy_version is not None
        and prev_applied is not None
        and body.applied_policy_version != prev_applied
        and body.applied_policy_version == current_version
    ):
        audit_service.emit(
            conn,
            tenant_id=principal.tenant_id,
            actor_role=_actor_role(principal),
            actor_id=principal.user_id,
            action="policy.propagated",
            target_type="device",
            target_id=device_id,
            device_id=device_id,
            payload={
                "from_version": prev_applied,
                "to_version": body.applied_policy_version,
            },
        )

    # device.heartbeat muestreado: ≤1 fila por device por intervalo.
    audit_due = True
    if last_audit_at is not None:
        age = datetime.now(timezone.utc) - last_audit_at
        audit_due = age.total_seconds() >= settings.heartbeat_audit_interval_seconds
    if audit_due:
        audit_service.emit(
            conn,
            tenant_id=principal.tenant_id,
            actor_role=_actor_role(principal),
            actor_id=principal.user_id,
            action="device.heartbeat",
            target_type="device",
            target_id=device_id,
            device_id=device_id,
            payload={
                "applied_policy_version": body.applied_policy_version,
                "uptime_seconds": body.uptime_seconds,
                "mode": body.mode,
            },
        )
        conn.execute(
            text(
                "UPDATE devices SET last_heartbeat_audit_at = now() "
                "WHERE id = CAST(:did AS uuid)"
            ),
            {"did": device_id},
        )

    update_available = (
        body.applied_policy_version is None
        or body.applied_policy_version != current_version
    )
    result = {
        "device_id": device_id,
        "current_policy_version": current_version,
        "policy_update_available": update_available,
        "heartbeat_interval_seconds": settings.heartbeat_interval_seconds,
        "online_window_seconds": max(
            60, settings.presence_online_window_seconds
        ),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }
    # Sliding renewal (spec 022 v1.1.0 REQ-HRB-010 / spec 027 §4 amend):
    # the PLUGIN path (Bearer = device token) gets a fresh device token in
    # the SAME transaction that already ran the DeviceGate above — the
    # device row is `active` RIGHT NOW, so re-issuing extends the session
    # without touching revocation semantics (revoked → 401 before we get
    # here). The session+MFA path does NOT renew here: the desktop has the
    # explicit POST /api/devices/{id}/token endpoint.
    if getattr(principal, "via", "") == "device_token":
        result["device_token"] = issue_device_token(
            settings,
            device_id=device_id,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
        )
        result["expires_in"] = settings.device_token_ttl_minutes * 60
    return result


@router.post("/device-heartbeat")
def device_heartbeat(request: Request, body: HeartbeatBody):
    """POST /api/me/device-heartbeat (spec 027 §4). Auth del bundle (§5.5).

    Transacción manual (como el endpoint del bundle): el camino plugin
    autentica con device token y NO hay principal de sesión para ``get_tx``.
    """
    auth = require_bundle_auth(request)
    engine = get_engine()
    with engine.begin() as conn:
        set_tenant(conn, auth.principal.tenant_id)
        return record_heartbeat(
            conn,
            principal=auth.principal,
            device_id=auth.device_id,
            body=body,
        )

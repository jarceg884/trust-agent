"""Attestation del agente + detección de overdue (spec 019 Inc. 3).

REQ-GOH-012 (D-GOH-06/ADR-018): al arrancar una sesión, el plugin
``trust-agent-governance`` reporta al control plane el hash del bundle
vigente, la ``policy_version`` aplicada, el modo operativo y el conteo de
vetos desde la última attestation::

    POST /api/devices/{device_id}/attestation
    Authorization: Bearer <device_token>   (camino plugin; también sesión+MFA)
    X-Device-Token: <device_token>
    {"bundle_hash": "<sha256 del JWS cacheado>", "policy_version": 3,
     "mode": "normal", "veto_counts": {"terminal": 3}}

El control plane lo registra en el audit del tenant (``device.attestation``,
con ``tenant_id``/``device_id``) y persiste ``devices.last_attestation_at``
— la señal de "agente reportando gobernanza". Sin attestation no hay forma
de saber si el agente corre gobernado: un plugin deshabilitado localmente
es indetectable hasta que el overdue lo delata (threat model honesto,
D-GOH-07).

REQ-GOH-013: dispositivo ``active`` sin attestation durante más de
``attestation_overdue_hours`` (policy key existente, default 24, piso 1)
→ notificación ``device_attestation_overdue`` a los org_admin (inbox real)
+ audit ``device.attestation_overdue``, SIN duplicar dentro de la misma
ventana (una notificación por dispositivo por ventana; la attestation
llegada resetea el reloj). El sweep lo corre el worker
:mod:`app.devices.attestation_worker` (propio, patrón spend-worker).

REQ-GOH-017: las tools con 3+ intentos bloqueados desde la última
attestation viajan en ``veto_counts`` y se destacan en el audit como
``escalated_tools`` — visibles para revisión del admin.

Auth idéntica al governance-bundle (spec 019 §5.5): ``require_bundle_auth``
(Bearer device token con MFA heredada, o sesión+MFA) + ``X-Device-Token``;
la fila ``active`` se comprueba en la transacción (revocación → 401
inmediato). Este módulo NO edita ``governance/bundle.py`` (hotspot de
t_150e8931): importa ``require_bundle_auth``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit_service
from ..auth.service import Principal
from ..db import get_engine, set_tenant
from ..errors import device_not_authorized, require_uuid
from ..governance.bundle import require_bundle_auth
from ..governance.notifications import notify_role
from .gate import check_device_access

router = APIRouter(prefix="/api/devices")

# REQ-GOH-017: umbral de escalación — 3+ intentos bloqueados de la misma
# tool se destacan en el audit de la attestation.
VETO_ESCALATION_THRESHOLD = 3

DEFAULT_OVERDUE_HOURS = 24


class AttestationBody(BaseModel):
    """Contrato del plugin (spec 019 §5.5 / D-GOH-06).

    ``bundle_hash`` null = sin bundle vigente (modo restringido sin caché);
    ``policy_version`` null = ídem (no hay policy aplicada verificable).
    """

    bundle_hash: str | None = None
    policy_version: int | None = None
    mode: str = Field(pattern="^(normal|restricted)$")
    veto_counts: dict[str, int] = Field(default_factory=dict)
    # Enforcement batch 2 (t_a868974a): desvíos config-vs-policy y pisos
    # (retención/checkpoints) que el plugin reporta por sesión. Opcional y
    # retrocompatible — un plugin sin el batch 2 no lo manda (default {}).
    # Estructura: {"status": {<código>: <estado>}, "warnings": {<código>:
    # {"count": N, "detail": "..."}}} — sin secretos (solo códigos/resúmenes).
    capability_findings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("veto_counts")
    @classmethod
    def _counts_non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        for tool, count in value.items():
            if count < 0:
                raise ValueError(f"veto count for '{tool}' must be >= 0")
        return value


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"


def org_attestation_overdue_hours(conn: Connection) -> int:
    """Ventana de overdue vigente a nivel org (policy key tipada, default 24).

    La key es de nivel org (D-GOH-08); el piso del proveedor (1 h) lo aplica
    el catálogo tipado en el upsert (REQ-GOH-014) — aquí solo se lee.
    """
    from ..governance.policies import _current_value

    value = _current_value(
        conn, level="org", department_id=None, user_id=None,
        key="attestation_overdue_hours",
    )
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_OVERDUE_HOURS


def record_attestation(
    conn: Connection,
    *,
    principal: Principal,
    device_id: str,
    body: AttestationBody,
) -> dict:
    """Persiste la attestation: devices.last_attestation_at + audit del tenant."""
    check_device_access(conn, device_id, principal, touch=False)

    conn.execute(
        text(
            "UPDATE devices SET last_attestation_at = now(), updated_at = now() "
            "WHERE id = CAST(:did AS uuid)"
        ),
        {"did": device_id},
    )

    veto_counts = {str(k): int(v) for k, v in body.veto_counts.items()}
    escalated = sorted(
        tool for tool, count in veto_counts.items()
        if count >= VETO_ESCALATION_THRESHOLD
    )
    audit_service.emit(
        conn,
        tenant_id=principal.tenant_id,
        actor_role=_actor_role(principal),
        actor_id=principal.user_id,
        action="device.attestation",
        target_type="device",
        target_id=device_id,
        device_id=device_id,
        payload={
            "bundle_hash": body.bundle_hash,
            "policy_version": body.policy_version,
            "mode": body.mode,
            "veto_counts": veto_counts,
            "escalated_tools": escalated,
            "capability_findings": body.capability_findings,
        },
    )
    return {
        "device_id": device_id,
        "recorded": True,
        "attested_at": datetime.now(timezone.utc).isoformat(),
        "attestation_overdue_hours": org_attestation_overdue_hours(conn),
        "escalated_tools": escalated,
    }


@router.post("/{device_id}/attestation")
def device_attestation(request: Request, device_id: str, body: AttestationBody):
    """POST /api/devices/{device_id}/attestation (REQ-GOH-012).

    Transacción manual (como el bundle/heartbeat): el camino plugin
    autentica con device token y NO hay principal de sesión para ``get_tx``.
    El device del path debe ser EXACTAMENTE el del token (un token no
    atestigua dispositivos ajenos).
    """
    auth = require_bundle_auth(request)
    device_id = require_uuid(device_id, "device_id")
    if device_id != auth.device_id:
        raise device_not_authorized()
    engine = get_engine()
    with engine.begin() as conn:
        set_tenant(conn, auth.principal.tenant_id)
        return record_attestation(
            conn,
            principal=auth.principal,
            device_id=device_id,
            body=body,
        )


# ---------------------------------------------------------------------------
# Overdue sweep (REQ-GOH-013) — lo ejecuta attestation_worker por tenant
# ---------------------------------------------------------------------------
def sweep_attestation_overdue(conn: Connection, *, tenant_id: str) -> dict:
    """Detecta dispositivos ``active`` sin attestation más allá del umbral.

    Por cada dispositivo overdue NO notificado en la ventana actual:
    notificación ``device_attestation_overdue`` a los org_admin + audit
    ``device.attestation_overdue`` + marca ``last_attestation_overdue_at``.
    La attestation llegada resetea el reloj (``last_attestation_at`` pasa a
    ser la referencia), así el ciclo notifica de nuevo en la SIGUIENTE
    ventana de overdue — nunca duplica dentro de la misma (REQ-GOH-013).
    """
    hours = org_attestation_overdue_hours(conn)
    rows = conn.execute(
        text(
            "SELECT id, device_name, user_id, fingerprint FROM devices "
            "WHERE status = 'active' "
            "  AND COALESCE(last_attestation_at, approved_at, enrolled_at) "
            "      < now() - make_interval(hours => :hours) "
            "  AND (last_attestation_overdue_at IS NULL "
            "       OR last_attestation_overdue_at "
            "           < now() - make_interval(hours => :hours)) "
            "ORDER BY enrolled_at"
        ),
        {"hours": hours},
    ).mappings().all()

    notified: list[str] = []
    for row in rows:
        device_id = str(row["id"])
        payload = {
            "device_id": device_id,
            "device_name": row["device_name"],
            "fingerprint": row["fingerprint"],
            "user_id": str(row["user_id"]),
            "overdue_hours": hours,
            "scope": f"device:{device_id}",
        }
        notify_role(
            conn,
            tenant_id=tenant_id,
            role_key="org_admin",
            type="device_attestation_overdue",
            payload=payload,
        )
        audit_service.emit(
            conn,
            tenant_id=tenant_id,
            actor_role="system",
            action="device.attestation_overdue",
            target_type="device",
            target_id=device_id,
            device_id=device_id,
            payload=payload,
        )
        conn.execute(
            text(
                "UPDATE devices SET last_attestation_overdue_at = now(), "
                "updated_at = now() WHERE id = CAST(:did AS uuid)"
            ),
            {"did": device_id},
        )
        notified.append(device_id)

    return {
        "overdue_hours": hours,
        "notified": len(notified),
        "device_ids": notified,
    }


def summarize(row: Any) -> dict:
    """Helper de evidencia: fila de devices → dict legible (tests/docs)."""
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "last_attestation_at": row["last_attestation_at"].isoformat()
        if row["last_attestation_at"] else None,
        "last_attestation_overdue_at": row["last_attestation_overdue_at"].isoformat()
        if row["last_attestation_overdue_at"] else None,
    }

"""Governance bundle — compilación y firma JWS del estado gobernado
(spec 019, REQ-GOH-001/002, ADR-016).

El bundle es el contrato entre el control plane (L4, quien lo firma) y el
plugin ``trust-agent-governance`` v2 (L2, quien lo enforce): la policy
efectiva compilada (deny-overrides de org/depto/usuario + invariables del
proveedor, REQ-GOV-024), los modelos efectivos, el presupuesto vigente por
nivel y las keys de gobernanza de agente — TODO en un payload firmado
RS256 (HS256 prohibido) con ``aud = device_id``, ``exp`` = gracia offline
y ``jti`` anti-replay.

Autenticación del endpoint (§5.5): Bearer + MFA + X-Device-Token. Se
aceptan DOS portadores del Bearer:

  1. Sesión/JWT OIDC del desktop (con MFA acreditada) — camino de la UI.
  2. El PROPIO device token del control plane (camino del plugin): el
     plugin headless solo posee ``TRUST_DEVICE_TOKEN``. El device token
     se emite/canjea EXCLUSIVAMENTE detrás de una sesión con MFA verificada
     (spec 007/010) y vive 15 min renovables solo con sesión+MFA+owner, de
     modo que la evidencia MFA del sync HEREDA la del desktop (spec 019
     §8, "MFA en el sync del bundle: hereda la sesión del desktop"). La
     DeviceGate (fila active del dispositivo) se aplica en ambos caminos.

No se emite fila de audit por sync: es una lectura del propio estado del
dispositivo y el gate ya actualiza ``devices.last_seen_at`` (throttle 60 s),
que es la señal de detección de "agente reportando" (D-GOH-06/ADR-018).
La attestation auditable llega con el incremento 3 (REQ-GOH-012).
"""
from __future__ import annotations

import logging

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Request
from sqlalchemy import text

from ..auth.service import (
    Principal,
    SESSION_PREFIX,
    authenticate,
    load_permissions,
    load_roles,
)
from ..config import get_settings
from ..db import get_engine, set_tenant
from ..devices.gate import check_device_access
from ..devices.tokens import verify_device_token
from ..errors import ApiError, device_not_authorized, invalid_token
from ..models import access as model_access
from . import budgets as budgets_service
from ..models import keys as keys_service
from . import jws_keys
from .service import effective_policy


logger = logging.getLogger(__name__)

# Piso del proveedor para la gracia offline (D-GOH-08: default 60, piso 15).
OFFLINE_GRACE_FLOOR_MINUTES = 15

# Schema versionado del bundle (D-GOH-11, REQ-GOH-022..024). Schema 1 =
# bundles emitidos antes de este requisito (sin el claim). Schema 2 añade
# schema_version + TODAS las keys del catálogo aplanadas (REQ-GOH-024).
BUNDLE_SCHEMA_VERSION = 2

# Claims estructurales reservados (§5.2): ninguna key del catálogo puede
# colisionar con ellos. ``allowed_connections`` se compila aparte (spec 024,
# REQ-024-010: conexiones efectivas con metadata NO-secreta) y NO es la
# policy key homónima del catálogo.
RESERVED_CLAIMS = frozenset({
    "iss", "aud", "exp", "iat", "jti",
    "tenant_id", "user_id", "device_id",
    "policy_version", "schema_version",
    "budget", "approved_models", "allowed_connections",
})

# Claims obligatorios del payload §5.2 (el verificador del plugin los exige).
# ``schema_version`` NO es obligatorio a propósito: los bundles schema 1
# (viejos) siguen verificando en plugins nuevos (compatibilidad retro,
# REQ-GOH-022).
REQUIRED_CLAIMS = (
    "iss", "aud", "exp", "iat", "jti", "tenant_id", "user_id", "device_id",
    "policy_version", "allowed_tools", "allowed_connectors",
    "approved_models", "budget", "offline_grace_minutes", "unbind_mode",
    "attestation_overdue_hours",
)

bundle_router = APIRouter(prefix="/api/me")
public_router = APIRouter()


@dataclass
class BundleAuth:
    principal: Principal
    device_id: str


def _principal_from_device_claims(claims: dict) -> Principal:
    """Principal resuelto desde un device token verificado (camino plugin).

    El tenant SIEMPRE sale del claim firmado del token (D-CP-03), nunca de
    configuración local. MFA: heredada — el device token solo existe detrás
    de un enrolamiento/canje con MFA verificada (ver docstring del módulo).
    """
    engine = get_engine()
    with engine.begin() as conn:
        set_tenant(conn, claims["tenant_id"])
        trow = conn.execute(
            text("SELECT id, status, slug, name FROM tenants")
        ).mappings().first()
        if trow is None or str(trow["id"]) != claims["tenant_id"]:
            raise invalid_token()
        if trow["status"] != "active":
            from ..errors import tenant_suspended

            raise tenant_suspended()
        urow = conn.execute(
            text(
                "SELECT id, email, display_name, department_id, status "
                "FROM users WHERE id = CAST(:uid AS uuid)"
            ),
            {"uid": claims["user_id"]},
        ).mappings().first()
        if urow is None or urow["status"] != "active":
            raise invalid_token()
        roles = load_roles(conn, str(urow["id"]))
        permissions = load_permissions(conn, [r.role_key for r in roles])
    return Principal(
        tenant_id=str(trow["id"]),
        tenant_slug=trow["slug"],
        tenant_name=trow["name"],
        user_id=str(urow["id"]),
        email=urow["email"],
        display_name=urow["display_name"],
        department_id=str(urow["department_id"]) if urow["department_id"] else None,
        roles=roles,
        permissions=permissions,
        session_id=None,
        session_kind="device",
        mfa_verified=True,  # heredada del enrolamiento/canje (spec 019 §8)
        device_id=str(claims["device_id"]),
        via="device_token",
        auth_via_cookie=False,
    )


def require_bundle_auth(request: Request) -> BundleAuth:
    """Bearer (sesión/JWT con MFA, o device token) + X-Device-Token.

    Orden de verificación: portador (401) → MFA (403, solo camino sesión)
    → device token (401 device_not_authorized). La fila `active` del
    dispositivo se comprueba en la transacción del endpoint.
    """
    settings = get_settings()
    device_token = request.headers.get("x-device-token", "")
    if not device_token:
        raise device_not_authorized()
    try:
        device_claims = verify_device_token(settings, device_token)
    except ApiError:
        raise device_not_authorized()

    auth = request.headers.get("authorization", "")
    bearer = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""

    principal: Principal | None = None
    if bearer and not bearer.startswith(SESSION_PREFIX):
        try:
            bearer_claims = verify_device_token(settings, bearer)
        except ApiError:
            bearer_claims = None
        if bearer_claims is not None:
            # Camino plugin: el Bearer ES un device token del control plane.
            if bearer_claims["device_id"] != device_claims["device_id"]:
                raise invalid_token(
                    "Bearer device token does not match X-Device-Token."
                )
            principal = _principal_from_device_claims(bearer_claims)

    if principal is None:
        # Camino desktop: sesión/JWT OIDC + MFA acreditada.
        principal = authenticate(request)
        if principal is None:
            raise invalid_token("Authentication required.")
        if not principal.mfa_verified:
            from ..errors import mfa_required

            raise mfa_required()
        if (
            device_claims["tenant_id"] != principal.tenant_id
            or device_claims["user_id"] != principal.user_id
        ):
            raise invalid_token("Device token does not match the principal.")

    return BundleAuth(principal=principal, device_id=str(device_claims["device_id"]))


# Policy Studio → CLI & Agente: each switch governs one family of Hermes
# tools (real names from hermes-agent toolsets.py). ``grant`` is what ON
# concedes; ``block`` is what OFF removes (adds legacy aliases so an OFF
# switch also strips names older bundles may carry).
#
# Before 2026-11-10 the switches were subtract-only over the provider
# default (``catalog.policy_keys.allowed_tools`` = conversational tools
# WITHOUT terminal), so "Terminal / shell" ON could never grant terminal:
# the console offered a switch that did nothing (live incident, e2e-lab).
STUDIO_TOOL_FAMILIES: dict[str, dict[str, tuple[str, ...]]] = {
    "cli_terminal": {
        "grant": ("terminal", "process_manage"),
        "block": ("terminal", "process_manage", "process", "shell", "bash"),
    },
    "cli_files_read": {
        "grant": ("read_file", "search_files"),
        "block": ("read_file", "search_files"),
    },
    "cli_files_write": {
        "grant": ("write_file", "patch"),
        "block": ("write_file", "patch", "edit_file"),
    },
    "cli_web": {
        "grant": ("web_search", "web_extract"),
        "block": ("web_search", "web_extract", "web_fetch", "browser",
                  "browser_navigate"),
    },
    "cli_code": {
        "grant": ("execute_code",),
        "block": ("execute_code", "python"),
    },
    # Desktop-app switches whose console hint promises an AGENT capability
    # (not just a Settings page): ON must also grant the tool behind it, or
    # the page is visible and the agent still cannot use it.
    "memory": {                      # "Memoria del agente (/memory ...)"
        "grant": ("memory",),
        "block": ("memory",),
    },
    "agents": {                      # "Agentes compartidos y subagentes"
        "grant": ("delegate_task",),
        "block": ("delegate_task",),
    },
    "capabilities": {                # "Skills, plugins, conectores, ..."
        "grant": ("skills_list", "skill_view"),
        "block": ("skills_list", "skill_view", "skill_manage"),
    },
    "cron": {                        # "Tareas programadas (/goal, /loop ...)"
        "grant": ("cronjob_manage",),
        "block": ("cronjob_manage",),
    },
    "voice": {                       # "Modo voz"
        "grant": ("text_to_speech",),
        "block": ("text_to_speech",),
    },
    "vault": {                       # "Vault y autofill"
        "grant": ("browser_vault",),
        "block": ("browser_vault",),
    },
    "browser": {                     # "Navegador integrado"
        "grant": ("browser_exec", "browser_cdp", "browser_dialog"),
        "block": ("browser_exec", "browser_cdp", "browser_dialog",
                  "browser_navigate", "browser"),
    },
}

# Technical policy keys a Studio switch needs to actually work. Without them
# the switch grants the tool and a plugin rule (P-x) still vetoes every call:
# live on e2e-lab, "Terminal / shell" ON reached the device and P-2 vetoed the
# local backend because the provider default is ["docker"] and a Windows
# desktop has no Docker.
STUDIO_IMPLIED_KEYS: dict[str, dict[str, Any]] = {
    "cli_terminal": {"allowed_terminal_backends": ["local", "docker"]},   # P-2
    "cron": {
        "allow_scheduled_tasks": True,     # P-9  cronjob_manage
        "allow_recurring_prompts": True,   # P-10 /loop, /heartbeat
        "allow_goal_mode": True,           # P-11 /goal
    },
    "voice": {"allow_voice_mode": True},              # P-21 /voice
    "vault": {"allow_credential_vault": True},        # P-16 browser_vault
}


def apply_studio_implied_keys(payload: dict, ui: dict, explicit: set[str]) -> None:
    """Set the technical keys implied by the Policy Studio switches.

    * switch ON  → the implied value, UNLESS the key has an explicit policy
      at org/dept/user level: that value is the admin's technical decision
      and stays as the ceiling (deny-overrides).
    * switch OFF → implied boolean keys are forced to False (the capability
      is off on every surface, even over an explicit true). List keys are
      left alone: the family's tools are already out of allowed_tools.
    * switch absent → nothing changes (no Studio bundle / older bundle).
    """
    for feature, implied in STUDIO_IMPLIED_KEYS.items():
        value = ui.get(feature)
        for key, on_value in implied.items():
            if value is True and key not in explicit:
                payload[key] = list(on_value) if isinstance(on_value, list) else on_value
            elif value is False and isinstance(on_value, bool):
                payload[key] = False


def clamp_tools_to_studio(
    base: list[str] | tuple[str, ...],
    ui: dict,
    *,
    explicit_ceiling: bool,
) -> list[str]:
    """allowed_tools after the Policy Studio CLI & Agente switches.

    * ``cli`` OFF → no tools at all (master switch, unchanged semantics).
    * family switch OFF → its ``block`` names are removed.
    * family switch ON → its ``grant`` names are added, UNLESS an explicit
      ``allowed_tools`` policy exists at org/dept/user level: that list is
      the admin's ceiling and, by deny-overrides, ON never exceeds it.
    * switch absent (no Studio bundle / older bundle) → base unchanged.

    Order is preserved and duplicates dropped (deterministic bundles).
    """
    if ui.get("cli") is False:
        return []
    tools = list(dict.fromkeys(str(t) for t in base))
    for feature, family in STUDIO_TOOL_FAMILIES.items():
        value = ui.get(feature)
        if value is False:
            blocked = set(family["block"])
            tools = [t for t in tools if t not in blocked]
        elif value is True and not explicit_ceiling:
            tools.extend(t for t in family["grant"] if t not in tools)
    return tools


def _explicit_policy_keys(
    conn, *, department_id: str | None, user_id: str
) -> set[str]:
    """Keys with an explicit active policy at the user's levels (org, the
    user's department, the user). Defaults from the catalog do not count.

    Same level filter as ``_policy_version``; tenant scoping comes from RLS
    (forced on ``policies``).
    """
    rows = conn.execute(
        text(
            "SELECT DISTINCT key FROM policies "
            "WHERE is_active AND ("
            "  level = 'org'"
            "  OR (level = 'dept' AND department_id = CAST(:dept AS uuid))"
            "  OR (level = 'user' AND user_id = CAST(:usr AS uuid))"
            ")"
        ),
        {"dept": department_id, "usr": user_id},
    ).scalars()
    return {str(key) for key in rows}


def _policy_version(conn, *, department_id: str | None, user_id: str) -> int:
    """Versión de la política efectiva del usuario (claim ``policy_version``).

    Máx versión activa de ``policies`` (org + depto del usuario + usuario)
    MÁS la revisión de contenido del tenant (``policy_revisions``, migración
    0047): Policy Studio y Model Access no versionan por fila, y sin este
    término sus cambios llegaban con la misma versión — el heartbeat no
    anunciaba la actualización y la attestation no probaba qué contenido
    aplicó el dispositivo. Granularidad de tenant: un cambio en otro
    departamento también sube la versión (el contenido de este usuario puede
    no cambiar; nunca al revés).
    """
    row = conn.execute(
        text(
            "SELECT COALESCE((SELECT MAX(version) FROM policies "
            "  WHERE is_active AND ("
            "    level = 'org'"
            "    OR (level = 'dept' AND department_id = CAST(:dept AS uuid))"
            "    OR (level = 'user' AND user_id = CAST(:usr AS uuid))"
            "  )), 0)"
            " + COALESCE((SELECT r.revision FROM policy_revisions r"
            "   JOIN users u ON u.tenant_id = r.tenant_id"
            "   WHERE u.id = CAST(:usr AS uuid)), 0) AS v"
        ),
        {"dept": department_id, "usr": user_id},
    ).mappings().first()
    return int(row["v"])


def compile_bundle_payload(
    conn, principal: Principal, *, device_id: str
) -> dict:
    """Payload §5.2: policy efectiva + modelos + presupuesto + keys agente.

    Schema 2 (D-GOH-11, REQ-GOH-024): además de los claims estructurales,
    el payload lleva ``schema_version`` y TODAS las keys del catálogo vigente
    ``catalog.policy_keys`` aplanadas como claims de primer nivel con su
    valor efectivo combinado (deny-overrides de org/depto/usuario +
    invariables del proveedor). La compilación es DINÁMICA sobre el catálogo:
    cuando la migración del catálogo tipado (t_fb62560f) añada las 29 keys
    nuevas, este endpoint las incluye SIN cambio de código.
    """
    effective = effective_policy(
        conn, user_id=principal.user_id,
        department_id=principal.department_id,
    )
    approved_models = model_access.effective_models_for_user(conn, principal)
    budget_status = budgets_service.status_for_user(
        conn, principal.tenant_id, principal.user_id,
        principal.department_id,
    )
    # Piso del proveedor (D-GOH-08): la gracia combinada jamás baja de 15 min.
    offline_grace = max(
        OFFLINE_GRACE_FLOOR_MINUTES, int(effective.get("offline_grace_minutes", 60))
    )
    now = int(datetime.now(timezone.utc).timestamp())
    # Spec 024 (REQ-024-010, D-24-06): ADDITIVE optional claim — the
    # effective connections (catalog slugs under the combined rule +
    # allowed custom connections with NON-secret metadata) so the
    # enrolled Hermes knows which connections it may use. Deliberately
    # NOT in REQUIRED_CLAIMS: bundles signed before/without this claim
    # keep verifying (schema versioned/extensible, spec 019 §5.2 intact).
    from ..vault.connections import effective_connections_for_user

    payload = {
        "iss": get_settings().policy_jws_issuer,
        "aud": device_id,
        "iat": now,
        "exp": now + offline_grace * 60,
        "jti": uuid.uuid4().hex,
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "device_id": device_id,
        "policy_version": _policy_version(
            conn, department_id=principal.department_id,
            user_id=principal.user_id,
        ),
        # D-GOH-11: schema versionado — el plugin lo usa para decidir qué
        # keys conoce; ausente = schema 1 (bundle viejo, retro-compat).
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "allowed_tools": list(effective.get("allowed_tools") or []),
        "allowed_connectors": list(effective.get("allowed_connectors") or []),
        "allowed_connections": effective_connections_for_user(
            conn, principal
        ),
        "approved_models": approved_models,
        "budget": {
            "user": {
                "remaining_cents": budget_status["user"]["remaining_cents"],
                "cutoff": budget_status["user"]["cutoff"],
            },
            "dept": {
                "remaining_cents": budget_status["department"]["remaining_cents"],
                "cutoff": budget_status["department"]["cutoff"],
            },
            "org": {
                "remaining_cents": budget_status["organization"]["remaining_cents"],
                "cutoff": budget_status["organization"]["cutoff"],
            },
        },
        "offline_grace_minutes": offline_grace,
        "unbind_mode": effective.get("unbind_mode", "readonly"),
        "attestation_overdue_hours": int(
            effective.get("attestation_overdue_hours", 24)
        ),
    }
    # Spec 044 (Policy Studio): el bundle firmado transporta la política
    # global del departamento del usuario — prompt de organización, mapa de
    # opciones de UI y credenciales/perfiles autorizados. El plugin los aplica
    # al armar cada sesión; el prompt NO se lee de SOUL.md ni del perfil local.
    from ..policy_bundles.router import effective_bundle_for_user

    studio_bundle = effective_bundle_for_user(conn, principal.user_id)
    payload["global_prompt"] = (studio_bundle or {}).get("global_prompt") or ""
    payload["ui_features"] = (studio_bundle or {}).get("desktop") or {}
    # White-label identity carried in the signed bundle. The desktop must never
    # present itself as the upstream Hermes Agent / Nous Research product.
    from ..org import branding as branding_service
    _branding = branding_service.get_branding(conn)
    payload["product_name"] = str(_branding.get("product_name") or "Trust Agent")
    payload["tenant_name"] = principal.tenant_name or principal.tenant_slug
    payload["policy_credentials"] = (studio_bundle or {}).get("credentials") or {}
    payload["policy_profiles"] = (studio_bundle or {}).get("profiles") or []
    payload["policy_models"] = (studio_bundle or {}).get("models") or []
    payload["policy_default_model"] = (studio_bundle or {}).get("default_model")
    payload["policy_agents"] = (studio_bundle or {}).get("agents") or []
    payload["policy_contracts"] = (studio_bundle or {}).get("contracts") or []
    if payload["policy_models"]:
        allowed = set(payload.get("approved_models") or [])
        payload["approved_models"] = [m for m in payload["policy_models"] if not allowed or m in allowed]

    # CLI/agent capabilities (Policy Studio → CLI & Agente). The switches
    # govern the tool surface the governance plugin enforces in EVERY session
    # (desktop chat, CLI, background runs): ON grants the family, OFF removes
    # it — bounded by an explicit allowed_tools policy if one exists.
    ui = payload.get("ui_features") or {}
    explicit_keys = _explicit_policy_keys(
        conn, department_id=principal.department_id, user_id=principal.user_id,
    )
    payload["allowed_tools"] = clamp_tools_to_studio(
        payload.get("allowed_tools") or [],
        ui,
        explicit_ceiling="allowed_tools" in explicit_keys,
    )

    # REQ-GOH-024: TODAS las keys del catálogo vigente, aplanadas. Los claims
    # estructurales (ya presentes, con su compilación propia: piso de gracia,
    # allowed_connections del vault, ...) NO se sobrescriben por keys del
    # catálogo homónimas (defensa contra colisiones futuras del catálogo).
    for key, value in effective.items():
        if key in RESERVED_CLAIMS or key in payload:
            continue
        payload[key] = value
    # After flattening: the implied keys override catalog DEFAULTS only
    # (explicit policies are left as the ceiling — see the function).
    apply_studio_implied_keys(payload, ui, explicit_keys)
    return payload


def sign_bundle(payload: dict) -> str:
    """JWS compacto RS256 con ``kid`` = thumbprint RFC 7638 (§5.1)."""
    settings = get_settings()
    return jwt.encode(
        payload,
        jws_keys.private_pem(settings),
        algorithm=jws_keys.ALG,
        headers={"kid": jws_keys.public_jwk(settings)["kid"], "typ": "JWT"},
    )


@bundle_router.get("/governance-bundle")
def governance_bundle(request: Request):
    """GET /api/me/governance-bundle (REQ-GOH-001): bundle JWS + metadatos
    de sync. Bearer + MFA + X-Device-Token (ver require_bundle_auth)."""
    auth = require_bundle_auth(request)
    engine = get_engine()
    with engine.begin() as conn:
        set_tenant(conn, auth.principal.tenant_id)
        # DeviceGate en la misma transacción: revocación → 401 inmediato.
        check_device_access(conn, auth.device_id, auth.principal)
        payload = compile_bundle_payload(conn, auth.principal, device_id=auth.device_id)
        # Spec 044: la política que el dispositivo acaba de recibir es la que
        # su clave del gateway debe enforcear. Sin esto, un modelo retirado de
        # la política sigue respondiendo con claves emitidas antes del cambio
        # (verificado en vivo: HTTP 200 tras quitarlo). Best-effort: si el
        # gateway está caído el sync no se rompe — el próximo sync reintenta.
        try:
            keys_service.rescope_user_keys(
                conn, auth.principal,
                user_id=auth.principal.user_id,
                models=list(payload.get("approved_models") or []),
            )
        except Exception:
            logger.warning("key rescope failed during bundle sync", exc_info=True)
    bundle = sign_bundle(payload)
    return {
        "bundle": bundle,
        "expires_at": datetime.fromtimestamp(payload["exp"], timezone.utc).isoformat(),
        "policy_version": payload["policy_version"],
        "offline_grace_minutes": payload["offline_grace_minutes"],
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


# --- Update gate (mandato Miller): el agente SOLO se actualiza por lo que
# el PROVEEDOR publicó para este tenant — nunca upstream de Hermes --------

@bundle_router.get("/update-check")
def update_check(
    request: Request, current_git_rev: str = "", platform: str = "windows"
):
    """GET /api/me/update-check: compara lo instalado contra lo PUBLICADO.

    El desktop/CLI gobernado jamás consulta el updater nativo de Hermes
    (upstream) — esta es la ÚNICA fuente de verdad de versión para un
    agente bajo gobernanza. Sin build publicado para la plataforma pedida
    → ``update_available=false`` (honestidad, constitution I: nunca se
    inventa una versión nueva). Misma auth que el bundle (device token).
    """
    import json as _json

    auth = require_bundle_auth(request)
    engine = get_engine()
    with engine.begin() as conn:
        set_tenant(conn, auth.principal.tenant_id)
        check_device_access(conn, auth.device_id, auth.principal)

    from ..download import installers as installers_service

    slug = auth.principal.tenant_slug
    if platform not in installers_service.PLATFORMS:
        platform = "windows"
    manifest_file = installers_service.current_installer_dir(slug) / "installer-manifest.json"
    base: dict = {
        "current_git_rev": current_git_rev or None,
        "platform": platform,
    }
    if not manifest_file.is_file():
        return {
            **base, "update_available": False,
            "published_git_rev": None, "published_version": None,
            "download_url": None, "sha256": None, "size": None,
            "message": "Your provider has not published an installer build yet.",
        }
    try:
        manifest = _json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            **base, "update_available": False,
            "published_git_rev": None, "published_version": None,
            "download_url": None, "sha256": None, "size": None,
            "message": "Installer manifest unreadable; treat as no update.",
        }
    published_git_rev = str(manifest.get("git_rev") or "") or None
    info = installers_service.available_installers(slug).get(platform)
    update_available = bool(
        published_git_rev and info
        and published_git_rev != (current_git_rev or None)
    )
    url_base = str(request.base_url).rstrip("/")
    return {
        **base,
        "update_available": update_available,
        "published_git_rev": published_git_rev,
        "published_version": manifest.get("plugin_version"),
        "download_url": (
            f"{url_base}/download/{slug}/installer/{platform}"
            if update_available else None
        ),
        "sha256": info["sha256"] if (update_available and info) else None,
        "size": info["size"] if (update_available and info) else None,
        "message": (
            "Your provider published a new build." if update_available
            else "You are on the version published by your provider."
        ),
    }


@public_router.get("/.well-known/policy-jwks.json")
def policy_jwks():
    """JWKS del firmante de bundles (§5.5) — público, para rotación de
    claves; el plugin pinea el kid inicial junto a su clave pública."""
    return jws_keys.public_jwks(get_settings())

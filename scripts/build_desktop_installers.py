#!/usr/bin/env python3
"""Build de los instaladores desktop por plataforma (spec 040, D-040-05/07).

Corre EN EL HOST (no en el contenedor api): necesita node 22, npm,
electron-builder y red. Para un tenant:

  1. Lee el tenant + branding PUBLICADO de la DB canónica (migrator,
     bypass RLS — mismo patrón que _public_tenant del router).
  2. Hornea resources/org-binding.json + resources/branding.json en
     platform/installer-app/ (D-040-02: binding DENTRO del instalador).
  3. Genera los iconos (build/icon.ico + icon.png) desde el branding.
  4. Asegura el toolset wine portable (NSIS en Linux lo necesita para
     extraer el uninstaller; el wine del host Ubuntu está roto en wow64).
  5. Corre electron-builder por plataforma (NSIS .exe / AppImage).
  6. Copia los artefactos a installer-artifacts/{slug}/{plugin_version}/
     y escribe installer-manifest.json (sha256 + size + filenames).

Uso:
  python3 scripts/build_desktop_installers.py --slug hermes-dl \
      [--api-base http://127.0.0.1:8000] [--platforms windows,linux] \
      [--artifacts-dir ./installer-artifacts]

Requisitos del host: node 22 + npm, wine64 + wine32:i386 (NSIS), el venv de
platform (psycopg, Pillow para los iconos).

macOS (.dmg) NO se puede construir desde Linux (spec 040 §2): usa el
workflow .github/workflows/desktop-installers-macos.yml en un runner
macos-14. Nada simulado (constitution I).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "platform" / "installer-app"
PLUGIN_DIR = REPO / "platform" / "hermes" / "plugins" / "trust-agent-governance"

DEFAULT_MODEL = "glm-5.2"


def die(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def sh(cmd: list[str], *, env: dict | None = None, cwd: Path | None = None) -> None:
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, env={**os.environ, **(env or {})},
                   cwd=cwd or REPO)


def plugin_version() -> str:
    manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*(\S+)", manifest, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


# --- 1. Tenant + branding desde la DB canónica --------------------------------

def load_tenant(slug: str) -> dict:
    try:
        import psycopg
    except ImportError:
        die("psycopg no está instalado — corre con el venv de platform "
            "(platform/.venv/bin/python)")
    url = os.environ.get("TRUST_DATABASE_MIGRATOR_URL") or (
        "postgresql://trust_migrator:"
        f"{os.environ.get('TRUST_MIGRATOR_PASSWORD') or 'trust_migrator_dev'}"
        "@127.0.0.1:55432/trust_agent"
    )
    dsn = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT id, name, slug, status, oidc_issuer_url, oidc_client_id, "
            "branding FROM tenants WHERE slug = %s", (slug,)
        ).fetchone()
        if row is None:
            die(f"tenant '{slug}' no existe en la DB canónica")
        tenant = {
            "id": str(row[0]), "name": row[1], "slug": row[2],
            "status": row[3], "oidc_issuer_url": row[4] or "",
            "oidc_client_id": row[5] or "trust-agent-web",
            "branding": row[6] or {},
        }
        # Logo publicado → data URI (mismo criterio que la página pública,
        # D-023-11: el instalador no debe llamar endpoints autenticados).
        for key in ("logo_light_asset_id", "logo_asset_id"):
            asset_id = (tenant["branding"] or {}).get(key)
            if asset_id:
                a = conn.execute(
                    "SELECT mime, content FROM branding_assets "
                    "WHERE id = %s", (asset_id,)
                ).fetchone()
                if a and a[0] == "image/png":
                    tenant["logo_data_uri"] = (
                        f"data:{a[0]};base64,"
                        f"{base64.b64encode(a[1]).decode()}"
                    )
                    break
        return tenant


# --- 2. Resources horneados (D-040-02) -----------------------------------------

def bake_resources(tenant: dict, api_base: str, litellm_base: str,
                   version: str) -> None:
    res = APP_DIR / "resources"
    res.mkdir(parents=True, exist_ok=True)
    branding = tenant["branding"] or {}
    binding = {
        "slug": tenant["slug"],
        "api_base": api_base,
        "litellm_base_url": litellm_base,
        "model": DEFAULT_MODEL,
        "oidc_issuer_url": tenant["oidc_issuer_url"],
        "oidc_client_id": tenant["oidc_client_id"],
        "device_type": "desktop",
        "plugin_version": version,
    }
    (res / "org-binding.json").write_text(
        json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    (res / "branding.json").write_text(json.dumps({
        "product_name": branding.get("product_name") or "Trust Agent",
        "tagline": branding.get("tagline") or "",
        "accent_color": branding.get("accent_color") or "#4F46E5",
        "logo_data_uri": tenant.get("logo_data_uri"),
    }, indent=2) + "\n", encoding="utf-8")
    print(f"  resources horneados: slug={tenant['slug']} "
          f"api_base={api_base} plugin=v{version}")


# --- 3. Iconos desde el branding ------------------------------------------------

def generate_icons(tenant: dict) -> None:
    from PIL import Image, ImageDraw

    branding = tenant["branding"] or {}
    accent = branding.get("accent_color") or "#4F46E5"
    letter = (branding.get("product_name") or "T").strip()[:1].upper()

    def hex_rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    build = APP_DIR / "build"
    build.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, 248, 248], radius=52, fill=hex_rgb(accent))
    # Letter centered (best-effort with the default bitmap font, drawn as
    # rectangles scaled up — keeps the build dependency-free).
    try:
        from PIL import ImageFont
        font = ImageFont.load_default(size=150)
        bbox = d.textbbox((0, 0), letter, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((256 - w) / 2 - bbox[0], (256 - h) / 2 - bbox[1]), letter,
               font=font, fill="white")
    except Exception:
        d.rounded_rectangle([96, 96, 160, 160], radius=18, fill="white")
    img.save(build / "icon.png")
    img.save(build / "icon.ico", sizes=[(256, 256), (128, 128), (64, 64),
                                        (48, 48), (32, 32), (16, 16)])
    print(f"  iconos generados ({accent}, '{letter}') → build/icon.png|.ico")


# --- 4. Requisito wine para NSIS en Linux ---------------------------------------

def ensure_wine() -> None:
    """NSIS en Linux: electron-builder ejecuta el instalador una vez bajo
    wine para extraer el uninstaller. El host necesita wine64 + wine32:i386
    (sin el loader 32-bit falla con syswow64\\ntdll.dll — evidencia en
    docs/evidencia/desktop-installers/)."""
    if shutil.which("wine") is None:
        die("wine no está en PATH — instala wine64 + wine32:i386 "
            "(Ubuntu: dpkg --add-architecture i386 && apt install wine32 wine64)")
    print("  wine del host ok (requisito NSIS en Linux)")


# --- 5/6. Build + artefactos ------------------------------------------------------

BUILDER_TARGET = {
    "windows": ["--win", "nsis"],
    "linux": ["--linux", "AppImage"],
    "macos": ["--mac", "dmg"],  # solo en un runner macOS (spec 040 §2)
}
ARTIFACT_GLOBS = {
    "windows": "trust-agent-setup-*-win-*.exe",
    "linux": "trust-agent-setup-*-linux-*.AppImage",
    "macos": "trust-agent-setup-*-mac-*.dmg",
}


def build_platform(platform: str, tenant: dict, version: str) -> Path:
    dist = APP_DIR / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    env = {
        "TA_PRODUCT_NAME": (tenant["branding"] or {}).get("product_name")
        or "Trust Agent Setup",
        "TA_SLUG": tenant["slug"],
        "TA_PLUGIN_VERSION": version,
    }
    npx = shutil.which("npx") or die("npx no está en PATH (node 22 requerido)")
    sh([npx, "electron-builder", *BUILDER_TARGET[platform],
        "--config", "electron-builder.config.cjs", "--publish", "never"],
       env=env, cwd=APP_DIR)
    matches = sorted(dist.glob(ARTIFACT_GLOBS[platform]))
    if not matches:
        die(f"electron-builder no produjo artefacto para {platform} "
            f"(glob {ARTIFACT_GLOBS[platform]} en {dist})")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--api-base",
                        default=os.environ.get("TRUST_API_BASE",
                                               "http://127.0.0.1:8000"))
    parser.add_argument("--platforms", default="windows,linux")
    parser.add_argument("--artifacts-dir", default=str(REPO / "installer-artifacts"))
    parser.add_argument("--skip-npm-install", action="store_true")
    args = parser.parse_args()

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    for p in platforms:
        if p not in BUILDER_TARGET:
            die(f"plataforma desconocida: {p} (usa {list(BUILDER_TARGET)})")
    if "macos" in platforms and sys.platform == "linux":
        die("el .dmg de macOS NO se puede construir desde Linux (spec 040 §2) "
            "— usa .github/workflows/desktop-installers-macos.yml")

    version = plugin_version()
    print(f"==> Build de instaladores para '{args.slug}' — plugin v{version}, "
          f"plataformas: {', '.join(platforms)}")
    tenant = load_tenant(args.slug)
    if tenant["status"] != "active":
        die(f"tenant '{args.slug}' no está active (status={tenant['status']})")
    litellm_base = os.environ.get("TRUST_LITELLM_BASE_URL",
                                  "http://127.0.0.1:4000")

    bake_resources(tenant, args.api_base.rstrip("/"), litellm_base, version)
    generate_icons(tenant)

    if "windows" in platforms and sys.platform == "linux":
        ensure_wine()

    if not args.skip_npm_install and not (APP_DIR / "node_modules").is_dir():
        npm = shutil.which("npm") or die("npm no está en PATH")
        # electron-builder PINNADO a 26.18.0 (estable): el flujo NSIS-Linux del
        # alpha 27 está roto (pasaje Z:\ al makensis nativo wfwns — evidencia
        # en docs/evidencia/desktop-installers/).
        sh([npm, "install", "--save-dev", "electron@40.10.2",
            "electron-builder@26.18.0"], cwd=APP_DIR)

    artifacts_dir = Path(args.artifacts_dir) / args.slug / version
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "slug": tenant["slug"],
        "plugin_version": version,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_rev": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
            capture_output=True, text=True).stdout.strip() or "unknown",
        "platforms": {},
    }
    for platform in platforms:
        print(f"==> Construyendo {platform}…")
        artifact = build_platform(platform, tenant, version)
        dest = artifacts_dir / artifact.name
        shutil.copy2(artifact, dest)
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        manifest["platforms"][platform] = {
            "filename": dest.name,
            "sha256": digest,
            "size": dest.stat().st_size,
        }
        print(f"    {dest.name} — sha256 {digest[:16]}… "
              f"({dest.stat().st_size // (1024 * 1024)} MB)")

    (artifacts_dir / "installer-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"==> Listo: {artifacts_dir}/installer-manifest.json")
    print(json.dumps(manifest["platforms"], indent=2))


if __name__ == "__main__":
    main()

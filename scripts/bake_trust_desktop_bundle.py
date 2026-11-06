#!/usr/bin/env python3
"""Hornea el bundle del Trust Agent Desktop (variante `trust` de Hermes
Desktop) con el branding PUBLICADO del tenant.

El build del desktop completo (apps/desktop, HERMES_DESKTOP_VARIANT=trust)
lee `apps/desktop/trust-agent-bundle/`:
  - bundle.json  -> org_code / api_base / litellm_base / model / skin
  - skin.yaml    -> skin white-label (colores + agent_name)
  - assets/icon  -> icono de la app

Hasta ahora ese bundle era ESTÁTICO (genérico "Trust Agent", indigo) — por
eso una organización con branding propio (p. ej. product_name="Shawm Agent")
veía la app con el nombre/color genérico, aunque la Admin Console sí mostraba
su branding. Este script lee el branding publicado de la DB canónica y
reescribe ambos archivos; el build posterior queda white-labeled de verdad.

Uso:
  python3 scripts/bake_trust_desktop_bundle.py --slug demo-miller \
      [--api-base http://127.0.0.1:8000] [--litellm-base http://127.0.0.1:4000]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DESKTOP = Path("/usr/local/lib/hermes-agent/apps/desktop")
BUNDLE = DESKTOP / "trust-agent-bundle"

DEFAULT_MODEL = os.environ.get("TRUST_AGENT_MODEL", "deepseek-v4.1-flash-1017")


def load_tenant(slug: str) -> dict:
    try:
        import psycopg
    except ImportError:
        sys.exit("psycopg no está instalado — usa el venv de platform")

    url = os.environ.get("TRUST_DATABASE_MIGRATOR_URL") or (
        "postgresql://trust_migrator:"
        f"{os.environ.get('TRUST_MIGRATOR_PASSWORD') or 'trust_migrator_dev'}"
        "@127.0.0.1:55432/trust_agent"
    )
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT slug, name, oidc_issuer_url, oidc_client_id, branding "
            "FROM tenants WHERE slug = %s",
            (slug,),
        )
        row = cur.fetchone()
        if not row:
            sys.exit(f"tenant {slug!r} no existe")
        branding = row[4] or {}
        logo_bytes = None
        logo_mime = None
        logo_id = branding.get("logo_light_asset_id") or branding.get("logo_dark_asset_id")
        if logo_id:
            cur.execute(
                "SELECT content, mime FROM public.branding_assets WHERE id = %s",
                (logo_id,),
            )
            asset = cur.fetchone()
            if asset:
                logo_bytes, logo_mime = bytes(asset[0]), asset[1]
        return {
            "slug": row[0], "name": row[1],
            "oidc_issuer_url": row[2] or "",
            "oidc_client_id": row[3] or "trust-agent-web",
            "branding": branding,
            "logo_bytes": logo_bytes,
            "logo_mime": logo_mime,
        }


def bake(slug: str, api_base: str, litellm_base: str, model: str = DEFAULT_MODEL) -> dict:
    tenant = load_tenant(slug)
    branding = tenant["branding"]
    product_name = (branding.get("product_name") or "Trust Agent").strip()
    accent = (branding.get("accent_color") or "#4F46E5").strip()
    skin_name = f"trust-{slug}"

    bundle = {
        "org_code": slug,
        "product_name": product_name,
        "api_base": api_base.rstrip("/"),
        "litellm_base": litellm_base.rstrip("/"),
        "model": model,
        "skin": skin_name,
        "oidc_issuer_url": tenant["oidc_issuer_url"],
        "oidc_client_id": tenant["oidc_client_id"],
    }
    BUNDLE.mkdir(parents=True, exist_ok=True)
    (BUNDLE / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")

    skin = f"""# Trust Agent — white-label skin generado desde el branding publicado.
# Tenant: {slug} — {tenant['name']}
# Activate with: hermes config set display.skin {skin_name}
name: {skin_name}
description: {product_name} — AI governance for your organization

colors:
  background: "#101318"
  ui_accent: "{accent}"
  banner_accent: "{accent}"
  banner_title: "#e7eaf0"
  banner_text: "#c6ccd8"
  ui_text: "#c6ccd8"
  ui_label: "#9aa3b2"
  banner_dim: "#7d8695"
  banner_border: "#2a303b"
  ui_border: "#2a303b"
  ui_ok: "#5af7b0"
  ui_warn: "#ffcf5f"
  ui_error: "#ff6b7d"
  prompt: "#e7eaf0"
  input_rule: "{accent}"
  response_border: "{accent}"
  status_bar_bg: "#0b0e12"
  status_bar_text: "#c6ccd8"
  status_bar_good: "#5af7b0"
  status_bar_warn: "#ffcf5f"
  status_bar_critical: "#ff6b7d"
  session_label: "#e7eaf0"
  session_border: "#2a303b"

branding:
  agent_name: "{product_name}"
  prompt_symbol: "❯"
  help_header: "Commands"
"""
    (BUNDLE / "skin.yaml").write_text(skin, encoding="utf-8")

    # --- Logo del tenant: UI brand mark + icono de la app -------------------
    # El branding publicado puede traer logo_light/logo_dark (PNG/SVG). Se
    # escribe el mismo archivo que consume BrandMark (public/trust-brand.png)
    # y se derivan los iconos de la app (icon.png + icon.ico). Sin logo, se
    # genera una baldosa con la inicial del producto y el color de acento —
    # nunca el fantasma de Hermes.
    import io
    from PIL import Image

    def square_logo(image: Image.Image, size: int) -> Image.Image:
        """Fit a non-square tenant logo without distortion.

        The branding asset may be wide or tall (wordmark, horizontal logo).
        The old code called ``resize((256, 256))`` directly, which stretched
        the mark into a square — visible as the deformed logo in the Windows
        installer. Letterbox on a transparent canvas instead.
        """
        image = image.convert("RGBA")
        bbox = image.getbbox()
        if bbox:
            image = image.crop(bbox)
        image.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(
            image,
            ((size - image.width) // 2, (size - image.height) // 2),
        )
        return canvas

    public_dir = DESKTOP / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    logo_bytes = tenant.get("logo_bytes")
    logo_mime = tenant.get("logo_mime") or ""
    logo = None
    if logo_bytes:
        try:
            logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
        except Exception:
            logo = None
    if logo is None:
        accent_rgb = tuple(int(accent.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        logo = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        from PIL import ImageDraw, ImageFont
        d = ImageDraw.Draw(logo)
        d.rounded_rectangle([32, 32, 992, 992], radius=200, fill=(*accent_rgb, 255))
        letter = (product_name or "T").strip()[:1].upper()
        try:
            font = ImageFont.load_default(size=600)
            bbox = d.textbbox((0, 0), letter, font=font)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            d.text(((1024 - w) / 2 - bbox[0], (1024 - h) / 2 - bbox[1]),
                   letter, font=font, fill="white")
        except Exception:
            pass
    square_logo(logo, 512).save(public_dir / "trust-brand.png", "PNG")
    (BUNDLE / "assets").mkdir(parents=True, exist_ok=True)
    square_logo(logo, 256).save(BUNDLE / "assets" / "icon.png", "PNG")
    square_logo(logo, 256).save(
        BUNDLE / "assets" / "icon.ico", "ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    # --- Windows MSIX tile assets (Start menu / taskbar / Apps list) --------
    # electron-builder's MSIX target stages assets/appx from the template
    # directory. In the trust variant before-build.mjs reads this generated
    # set instead, so the Windows shell never shows the upstream Hermes
    # anime-girl artwork on a tenant-branded install.
    appx_src = DESKTOP / "assets" / "appx"
    appx_out = BUNDLE / "assets" / "appx"
    appx_out.mkdir(parents=True, exist_ok=True)
    if appx_src.is_dir():
        for source in sorted(appx_src.glob("*.png")):
            with Image.open(source) as reference:
                width, height = reference.size
            canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
            mark = square_logo(logo, max(16, int(min(width, height) * 0.72)))
            canvas.alpha_composite(
                mark,
                ((width - mark.width) // 2, (height - mark.height) // 2),
            )
            canvas.save(appx_out / source.name, "PNG")
        print(f"MSIX appx assets: {len(list(appx_out.glob('*.png')))} → {appx_out}")

    print(f"branding assets: {public_dir / 'trust-brand.png'} + icon.png/ico")
    print(f"bundle.json + skin.yaml horneados: {slug} -> {product_name} ({accent})")
    return {"product_name": product_name, "skin": skin_name, "bundle": bundle}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--api-base", default=os.environ.get("TRUST_API_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--litellm-base", default=os.environ.get("TRUST_LITELLM_BASE_URL", "http://127.0.0.1:4000"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    out = bake(args.slug, args.api_base, args.litellm_base, args.model)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

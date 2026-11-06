#!/usr/bin/env python3
"""Bake the platform-neutral Trust Agent desktop bundle.

The universal MSIX must not contain a tenant binding: every organization
creates its own agent at activation time by redeeming a one-time enrollment
token against its portal. This script writes the neutral bundle.json, a
generic skin and neutral app icons into the desktop build tree.

Usage:
  python3 scripts/bake_generic_desktop_bundle.py
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DESKTOP = Path("/usr/local/lib/hermes-agent/apps/desktop")
BUNDLE = DESKTOP / "trust-agent-bundle"
PRODUCT = "Trust Agent"
ACCENT = "#4F46E5"
SKIN = "trust-agent"


def square(image: Image.Image, size: int) -> Image.Image:
    image = image.convert("RGBA")
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    image.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    return canvas


def generic_logo(size: int = 1024) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rgb = tuple(int(ACCENT.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        [size // 32, size // 32, size - size // 32, size - size // 32],
        radius=size // 6,
        fill=(*rgb, 255),
    )
    try:
        font = ImageFont.load_default(size=int(size * 0.58))
        bbox = draw.textbbox((0, 0), "T", font=font)
        draw.text(
            ((size - (bbox[2] - bbox[0])) / 2 - bbox[0], (size - (bbox[3] - bbox[1])) / 2 - bbox[1]),
            "T",
            font=font,
            fill="white",
        )
    except Exception:
        pass
    return image


def main() -> None:
    BUNDLE.mkdir(parents=True, exist_ok=True)
    (BUNDLE / "bundle.json").write_text(
        json.dumps(
            {
                "org_code": "",
                "product_name": PRODUCT,
                "api_base": "",
                "litellm_base": "",
                "model": "",
                "skin": SKIN,
                "oidc_issuer_url": "",
                "oidc_client_id": "trust-agent-web",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (BUNDLE / "skin.yaml").write_text(
        f"""name: {SKIN}
description: {PRODUCT} — AI governance for your organization

colors:
  background: "#101318"
  ui_accent: "{ACCENT}"
  banner_accent: "{ACCENT}"
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
  input_rule: "{ACCENT}"
  response_border: "{ACCENT}"
  status_bar_bg: "#0b0e12"
  status_bar_text: "#c6ccd8"
  status_bar_good: "#5af7b0"
  status_bar_warn: "#ffcf5f"
  status_bar_critical: "#ff6b7d"
  session_label: "#e7eaf0"
  session_border: "#2a303b"

branding:
  agent_name: {PRODUCT}
  prompt_symbol: "❯"
  help_header: "Commands"
""",
        encoding="utf-8",
    )

    logo = generic_logo(1024)
    public = DESKTOP / "public"
    public.mkdir(parents=True, exist_ok=True)
    square(logo, 512).save(public / "trust-brand.png", "PNG")
    (BUNDLE / "assets").mkdir(parents=True, exist_ok=True)
    square(logo, 256).save(BUNDLE / "assets" / "icon.png", "PNG")
    square(logo, 256).save(
        BUNDLE / "assets" / "icon.ico",
        "ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    appx_src = DESKTOP / "assets" / "appx"
    appx_out = BUNDLE / "assets" / "appx"
    appx_out.mkdir(parents=True, exist_ok=True)
    if appx_src.is_dir():
        for source in sorted(appx_src.glob("*.png")):
            with Image.open(source) as reference:
                width, height = reference.size
            canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
            mark = square(logo, max(16, int(min(width, height) * 0.72)))
            canvas.alpha_composite(mark, ((width - mark.width) // 2, (height - mark.height) // 2))
            canvas.save(appx_out / source.name, "PNG")
    print(f"generic bundle written: {BUNDLE}")


if __name__ == "__main__":
    main()

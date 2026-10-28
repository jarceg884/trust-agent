"""Página pública de descarga (spec 023, REQ-HDL-001, D-023-11/12).

HTML autocontenido servido por el control plane (el origen estable del link
no tiene runtime Node): aplica el estándar ui-standard.md en su lenguaje
visual — iconografía **lucide** oficial (SVGs extraídos del paquete
lucide-react v1.61.0 instalado en platform/console, stroke-width 1.75, nada
de SVG casero), entrada escalonada `ta-rise` bajo `prefers-reduced-motion`,
transiciones en los interactivos, `focus-visible`, y el branding PUBLICADO
del tenant inline (logo como data URI). UI copy en inglés.
"""
from __future__ import annotations

import html
from typing import Any

# Official lucide icon nodes (lucide-react v1.61.0, ISC) — element specs as
# (tag, attrs) pairs; rendered at stroke-width 1.75 per ui-standard.md.
_ICONS: dict[str, list[tuple[str, dict[str, str]]]] = {
    "download": [("path", {"d": "M12 15V3"}),
                 ("path", {"d": "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"}),
                 ("path", {"d": "m7 10 5 5 5-5"})],
    "shield-check": [("path", {"d": "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"}),
                     ("path", {"d": "m9 12 2 2 4-4"})],
    "user-check": [("path", {"d": "m16 11 2 2 4-4"}),
                   ("path", {"d": "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"}),
                   ("circle", {"cx": "9", "cy": "7", "r": "4"})],
    "lock": [("path", {"d": "M7 11V7a5 5 0 0 1 10 0v4"}),
             ("rect", {"x": "3", "y": "11", "width": "18", "height": "11", "rx": "2"})],
    "monitor-check": [("path", {"d": "m9 10 2 2 4-4"}),
                      ("path", {"d": "M12 17v4"}),
                      ("path", {"d": "M8 21h8"}),
                      ("rect", {"x": "2", "y": "3", "width": "20", "height": "14", "rx": "2"})],
    "terminal": [("path", {"d": "m4 17 6-6-6-6"}),
                 ("path", {"d": "M12 19h8"})],
    "copy": [("rect", {"x": "9", "y": "9", "width": "13", "height": "13", "rx": "2", "ry": "2"}),
             ("path", {"d": "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"})],
    "check": [("path", {"d": "M20 6 9 17l-5-5"})],
}

_CSS = """
:root{--accent:__ACCENT__;--bg:#f7f8fa;--surface:#ffffff;--text:#171b23;
--muted:#5b6472;--border:#e3e6ec;--ok:#12805c;--warn:#8a5a00}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,
'Helvetica Neue',Arial,sans-serif;background:var(--bg);color:var(--text);
line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:880px;margin:0 auto;padding:0 24px}
a{color:inherit}
.icon{display:inline-flex;align-items:center;justify-content:center;
flex-shrink:0;color:var(--accent)}
.icon svg{width:20px;height:20px;stroke-width:1.75;fill:none;stroke:currentColor;
stroke-linecap:round;stroke-linejoin:round}
.icon.lg svg{width:26px;height:26px}
header{border-bottom:1px solid var(--border);background:var(--surface)}
header .wrap{display:flex;align-items:center;gap:12px;padding-top:18px;
padding-bottom:18px}
.logo{display:flex;align-items:center;gap:10px;font-weight:650;font-size:15px;
letter-spacing:-.01em}
.logo img{height:30px;width:auto;display:block}
.logo .mark{display:flex;align-items:center;justify-content:center;width:32px;
height:32px;border-radius:8px;background:var(--accent);color:#fff;
font-size:15px;font-weight:700}
.tagline{color:var(--muted);font-size:13px}
main .wrap{padding-top:56px;padding-bottom:72px}
.hero{max-width:640px}
.kicker{display:inline-flex;align-items:center;gap:8px;font-size:12.5px;
font-weight:600;color:var(--accent);background:color-mix(in srgb,
var(--accent) 9%,white);border:1px solid color-mix(in srgb,var(--accent) 22%,white);
padding:5px 12px;border-radius:999px}
h1{font-size:clamp(30px,5vw,42px);line-height:1.12;letter-spacing:-.025em;
font-weight:700;margin-top:18px}
h1 em{font-style:normal;color:var(--accent)}
.lede{margin-top:14px;color:var(--muted);font-size:16.5px;max-width:56ch}
.actions{display:flex;align-items:center;gap:16px;margin-top:28px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:10px;background:var(--accent);
color:#fff;font-weight:600;font-size:15px;padding:12px 22px;border-radius:10px;
text-decoration:none;border:1px solid transparent;transition:all .15s ease-out}
.btn:hover{filter:brightness(1.06)}
.btn:active{transform:scale(.98)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.btn[aria-disabled=true]{pointer-events:none;opacity:.55}
.btn.ghost{background:var(--surface);color:var(--text);border-color:var(--border)}
.btn.ghost:hover{border-color:var(--accent);filter:none}
.note{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
gap:16px;margin-top:56px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;
padding:22px}
.card .icon{width:40px;height:40px;border-radius:10px;background:color-mix(in srgb,
var(--accent) 9%,white);margin-bottom:14px}
.card h2{font-size:15.5px;font-weight:650;letter-spacing:-.01em}
.card p{margin-top:6px;color:var(--muted);font-size:13.5px}
.banner{display:flex;gap:12px;align-items:flex-start;border:1px solid;
border-radius:12px;padding:16px 18px;margin-top:28px;font-size:14px}
.banner.warn{background:#fff8eb;border-color:#f1dfae;color:#6b4a00}
.banner.bad{background:#fdf0f0;border-color:#f0c6c6;color:#8f2424}
.banner .icon{margin-top:1px}
.banner strong{display:block;font-size:14.5px;margin-bottom:2px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.92em;
background:var(--surface);border:1px solid var(--border);border-radius:6px;
padding:2px 7px}
.cli{margin-top:40px;background:var(--surface);border:1px solid var(--border);
border-radius:16px;padding:26px}
.cli h2{font-size:19px;font-weight:700;letter-spacing:-.015em;display:flex;
align-items:center;gap:10px}
.cli .sub{margin-top:6px;color:var(--muted);font-size:14px;max-width:64ch}
.cmd{display:flex;align-items:center;gap:10px;margin-top:16px}
.cmd code{flex:1;min-width:0;overflow-x:auto;white-space:nowrap;font-size:13px;
padding:12px 14px;background:#0f1218;color:#e7eaf0;border:1px solid #262c37;
border-radius:10px}
.copy{display:inline-flex;align-items:center;gap:7px;background:var(--accent);
color:#fff;border:none;border-radius:9px;padding:10px 16px;font-weight:600;
font-size:13.5px;cursor:pointer;transition:all .15s ease-out;font-family:inherit}
.copy:hover{filter:brightness(1.06)}
.copy:active{transform:scale(.98)}
.copy:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.copy.ok{background:var(--ok)}
.req{margin-top:14px;color:var(--muted);font-size:12.5px}
.req strong{color:var(--text);font-weight:600}
footer{border-top:1px solid var(--border)}
footer .wrap{display:flex;align-items:center;justify-content:space-between;
gap:12px;padding-top:20px;padding-bottom:28px;color:var(--muted);font-size:12.5px;
flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--border);
background:var(--surface);border-radius:999px;padding:4px 12px;font-size:12px;
font-weight:600}
@media (prefers-reduced-motion: no-preference){
.ta-rise{animation:rise .4s ease-out both}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;
transform:none}}
header .wrap{animation:rise .4s ease-out both}
.steps .card:nth-child(1){animation:rise .4s ease-out 70ms both}
.steps .card:nth-child(2){animation:rise .4s ease-out 140ms both}
.steps .card:nth-child(3){animation:rise .4s ease-out 210ms both}
}
"""


def _icon(name: str, cls: str = "") -> str:
    parts = []
    for tag, attrs in _ICONS[name]:
        attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        parts.append(f"<{tag} {attr_str}/>")
    return (
        f'<span class="icon {cls}" aria-hidden="true">'
        f'<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
        f"{''.join(parts)}</svg></span>"
    )


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _logo_html(branding: dict, logo_data_uri: str | None) -> str:
    if logo_data_uri:
        return f'<img src="{logo_data_uri}" alt="{_esc(branding["product_name"])} logo">'
    initial = _esc((branding.get("product_name") or "T")[:1].upper())
    return f'<span class="mark">{initial}</span>'


def _cli_section(slug: str, curl_cmd: str) -> str:
    """Sección CLI de la página pública (spec 034, REQ-034-003).

    Comando curl copiable (clipboard API + fallback execCommand) e
    instrucciones para los targets oficiales del CLI: Linux/macOS/WSL2.
    """
    cmd = _esc(curl_cmd)
    return f"""<section class="cli ta-rise" id="cli">
  <h2>{_icon("terminal", "lg")} Work on a server or VPS? Use the CLI</h2>
  <p class="sub">The same governed agent, installed from your terminal in one
  command. It comes pre-bound to <strong>{_esc(slug)}</strong> — your device
  enrolls automatically on first run and an admin approves it, exactly like
  the desktop app.</p>
  <div class="cmd">
    <code id="curl-cmd">{cmd}</code>
    <button class="copy" type="button" data-copy="{cmd}"
      aria-label="Copy the install command">
      {_icon("copy")} <span>Copy</span></button>
  </div>
  <p class="req"><strong>Requirements:</strong> Linux, macOS or WSL2 with
  <code>bash</code>, <code>curl</code> and <code>tar</code>. The installer
  adds the Hermes CLI (official installer) if it is not present, plus the
  governance plugin and your organization's branding. On a headless server,
  run <code>trust-login</code> once from a machine with a browser and copy
  the resulting <code>id-token.txt</code> over.</p>
</section>
<script>
(function() {{
  var btn = document.querySelector('.copy');
  if (!btn) return;
  btn.addEventListener('click', function() {{
    var text = btn.getAttribute('data-copy');
    var done = function() {{
      btn.classList.add('ok');
      btn.querySelector('span').textContent = 'Copied';
      setTimeout(function() {{
        btn.classList.remove('ok');
        btn.querySelector('span').textContent = 'Copy';
      }}, 2000);
    }};
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(done);
    }} else {{
      var ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); ta.remove(); done();
    }}
  }});
}})();
</script>"""


def render_page(
    *,
    slug: str,
    org_name: str,
    branding: dict,
    logo_data_uri: str | None,
    enabled: bool,
    api_base: str = "",
) -> str:
    """Página pública 200 (estado sano o disabled — D-023-12).

    Dos opciones (spec 034, REQ-034-003): Desktop app (bundle existente,
    spec 023) y CLI (comando curl copiable — instalador por tenant).
    Branding del tenant en ambas.
    """
    product = _esc(branding.get("product_name") or "Trust Agent")
    tagline = _esc(branding.get("tagline") or "")
    accent = _esc(branding.get("accent_color") or "#4F46E5")
    org = _esc(org_name)
    bundle_url = f"/download/{_esc(slug)}/bundle"
    cli_url = f"{_esc(api_base)}/download/{_esc(slug)}/cli"
    curl_cmd = f"curl -fsSL {cli_url} | bash"

    if enabled:
        cta = (
            f'<div class="actions ta-rise">'
            f'<a class="btn" href="{bundle_url}" download>'
            f'{_icon("download")} Download for desktop</a>'
            f'<a class="btn ghost" href="#cli">'
            f'{_icon("terminal")} Install on a server (CLI)</a>'
            f'<span class="note">{_icon("lock")} No account needed to '
            f"download — sign-in happens inside the app.</span></div>"
        )
        banner = ""
        cli_section = _cli_section(slug, curl_cmd)
    else:
        cta = (
            f'<div class="actions ta-rise">'
            f'<span class="btn" aria-disabled="true">{_icon("download")} '
            f"Downloads are currently disabled</span></div>"
        )
        banner = (
            f'<div class="banner warn ta-rise">{_icon("lock", "lg")}<div>'
            f"<strong>Downloads are temporarily disabled</strong>"
            f"Your organization admin has disabled new downloads. "
            f"Please contact them if you need the app.</div></div>"
        )
        cli_section = ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{product} — Download</title>
<style>{_CSS.replace("__ACCENT__", accent)}</style>
</head>
<body>
<header><div class="wrap">
  <div class="logo">{_logo_html(branding, logo_data_uri)}<div>
    <div>{product}</div>
    <div class="tagline">{tagline}</div>
  </div></div>
</div></header>
<main><div class="wrap">
  <section class="hero">
    <span class="kicker ta-rise">{_icon("shield-check")} {org} · organization edition</span>
    <h1 class="ta-rise">Your organization's AI agent,<br><em>governed from day one.</em></h1>
    <p class="lede ta-rise">Download the complete {product} desktop agent, sign in
    with your organization account, and your device is enrolled automatically —
    running under your organization's security policies.</p>
    {banner}{cta}
  </section>
  {cli_section}
  <section class="steps">
    <div class="card">{_icon("download", "lg")}<h2>1 · Download &amp; install</h2>
      <p>Unpack and run <code>./install.sh</code>. It installs the full agent
      with your organization's branding preconfigured.</p></div>
    <div class="card">{_icon("user-check", "lg")}<h2>2 · Sign in once</h2>
      <p>Run <code>trust-login</code> and authenticate with your organization
      account (SSO + MFA) in the browser.</p></div>
    <div class="card">{_icon("monitor-check", "lg")}<h2>3 · Governed automatically</h2>
      <p>Your device enrolls itself on first run. Once your admin approves it,
      organization policies apply to every session.</p></div>
  </section>
</div></main>
<footer><div class="wrap">
  <span>Organization code: <span class="chip">{_icon("lock")} {_esc(slug)}</span></span>
  <span>Powered by Trust Agent</span>
</div></footer>
</body>
</html>"""


def render_suspended_page(*, branding: dict, logo_data_uri: str | None) -> str:
    """Tenant suspendido → rehusado fail-closed (D-023-12, REQ-HDL-007)."""
    product = _esc(branding.get("product_name") or "Trust Agent")
    accent = _esc(branding.get("accent_color") or "#4F46E5")
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{product} — unavailable</title>
<style>{_CSS.replace("__ACCENT__", accent)}</style></head>
<body>
<main><div class="wrap" style="padding-top:96px;padding-bottom:96px">
  <section class="hero">
    <div class="banner bad">{_icon("lock", "lg")}<div>
      <strong>This organization's download page is unavailable.</strong>
      The organization is not currently active. Please contact your
      administrator.</div></div>
  </section>
</div></main>
</body></html>"""

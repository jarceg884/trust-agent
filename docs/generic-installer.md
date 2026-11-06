# Instalador universal para cualquier organización

Este documento describe el flujo soportado para que **cualquier tenant** use
el mismo instalador Windows. No hay un binario por cliente: el MSIX se
construye una vez con un bundle neutro y el empleado lo liga a su
organización en la activación mediante un token de un solo uso.

## 1. Por qué un solo MSIX

ADR-003: el instalador es uno; el branding y el binding viven en el control
plane. Un build por tenant obliga a reconstruir y firmar un binario de ~744 MB
para cada cliente y hace imposible que un clon del repo sirva a una org nueva
sin un build manual.

El MSIX universal incluye:

- el shell Hermes Desktop con la variante gobernada `trust`;
- el plugin `trust-agent-governance` (política, enrollment, enforcement);
- una pantalla de activación obligatoria: **Portal URL + Organization +
  Organization token**;
- `bundle.json` neutro (`org_code: ""`, sin endpoint ni credenciales).

## 2. Flujo de activación

1. El admin del cliente crea el token one-time desde su Admin Console
   (`Devices → + Enroll device`).
2. El empleado abre el MSIX e introduce la URL pública del control plane, el
   slug de la organización y el token.
3. El desktop valida el tenant contra
   `GET /download/{slug}/desktop-config` (datos públicos: branding, modelo
   por defecto, gateway, OIDC) **antes** de consumir el token.
4. `POST /api/devices/enroll/exchange` canjea el token y devuelve
   `device_token` + `model_key`.
5. El desktop escribe `~/.trust-agent/{config.yaml,SOUL.md,.env,skins/*}` con
   el producto, color, modelo y gateway del tenant. A partir de ahí el agente
   corre gobernado por la organización.

## 3. Construcción y publicación

```bash
# 1) Bundle neutro (sin tenant)
python3 scripts/bake_generic_desktop_bundle.py

# 2) Build Windows: misma receta del desktop trust, con
#    HERMES_TRUST_DISPLAY_NAME="Trust Agent"
#    (en el host Windows/VM de build; ver docs/desktop-installers.md)

# 3) Publicar el MSIX para todos los tenants
python3 scripts/publish_universal_installer.py \
  --msix release/TrustAgentDesktop-0.0.0-win-x64.msix \
  --version 0.6.0
```

El script deja:

```
installer-artifacts/_universal/0.6.0/
  TrustAgentDesktop-0.0.0-win-x64.msix
  installer-manifest.json   # filename + sha256 + size reales
```

`installers_service.available_installers(slug)` devuelve el build por tenant
si existe; si no, cae al `_universal`. Por eso **cualquier tenant activo**
obtiene el botón Windows en `GET /download/{slug}` sin rebuild.

## 4. Verificación mínima

```bash
# Cualquier slug activo
curl -s http://127.0.0.1:8000/download/{slug}/desktop-config
curl -s -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:8000/download/{slug}/installer/windows

# El manifest debe apuntar a un archivo existente y el SHA coincidir
sha256sum installer-artifacts/_universal/0.6.0/TrustAgentDesktop-0.0.0-win-x64.msix
```

## 5. Builds por tenant

Siguen soportados para clientes que necesiten un binario con branding
horneado: `scripts/build_desktop_installers.py --slug <slug>` escribe en
`installer-artifacts/<slug>/<version>/` y tiene precedencia sobre el
universal. El universal es el camino recomendado para self-service.

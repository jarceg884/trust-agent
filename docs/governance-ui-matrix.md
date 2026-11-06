# Matriz de gobernanza de UI — Trust Agent Desktop

Este documento es la referencia única de **qué puede ver y qué no un usuario
gobernado** en el desktop. Cada superficie tiene una clave de política
(`policy_bundles.desktop`, transportada en el bundle firmado como
`ui_features`). El build gobernado aplica **deny-by-default** para superficies
sensibles: si la clave no existe en el bundle (políticas antiguas), la
superficie queda bloqueada, nunca expuesta.

## Settings (engranaje / paleta de comandos)

| Sección / ruta | Clave(s) de política | Defecto gobernado | Qué controla |
|---|---|---|---|
| Model · Main model | `model_main` | bloqueado | Selector y edición del modelo principal |
| Model · Fallback models | `model_fallback` | bloqueado | Modelos de respaldo |
| Model · Mixture of Agents | `model_moa` | bloqueado | Presets MoA |
| Chat | — (no política) | visible | Comportamiento de chat, adjuntos, personalidad |
| Appearance | — | visible | Tema, tipografía, ventana |
| Workspace | `workspace` | allow (true) | Proyectos, shell y archivos |
| Safety | `terminal` | bloqueado cuando terminal=false | Aprobaciones, allowlist, red privada |
| Browser | `browser` | bloqueado | Navegador integrado |
| Passwords & Logins | `vault` | bloqueado | Vault y autofill |
| Memory & Context | `memory` | allow (true) | Memoria del agente |
| Voice | `voice` | bloqueado | Modo voz, TTS/STT |
| Advanced | `terminal` | bloqueado | Terminal, toolsets, límites |
| Notifications | — | visible | Alertas y sonidos |
| Billing | `billing` | bloqueado | Planes, suscripciones, facturación |
| Providers | `providers` | bloqueado | Cuentas, API keys, **Custom Endpoints**, Local Models |
| Gateways | `gateways` | bloqueado | Conexiones de gateway |
| Web access | `web_access` | bloqueado | Servir webapp desde el equipo |
| Keyboard Shortcuts | — | visible | Atajos |
| Tools & Keys | `keys` (+ `providers`) | bloqueado | API keys de herramientas y variables de servidor |
| Sessions | `sessions` | allow (true) | Borrado permanente de chats |
| Governance | — | siempre visible (solo lectura) | Estado del bundle, modelos aprobados, budget |
| About | — | siempre visible | Versión y diagnóstico |

Además, en un build gobernado el pie de Settings **no muestra**
Importar / Exportar / Reset to defaults: resetear la configuración borraría el
gateway del modelo y las restricciones administradas.

## Navegación principal

| Superficie | Clave | Defecto gobernado |
|---|---|---|
| Capabilities (Skills, Plugins, Connectors, Tools) | `capabilities` | bloqueado |
| Messaging | `messaging` | bloqueado |
| Artifacts | `artifacts` | bloqueado |
| Scheduled jobs (cron) | `cron` | bloqueado |
| Agents | `agents` | bloqueado |
| Profiles | `profiles` | bloqueado |
| Starmap | `starmap` | bloqueado |
| Webhooks | `webhooks` | bloqueado |

El sidebar, la paleta ⌘K y el router aplican el mismo mapa: una página oculta
no se lista **y** una URL guardada redirige al chat/governance en vez de
renderizar la página.

## Composer / chat

| Control | Clave | Comportamiento gobernado |
|---|---|---|
| Píldora de modelo en el compositor | `chat_model_switch` | Texto de solo lectura; sin menú, sin atajo |
| Model picker (diálogo completo) | `chat_model_switch` / `model_main` | Nunca se abre |
| Visibilidad de modelos por sesión | `model_main` / `chat_model_switch` | Nunca se abre |
| Onboarding de proveedor/modelo | (build gobernado) | Se omite por completo: la organización aprovisiona el modelo |

## Aplicación de la política

1. **Bundle firmado**: `GET /api/me/governance-bundle` incluye `ui_features`
   desde `policy_bundles.desktop`. El plugin verifica firma/aud/exp antes de
   escribir `governance_status.json`.
2. **Renderer**: `useGovernanceUiFeatures()` lee ese estado vía IPC y aplica el
   mapa en Settings, sidebar, paleta, rutas y composer.
3. **Rutas**: `GovernedPage` bloquea páginas completas; Settings redirige
   cualquier `?tab=` bloqueado.
4. **Composer**: sin `chat_model_switch` no hay picker ni hot-swap.
5. **CLI/agente**: el plugin recorta `allowed_tools` según `ui_features`
   (`cli`, `cli_terminal`, `cli_files_*`, `cli_web`, `cli_code`), por lo que la
   gobernanza no depende solo de la UI.
6. **Proveedores**: `allowed_providers` del bundle solo admite
   `litellm_gateway`; el plugin inyecta aviso de política y el tráfico de
   modelos pasa por LiteLLM (gate duro de cuota/credenciales).

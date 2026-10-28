# Evidencia t_90e899e0 — inputs pierden foco por keystroke (repro Miller)

## Causa raíz
`Dialog` en `platform/console/src/components/ui.tsx` (y copia en
provider-console) tenía el efecto de focus inicial con deps inestables:

```ts
useEffect(() => { ... setTimeout(() => ref.current?.focus(), 0) ... }, [open, onClose]);
```

Todas las páginas pasan `onClose` como arrow inline (`onClose={() => setAddOpen(false)}`)
y guardan el estado del formulario del dialog en el componente PÁGINA. Cada
keystroke → setState en la página → re-render → nueva identidad de `onClose` →
el efecto se limpia y re-ejecuta → `setTimeout(...focus())` le roba el foco al
input para dárselo al panel del diálogo. Síntoma exacto de Miller: "escribo
una letra y me saca del box".

## Fix de raíz (componente, no por página)
El callback vive en un ref (siempre fresco al leerse en el evento) y el efecto
depende SOLO de `open`: focus inicial + scroll-lock ocurren una única vez al
abrir. Mismo patrón para `onConfirm` en `ConfirmDialog`. Aplicado en las DOS
apps (console + provider-console).

## Repro real (nada simulado)
- Build PRE-FIX del console (HEAD `ui.tsx` con el hunk del Dialog revertido,
  servido en :3199 desde el workspace de la tarea): `e2e-RED-prefix.log` —
  **11 FAIL**, todos `foco perdido tras 'X' (activeElement=DIV[role=dialog])`:
  departments (Add/Rename), budgets (Edit budget/Edit), users (Invite user),
  credentials (Add secret ×3), organization (New policy).
- Deployment POST-FIX (:3100 console, :3200 provider-console):
  `e2e-GREEN-postfix.log` — **67/67 PASS, 0 FAIL**.

## Barrido E2E (platform/console/e2e/focus-sweep.mjs)
Escribe frases completas ("Focus ok 2026!", adaptada por tipo de input)
carácter a carácter afirmando foco + valor tras CADA keystroke:
- Console: inputs de página en 14 rutas + 9 diálogos (allowlist segura,
  nunca submit) + input inline "+ Add item" de policies.
- Auth panel: /login /register /signup.
- Provider console: /login, /tenants/new, dialog "Register provider",
  "Delete tenant…" (confirm tipado con texto FALSO + Escape).

Re-run:
```bash
../../platform/.venv/bin/python ../scripts/e2e_policies_ux.py FocusSweep2026! /tmp/cookies.json
NODE_PATH=/usr/local/lib/hermes-agent/node_modules \
CONSOLE_COOKIES=/tmp/cookies.json PROVIDER_ADMIN_PASSWORD='...' \
node e2e/focus-sweep.mjs
```

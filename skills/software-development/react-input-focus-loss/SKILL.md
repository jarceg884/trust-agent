---
name: react-input-focus-loss
description: Diagnostica y corrige inputs que pierden el foco por keystroke en apps React/Next (síntoma "escribo una letra y me saca del box") — patrones de causa raíz, fix de raíz y E2E de regresión.
category: software-development
---

# Inputs que pierden foco al escribir (React/Next)

Use when un usuario reporta que al escribir un carácter el input pierde el foco
y hay que re-clickear por cada letra. Casi nunca es "mal estado del input":
es un componente que se re-monta o un efecto que roba el foco en cada render.

## Causas raíz (en orden de frecuencia en este proyecto)

1. **Efecto con deps inestables + `focus()`/`setTimeout(focus)`** (la de este
   repo, t_90e899e0): el `Dialog` de `ui.tsx` tenía
   `useEffect(..., [open, onClose])` con `setTimeout(() => panel.focus(), 0)`.
   Las páginas pasan `onClose` como arrow inline y guardan el estado del form
   en la página ⇒ cada keystroke re-renderiza la página ⇒ nueva identidad de
   `onClose` ⇒ el efecto se limpia y re-corre ⇒ el focus() le roba el foco al
   input. Síntoma exacto: UNA letra y el foco salta al panel
   (`activeElement=DIV[role=dialog]`).
   **Fix de raíz**: callback en `useRef` (se refresca en cada render, se lee
   al disparar el evento) + deps SOLO de estado real (`[open]`). NO parchar
   por página ni `e.stopPropagation`.

2. **Componente definido dentro del render** (`function Input() {...}` dentro
   del componente padre): nueva identidad por render ⇒ React desmonta y
   remonta el input en cada keystroke (pierde foco Y el caret).
   **Fix**: sacar el componente a módulo o memoizarlo fuera.

3. **`key` inestable** en el ancestro del input (`key={value}`,
   `key={JSON.stringify(filters)}`): remount por cambio de key.
   **Fix**: key estable (id de entidad, no del contenido editado).

4. **`autoFocus` en un elemento que se re-renderiza condicionalmente**: si un
   flag de estado alterna la visibilidad por keystroke, el re-monte re-dispara
   el autoFocus. `autoFocus` solo dispara en MOUNT — es seguro si el elemento
   no se desmonta.

## Cómo diagnosticar (nada simulado)

- En DevTools/CDP tras cada keystroke: `document.activeElement` — si salta a
  otro elemento (p.ej. el panel del dialog), es robo de foco (causa 1/4);
  si el input mismo se re-crea (nuevo nodo), es remount (causa 2/3).
- Reproducir contra un build PRE-FIX real antes de tocar código: revertir SOLO
  el hunk sospechoso en una copia scratch (`git show HEAD:file > copia`,
  `next build`, `next start --port <libre>`), repro, y guardar el log como
  evidencia RED. OJO: `grep onCloseRef` en bundles minificados NO sirve
  (minification renombra variables locales) — verificar con comportamiento.

## E2E de regresión (patrón focus-sweep)

`platform/console/e2e/focus-sweep.mjs` (t_90e899e0): escribe frases completas
carácter a carácter afirmando `activeElement === input` Y
`value === esperado` tras CADA keystroke. Trampas aprendidas:

- **Prefill**: el click pone el caret a mitad del texto → `Control+A` y
  reemplazar; valor esperado = lo tecleado.
- **type=email** sanitiza espacios y **type=number** rechaza no-numéricos del
  `.value` — NO es un bug de foco: adaptar la frase por tipo
  (email `focus@ok.dev`, number `2026`).
- Inputs de listas filtradas desaparecen al re-render del filtro: re-resolver
  el selector antes de teclear y tratar "no encontrado" como skip, no FAIL.
- El topbar comparte labels de botones con la página ("Invite user"): scope
  `main button` al abrir dialogs.
- En forms de login, la frase del password debe SER la contraseña real para
  poder hacer submit después del barrido.
- Dialogs peligrosos (delete): abrir, teclear texto FALSO, cerrar con Escape —
  nunca submit.

Playwright sin dep local: `NODE_PATH=/usr/local/lib/hermes-agent/node_modules`
+ `executablePath` resuelto del chromium más nuevo en `~/.cache/ms-playwright`
(patrón de `provider-console/e2e/delete-panel.mjs`).

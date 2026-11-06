/**
 * Regla de composición D-41-03 (spec 041) — funciones PURAS sobre el estado
 * del editor. Sin React, sin red: la UI llama a estas funciones y pinta.
 *
 * - Una key lista (allowed_tools) se escribe en la definition IFF ≥1 de sus
 *   ítems está ON, con valor = ítems ON (los extra del valor cargado se
 *   conservan: el admin nunca pierde por togglear algo invisible).
 * - Una key escalar se escribe IFF su ítem está ON, con el valor elegido
 *   (preseleccionado = default_value del contrato).
 * - Al activar el PRIMER ítem de una key, los hermanos toman su default_on
 *   (p. ej. activar "Web search" deja allowed_tools en el default completo;
 *   el admin poda después).
 * - Ítems requires_choice: al activarse quedan "pendientes de elección" y
 *   BLOQUEAN el guardado hasta que el admin elija valor (cualquier cambio
 *   del control cuenta como elección). Los valores cargados de una
 *   definition existente (política o plantilla) ya son elecciones hechas.
 */
import type { BuilderBlock, BuilderItem } from "./types";

export interface EditorState {
  /** itemId → capacidad activada. */
  on: Record<string, boolean>;
  /** key → valor elegido (scalar) o lista vigente (list-item). */
  values: Record<string, unknown>;
  /** itemId requires_choice activado sin elección todavía. */
  pending: Record<string, boolean>;
}

function cloneValue(value: unknown): unknown {
  return typeof value === "object" && value !== null
    ? (JSON.parse(JSON.stringify(value)) as unknown)
    : value;
}

/** Ítems del contrato que componen una key (list-items + scalar). */
export function itemsForKey(builder: BuilderBlock, key: string): BuilderItem[] {
  return builder.items.filter((i) => i.key === key);
}

/** Estado inicial del editor desde una definition existente (o vacío). */
export function initState(
  builder: BuilderBlock,
  definition: Record<string, unknown> | null,
): EditorState {
  const state: EditorState = { on: {}, values: {}, pending: {} };
  if (!definition) return state;
  for (const [key, value] of Object.entries(definition)) {
    const items = itemsForKey(builder, key);
    if (!items.length) continue; // key fuera del contrato: se cae (imposible hoy)
    if (items[0].type === "list-item") {
      const list = Array.isArray(value) ? value.map(String) : [];
      for (const i of items) {
        if (i.item && list.includes(i.item)) state.on[i.id] = true;
      }
      state.values[key] = list;
    } else {
      state.on[items[0].id] = true;
      state.values[key] = cloneValue(value);
    }
  }
  return state;
}

/** Toggle de una capacidad (D-41-03). Devuelve el NUEVO estado. */
export function toggleItem(
  builder: BuilderBlock,
  state: EditorState,
  itemId: string,
  next: boolean,
): EditorState {
  const item = builder.items.find((i) => i.id === itemId);
  if (!item) return state;
  const siblings = itemsForKey(builder, item.key);
  const keyActive = siblings.some((s) => s.id !== itemId && state.on[s.id]);

  if (!next) {
    const on = { ...state.on, [itemId]: false };
    const pending = { ...state.pending, [itemId]: false };
    const values = { ...state.values };
    if (!keyActive) delete values[item.key];
    else if (item.type === "list-item" && item.item) {
      values[item.key] = (Array.isArray(values[item.key])
        ? (values[item.key] as string[]).filter((v) => v !== item.item)
        : []) as unknown;
    }
    return { on, values, pending };
  }

  const on: Record<string, boolean> = { ...state.on };
  const pending: Record<string, boolean> = { ...state.pending };
  const values: Record<string, unknown> = { ...state.values };

  if (!keyActive) {
    // PRIMER ítem de la key: los hermanos toman su default_on (D-41-03).
    for (const sib of siblings) {
      if (sib.type === "list-item") {
        on[sib.id] = sib.id === itemId ? true : Boolean(sib.default_on);
      }
    }
    if (item.type === "scalar") {
      on[itemId] = true;
      values[item.key] = cloneValue(item.default_value);
      pending[itemId] = Boolean(item.requires_choice);
    } else {
      const list = siblings
        .filter((s) => s.type === "list-item" && on[s.id] && s.item)
        .map((s) => s.item as string);
      values[item.key] = list;
    }
  } else {
    on[itemId] = true;
    if (item.type === "list-item" && item.item) {
      const prev = Array.isArray(values[item.key])
        ? (values[item.key] as string[])
        : [];
      values[item.key] = prev.includes(item.item)
        ? prev
        : [...prev, item.item];
    }
  }
  return { on, values, pending };
}

/** Elección de valor de una key (cambio del control de spec 036): cuenta
 *  como elección y limpia el bloqueo requires_choice de su ítem. */
export function setValue(
  builder: BuilderBlock,
  state: EditorState,
  key: string,
  value: unknown,
): EditorState {
  const values = { ...state.values, [key]: value };
  const pending = { ...state.pending };
  for (const i of itemsForKey(builder, key)) pending[i.id] = false;
  return { ...state, values, pending };
}

/** La definition compuesta (mapa key → valor) — lo que se envuelve al
 *  guardar. Keys con 0 ítems ON quedan FUERA (D-41-03). */
export function composeDefinition(
  builder: BuilderBlock,
  state: EditorState,
): Record<string, unknown> {
  const definition: Record<string, unknown> = {};
  const seen = new Set<string>();
  for (const item of builder.items) {
    if (seen.has(item.key)) continue;
    seen.add(item.key);
    const siblings = itemsForKey(builder, item.key);
    if (!siblings.some((s) => state.on[s.id])) continue;
    if (siblings[0].type === "list-item") {
      const list = Array.isArray(state.values[item.key])
        ? (state.values[item.key] as string[])
        : [];
      definition[item.key] = list;
    } else if (state.values[item.key] !== undefined) {
      definition[item.key] = state.values[item.key];
    } else {
      definition[item.key] = cloneValue(siblings[0].default_value);
    }
  }
  return definition;
}

/** Nº de capacidades activas (ítems ON). */
export function activeCapabilities(state: EditorState): number {
  return Object.values(state.on).filter(Boolean).length;
}

/** Ítems requires_choice activados y sin elección — bloquean el guardado. */
export function pendingChoices(
  builder: BuilderBlock,
  state: EditorState,
): BuilderItem[] {
  return builder.items.filter((i) => state.on[i.id] && state.pending[i.id]);
}

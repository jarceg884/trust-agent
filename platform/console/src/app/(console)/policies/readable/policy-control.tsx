"use client";

/**
 * Controles legibles del panel de políticas (spec 036, REQ-036-002/003/005).
 *
 * Cada kind del contrato se renderiza como un control en palabras — NUNCA
 * como caja JSON (D-36-08): los multiselects son sub-toggles por ítem con
 * estado ✓/✗ (el ejemplo de Miller: "Búsqueda en internet" dentro de
 * allowed_tools), los enums son selects con labels en palabras, los números
 * llevan su unidad. La escritura la hace la tarjeta (PUT/DELETE existentes
 * del nivel elegido); este componente solo propone el nuevo valor.
 */
import { useState } from "react";
import type { DynamicItem, PolicyControl } from "@/lib/types";
import { Button, Input, Select, Textarea } from "@/components/ui";
import { itemLabel, t, toggleLabel, unitLabel } from "./presentation";

/** Switch accesible del design system (role=switch, foco por teclado). */
export function Switch({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 ${
        checked
          ? "border-accent/60 bg-accent/80"
          : "border-border-subtle bg-surface-alt"
      }`}
    >
      <span
        aria-hidden="true"
        className={`ml-0.5 h-3.5 w-3.5 rounded-full bg-surface shadow-sm transition-transform ${
          checked ? "translate-x-4" : "translate-x-0"
        }`}
      />
    </button>
  );
}

/** Fila de sub-toggle por ítem (D-36-08): etiqueta legible + ✓/✗. */
function ItemToggle({
  label,
  checked,
  disabled,
  onToggle,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onToggle: (next: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="flex min-w-0 items-center gap-2 text-sm text-foreground">
        <span
          aria-hidden="true"
          className={`w-4 text-center text-xs font-semibold ${
            checked ? "text-success" : "text-faint"
          }`}
        >
          {checked ? "✓" : "✗"}
        </span>
        <span className="truncate" title={label}>
          {label}
        </span>
      </span>
      <Switch checked={checked} disabled={disabled} onChange={onToggle} label={label} />
    </div>
  );
}

export interface PolicyControlProps {
  control: PolicyControl;
  /** Valor vigente que muestra el control (el EFECTIVO del destinatario). */
  value: unknown;
  disabled?: boolean;
  /** Ítems dinámicos del item_source (models/connectors/connections). */
  dynamicItems?: DynamicItem[];
  /** Base para aria-labels accesibles (nombre legible de la policy). */
  name: string;
  /** Propone el nuevo valor completo de la policy (la tarjeta hace el PUT). */
  onChange: (next: unknown) => void;
}

export function ReadablePolicyControl({
  control,
  value,
  disabled,
  dynamicItems,
  name,
  onChange,
}: PolicyControlProps) {
  switch (control.kind) {
    case "toggle-allow":
    case "toggle-require":
      return (
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-muted">
            {toggleLabel(control, Boolean(value))}
          </span>
          <Switch
            checked={Boolean(value)}
            disabled={disabled}
            label={name}
            onChange={(next) => onChange(next)}
          />
        </div>
      );

    case "select":
      return (
        <Select
          aria-label={name}
          value={String(value ?? "")}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          className="w-full max-w-sm text-sm"
        >
          {(control.options ?? []).map((o) => (
            <option key={o.value} value={o.value}>
              {t(o.label)}
            </option>
          ))}
        </Select>
      );

    case "number":
      return <NumberControl control={control} value={value} disabled={disabled} name={name} onChange={onChange} />;

    case "multiselect-allow":
    case "multiselect-deny":
      return (
        <MultiselectControl
          control={control}
          value={value}
          disabled={disabled}
          dynamicItems={dynamicItems}
          name={name}
          onChange={onChange}
        />
      );

    case "domains":
      return <DomainsControl control={control} value={value} disabled={disabled} name={name} onChange={onChange} />;

    case "messaging":
      return <MessagingControl control={control} value={value} disabled={disabled} dynamicItems={dynamicItems} name={name} onChange={onChange} />;

    case "raw":
      return <RawControl value={value} disabled={disabled} name={name} onChange={onChange} />;

    case "locked":
    default:
      return null;
  }
}

// --- number ----------------------------------------------------------------------

function NumberControl({
  control,
  value,
  disabled,
  name,
  onChange,
}: Omit<PolicyControlProps, "dynamicItems"> & { control: PolicyControl }) {
  const [text, setText] = useState(String(value ?? control.min ?? 0));
  const [error, setError] = useState<string | null>(null);
  const current = typeof value === "number" ? value : Number(value);

  function apply() {
    const n = Number(text);
    if (!Number.isInteger(n) || n < (control.min ?? 0)) {
      setError(`Must be a whole number of ${control.min ?? 0} or more.`);
      return;
    }
    setError(null);
    if (n !== current) onChange(n);
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input
        aria-label={name}
        type="number"
        min={control.min ?? 0}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && apply()}
        className="w-24 text-sm"
      />
      <span className="text-xs text-muted">{unitLabel(control.unit, Number(text))}</span>
      <Button variant="secondary" size="sm" disabled={disabled} onClick={apply}>
        Apply
      </Button>
      {error ? (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

// --- multiselect (sub-toggles por ítem, D-36-08) ----------------------------------

function MultiselectControl({
  control,
  value,
  disabled,
  dynamicItems,
  name,
  onChange,
}: PolicyControlProps & { control: PolicyControl }) {
  const items = Array.isArray(value) ? value.map(String) : [];
  const [custom, setCustom] = useState("");
  const [adding, setAdding] = useState(false);

  const known = control.known_items ?? [];
  const dynamic = dynamicItems ?? [];
  const knownValues = new Set(known.map((i) => i.value));
  const dynamicValues = new Set(dynamic.map((i) => i.value));

  /** Ítems extra: presentes en el valor pero sin etiqueta conocida — se
   *  muestran tal cual (spec 036 §5: es un nombre, no un key técnico). */
  const extras = items.filter(
    (v) => !knownValues.has(v) && !dynamicValues.has(v) && v !== control.wildcard?.value,
  );

  function toggleItem(item: string, next: boolean) {
    const wildcard = control.wildcard;
    if (wildcard && item === wildcard.value) {
      // El comodín reemplaza la lista (["*"] = todo); quitarlo deja el resto.
      onChange(next ? [wildcard.value] : items.filter((v) => v !== wildcard.value));
      return;
    }
    const set = new Set(items);
    if (next) set.add(item);
    else set.delete(item);
    onChange([...set].sort());
  }

  function addCustom() {
    const v = custom.trim();
    if (!v) return;
    if (!items.includes(v)) onChange([...items, v].sort());
    setCustom("");
    setAdding(false);
  }

  const rows: { value: string; label: string }[] = [];
  if (control.wildcard) {
    rows.push({ value: control.wildcard.value, label: t(control.wildcard.label) });
  }
  for (const k of known) rows.push({ value: k.value, label: itemLabel(k.value, known, dynamic) });
  for (const d of dynamic) {
    if (!rows.some((r) => r.value === d.value)) rows.push({ value: d.value, label: d.label });
  }
  for (const e of extras) rows.push({ value: e, label: e });

  return (
    <div className="space-y-0.5">
      {rows.map((row) => (
        <ItemToggle
          key={row.value}
          label={row.label}
          checked={items.includes(row.value)}
          disabled={disabled}
          onToggle={(next) => toggleItem(row.value, next)}
        />
      ))}
      {control.allow_custom_items ? (
        adding ? (
          <div className="flex items-center gap-2 pt-1">
            <Input
              aria-label={`Add item — ${name}`}
              value={custom}
              autoFocus
              placeholder={t(control.items_hint) || "item"}
              disabled={disabled}
              onChange={(e) => setCustom(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") addCustom();
                if (e.key === "Escape") setAdding(false);
              }}
              className="h-8 py-1 text-sm"
            />
            <Button variant="secondary" size="sm" disabled={disabled || !custom.trim()} onClick={addCustom}>
              Add
            </Button>
          </div>
        ) : (
          <button
            type="button"
            disabled={disabled}
            onClick={() => setAdding(true)}
            className="mt-1 text-xs font-medium text-accent hover:underline disabled:cursor-not-allowed disabled:opacity-50"
          >
            + Add {t(control.items_hint) ? `(${t(control.items_hint)})` : "item"}
          </button>
        )
      ) : null}
    </div>
  );
}

// --- domains (web_domain_policy) ----------------------------------------------------

function DomainsControl({
  control,
  value,
  disabled,
  name,
  onChange,
}: Omit<PolicyControlProps, "dynamicItems"> & { control: PolicyControl }) {
  const spec = (value ?? {}) as { mode?: string; domains?: string[] };
  const [mode, setMode] = useState(spec.mode ?? "open");
  const [text, setText] = useState((spec.domains ?? []).join("\n"));
  const [error, setError] = useState<string | null>(null);

  function apply() {
    const domains = text
      .split(/[\n,]/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (mode !== "open" && !domains.length) {
      setError("Add at least one domain, or switch the mode back to open.");
      return;
    }
    setError(null);
    onChange({ mode, domains });
  }

  return (
    <div className="space-y-2">
      <Select
        aria-label={`${name} — mode`}
        value={mode}
        disabled={disabled}
        onChange={(e) => setMode(e.target.value)}
        className="w-full max-w-sm text-sm"
      >
        {(control.mode_options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {t(o.label)}
          </option>
        ))}
      </Select>
      {mode !== "open" ? (
        <Textarea
          aria-label={`${name} — ${t(control.domains_label) || "domains"}`}
          rows={2}
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          placeholder={t(control.domains_hint)}
        />
      ) : null}
      <div className="flex items-center gap-2">
        <Button variant="secondary" size="sm" disabled={disabled} onClick={apply}>
          Apply
        </Button>
        {error ? (
          <p role="alert" className="text-xs text-danger">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}

// --- messaging (messaging_permissions) ----------------------------------------------

function MessagingControl({
  control,
  value,
  disabled,
  dynamicItems,
  name,
  onChange,
}: PolicyControlProps & { control: PolicyControl }) {
  const spec = (value ?? {}) as Record<
    string,
    { allowed?: boolean; mode?: string } | undefined
  >;
  const known = control.known_items ?? [];
  const dynamic = dynamicItems ?? [];
  const platforms = new Set<string>([
    ...Object.keys(spec),
    ...dynamic.map((d) => d.value),
  ]);

  function update(platform: string, patch: { allowed?: boolean; mode?: string }) {
    const current = spec[platform] ?? { allowed: false };
    const next = { ...current, ...patch };
    onChange({ ...spec, [platform]: next });
  }

  return (
    <div className="space-y-1">
      {[...platforms].sort().map((platform) => {
        const p = spec[platform];
        const label = itemLabel(platform, known, dynamic);
        return (
          <div
            key={platform}
            className="flex flex-wrap items-center justify-between gap-2 py-1"
          >
            <span className="flex min-w-0 items-center gap-2 text-sm text-foreground">
              <span
                aria-hidden="true"
                className={`w-4 text-center text-xs font-semibold ${
                  p?.allowed ? "text-success" : "text-faint"
                }`}
              >
                {p?.allowed ? "✓" : "✗"}
              </span>
              <span className="truncate" title={label}>
                {label}
              </span>
            </span>
            <div className="flex items-center gap-2">
              {p?.allowed ? (
                <Select
                  aria-label={`${name} — ${label} — mode`}
                  value={p.mode ?? "read_only"}
                  disabled={disabled}
                  onChange={(e) => update(platform, { mode: e.target.value })}
                  className="h-8 w-40 py-1 text-xs"
                >
                  {(control.mode_options ?? []).map((o) => (
                    <option key={o.value} value={o.value}>
                      {t(o.label)}
                    </option>
                  ))}
                </Select>
              ) : null}
              <Switch
                checked={Boolean(p?.allowed)}
                disabled={disabled}
                label={`${name} — ${label}`}
                onChange={(next) =>
                  update(platform, next ? { allowed: true, mode: "read_only" } : { allowed: false })
                }
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// --- raw (fallback D-36-05) -----------------------------------------------------------

function RawControl({
  value,
  disabled,
  name,
  onChange,
}: {
  value: unknown;
  disabled?: boolean;
  name: string;
  onChange: (next: unknown) => void;
}) {
  const [text, setText] = useState(JSON.stringify(value ?? null, null, 0));
  const [error, setError] = useState<string | null>(null);

  function apply() {
    try {
      onChange(JSON.parse(text));
      setError(null);
    } catch {
      setError("The value must be valid JSON.");
    }
  }

  return (
    <div className="space-y-2">
      <Textarea
        aria-label={name}
        rows={2}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="flex items-center gap-2">
        <Button variant="secondary" size="sm" disabled={disabled} onClick={apply}>
          Apply
        </Button>
        {error ? (
          <p role="alert" className="text-xs text-danger">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}

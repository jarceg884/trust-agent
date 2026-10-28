"use client";

/**
 * Tarjeta legible de una policy (spec 036, REQ-036-002/003/004/005).
 *
 * Muestra: icono + nombre humano + descripción (del bloque `presentation`,
 * JAMÁS hardcodeados), la FRASE derivada del valor efectivo del backend
 * (D-36-04), el badge de origen legible y el control en palabras. El 422
 * del control plane (no-relajación) se muestra INLINE con el mensaje real
 * + explicación en palabras, y el control vuelve al valor vigente
 * (REQ-036-004). Las invariables renderizan candado, sin control (D-36-09).
 */
import { Badge, Button } from "@/components/ui";
import { Lock } from "lucide-react";
import type {
  DynamicItem,
  PolicyCatalogEntry,
  PolicyCategory,
} from "@/lib/types";
import { ReadablePolicyControl } from "./policy-control";
import { PolicyIcon } from "./policy-icons";
import {
  buildStatement,
  itemLabel,
  originLabel,
  t,
  type PolicySource,
  type ResolvedPresentation,
} from "./presentation";

export type ScopeLevel = "org" | "dept" | "user";

export interface PolicyCardProps {
  entry: PolicyCatalogEntry;
  resolved: ResolvedPresentation;
  categories: PolicyCategory[];
  /** Valor EFECTIVO del destinatario seleccionado (backend, D-36-04). */
  effective: unknown;
  source: PolicySource;
  /** Nivel que se está VIENDO (para el badge de origen legible). */
  viewLevel: ScopeLevel;
  /** Nivel al que escribe el control (org_only → siempre "org"). */
  writeLevel: ScopeLevel;
  /** Permiso del principal para escribir en writeLevel. */
  canEdit: boolean;
  /** ¿Existe override explícito en el nivel del destinatario? (para Reset) */
  hasOverride: boolean;
  saving: boolean;
  /** Mensaje real del 422 del control plane (inline, REQ-036-004). */
  error: string | null;
  dynamicItems?: DynamicItem[];
  /** Identidad del destinatario visto (nivel:depto:usuario). */
  scopeKey: string;
  /** Bump por escritura terminada → el control vuelve al valor vigente. */
  revision: number;
  onWrite: (value: unknown) => void;
  onReset: () => void;
}

/** Explicación en palabras del principio de restrictividad (REQ-036-004). */
const NO_RELAXATION_EXPLANATION =
  "A lower level can only tighten (restrict) what a higher level already set — it cannot relax it. The control went back to the current value.";

export function PolicyCard({
  entry,
  resolved,
  categories,
  effective,
  source,
  viewLevel,
  writeLevel,
  canEdit,
  hasOverride,
  saving,
  error,
  dynamicItems,
  scopeKey,
  revision,
  onWrite,
  onReset,
}: PolicyCardProps) {
  const { pres, untranslated } = resolved;
  const invariable = entry.invariable || pres.control.kind === "locked";
  const orgOnlyHere = Boolean(entry.org_only) && writeLevel !== "org";

  const labelFor = (v: string) =>
    itemLabel(v, pres.control.known_items, dynamicItems);

  const statement = buildStatement(pres, effective, labelFor);

  return (
    <div
      className={`flex flex-col gap-2 rounded-xl border bg-surface p-4 transition-shadow hover:shadow-sm ${
        error ? "border-danger/40" : "border-border-subtle"
      }`}
    >
      {/* Cabecera: icono + nombre + badges */}
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-alt text-accent">
          {invariable ? (
            <Lock size={16} strokeWidth={1.75} aria-hidden="true" />
          ) : (
            <PolicyIcon
              name={pres.icon}
              category={pres.category}
              categories={categories}
              size={16}
            />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold leading-5 text-foreground">
            {t(pres.name)}
          </h3>
          <p className="mt-0.5 text-xs leading-4 text-muted">
            {t(pres.description)}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-1">
          {untranslated ? <Badge tone="warning">Untranslated</Badge> : null}
          {invariable ? <Badge tone="accent">Provider protection</Badge> : null}
          {entry.org_only ? <Badge>Organization-wide</Badge> : null}
        </div>
      </div>

      {/* Frase efectiva + badge de origen (REQ-036-005) */}
      <p className="text-sm font-medium text-foreground">
        <span className="sr-only">Current state: </span>
        {statement}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={source === "user" || source === "dept" ? "warning" : "neutral"}>
          {originLabel(source, viewLevel)}
        </Badge>
        {orgOnlyHere ? (
          <span className="text-[11px] text-muted">
            Defined at organization level — edits apply to everyone.
          </span>
        ) : null}
        {saving ? (
          <span className="text-[11px] text-muted" role="status">
            Saving…
          </span>
        ) : null}
      </div>

      {/* 422 real del control plane, inline (REQ-036-004) */}
      {error ? (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2"
        >
          <p className="text-xs font-medium text-danger">{error}</p>
          <p className="mt-0.5 text-xs text-danger/80">{NO_RELAXATION_EXPLANATION}</p>
        </div>
      ) : null}

      {/* Control legible — o candado para invariables (D-36-09) */}
      {invariable ? (
        <p className="text-xs text-faint">
          Fixed by the provider — always on, cannot be turned off at any level.
        </p>
      ) : (
        <>
          <ReadablePolicyControl
            /* Remount al cambiar de destinatario o al terminar una escritura
             * (éxito o 422): los controles con borrador local (number/domains/
             * raw) se reinician desde el valor EFECTIVO vigente (REQ-036-004). */
            key={`${scopeKey}#${revision}`}
            control={pres.control}
            value={effective}
            disabled={!canEdit || saving}
            dynamicItems={dynamicItems}
            name={t(pres.name)}
            onChange={onWrite}
          />
          {canEdit && hasOverride ? (
            <div>
              <Button
                variant="ghost"
                size="sm"
                disabled={saving}
                onClick={onReset}
                className="h-7 px-2 text-xs text-muted"
              >
                Reset to inherited
              </Button>
            </div>
          ) : null}
          {!canEdit ? (
            <p className="text-[11px] text-faint">
              Read-only — your role cannot manage policies at this level.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}

"use client";

/**
 * Editor de política nombrada (spec 041, REQ-041-012 / D-41-03).
 *
 * El SIDEBAR de capacidades llega del bloque `builder` (6 grupos, 47 ítems
 * — JAMÁS hardcodeado, D-41-02). Cada capacidad es un toggle; al activar
 * una ESCALAR aparece su control legible de spec 036 (sub-toggles por ítem,
 * select, número, dominios, mensajería). La composición (key escrita iff
 * ≥1 ítem ON, hermanos → default_on, requires_choice bloquea el guardado)
 * vive en ./composition.ts. Guardar = PUT/POST real con el 422 inline;
 * la sección Assignments ofrece unassign y "Apply changes" (reapply)
 * cuando alguna asignación quedó stale (D-41-06).
 */
import { useMemo, useState } from "react";
import type { UseApiState } from "@/hooks/use-api";
import { ApiError, api } from "@/lib/api-client";
import type {
  DirectoryList,
  PolicyCategory,
  PolicyMatrix,
  WhoAmI,
} from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Input,
  useToast,
} from "@/components/ui";
import { ArrowLeft, RefreshCw, AlertTriangle } from "lucide-react";
import { PolicyIcon } from "../readable/policy-icons";
import { ReadablePolicyControl, Switch } from "../readable/policy-control";
import { t } from "../readable/presentation";
import {
  activeCapabilities,
  composeDefinition,
  initState,
  pendingChoices,
  setValue,
  toggleItem,
  type EditorState,
} from "./composition";
import type {
  BuilderBlock,
  BuilderItem,
  NamedPolicy,
  ReapplyResult,
  UnassignResult,
} from "./types";
import {
  dynamicItemsFor,
  keyDisplayName,
  presentationFor,
  readableValue,
  recipientLabel,
  type DynamicSources,
} from "./present";
import { AssignDialog } from "./assign-dialog";

export function PolicyEditor({
  me,
  policy: initial,
  builder,
  matrix,
  directory,
  sources,
  onExit,
  onChanged,
  onMaterialized,
}: {
  me: WhoAmI;
  policy: NamedPolicy;
  builder: BuilderBlock;
  matrix: UseApiState<PolicyMatrix>;
  directory: UseApiState<DirectoryList>;
  sources: DynamicSources;
  onExit: () => void;
  /** Refresca la lista de políticas del builder. */
  onChanged: () => void;
  /** Refresca lista + matrix (una asignación cambió lo materializado). */
  onMaterialized: () => void;
}) {
  const { push } = useToast();
  const [current, setCurrent] = useState<NamedPolicy>(initial);
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description ?? "");
  const [state, setState] = useState<EditorState>(() =>
    initState(builder, initial.definition),
  );
  const [saving, setSaving] = useState(false);
  const [saveErrors, setSaveErrors] = useState<string[]>([]);
  const [assignOpen, setAssignOpen] = useState(false);
  const [unassigningId, setUnassigningId] = useState<string | null>(null);
  const [reapplying, setReapplying] = useState(false);
  const [reapplyResult, setReapplyResult] = useState<ReapplyResult | null>(null);

  const isDraft = !current.id;
  const definition = useMemo(
    () => composeDefinition(builder, state),
    [builder, state],
  );
  const active = activeCapabilities(state);
  const pending = pendingChoices(builder, state);
  const anyStale = current.assignments.some((a) => a.stale);

  function toggle(itemId: string, next: boolean) {
    setState((prev) => toggleItem(builder, prev, itemId, next));
  }

  function chooseValue(key: string, value: unknown) {
    setState((prev) => setValue(builder, prev, key, value));
  }

  /** Validaciones de guardado en palabras (AC-041-012: error inline, no PUT). */
  function validate(): string[] {
    const errors: string[] = [];
    if (!name.trim()) errors.push("Policy name is required.");
    if (Object.keys(definition).length === 0)
      errors.push("Enable at least one capability before saving.");
    if (pending.length)
      errors.push(
        `Choose a value for: ${pending.map((p) => t(p.name)).join(", ")}.`,
      );
    return errors;
  }

  async function save() {
    const errors = validate();
    setSaveErrors(errors);
    if (errors.length) return;
    setSaving(true);
    try {
      const body = {
        name: name.trim(),
        description: description.trim() || null,
        definition,
      };
      const saved = isDraft
        ? await api<NamedPolicy>("/api/named-policies", { method: "POST", body })
        : await api<NamedPolicy>(`/api/named-policies/${current.id}`, {
            method: "PUT",
            body,
          });
      setCurrent(saved);
      push(
        "success",
        isDraft
          ? `Policy “${saved.name}” created.`
          : `Policy “${saved.name}” saved.` +
              (saved.assignments.some((a) => a.stale)
                ? " Existing assignments are now outdated — use “Apply changes” to re-apply them."
                : ""),
      );
      onChanged();
    } catch (cause) {
      // 422 real del control plane (nombre duplicado, key inválida…), inline.
      setSaveErrors([
        cause instanceof ApiError
          ? cause.message
          : "Save failed. Try again.",
      ]);
    } finally {
      setSaving(false);
    }
  }

  async function unassign(assignmentId: string) {
    setUnassigningId(assignmentId);
    try {
      const res = await api<UnassignResult>(
        `/api/named-policies/assignments/${encodeURIComponent(assignmentId)}`,
        { method: "DELETE" },
      );
      const label = recipientLabel(
        current.assignments.find((a) => a.assignment_id === assignmentId)!,
        matrix.data,
        directory.data,
      );
      push(
        "success",
        `Unassigned from ${label} — ${res.deactivated.length} ${
          res.deactivated.length === 1 ? "key" : "keys"
        } removed` +
          (res.kept_modified.length
            ? `; ${res.kept_modified.length} kept (modified after the assignment).`
            : "."),
      );
      setCurrent((prev) => ({
        ...prev,
        assignments: prev.assignments.filter(
          (a) => a.assignment_id !== assignmentId,
        ),
        assignments_count: Math.max(0, prev.assignments_count - 1),
        stale_assignments: prev.assignments
          .filter((a) => a.assignment_id !== assignmentId)
          .some((a) => a.stale),
      }));
      onMaterialized();
    } catch (cause) {
      push(
        "error",
        cause instanceof ApiError ? cause.message : "Unassign failed. Try again.",
      );
    } finally {
      setUnassigningId(null);
    }
  }

  async function reapply() {
    setReapplying(true);
    setReapplyResult(null);
    try {
      const res = await api<ReapplyResult>(
        `/api/named-policies/${current.id}/reapply`,
        { method: "POST" },
      );
      setReapplyResult(res);
      const failed = res.results.filter((r) => r.status === "failed").length;
      push(
        failed ? "error" : "success",
        failed
          ? `Re-applied to ${res.results.length - failed} of ${res.results.length} recipients — ${failed} failed (see details).`
          : `Re-applied to all ${res.results.length} recipients.`,
      );
      // Las exitosas dejan de ser stale (snapshot refrescado, D-41-06).
      setCurrent((prev) => ({
        ...prev,
        assignments: prev.assignments.map((a) => {
          const r = res.results.find((x) => x.assignment_id === a.assignment_id);
          return r && r.status === "applied" ? { ...a, stale: false } : a;
        }),
        stale_assignments: prev.assignments.some((a) => {
          const r = res.results.find((x) => x.assignment_id === a.assignment_id);
          return r ? r.status !== "applied" && a.stale : a.stale;
        }),
      }));
      onMaterialized();
    } catch (cause) {
      push(
        "error",
        cause instanceof ApiError ? cause.message : "Re-apply failed. Try again.",
      );
    } finally {
      setReapplying(false);
    }
  }

  const groups = builder.groups;
  // Los grupos del builder son compatibles con PolicyCategory para el
  // fallback de iconos (id + icon + name es/en).
  const groupCategories = groups as unknown as PolicyCategory[];

  return (
    <div className="space-y-4">
      {/* Cabecera del editor: volver, nombre, descripción, acciones */}
      <div className="flex flex-wrap items-end gap-3">
        <Button variant="ghost" size="sm" onClick={onExit} className="h-9">
          <ArrowLeft size={14} aria-hidden="true" />
          All policies
        </Button>
        <div className="min-w-56 flex-1">
          <label htmlFor="policy-name" className="sr-only">
            Policy name
          </label>
          <Input
            id="policy-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Policy name"
            className="text-sm font-semibold"
          />
        </div>
        <div className="min-w-56 flex-[2]">
          <label htmlFor="policy-description" className="sr-only">
            Policy description
          </label>
          <Input
            id="policy-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Description (what this policy is for)"
            className="text-sm"
          />
        </div>
        <Button
          variant="secondary"
          size="md"
          disabled={isDraft}
          title={isDraft ? "Save the policy first" : undefined}
          onClick={() => setAssignOpen(true)}
        >
          Assign to…
        </Button>
        <Button variant="primary" size="md" loading={saving} onClick={save}>
          {isDraft ? "Create policy" : "Save policy"}
        </Button>
      </div>

      {/* Errores de guardado inline (AC-041-012: sin PUT, mensaje real). */}
      {saveErrors.length ? (
        <div role="alert" className="rounded-lg border border-danger/30 bg-danger/10 px-3.5 py-2.5">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-danger">
            <AlertTriangle size={13} aria-hidden="true" />
            The policy cannot be saved yet
          </p>
          <ul className="mt-1 space-y-0.5">
            {saveErrors.map((e) => (
              <li key={e} className="text-xs text-danger/90">
                {e}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* Resumen de la composición (D-41-03): capacidades + keys legibles */}
      <Card>
        <CardHeader
          title={
            <>
              {active} {active === 1 ? "capability" : "capabilities"} active
              <Badge className="ml-2">
                {Object.keys(definition).length}{" "}
                {Object.keys(definition).length === 1 ? "key" : "keys"}
              </Badge>
              {pending.length ? (
                <Badge tone="warning" className="ml-1">
                  {pending.length} choice{pending.length === 1 ? "" : "s"} required
                </Badge>
              ) : null}
            </>
          }
          description="This is exactly what gets applied when you assign the policy — every key below is written with the existing validation, audit and deny-overrides guarantees."
        />
        <CardBody>
          {Object.keys(definition).length ? (
            <ul className="flex flex-wrap gap-1.5">
              {Object.entries(definition).map(([key, value]) => (
                <li
                  key={key}
                  className="inline-flex items-baseline gap-1.5 rounded-full border border-border-subtle bg-surface-alt px-2.5 py-1 text-xs"
                >
                  <span className="font-medium text-foreground">
                    {matrix.data ? keyDisplayName(matrix.data, key) : key}
                  </span>
                  <span className="text-muted">
                    {matrix.data
                      ? readableValue(matrix.data, key, value, sources)
                      : JSON.stringify(value)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted">
              Nothing yet — enable capabilities from the sidebar below and they
              will compose this policy.
            </p>
          )}
        </CardBody>
      </Card>

      {/* Sidebar de capacidades (6 grupos del contrato, D-41-03) */}
      <div className="grid gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav
          aria-label="Capability groups"
          className="hidden self-start rounded-xl border border-border-subtle bg-surface p-2 lg:sticky lg:top-4 lg:block"
        >
          {groups.map((g) => {
            const items = builder.items.filter((i) => i.group === g.id);
            const on = items.filter((i) => state.on[i.id]).length;
            return (
              <button
                key={g.id}
                type="button"
                onClick={() =>
                  document
                    .getElementById(`builder-group-${g.id}`)
                    ?.scrollIntoView({ behavior: "smooth", block: "start" })
                }
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs transition-colors hover:bg-surface-alt"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-alt text-accent">
                  <PolicyIcon
                    name={g.icon}
                    category={g.id}
                    categories={groupCategories}
                    size={13}
                  />
                </span>
                <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                  {t(g.name)}
                </span>
                <span
                  className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                    on ? "bg-accent/15 text-accent" : "bg-surface-alt text-faint"
                  }`}
                >
                  {on}/{items.length}
                </span>
              </button>
            );
          })}
        </nav>

        <div className="space-y-5">
          {groups.map((g) => {
            const items = builder.items.filter((i) => i.group === g.id);
            return (
              <section
                key={g.id}
                id={`builder-group-${g.id}`}
                aria-label={t(g.name)}
                className="scroll-mt-4 rounded-xl border border-border-subtle bg-surface"
              >
                <header className="flex items-start gap-3 border-b border-border-subtle px-4 py-3">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-alt text-accent">
                    <PolicyIcon
                      name={g.icon}
                      category={g.id}
                      categories={groupCategories}
                      size={16}
                    />
                  </span>
                  <div className="min-w-0">
                    <h2 className="text-sm font-semibold text-foreground">
                      {t(g.name)}
                    </h2>
                    <p className="text-xs text-muted">{t(g.description)}</p>
                  </div>
                </header>
                <div className="divide-y divide-border-subtle">
                  {items.map((item) => (
                    <CapabilityRow
                      key={item.id}
                      item={item}
                      on={Boolean(state.on[item.id])}
                      pending={Boolean(state.on[item.id] && state.pending[item.id])}
                      value={state.values[item.key]}
                      matrixData={matrix.data}
                      sources={sources}
                      groupCategories={groupCategories}
                      onToggle={(next) => toggle(item.id, next)}
                      onValue={(v) => chooseValue(item.key, v)}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      </div>

      {/* Asignaciones (D-41-06): destinatarios, unassign, Apply changes */}
      <Card>
        <CardHeader
          title="Assignments"
          description="Who this policy is currently applied to. Editing the policy does not auto-propagate — outdated assignments show a badge until you re-apply them."
          actions={
            anyStale ? (
              <Button
                variant="primary"
                size="sm"
                loading={reapplying}
                onClick={reapply}
              >
                <RefreshCw size={13} aria-hidden="true" />
                Apply changes
              </Button>
            ) : null
          }
        />
        <CardBody>
          {current.assignments.length === 0 ? (
            <EmptyState
              title="Not assigned yet"
              description="Use “Assign to…” to apply this policy to the organization, a department or a user — with a preview of exactly what will change."
            />
          ) : (
            <ul className="divide-y divide-border-subtle">
              {current.assignments.map((a) => (
                <li
                  key={a.assignment_id}
                  className="flex flex-wrap items-center gap-2 py-2.5 first:pt-0 last:pb-0"
                >
                  <span className="text-sm font-medium text-foreground">
                    {recipientLabel(a, matrix.data, directory.data)}
                  </span>
                  <Badge>
                    {Object.keys(a.applied_keys).length}{" "}
                    {Object.keys(a.applied_keys).length === 1 ? "key" : "keys"}
                  </Badge>
                  {a.stale ? <Badge tone="warning">Outdated</Badge> : null}
                  <span className="text-xs text-faint">
                    applied {new Date(a.applied_at).toLocaleString()}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto h-7 px-2 text-xs text-muted"
                    loading={unassigningId === a.assignment_id}
                    disabled={unassigningId !== null}
                    onClick={() => unassign(a.assignment_id)}
                  >
                    Unassign
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {reapplyResult ? (
            <div className="mt-3 rounded-lg border border-border-subtle bg-surface-alt px-3.5 py-2.5">
              <p className="text-xs font-semibold text-foreground">
                Re-apply results
              </p>
              <ul className="mt-1 space-y-1">
                {reapplyResult.results.map((r) => (
                  <li key={r.assignment_id} className="text-xs">
                    <span
                      className={
                        r.status === "applied" ? "text-success" : "text-danger"
                      }
                    >
                      {r.status === "applied" ? "✓" : "✗"}
                    </span>{" "}
                    <span className="font-medium text-foreground">
                      {recipientLabel(r, matrix.data, directory.data)}
                    </span>{" "}
                    {r.status === "applied" ? (
                      <span className="text-muted">
                        — {Object.keys(r.applied_keys ?? {}).length} keys re-applied
                        {r.skipped?.length
                          ? `, ${r.skipped.length} skipped (organization-level only)`
                          : ""}
                      </span>
                    ) : (
                      <span className="text-danger/90">
                        — {(r.errors ?? []).join(" ")}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </CardBody>
      </Card>

      {assignOpen ? (
        <AssignDialog
          open={assignOpen}
          onClose={() => setAssignOpen(false)}
          policy={current}
          me={me}
          matrix={matrix}
          directory={directory}
          sources={sources}
          onAssigned={() => {
            onChanged();
            onMaterialized();
          }}
        />
      ) : null}
    </div>
  );
}

/** Fila de capacidad del sidebar: toggle + (si escalar y ON) su control
 *  legible de spec 036. Los list-item SON el control (sus hermanos del
 *  grupo son las demás capacidades de la misma key, D-41-03). */
function CapabilityRow({
  item,
  on,
  pending,
  value,
  matrixData,
  sources,
  groupCategories,
  onToggle,
  onValue,
}: {
  item: BuilderItem;
  on: boolean;
  pending: boolean;
  value: unknown;
  matrixData: PolicyMatrix | null;
  sources: DynamicSources;
  groupCategories: PolicyCategory[];
  onToggle: (next: boolean) => void;
  onValue: (value: unknown) => void;
}) {
  // Presentación legible de spec 036 (contrato o fallback D-36-05).
  const resolved = matrixData ? presentationFor(matrixData, item.key) : null;

  const dynamic = matrixData ? dynamicItemsFor(matrixData, item.key, sources) : undefined;

  return (
    <div className={`px-4 py-3 ${on ? "bg-accent-soft/30" : ""}`}>
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-alt text-accent">
          <PolicyIcon
            name={item.icon}
            category={item.group}
            categories={groupCategories}
            size={14}
          />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-medium leading-5 text-foreground">
            {t(item.name)}
          </h3>
          <p className="mt-0.5 text-xs leading-4 text-muted">
            {t(item.description)}
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {item.org_only ? (
              <Badge>Organization-wide</Badge>
            ) : null}
            {on && item.requires_choice ? (
              <Badge tone={pending ? "warning" : "neutral"}>
                {pending ? "Choice required" : "Configured"}
              </Badge>
            ) : null}
          </div>
        </div>
        <Switch
          checked={on}
          onChange={onToggle}
          label={`${t(item.name)} — ${on ? "included in this policy" : "excluded from this policy"}`}
        />
      </div>

      {/* Control de spec 036 al activar una capacidad escalar (D-41-03). */}
      {on && item.type === "scalar" && resolved ? (
        <div className="mt-2 pl-10">
          <ReadablePolicyControl
            key={`${item.id}:${on ? "on" : "off"}`}
            control={resolved.pres.control}
            value={value}
            dynamicItems={dynamic}
            name={t(item.name)}
            onChange={onValue}
          />
          {item.org_only ? (
            <p className="mt-1.5 text-[11px] text-muted">
              Defined at organization level — skipped when this policy is
              assigned to a department or a user.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

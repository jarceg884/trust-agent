"use client";

/**
 * Organization structure wizard (spec 038, D-38-02/04/06/07).
 *
 * Presented ONCE to each org_admin (departments.manage) while the tenant's
 * structure onboarding is pending; re-openable from the Departments page
 * ("Set up structure") and from the empty-states. Zero new mutation
 * endpoints: every change travels the existing department CRUD
 * (POST/PUT/DELETE /api/departments, spec 012/016 — audits included), and
 * closing the onboarding is a single PUT /api/org/onboarding
 * {source: "wizard"|"skip", created, renamed, deleted}.
 *
 * Template picks pre-fill the LOCALIZED display name (name_es) but the name
 * stays editable — the department name is customer data, not UI copy
 * (D-38-04: "Finanzas" with template `finance`). Deleting a non-empty
 * department surfaces the real 409 with its counts and guidance (D-16-02,
 * no bypass).
 */
import { useEffect, useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type { WhoAmI } from "@/lib/types";
import {
  Badge,
  Button,
  Dialog,
  Field,
  Input,
  useToast,
} from "@/components/ui";
import { IconPlus } from "@/components/icons";

interface ProfileTemplateDto {
  key: string;
  name: string;
  name_es: string;
  description: string;
  description_es: string;
  suggested_monthly_budget_usd: number;
}

interface DepartmentListDto {
  departments: { department_id: string; name: string }[];
}

interface OnboardingStateDto {
  structure_onboarding_pending: boolean;
}

interface DraftRow {
  clientId: string;
  /** existing department id — present => rename/delete instead of create */
  id?: string;
  originalName?: string;
  name: string;
  profileTemplate: string | null;
  /** existing row flagged for deletion on confirm */
  markedDeleted?: boolean;
}

let clientSeq = 0;

export function StructureWizard({
  open,
  onClose,
  onCompleted,
}: {
  open: boolean;
  onClose: () => void;
  onCompleted?: () => void;
}) {
  const { push } = useToast();
  const templates = useApi<{ templates: ProfileTemplateDto[] }>(
    open ? "/api/org/profile-templates" : null,
  );
  const current = useApi<DepartmentListDto>(open ? "/api/departments" : null);

  const [rows, setRows] = useState<DraftRow[] | null>(null);
  const [customName, setCustomName] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  // Seed the draft from the real current structure once per open.
  useEffect(() => {
    if (open && current.data && rows === null) {
      setRows(
        current.data.departments.map((d) => ({
          clientId: `existing-${d.department_id}`,
          id: d.department_id,
          originalName: d.name,
          name: d.name,
          profileTemplate: null,
        })),
      );
    }
    if (!open) {
      setRows(null);
      setCustomName("");
      setErrors([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, current.data]);

  if (!open) return null;

  // The endpoint returns {templates: [...]}; fall back to [] while loading.
  const tplList = templates.data?.templates ?? [];
  const usedTemplateKeys = new Set(
    (rows ?? [])
      .filter((r) => !r.markedDeleted && r.profileTemplate)
      .map((r) => r.profileTemplate),
  );

  function addFromTemplate(t: ProfileTemplateDto) {
    setRows((prev) => [
      ...(prev ?? []),
      {
        clientId: `new-${++clientSeq}`,
        // D-38-04: localized default, EDITABLE — customer data.
        name: t.name_es,
        profileTemplate: t.key,
      },
    ]);
  }

  function addCustom() {
    const name = customName.trim();
    if (!name) return;
    setRows((prev) => [
      ...(prev ?? []),
      { clientId: `new-${++clientSeq}`, name, profileTemplate: null },
    ]);
    setCustomName("");
  }

  function renameRow(clientId: string, name: string) {
    setRows((prev) =>
      (prev ?? []).map((r) => (r.clientId === clientId ? { ...r, name } : r)),
    );
  }

  function removeRow(clientId: string) {
    setRows((prev) =>
      (prev ?? []).flatMap((r) => {
        if (r.clientId !== clientId) return [r];
        // New rows just drop out of the draft; existing ones are deleted
        // on confirm (the API enforces the empty-only rule, D-16-02).
        return r.id ? [{ ...r, markedDeleted: true }] : [];
      }),
    );
  }

  function undoRemoveRow(clientId: string) {
    setRows((prev) =>
      (prev ?? []).map((r) =>
        r.clientId === clientId ? { ...r, markedDeleted: false } : r,
      ),
    );
  }

  function msg(cause: unknown, fallback: string): string {
    return cause instanceof ApiError ? cause.message : fallback;
  }

  async function closeOnboarding(
    source: "wizard" | "skip",
    counts: { created: number; renamed: number; deleted: number },
  ) {
    await api("/api/org/onboarding", {
      method: "PUT",
      body: { source, ...counts },
    });
  }

  async function skip() {
    setBusy(true);
    try {
      await closeOnboarding("skip", { created: 0, renamed: 0, deleted: 0 });
      push("success", "Structure setup skipped — you can restart it anytime from Departments.");
      onCompleted?.();
      onClose();
    } catch (cause) {
      push("error", msg(cause, "Skip failed."));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (rows === null) return;
    setBusy(true);
    setErrors([]);
    const created: string[] = [];
    const renamed: string[] = [];
    const deleted: string[] = [];
    const failed: string[] = [];

    for (const r of rows) {
      const name = r.name.trim();
      if (!r.markedDeleted && !name) {
        failed.push("A department name is empty.");
        continue;
      }
      try {
        if (r.markedDeleted && r.id) {
          await api(`/api/departments/${r.id}`, { method: "DELETE" });
          deleted.push(r.id);
        } else if (!r.id) {
          await api("/api/departments", {
            method: "POST",
            body: {
              name,
              ...(r.profileTemplate ? { profile_template: r.profileTemplate } : {}),
            },
          });
          created.push(name);
        } else if (
          r.originalName !== undefined &&
          name.toLowerCase() !== r.originalName.toLowerCase()
        ) {
          await api(`/api/departments/${r.id}`, {
            method: "PUT",
            body: { name },
          });
          renamed.push(r.id);
        }
      } catch (cause) {
        // Real API error (409 non-empty with counts, 422 duplicate, …).
        failed.push(msg(cause, `Operation on "${name}" failed.`));
      }
    }

    if (failed.length > 0) {
      // Keep the wizard open with the real errors; nothing was closed.
      setErrors(failed);
      setRows((prev) =>
        (prev ?? []).filter((r) => !(r.id && deleted.includes(r.id))),
      );
      setBusy(false);
      return;
    }

    try {
      await closeOnboarding("wizard", {
        created: created.length,
        renamed: renamed.length,
        deleted: deleted.length,
      });
      push(
        "success",
        `Organization structure saved: ${created.length} created, ${renamed.length} renamed, ${deleted.length} deleted.`,
      );
      onCompleted?.();
      onClose();
    } catch (cause) {
      push("error", msg(cause, "Saving failed."));
    } finally {
      setBusy(false);
    }
  }

  const visibleRows = (rows ?? []).filter((r) => !r.markedDeleted);
  const markedDeletedRows = (rows ?? []).filter((r) => r.markedDeleted);

  return (
    <Dialog
      open={open}
      onClose={busy ? () => undefined : onClose}
      title="Organization structure"
      description="Declare the real departments of your organization. Pick a suggested template (its name is just a pre-filled default you can edit) or add your own — e.g. “Finanzas”. You can also rename or remove what is already here, and set budgets later from Budgets."
      width="max-w-3xl"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={skip} disabled={busy}>
            Skip for now
          </Button>
          <Button
            variant="primary"
            size="sm"
            loading={busy}
            onClick={confirm}
            disabled={rows === null}
          >
            Save structure
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {templates.error ? (
          <p className="rounded-lg border border-danger/25 bg-danger-soft px-3.5 py-2.5 text-xs text-danger">
            Could not load the template catalog: {templates.error.message}
          </p>
        ) : (
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
              Suggested templates
            </p>
            <div className="flex flex-wrap gap-1.5">
              {templates.loading ? (
                <span className="text-xs text-muted">Loading…</span>
              ) : (
                tplList.map((t) => {
                  const used = usedTemplateKeys.has(t.key);
                  return (
                    <button
                      key={t.key}
                      type="button"
                      disabled={used}
                      onClick={() => addFromTemplate(t)}
                      title={t.description_es}
                      className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                        used
                          ? "cursor-not-allowed border-border-subtle bg-surface text-faint opacity-60"
                          : "border-border-subtle bg-surface text-muted hover:bg-surface-alt hover:text-foreground"
                      }`}
                    >
                      + {t.name_es}
                    </button>
                  );
                })
              )}
            </div>
          </div>
        )}

        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
            Your departments
          </p>
          {visibleRows.length === 0 && markedDeletedRows.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border-subtle px-3 py-3 text-xs text-muted">
              No departments yet — add the first one from a template above or
              with a custom name below.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {visibleRows.map((r) => (
                <li
                  key={r.clientId}
                  className="flex items-center gap-2 rounded-lg border border-border-subtle px-2.5 py-1.5"
                >
                  <Input
                    aria-label="Department name"
                    value={r.name}
                    maxLength={80}
                    onChange={(e) => renameRow(r.clientId, e.target.value)}
                    className="flex-1"
                  />
                  {r.profileTemplate ? (
                    <Badge tone="info">{r.profileTemplate}</Badge>
                  ) : r.id ? (
                    <Badge tone="neutral">existing</Badge>
                  ) : (
                    <Badge tone="neutral">new</Badge>
                  )}
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => removeRow(r.clientId)}
                  >
                    Remove
                  </Button>
                </li>
              ))}
              {markedDeletedRows.map((r) => (
                <li
                  key={r.clientId}
                  className="flex items-center justify-between gap-2 rounded-lg border border-danger/25 bg-danger-soft px-2.5 py-1.5 text-xs text-danger"
                >
                  <span className="line-through">{r.originalName}</span>
                  <span className="flex items-center gap-2">
                    will be deleted on save
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => undoRemoveRow(r.clientId)}
                    >
                      Undo
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Field label="Custom department" htmlFor="wizard-custom-name">
              <Input
                id="wizard-custom-name"
                value={customName}
                maxLength={80}
                onChange={(e) => setCustomName(e.target.value)}
                placeholder="Recursos Humanos"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addCustom();
                  }
                }}
              />
            </Field>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={addCustom}
            disabled={!customName.trim()}
          >
            <IconPlus size={13} /> Add custom
          </Button>
        </div>

        {errors.length > 0 ? (
          <ul className="space-y-1 rounded-lg border border-danger/25 bg-danger-soft px-3.5 py-2.5 text-xs text-danger">
            {errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
            <li className="pt-1 text-faint">
              Nothing was closed — fix the blockers above and save again, or
              skip for now.
            </li>
          </ul>
        ) : null}

        <p className="text-xs leading-relaxed text-muted">
          Only empty departments can be removed — if users or spend still
          reference one, the control plane will reject the delete and explain
          what to move first. Monthly budgets are set later from the Budgets
          page.
        </p>
      </div>
    </Dialog>
  );
}

/**
 * One-shot gate (D-38-02): mounted in the console layout. While the
 * tenant's structure onboarding is pending and the principal can manage
 * departments (org_admin), the wizard overlays the console exactly once.
 */
export function StructureWizardGate({ me }: { me: WhoAmI }) {
  const canManage = me.permissions.includes("departments.manage");
  const state = useApi<OnboardingStateDto>(
    canManage ? "/api/org/onboarding" : null,
  );
  const [dismissed, setDismissed] = useState(false);

  if (!canManage || dismissed) return null;
  if (state.loading || state.error) return null;
  if (!state.data?.structure_onboarding_pending) return null;

  return (
    <StructureWizard
      open
      onClose={() => setDismissed(true)}
      onCompleted={() => {
        setDismissed(true);
        // The page underneath was rendered against the OLD structure
        // (usually the empty overview) — refresh it with the real one.
        window.location.reload();
      }}
    />
  );
}

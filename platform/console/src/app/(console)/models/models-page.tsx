"use client";

/**
 * Model Access (spec 009 REQ-AC-015): matriz del catálogo con aprobaciones
 * por nivel (GET /api/models/access), aprobar (PUT /api/models/access) y
 * revocar (POST /api/models/access/revoke). Modo central/BYOK (spec 005).
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type { AccessModelRow, AccessOverview, BudgetOverview } from "@/lib/types";
import { usdPerMillion } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconModels, IconPlus } from "@/components/icons";

function ApprovalBadges({ row }: { row: AccessModelRow }) {
  if (row.approvals.length === 0) {
    return <span className="text-xs text-faint">Not approved</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {row.approvals.map((a, i) => (
        <Badge
          key={i}
          tone={a.level === "org" ? "success" : a.level === "dept" ? "info" : "neutral"}
        >
          {a.level}
          {a.mode === "byok" ? " · BYOK" : ""}
        </Badge>
      ))}
    </div>
  );
}

export function ModelsPage() {
  const overview = useApi<AccessOverview>("/api/models/access");
  const budgets = useApi<BudgetOverview>("/api/budgets/overview");
  const { push } = useToast();

  const [approveFor, setApproveFor] = useState<AccessModelRow | null>(null);
  const [revokeFor, setRevokeFor] = useState<AccessModelRow | null>(null);
  const [level, setLevel] = useState<"org" | "dept" | "user">("org");
  const [departmentId, setDepartmentId] = useState("");
  const [userId, setUserId] = useState("");
  const [mode, setMode] = useState("central");
  const [submitting, setSubmitting] = useState(false);

  const departments = budgets.data?.departments ?? [];

  async function approve() {
    if (!approveFor) return;
    setSubmitting(true);
    try {
      await api("/api/models/access", {
        method: "PUT",
        body: {
          model_name: approveFor.model_name,
          level,
          department_id: level === "dept" ? departmentId || null : null,
          user_id: level === "user" ? userId || null : null,
          mode,
        },
      });
      push("success", `${approveFor.display_name} approved at ${level} level.`);
      setApproveFor(null);
      overview.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Approval failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function revoke() {
    if (!revokeFor) return;
    setSubmitting(true);
    try {
      await api("/api/models/access/revoke", {
        method: "POST",
        body: {
          model_name: revokeFor.model_name,
          level: "org",
          department_id: null,
          user_id: null,
        },
      });
      push("success", `Org-level approval for ${revokeFor.display_name} revoked.`);
      setRevokeFor(null);
      overview.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Revoke failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Model catalog & approvals"
        description="Only approved models can be used. Pricing in USD per million tokens. Approvals cascade: org → department → user."
        actions={
          <Button
            variant="primary"
            size="sm"
            disabled={overview.data?.models.length ? false : true}
            onClick={() => {
              const first = overview.data?.models.find((m) => m.approvals.length === 0);
              if (first) {
                setApproveFor(first);
                setLevel("org");
              } else {
                push("error", "Pick a model from the table to approve it.");
              }
            }}
          >
            <IconPlus size={13} /> Approve model
          </Button>
        }
      />
      {overview.loading ? (
        <TableSkeleton rows={6} cols={5} />
      ) : overview.error ? (
        overview.error.status === 403 ? (
          <EmptyState
            icon={<IconModels size={18} />}
            title="Only admins view the Model Access matrix"
            description="Your role does not include permission to manage model approvals."
          />
        ) : (
          <ErrorState error={overview.error} onRetry={overview.refetch} context="GET /api/models/access" />
        )
      ) : (overview.data?.models.length ?? 0) === 0 ? (
        <EmptyState
          title="The model catalog is empty"
          description="The provider seeds the global model catalog during deployment."
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Model</Th>
              <Th>Provider</Th>
              <Th>Input price</Th>
              <Th>Output price</Th>
              <Th>Approvals</Th>
              <Th className="w-44" />
            </tr>
          </thead>
          <tbody>
            {overview.data!.models.map((m) => (
              <tr key={m.model_name} className="hover:bg-surface-alt/50">
                <Td>
                  <p className="font-medium">{m.display_name}</p>
                  <p className="font-mono text-[11px] text-muted">{m.model_name}</p>
                </Td>
                <Td className="text-xs text-muted">{m.provider}</Td>
                <Td className="text-xs">{usdPerMillion(m.input_price_cents_per_m)}</Td>
                <Td className="text-xs">{usdPerMillion(m.output_price_cents_per_m)}</Td>
                <Td>
                  <ApprovalBadges row={m} />
                </Td>
                <Td>
                  <div className="flex justify-end gap-1.5">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => {
                        setApproveFor(m);
                        setLevel("org");
                      }}
                    >
                      Approve
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      disabled={!m.approvals.some((a) => a.level === "org")}
                      onClick={() => setRevokeFor(m)}
                    >
                      Revoke
                    </Button>
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      {/* Aprobar */}
      <Dialog
        open={approveFor !== null}
        onClose={() => setApproveFor(null)}
        title={`Approve ${approveFor?.display_name ?? ""}`}
        description="Choose the governance level and the access mode."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setApproveFor(null)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={submitting} onClick={approve}>
              Approve model
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Level" htmlFor="approve-level" hint="Org: everyone. Department: members of one department. User: a single user.">
            <Select id="approve-level" value={level} onChange={(e) => setLevel(e.target.value as "org" | "dept" | "user")}>
              <option value="org">Organization</option>
              <option value="dept">Department</option>
              <option value="user">User</option>
            </Select>
          </Field>
          {level === "dept" ? (
            <Field label="Department" htmlFor="approve-dept">
              <Select id="approve-dept" value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
                <option value="">Select a department…</option>
                {departments.map((d) => (
                  <option key={d.department_id} value={d.department_id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          {level === "user" ? (
            <Field
              label="User ID"
              htmlFor="approve-user"
              hint="Paste the user's UUID (the user directory API is pending — see Users & Roles)."
            >
              <Input
                id="approve-user"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="00000000-0000-0000-0000-000000000000"
                className="font-mono text-xs"
              />
            </Field>
          ) : null}
          <Field label="Access mode" htmlFor="approve-mode" hint="Central: Huawei Cloud MaaS via the platform gateway. BYOK: the user&apos;s own key.">
            <Select id="approve-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="central">Central (platform gateway)</option>
              <option value="byok">BYOK (user&apos;s own key)</option>
            </Select>
          </Field>
        </div>
      </Dialog>

      {/* Revocar */}
      <ConfirmDialog
        open={revokeFor !== null}
        onClose={() => setRevokeFor(null)}
        onConfirm={revoke}
        loading={submitting}
        title={`Revoke org approval — ${revokeFor?.display_name ?? ""}`}
        message="Users will no longer be able to use this model at the organization level (department/user approvals, if any, remain). Usage attempts will be denied and audited."
        confirmLabel="Revoke approval"
      />
    </Card>
  );
}

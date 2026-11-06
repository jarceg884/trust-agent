"use client";

/**
 * Users & Roles (spec 009 REQ-AC-013 + spec 017, feedback Miller #4):
 * directorio REAL de GET /api/users con gestión completa — asignar/quitar
 * roles inline (POST/DELETE /api/users/{id}/roles), cambiar departamento y
 * estado (PUT /api/users/{id}), desactivar (POST /users/{id}/disable) e
 * invitar (POST /api/users, spec 012). Los controles se muestran según los
 * permisos REALES de /api/me (roles.assign / users.manage / users.invite);
 * la fila propia nunca ofrece auto-modificación de roles (D-17-02, el
 * servidor además lo rechaza). Toda mutación pasa por el proxy BFF con
 * CSRF; confirmación explícita para acciones destructivas; audit del lado
 * del control plane (role.assigned / role.revoked / user.updated /
 * user.disabled).
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type { DepartmentsList, DirectoryList, DirectoryUser, WhoAmI } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  CardBody,
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
import { IconPlus, IconUsers } from "@/components/icons";
import { AllowedConnectionsSelector } from "@/components/connection-policy";
import type { PolicyList } from "@/lib/types";

const TENANT_ROLES = ["org_admin", "dept_admin", "user"] as const;

export function UsersPage() {
  const directory = useApi<DirectoryList>("/api/users");
  const me = useApi<WhoAmI>("/api/me");
  const departments = useApi<DepartmentsList>("/api/departments");
  const { push } = useToast();

  const [assignFor, setAssignFor] = useState<DirectoryUser | null>(null);
  const [assignRole, setAssignRole] = useState<string>("user");
  const [assignDept, setAssignDept] = useState<string>("");
  const [revoke, setRevoke] = useState<{
    user: DirectoryUser;
    role: { role_key: string; department_id: string | null };
  } | null>(null);
  const [deptFor, setDeptFor] = useState<DirectoryUser | null>(null);
  const [deptChoice, setDeptChoice] = useState<string>("");
  const [disableFor, setDisableFor] = useState<DirectoryUser | null>(null);
  const [deleteFor, setDeleteFor] = useState<DirectoryUser | null>(null);
  const [reactivateFor, setReactivateFor] = useState<DirectoryUser | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteName, setInviteName] = useState("");
  const [inviteDept, setInviteDept] = useState("");
  const [inviteRole, setInviteRole] = useState("user");
  const [submitting, setSubmitting] = useState(false);
  // Per-user enrollment token (spec 044): admin generates it from the user row.
  const [tokenFor, setTokenFor] = useState<DirectoryUser | null>(null);
  const [enrollToken, setEnrollToken] = useState("");
  const [tokenBusy, setTokenBusy] = useState(false);
  const [tokenCopied, setTokenCopied] = useState(false);

  // spec 024: allowed_connections a nivel usuario (selector catálogo + custom)
  const [policiesFor, setPoliciesFor] = useState<DirectoryUser | null>(null);
  const userPolicies = useApi<PolicyList>(
    policiesFor ? `/api/policies?level=user&user_id=${policiesFor.user_id}` : null,
  );
  const [userConnTokens, setUserConnTokens] = useState<string[] | null>(null);
  const [savingUserConn, setSavingUserConn] = useState(false);

  function currentUserConnTokens(): string[] {
    const entry = userPolicies.data?.policies.find(
      (p) => p.key === "allowed_connections",
    );
    return Array.isArray(entry?.value) ? (entry!.value as string[]) : ["*"];
  }

  async function saveUserConnections() {
    if (!policiesFor || userConnTokens === null) return;
    setSavingUserConn(true);
    try {
      await api(`/api/policies/user/${policiesFor.user_id}`, {
        method: "PUT",
        body: { key: "allowed_connections", value: userConnTokens },
      });
      push("success", `allowed_connections saved for ${policiesFor.email}. The next runtime use is checked against the new effective policy.`);
      setUserConnTokens(null);
      userPolicies.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Save failed.");
    } finally {
      setSavingUserConn(false);
    }
  }

  async function issueEnrollmentToken(user: DirectoryUser) {
    setTokenFor(user);
    setEnrollToken("");
    setTokenCopied(false);
    setTokenBusy(true);
    try {
      const result = await api<{ enrollment_token: string }>("/api/devices/preauthorize", {
        method: "POST",
        body: {
          user_id: user.user_id,
          device_name: `${user.display_name || user.email} · desktop`,
          platform: "Windows",
        },
      });
      setEnrollToken(result.enrollment_token);
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo generar el token.");
      setTokenFor(null);
    } finally {
      setTokenBusy(false);
    }
  }

  async function removeUserConnections() {
    if (!policiesFor) return;
    setSavingUserConn(true);
    try {
      await api(`/api/policies/user/allowed_connections?user_id=${policiesFor.user_id}`, {
        method: "DELETE",
      });
      push("success", "User-level allowed_connections removed (inherits department and organization).");
      setUserConnTokens(null);
      userPolicies.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Remove failed.");
    } finally {
      setSavingUserConn(false);
    }
  }

  const canAssignRoles =
    me.data?.permissions.includes("roles.assign") ?? false;
  const canManageUsers = me.data?.permissions.includes("users.manage") ?? false;
  const canInvite = me.data?.permissions.includes("users.invite") ?? false;

  const deptName = (id: string | null) =>
    id ? (departments.data?.departments.find((d) => d.department_id === id)?.name ?? id) : "—";

  async function run(
    action: () => Promise<unknown>,
    okMessage: string,
    close?: () => void,
  ) {
    setSubmitting(true);
    try {
      await action();
      push("success", okMessage);
      // Close the dialog that started the mutation ONLY on success — a
      // failed request keeps it open so the admin can retry or cancel.
      close?.();
      directory.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Request failed.");
    } finally {
      setSubmitting(false);
    }
  }

  const assign = () =>
    run(
      () =>
        api(`/api/users/${assignFor!.user_id}/roles`, {
          method: "POST",
          body: {
            role_key: assignRole,
            ...(assignRole === "dept_admin" && assignDept
              ? { department_id: assignDept }
              : {}),
          },
        }),
      `Role ${assignRole} assigned to ${assignFor?.email}.`,
      () => setAssignFor(null),
    );

  const doRevoke = () =>
    run(
      () => {
        const { user, role } = revoke!;
        const query = role.department_id ? `?department_id=${role.department_id}` : "";
        return api(`/api/users/${user.user_id}/roles/${role.role_key}${query}`, {
          method: "DELETE",
        });
      },
      `Role ${revoke?.role.role_key} removed from ${revoke?.user.email}.`,
      () => setRevoke(null),
    );

  const moveDept = () => {
    if (!deptChoice) {
      push("error", "A department is required for every user.");
      return;
    }
    return run(
      () =>
        api(`/api/users/${deptFor!.user_id}`, {
          method: "PUT",
          body: { department_id: deptChoice },
        }),
      `${deptFor?.email} moved to ${deptName(deptChoice)}.`,
      () => setDeptFor(null),
    );
  };

  const disable = () =>
    run(
      () => api(`/api/users/${disableFor!.user_id}/disable`, { method: "POST" }),
      `${disableFor?.email} disabled — sessions and devices revoked.`,
      () => setDisableFor(null),
    );

  const reactivate = () =>
    run(
      () =>
        api(`/api/users/${reactivateFor!.user_id}`, {
          method: "PUT",
          body: { status: "active" },
        }),
      `${reactivateFor?.email} reactivated.`,
      () => setReactivateFor(null),
    );

  const removeUser = () =>
    run(
      () => api(`/api/users/${deleteFor!.user_id}`, { method: "DELETE" }),
      `${deleteFor?.email} deleted permanently.`,
      () => setDeleteFor(null),
    );

  const invite = () =>
    run(
      () =>
        api("/api/users", {
          method: "POST",
          body: {
            email: inviteEmail,
            display_name: inviteName || null,
            department_id: inviteDept,
            role_key: inviteRole,
          },
        }),
      `Invitation queued for ${inviteEmail} — the worker provisions the user.`,
      () => setInviteOpen(false),
    );

  const pending =
    directory.error !== null &&
    (directory.error.status === 404 || directory.error.status === 405);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Users & roles"
          description="Directory of the organization's users, their departments and role assignments. Role changes are audited (role.assigned / role.revoked)."
          actions={
            <Button
              variant="primary"
              size="sm"
              disabled={!canInvite}
              title={
                canInvite
                  ? "POST /api/users — asynchronous invite job (spec 012)"
                  : "Requires the users.invite permission"
              }
              onClick={() => {
                setInviteEmail("");
                setInviteName("");
                setInviteDept("");
                setInviteRole("user");
                setInviteOpen(true);
              }}
            >
              <IconPlus size={13} /> Invite user
            </Button>
          }
        />
        {directory.loading ? (
          <TableSkeleton rows={5} cols={6} />
        ) : pending ? (
          <EmptyState
            icon={<IconUsers size={18} />}
            title="User directory API not available"
            description="GET /api/users did not answer in this build. No placeholder data is shown."
            action={
              <Button variant="secondary" size="sm" onClick={directory.refetch}>
                Check again
              </Button>
            }
          />
        ) : directory.error ? (
          <ErrorState error={directory.error} onRetry={directory.refetch} context="GET /api/users" />
        ) : (directory.data?.users.length ?? 0) === 0 ? (
          <EmptyState
            title="No users found"
            description="The directory returned no users for your scope."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>User</Th>
                <Th>Department</Th>
                <Th>Roles</Th>
                <Th>Status</Th>
                <Th>MFA</Th>
                <Th className="w-56" />
              </tr>
            </thead>
            <tbody>
              {directory.data!.users.map((u) => {
                const self = u.user_id === me.data?.user_id;
                return (
                  <tr key={u.user_id} className="hover:bg-surface-alt/50">
                    <Td>
                      <p className="font-medium">{u.display_name || u.email}</p>
                      <p className="text-[11px] text-muted">{u.email}</p>
                    </Td>
                    <Td className="text-xs">
                      {self ? (
                        <span className="text-muted">{deptName(u.department_id)}</span>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={!canManageUsers}
                          title={canManageUsers ? "Move to another department" : "Requires users.manage"}
                          onClick={() => {
                            setDeptFor(u);
                            setDeptChoice(u.department_id ?? "");
                          }}
                        >
                          {deptName(u.department_id)}
                        </Button>
                      )}
                    </Td>
                    <Td>
                      <div className="flex flex-wrap items-center gap-1">
                        {u.roles.map((r) => (
                          <span key={`${r.role_key}-${r.department_id ?? ""}`} className="inline-flex items-center">
                            <Badge tone="accent">
                              {r.role_key}
                              {r.department_id ? ` · ${deptName(r.department_id)}` : ""}
                            </Badge>
                            {canAssignRoles && !self && (
                              <button
                                type="button"
                                aria-label={`Remove role ${r.role_key}`}
                                title={`Remove ${r.role_key}${r.department_id ? ` (scope ${deptName(r.department_id)})` : ""}`}
                                className="ml-0.5 text-xs text-muted hover:text-danger"
                                onClick={() =>
                                  setRevoke({
                                    user: u,
                                    role: { role_key: r.role_key, department_id: r.department_id },
                                  })
                                }
                              >
                                ✕
                              </button>
                            )}
                          </span>
                        ))}
                        {canAssignRoles && !self && (
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Assign a role"
                            onClick={() => {
                              setAssignFor(u);
                              setAssignRole("user");
                              setAssignDept("");
                            }}
                          >
                            + role
                          </Button>
                        )}
                      </div>
                    </Td>
                    <Td>
                      <Badge tone={u.status === "active" ? "success" : "neutral"}>{u.status}</Badge>
                    </Td>
                    <Td>
                      {/* Spec 015 D-AUTH-11: mfa_enrolled synced from the IdP at
                          the auth funnels (high-water mark, not editable here). */}
                      <Badge tone={u.mfa_enrolled ? "success" : "neutral"}>
                        {u.mfa_enrolled ? "enrolled" : "none"}
                      </Badge>
                    </Td>
                    <Td>
                      <div className="flex justify-end gap-1.5">
                        {canManageUsers && (
                          <Button
                            variant="secondary"
                            size="sm"
                            title="Generar token de enrolamiento para este usuario"
                            onClick={() => void issueEnrollmentToken(u)}
                          >
                            Token
                          </Button>
                        )}
                        {canAssignRoles && (
                          <Button
                            variant="secondary"
                            size="sm"
                            title="Allowed connections policy (spec 024)"
                            onClick={() => {
                              setPoliciesFor(u);
                              setUserConnTokens(null);
                            }}
                          >
                            Policies
                          </Button>
                        )}
                        {canManageUsers && !self && u.status === "active" && (
                          <Button variant="secondary" size="sm" onClick={() => setDisableFor(u)}>
                            Disable
                          </Button>
                        )}
                        {canManageUsers && !self && u.status === "disabled" && (
                          <Button variant="secondary" size="sm" onClick={() => setReactivateFor(u)}>
                            Reactivate
                          </Button>
                        )}
                        {canManageUsers && !self && (
                          <Button variant="danger" size="sm" onClick={() => setDeleteFor(u)}>
                            Delete
                          </Button>
                        )}
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
        <CardBody className="border-t border-border-subtle bg-surface-alt/40">
          <p className="text-[11px] leading-relaxed text-muted">
            <strong className="text-foreground">Governance</strong> — only
            organization admins hold <code className="font-mono">roles.assign</code>;
            role changes, department moves and status transitions are written to
            the immutable audit log. You can never modify your own roles or
            disable your own account.
          </p>
        </CardBody>
      </Card>

      {/* Assign role */}
      <Dialog
        open={assignFor !== null}
        onClose={() => setAssignFor(null)}
        title={`Assign role — ${assignFor?.email ?? ""}`}
        description="dept_admin requires a department scope; the assignment is audited as role.assigned."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setAssignFor(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={submitting}
              disabled={assignRole === "dept_admin" && !assignDept}
              onClick={assign}
            >
              Assign role
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Role" htmlFor="assign-role">
            <Select
              id="assign-role"
              value={assignRole}
              onChange={(e) => setAssignRole(e.target.value)}
            >
              {TENANT_ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </Select>
          </Field>
          {assignRole === "dept_admin" && (
            <Field label="Department scope" htmlFor="assign-dept">
              <Select
                id="assign-dept"
                value={assignDept}
                onChange={(e) => setAssignDept(e.target.value)}
              >
                <option value="">Select a department…</option>
                {departments.data?.departments.map((d) => (
                  <option key={d.department_id} value={d.department_id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
        </div>
      </Dialog>

      {/* Remove role */}
      <ConfirmDialog
        open={revoke !== null}
        onClose={() => setRevoke(null)}
        onConfirm={doRevoke}
        loading={submitting}
        title={`Remove role — ${revoke?.user.email ?? ""}`}
        message={`Remove ${revoke?.role.role_key}${
          revoke?.role.department_id ? ` (scope ${deptName(revoke.role.department_id)})` : ""
        }? The user immediately loses the permissions of that role. Audited as role.revoked.`}
        confirmLabel="Remove role"
      />

      {/* Change department */}
      <Dialog
        open={deptFor !== null}
        onClose={() => setDeptFor(null)}
        title={`Move department — ${deptFor?.email ?? ""}`}
        description="The user's budgets and policies follow the new department scope. Audited as user.updated."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setDeptFor(null)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={submitting} disabled={!deptChoice} onClick={moveDept}>
              Move user
            </Button>
          </>
        }
      >
        <Field label="Department" htmlFor="move-dept">
          <Select
            id="move-dept"
            value={deptChoice}
            onChange={(e) => setDeptChoice(e.target.value)}
          >
            <option value="">Select a department…</option>
            {departments.data?.departments.map((d) => (
              <option key={d.department_id} value={d.department_id}>
                {d.name}
              </option>
            ))}
          </Select>
        </Field>
      </Dialog>

      {/* Disable */}
      <ConfirmDialog
        open={disableFor !== null}
        onClose={() => setDisableFor(null)}
        onConfirm={disable}
        loading={submitting}
        title={`Disable — ${disableFor?.email ?? ""}`}
        message={`Disable ${disableFor?.email}? Their sessions and devices are revoked immediately in the same transaction. Audited as user.disabled.`}
        confirmLabel="Disable user"
      />

      {/* Delete */}
      <ConfirmDialog
        open={deleteFor !== null}
        onClose={() => setDeleteFor(null)}
        onConfirm={removeUser}
        loading={submitting}
        title={`Delete — ${deleteFor?.email ?? ""}`}
        message={`Delete ${deleteFor?.email} permanently? Their devices, sessions, keys and grants are removed. This cannot be undone. The last organization administrator cannot be deleted.`}
        confirmLabel="Delete permanently"
      />

      {/* Reactivate */}
      <ConfirmDialog
        open={reactivateFor !== null}
        onClose={() => setReactivateFor(null)}
        onConfirm={reactivate}
        loading={submitting}
        title={`Reactivate — ${reactivateFor?.email ?? ""}`}
        message={`Reactivate ${reactivateFor?.email}? They will be able to log in again (the identity provider still controls authentication). Audited as user.updated.`}
        confirmLabel="Reactivate"
        danger={false}
      />

      {/* Políticas de usuario: allowed_connections (spec 024) */}
      <Dialog
        open={policiesFor !== null}
        onClose={() => {
          setPoliciesFor(null);
          setUserConnTokens(null);
        }}
        title={`Allowed connections — ${policiesFor?.display_name || (policiesFor?.email ?? "")}`}
        description="User-level allowed_connections (deny-overrides: it can only tighten the department and organization levels). The next runtime use of a connection is checked against the new effective policy."
        width="max-w-2xl"
      >
        {userPolicies.loading ? (
          <p className="py-6 text-center text-xs text-muted">Loading…</p>
        ) : userPolicies.error ? (
          <ErrorState error={userPolicies.error} context="GET /api/policies?level=user" />
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-mono text-xs text-muted">
                Current: {JSON.stringify(currentUserConnTokens())}
                {!userPolicies.data?.policies.some((p) => p.key === "allowed_connections")
                  ? " — inherited default (no user-level policy)"
                  : ""}
              </p>
              <div className="flex gap-1.5">
                {userConnTokens === null ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setUserConnTokens(currentUserConnTokens())}
                  >
                    Edit
                  </Button>
                ) : (
                  <>
                    <Button variant="ghost" size="sm" onClick={() => setUserConnTokens(null)}>
                      Cancel
                    </Button>
                    <Button
                      variant="primary"
                      size="sm"
                      loading={savingUserConn}
                      onClick={saveUserConnections}
                    >
                      Save
                    </Button>
                  </>
                )}
                {userPolicies.data?.policies.some((p) => p.key === "allowed_connections") ? (
                  <Button variant="danger" size="sm" loading={savingUserConn} onClick={removeUserConnections}>
                    Remove
                  </Button>
                ) : null}
              </div>
            </div>
            {userConnTokens !== null ? (
              <AllowedConnectionsSelector value={userConnTokens} onChange={setUserConnTokens} />
            ) : null}
          </div>
        )}
      </Dialog>

      {/* Invite (spec 012: async job) */}
      <Dialog
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        title="Invite user"
        description="POST /api/users queues an asynchronous provisioning job: the worker creates the identity and delivers a one-time password through the jobs API (spec 012)."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setInviteOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={submitting}
              disabled={!inviteEmail.includes("@") || !inviteDept}
              onClick={invite}
            >
              Send invitation
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Email" htmlFor="invite-email">
            <Input
              id="invite-email"
              type="email"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              placeholder="name@company.com"
            />
          </Field>
          <Field label="Display name (optional)" htmlFor="invite-name">
            <Input
              id="invite-name"
              value={inviteName}
              onChange={(e) => setInviteName(e.target.value)}
              placeholder="Jane Doe"
            />
          </Field>
          <Field label="Department (required)" htmlFor="invite-dept">
            <Select
              id="invite-dept"
              value={inviteDept}
              onChange={(e) => setInviteDept(e.target.value)}
            >
              <option value="">Select a department…</option>
              {departments.data?.departments.map((d) => (
                <option key={d.department_id} value={d.department_id}>
                  {d.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Initial role" htmlFor="invite-role">
            <Select
              id="invite-role"
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
            >
              {TENANT_ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </Dialog>

      <Dialog
        open={Boolean(tokenFor)}
        onClose={() => setTokenFor(null)}
        title="Token de enrolamiento"
        description={
          tokenFor
            ? `Token de un solo uso para ${tokenFor.display_name || tokenFor.email}. Se muestra una sola vez.`
            : ""
        }
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setTokenFor(null)}>
              Cerrar
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={!enrollToken}
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(enrollToken);
                  setTokenCopied(true);
                  window.setTimeout(() => setTokenCopied(false), 1500);
                } catch {
                  push("error", "No se pudo copiar; selecciona el texto manualmente.");
                }
              }}
            >
              {tokenCopied ? "Copiado" : "Copiar token"}
            </Button>
          </div>
        }
      >
        <div className="space-y-3">
          {tokenBusy ? (
            <p className="text-sm text-muted">Generando token…</p>
          ) : (
            <>
              <p className="text-xs text-muted">
                En el instalador de Trust Agent: Portal URL, organización y este token.
              </p>
              <pre className="max-h-40 overflow-auto rounded-lg border border-border-subtle bg-surface p-3 font-mono text-[11px] break-all whitespace-pre-wrap">
                {enrollToken || "—"}
              </pre>
            </>
          )}
        </div>
      </Dialog>
    </div>
  );
}

"use client";

/**
 * Devices (spec 009 REQ-AC-017 + spec 027 live presence): administrative
 * table (GET /api/devices with server-side filters), approve/reject for
 * pending and revoke with immediate session cut (spec 007). The live strip
 * shows HOW MANY Hermes desktops are connected right now and WHO they are
 * (user, department, applied policy version, last heartbeat) from
 * GET /api/devices/presence, auto-refreshed every 15 s (REQ-027-06).
 */
import { useEffect, useMemo, useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type {
  DeviceList,
  DeviceRow,
  DeviceStatus,
  PresenceResponse,
} from "@/lib/types";
import { dateTime, timeAgo } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  OneTimeSecret,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconDevices } from "@/components/icons";
import type { DirectoryList } from "@/lib/types";

const STATUS_TONES: Record<DeviceStatus, "success" | "warning" | "danger" | "neutral"> = {
  active: "success",
  pending: "warning",
  rejected: "danger",
  revoked: "neutral",
};

const PRESENCE_POLL_MS = 15_000;

export function DevicesPage() {
  const [status, setStatus] = useState("");
  const [action, setAction] = useState<{
    device: DeviceRow;
    kind: "approve" | "reject" | "revoke" | "delete";
  } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [enrollUser, setEnrollUser] = useState("");
  const [issuing, setIssuing] = useState(false);
  const [issued, setIssued] = useState<{
    enrollment_token: string;
    user_email: string;
  } | null>(null);
  const { push } = useToast();

  const devices = useApi<DeviceList>(
    `/api/devices${status ? `?status=${status}` : ""}`,
  );
  const presence = useApi<PresenceResponse>("/api/devices/presence");
  const directory = useApi<DirectoryList>(enrollOpen ? "/api/users" : null);

  // Live auto-refresh (REQ-027-06): poll both endpoints every 15 s while
  // the page is mounted. Cleanup on unmount; no setState outside callbacks.
  useEffect(() => {
    const timer = window.setInterval(() => {
      presence.refetch();
      devices.refetch();
    }, PRESENCE_POLL_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Presence by device id — merged into the admin table rows.
  const presenceById = useMemo(() => {
    const map = new Map<string, PresenceResponse["devices"][number]>();
    for (const p of presence.data?.devices ?? []) map.set(p.id, p);
    return map;
  }, [presence.data]);

  async function runAction() {
    if (!action) return;
    setSubmitting(true);
    try {
      await api(`/api/devices/${action.device.id}${action.kind === "delete" ? "" : `/${action.kind}`}`, {
        method: action.kind === "delete" ? "DELETE" : "POST",
      });
      const verbs = { approve: "approved", reject: "rejected", revoke: "revoked", delete: "deleted" };
      push(
        "success",
        `Device ${action.device.device_name || action.device.fingerprint.slice(0, 12)} ${verbs[action.kind]}.`,
      );
      setAction(null);
      devices.refetch();
      presence.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Action failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function issueEnrollmentToken() {
    if (!enrollUser) return;
    setIssuing(true);
    try {
      const result = await api<{
        enrollment_token: string;
        user_email: string;
      }>("/api/devices/preauthorize", {
        method: "POST",
        body: { user_id: enrollUser },
      });
      setIssued(result);
      devices.refetch();
      presence.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Could not issue the token.");
    } finally {
      setIssuing(false);
    }
  }

  function closeEnroll() {
    setEnrollOpen(false);
    setEnrollUser("");
    setIssued(null);
  }

  const live = presence.data;

  return (
    <Card>
      <CardHeader
        title="Enrolled devices"
        description="Devices link desktop clients to users. Revocation invalidates their sessions immediately."
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                setEnrollOpen(true);
                setIssued(null);
              }}
            >
              + Enrol device
            </Button>
            {live && (
              <div
                className="flex items-center gap-2 rounded-lg border border-border bg-surface-alt/60 px-3 py-1.5"
                title={`Online = heartbeat within the last ${Math.round(live.online_window_seconds / 60)} min. Auto-refreshes every 15 s.`}
              >
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60 motion-reduce:animate-none" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                </span>
                <span className="text-xs font-medium">
                  {live.online_count} online
                </span>
                <span className="text-[11px] text-faint">
                  / {live.active_count} active · live
                </span>
              </div>
            )}
            <label htmlFor="device-status" className="sr-only">
              Filter by status
            </label>
            <Select
              id="device-status"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="w-36 text-xs"
            >
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="active">Active</option>
              <option value="rejected">Rejected</option>
              <option value="revoked">Revoked</option>
            </Select>
          </div>
        }
      />
      {devices.loading ? (
        <TableSkeleton rows={5} cols={8} />
      ) : devices.error ? (
        devices.error.status === 403 ? (
          <EmptyState
            icon={<IconDevices size={18} />}
            title="Device management requires the devices.manage permission"
            description="Your role cannot approve, reject or revoke devices."
          />
        ) : (
          <ErrorState error={devices.error} onRetry={devices.refetch} context="GET /api/devices" />
        )
      ) : (devices.data?.devices.length ?? 0) === 0 ? (
        <EmptyState
          icon={<IconDevices size={18} />}
          title={status ? `No ${status} devices` : "No devices enrolled yet"}
          description="Users download the governed desktop app from your organization's download link; after they sign in with SSO, their device enrolls automatically and the request appears here for approval."
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Device</Th>
              <Th>User</Th>
              <Th>Status</Th>
              <Th>Online</Th>
              <Th>Policy</Th>
              <Th>Last heartbeat</Th>
              <Th>Sessions</Th>
              <Th>Enrolled</Th>
              <Th className="w-52" />
            </tr>
          </thead>
          <tbody>
            {devices.data!.devices.map((d) => {
              const p = presenceById.get(d.id);
              const policyCell = !p ? (
                <span className="text-[11px] text-faint">—</span>
              ) : p.applied_policy_version === null ? (
                <span className="text-[11px] text-faint">no bundle yet</span>
              ) : p.policy_up_to_date ? (
                <span className="font-mono text-xs">v{p.applied_policy_version}</span>
              ) : (
                <span className="flex items-center gap-1.5">
                  <span className="font-mono text-xs">v{p.applied_policy_version}</span>
                  <Badge tone="warning">→ v{p.current_policy_version} pending</Badge>
                </span>
              );
              return (
                <tr key={d.id} className="hover:bg-surface-alt/50">
                  <Td>
                    <p className="font-medium">
                      {d.device_name || "Unnamed device"}{" "}
                      {d.device_type === "cli" ? (
                        <Badge tone="info">CLI</Badge>
                      ) : (
                        <Badge tone="neutral">Desktop</Badge>
                      )}
                    </p>
                    <p className="font-mono text-[11px] text-muted">
                      {d.fingerprint.slice(0, 16)}… · {d.platform || "unknown platform"}
                    </p>
                  </Td>
                  <Td>
                    <p className="text-xs font-medium">{d.display_name || d.email}</p>
                    <p className="text-[11px] text-muted">{d.department_name ?? "No department"}</p>
                  </Td>
                  <Td>
                    <Badge tone={STATUS_TONES[d.status]}>{d.status}</Badge>
                  </Td>
                  <Td>
                    {p?.online ? (
                      <Badge tone="success">online</Badge>
                    ) : p && d.status === "active" ? (
                      <Badge tone="neutral">offline</Badge>
                    ) : (
                      <span className="text-[11px] text-faint">—</span>
                    )}
                  </Td>
                  <Td>{policyCell}</Td>
                  <Td className="text-xs text-muted">
                    {p?.last_heartbeat_at ? timeAgo(p.last_heartbeat_at) : "—"}
                  </Td>
                  <Td className="text-xs">{d.active_sessions}</Td>
                  <Td className="text-xs text-muted">{dateTime(d.enrolled_at)}</Td>
                  <Td>
                    <div className="flex justify-end gap-1.5">
                      {d.status === "pending" ? (
                        <>
                          <Button variant="primary" size="sm" onClick={() => setAction({ device: d, kind: "approve" })}>
                            Approve
                          </Button>
                          <Button variant="danger" size="sm" onClick={() => setAction({ device: d, kind: "reject" })}>
                            Reject
                          </Button>
                        </>
                      ) : d.status === "active" ? (
                        <>
                          <Button variant="danger" size="sm" onClick={() => setAction({ device: d, kind: "revoke" })}>
                            Revoke
                          </Button>
                          <Button variant="secondary" size="sm" onClick={() => setAction({ device: d, kind: "delete" })}>
                            Delete
                          </Button>
                        </>
                      ) : (
                        <Button variant="secondary" size="sm" onClick={() => setAction({ device: d, kind: "delete" })}>
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

      <Dialog
        open={enrollOpen}
        onClose={closeEnroll}
        title={issued ? "Enrollment token" : "Enrol a device"}
        description={
          issued
            ? "This token is shown exactly once. Send it to the person together with the organization ID."
            : "Pick the person who will activate the device. The token is valid for a single activation."
        }
        footer={
          issued ? (
            <Button variant="primary" onClick={closeEnroll}>
              Done
            </Button>
          ) : (
            <>
              <Button variant="secondary" onClick={closeEnroll}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={issueEnrollmentToken}
                disabled={!enrollUser || issuing}
              >
                {issuing ? "Issuing…" : "Issue token"}
              </Button>
            </>
          )
        }
      >
        {issued ? (
          <div className="space-y-3">
            <p className="text-xs text-muted">
              For <span className="font-medium text-foreground">{issued.user_email}</span>
            </p>
            <OneTimeSecret value={issued.enrollment_token} />
            <p className="text-xs text-muted">
              In the Trust Agent app: enter the portal URL, the organization ID, and this
              token. The device is activated automatically — no further approval.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            <label htmlFor="enroll-user" className="text-xs font-medium text-foreground">
              Person
            </label>
            <Select
              id="enroll-user"
              value={enrollUser}
              onChange={(e) => setEnrollUser(e.target.value)}
              className="w-full"
            >
              <option value="">Select a user…</option>
              {(directory.data?.users ?? [])
                .filter((u) => u.status === "active")
                .map((u) => (
                  <option key={u.user_id} value={u.user_id}>
                    {u.display_name ? `${u.display_name} · ${u.email}` : u.email}
                  </option>
                ))}
            </Select>
            {directory.loading ? (
              <p className="text-[11px] text-faint">Loading directory…</p>
            ) : null}
          </div>
        )}
      </Dialog>

      <ConfirmDialog
        open={action !== null}
        onClose={() => setAction(null)}
        onConfirm={runAction}
        loading={submitting}
        title={
          action?.kind === "approve"
            ? "Approve device"
            : action?.kind === "reject"
              ? "Reject device"
              : action?.kind === "delete"
                ? "Delete device"
                : "Revoke device"
        }
        message={
          action?.kind === "approve"
            ? `Approve "${action.device.device_name || "this device"}" for ${action.device.email}? The app completes its enrollment automatically on the next poll.`
            : action?.kind === "reject"
              ? `Reject the enrollment of "${action.device.device_name || "this device"}"? The user will need to enroll again.`
              : action?.kind === "delete"
                ? `Delete "${action.device.device_name || "this device"}" permanently? Its sessions and model keys are removed. This cannot be undone.`
                : `Revoke "${action?.device.device_name || "this device"}"? Its ${action?.device.active_sessions ?? 0} active session(s) are invalidated immediately and the device loses access.`
        }
        confirmLabel={
          action?.kind === "approve" ? "Approve" : action?.kind === "reject" ? "Reject" : action?.kind === "delete" ? "Delete permanently" : "Revoke now"
        }
      />
    </Card>
  );
}

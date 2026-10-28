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
  EmptyState,
  ErrorState,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconDevices } from "@/components/icons";

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
    kind: "approve" | "reject" | "revoke";
  } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { push } = useToast();

  const devices = useApi<DeviceList>(
    `/api/devices${status ? `?status=${status}` : ""}`,
  );
  const presence = useApi<PresenceResponse>("/api/devices/presence");

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
      await api(`/api/devices/${action.device.id}/${action.kind}`, {
        method: "POST",
      });
      const verbs = { approve: "approved", reject: "rejected", revoke: "revoked" };
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

  const live = presence.data;

  return (
    <Card>
      <CardHeader
        title="Enrolled devices"
        description="Devices link desktop clients to users. Revocation invalidates their sessions immediately."
        actions={
          <div className="flex items-center gap-2">
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
          description="Users download Hermes from your organization's download link; after they sign in with SSO, their Hermes enrolls automatically and the request appears here for approval."
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
                        <Button variant="danger" size="sm" onClick={() => setAction({ device: d, kind: "revoke" })}>
                          Revoke
                        </Button>
                      ) : (
                        <span className="text-[11px] text-faint">No actions</span>
                      )}
                    </div>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}

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
              : "Revoke device"
        }
        message={
          action?.kind === "approve"
            ? `Approve "${action.device.device_name || "this device"}" for ${action.device.email}? Their Hermes completes its enrollment automatically on the next poll.`
            : action?.kind === "reject"
              ? `Reject the enrollment of "${action.device.device_name || "this device"}"? The user will need to enroll again.`
              : `Revoke "${action?.device.device_name || "this device"}"? Its ${action?.device.active_sessions ?? 0} active session(s) are invalidated immediately and the device loses access.`
        }
        confirmLabel={
          action?.kind === "approve" ? "Approve" : action?.kind === "reject" ? "Reject" : "Revoke now"
        }
      />
    </Card>
  );
}

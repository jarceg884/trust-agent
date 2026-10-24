"use client";

/**
 * Devices (spec 009 REQ-AC-017): tabla administrativa (GET /api/devices con
 * filtros server-side), approve/reject para pending y revoke con revocación
 * inmediata de sesiones (spec 007). Los usuarios se enrolan desde el desktop.
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type { DeviceList, DeviceRow, DeviceStatus } from "@/lib/types";
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
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Action failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Enrolled devices"
        description="Devices link desktop clients to users. Revocation invalidates their sessions immediately."
        actions={
          <div className="flex items-center gap-2">
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
        <TableSkeleton rows={5} cols={6} />
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
          description="Users enroll their devices from the desktop app with their organization code. Enrollment requests appear here for approval."
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Device</Th>
              <Th>User</Th>
              <Th>Status</Th>
              <Th>Sessions</Th>
              <Th>Last seen</Th>
              <Th>Enrolled</Th>
              <Th className="w-52" />
            </tr>
          </thead>
          <tbody>
            {devices.data!.devices.map((d) => (
              <tr key={d.id} className="hover:bg-surface-alt/50">
                <Td>
                  <p className="font-medium">{d.device_name || "Unnamed device"}</p>
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
                <Td className="text-xs">{d.active_sessions}</Td>
                <Td className="text-xs text-muted">{timeAgo(d.last_seen_at)}</Td>
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
            ))}
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
            ? `Approve "${action.device.device_name || "this device"}" for ${action.device.email}? The desktop will complete its enrollment on the next poll.`
            : action?.kind === "reject"
              ? `Reject the enrollment of "${action.device.device_name || "this device"}"? The user will need to enroll again.`
              : `Revoke "${action?.device.device_name || "this device"}"? Its ${action?.device.active_sessions ?? 0} active session(s) are invalidated immediately and the desktop loses access.`
        }
        confirmLabel={
          action?.kind === "approve" ? "Approve" : action?.kind === "reject" ? "Reject" : "Revoke now"
        }
      />
    </Card>
  );
}

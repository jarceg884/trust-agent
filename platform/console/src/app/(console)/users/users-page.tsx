"use client";

/**
 * Users & Roles (spec 009 REQ-AC-013, D-AC-06): directorio contra el contrato
 * GET /api/users (spec 001 AC-ORG-002). El endpoint NO existe hoy: la página
 * muestra el estado REAL del control plane (404) con el contrato documentado
 * — cero datos inventados (constitution I). En cuanto la tarea dev lo
 * implemente, esta página se activa sin cambios.
 */
import { useApi } from "@/hooks/use-api";
import type { DirectoryList } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  ErrorState,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconPlus, IconUsers } from "@/components/icons";

export function UsersPage() {
  const directory = useApi<DirectoryList>("/api/users");
  const { push } = useToast();

  const pending =
    directory.error !== null &&
    (directory.error.status === 404 || directory.error.status === 405);

  return (
    <Card>
      <CardHeader
        title="Users & roles"
        description="Directory of the organization's users, their departments and role assignments."
        actions={
          <Button
            variant="primary"
            size="sm"
            disabled
            title="Requires the user directory API (POST /api/users — pending in the control plane)"
            onClick={() => push("error", "The invite flow requires the user directory API (pending).")}
          >
            <IconPlus size={13} /> Invite user
          </Button>
        }
      />
      {directory.loading ? (
        <TableSkeleton rows={5} cols={4} />
      ) : pending ? (
        <EmptyState
          icon={<IconUsers size={18} />}
          title="User directory API not available yet"
          description="The control plane does not expose GET /api/users in this build. Users are created today via the identity provider during provisioning; the directory endpoint (contract: spec 001 AC-ORG-002, invite via POST /api/users → job) is queued as a development task. This page activates automatically once it ships — no placeholder data is shown."
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
            </tr>
          </thead>
          <tbody>
            {directory.data!.users.map((u) => (
              <tr key={u.user_id} className="hover:bg-surface-alt/50">
                <Td>
                  <p className="font-medium">{u.display_name || u.email}</p>
                  <p className="text-[11px] text-muted">{u.email}</p>
                </Td>
                <Td className="font-mono text-xs text-muted">{u.department_id ?? "—"}</Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    {u.roles.map((r) => (
                      <Badge key={`${r.role_key}-${r.department_id ?? ""}`} tone="accent">
                        {r.role_key}
                      </Badge>
                    ))}
                  </div>
                </Td>
                <Td>
                  <Badge tone={u.status === "active" ? "success" : "neutral"}>{u.status}</Badge>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <CardBody className="border-t border-border-subtle bg-surface-alt/40">
        <p className="text-[11px] leading-relaxed text-muted">
          <strong className="text-foreground">Pending contract</strong> —
          <code className="font-mono"> GET /api/users</code> (directory),
          <code className="font-mono"> POST /api/users</code> (invite → job
          queue), role assignment endpoints. Defined in the platform spec
          (REQ-ORG-002/003); tracked as a control-plane development task.
        </p>
      </CardBody>
    </Card>
  );
}

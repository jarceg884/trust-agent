"use client";

/**
 * Vista "Policies" del builder (spec 041, REQ-041-012 / D-41-10) — la vista
 * por DEFECTO de /policies.
 *
 * - Lista de políticas nombradas reales (GET /api/named-policies): tarjetas
 *   con nombre, descripción, badge Template (source_template), nº de
 *   asignaciones y badge "Outdated" si alguna asignación stale (D-41-06).
 * - "+ New policy": en blanco (editor draft) o "from template" (POST con
 *   template_slug — copia la definition de la plantilla, D-41-08).
 * - Buscador por nombre/descripción.
 * - Borrado con estados reales: 409 (tiene asignaciones) → diálogo con
 *   "Unapply everywhere and delete" vía cascade=unassign (D-41-07).
 *
 * Cero simulación (REQ-041-016): todo proviene de la API; grupos y
 * capacidades del editor salen del bloque `builder` (D-41-02).
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import type { UseApiState } from "@/hooks/use-api";
import { ApiError, api } from "@/lib/api-client";
import type { DirectoryList, ModelList, PolicyMatrix, WhoAmI } from "@/lib/types";
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
  Skeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import { IconPlus, IconSearch } from "@/components/icons";
import { FileCheck2, Trash2 } from "lucide-react";
import type { NamedPolicy, NamedPolicyList } from "./types";
import {
  recipientLabel,
  type ConnectorCatalogList,
  type DynamicSources,
  type VaultConnectionList,
} from "./present";
import { PolicyEditor } from "./policy-editor";
import { t } from "../readable/presentation";

export function PolicyBuilder({
  me,
  namedPolicies,
  matrix,
  directory,
}: {
  me: WhoAmI;
  namedPolicies: UseApiState<NamedPolicyList>;
  matrix: UseApiState<PolicyMatrix>;
  directory: UseApiState<DirectoryList>;
}) {
  const { push } = useToast();
  // Ítems dinámicos de los controles (D-36-05, mismos endpoints que 036).
  const models = useApi<ModelList>("/api/models");
  const connectors = useApi<ConnectorCatalogList>("/api/connectors");
  const connections = useApi<VaultConnectionList>("/api/vault/connections");
  const sources: DynamicSources = {
    models: models.data,
    connectors: connectors.data,
    connections: connections.data,
  };

  const [query, setQuery] = useState("");
  // El editor captura la policy + el bloque builder AL ABRIRLO: así un
  // refetch de la lista (tras cada save/assign) jamás desmonta el editor
  // ni resetea su estado — el refresh es invisible hasta volver.
  const [editing, setEditing] = useState<{
    policy: NamedPolicy;
    builder: NamedPolicyList["builder"];
  } | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<NamedPolicy | null>(null);
  const [deleteConflict, setDeleteConflict] = useState<
    { level: string; department_id: string | null; user_id: string | null }[] | null
  >(null);
  const [deleting, setDeleting] = useState(false);

  function refreshAll() {
    namedPolicies.refetch();
    matrix.refetch();
  }

  async function requestDelete(policy: NamedPolicy, cascade = false) {
    setDeleting(true);
    try {
      await api(
        `/api/named-policies/${encodeURIComponent(policy.id)}${
          cascade ? "?cascade=unassign" : ""
        }`,
        { method: "DELETE" },
      );
      push(
        "success",
        cascade
          ? `Policy “${policy.name}” unapplied everywhere and deleted.`
          : `Policy “${policy.name}” deleted.`,
      );
      setDeleteTarget(null);
      setDeleteConflict(null);
      refreshAll();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        // D-41-07: destinatarios reales del envelope → decisión consciente.
        const assignments = (cause.payload?.assignments ?? null) as
          | { level: string; department_id: string | null; user_id: string | null }[]
          | null;
        setDeleteConflict(
          assignments ?? [
            { level: "unknown", department_id: null, user_id: null },
          ],
        );
      } else {
        push(
          "error",
          cause instanceof ApiError ? cause.message : "Delete failed. Try again.",
        );
      }
    } finally {
      setDeleting(false);
    }
  }

  // --- Estados de carga/error reales (solo la lista; el editor vive
  //     con su snapshot de policy + builder) ------------------------------

  if (editing) {
    return (
      <PolicyEditor
        me={me}
        policy={editing.policy}
        builder={editing.builder}
        matrix={matrix}
        directory={directory}
        sources={sources}
        onExit={() => {
          setEditing(null);
          namedPolicies.refetch();
        }}
        onChanged={namedPolicies.refetch}
        onMaterialized={refreshAll}
      />
    );
  }

  if (namedPolicies.loading) {
    return (
      <Card>
        <CardHeader title="Policies" description="Loading named policies…" />
        <CardBody className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-36 rounded-xl" />
          ))}
        </CardBody>
      </Card>
    );
  }
  if (namedPolicies.error) {
    return (
      <Card>
        <CardHeader title="Policies" />
        {namedPolicies.error.status === 403 ? (
          <EmptyState
            title="Only policy admins view this"
            description="Your role does not include the policies.org.manage or policies.dept.manage permissions."
          />
        ) : (
          <ErrorState
            error={namedPolicies.error}
            onRetry={namedPolicies.refetch}
            context="GET /api/named-policies"
          />
        )}
      </Card>
    );
  }

  const data = namedPolicies.data!;

  // --- Lista ---------------------------------------------------------------
  const q = query.trim().toLowerCase();
  const filtered = data.policies.filter(
    (p) =>
      !q ||
      p.name.toLowerCase().includes(q) ||
      (p.description ?? "").toLowerCase().includes(q),
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-64 flex-1 sm:max-w-sm">
          <IconSearch
            size={14}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
          />
          <label htmlFor="policy-builder-search" className="sr-only">
            Search policies
          </label>
          <Input
            id="policy-builder-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name or description…"
            className="pl-8"
          />
        </div>
        <Button variant="primary" onClick={() => setNewOpen(true)}>
          <IconPlus size={14} />
          New policy
        </Button>
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          icon={<FileCheck2 size={20} strokeWidth={1.75} />}
          title={q ? "No policies match your search" : "No policies yet"}
          description={
            q
              ? "Try another word — the search covers policy names and descriptions."
              : "Create a named policy, enable capabilities from the sidebar, and assign it to the organization, a department or a user."
          }
          action={
            <Button variant="primary" size="sm" onClick={() => setNewOpen(true)}>
              <IconPlus size={14} />
              New policy
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((p) => (
            <Card key={p.id} className="transition-shadow hover:shadow-md">
              {/* La tarjeta entera abre el editor; el botón de borrar
                  detiene la propagación (accesible por teclado). */}
              <button
                type="button"
                onClick={() => setEditing({ policy: p, builder: data.builder })}
                aria-label={`Edit policy ${p.name}`}
                className="w-full rounded-t-xl text-left"
              >
                <CardBody className="space-y-2">
                  <h3 className="text-sm font-semibold text-foreground">
                    {p.name}
                  </h3>
                  {p.description ? (
                    <p className="line-clamp-2 text-xs leading-4 text-muted">
                      {p.description}
                    </p>
                  ) : null}
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {p.source_template ? (
                      <Badge tone="accent">Template</Badge>
                    ) : null}
                    <Badge>
                      {p.assignments_count}{" "}
                      {p.assignments_count === 1
                        ? "assignment"
                        : "assignments"}
                    </Badge>
                    {p.stale_assignments ? (
                      <Badge tone="warning">Outdated</Badge>
                    ) : null}
                  </div>
                </CardBody>
              </button>
              <div className="flex justify-end border-t border-border-subtle px-3 py-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs text-muted hover:text-danger"
                  aria-label={`Delete policy ${p.name}`}
                  onClick={() => {
                    setDeleteConflict(null);
                    setDeleteTarget(p);
                  }}
                >
                  <Trash2 size={13} aria-hidden="true" />
                  Delete
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* --- Nueva política: en blanco o desde plantilla (D-41-08) -------- */}
      {newOpen ? (
        <NewPolicyDialog
          open={newOpen}
          templates={data.builder.templates}
          onClose={() => setNewOpen(false)}
          onCreated={(policy) => {
            setNewOpen(false);
            namedPolicies.refetch();
            setEditing({ policy, builder: data.builder });
          }}
        />
      ) : null}

      {/* --- Borrado: directo o con 409 → cascade (D-41-07) ---------------- */}
      {deleteTarget && !deleteConflict ? (
        <ConfirmDialog
          open
          onClose={() => setDeleteTarget(null)}
          onConfirm={() => requestDelete(deleteTarget)}
          loading={deleting}
          title={`Delete “${deleteTarget.name}”?`}
          message="This permanently deletes the named policy. Policies already materialized on recipients stay in place unless you unassign them first."
          confirmLabel="Delete policy"
        />
      ) : null}
      {deleteTarget && deleteConflict ? (
        <Dialog
          open
          onClose={() => {
            setDeleteTarget(null);
            setDeleteConflict(null);
          }}
          title={`“${deleteTarget.name}” has active assignments`}
          description="Deleting it would leave its policy keys materialized with no way to unassign them from here. Choose explicitly:"
          footer={
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setDeleteTarget(null);
                  setDeleteConflict(null);
                }}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                size="sm"
                loading={deleting}
                onClick={() => requestDelete(deleteTarget, true)}
              >
                Unapply everywhere and delete
              </Button>
            </>
          }
        >
          <ul className="space-y-1">
            {deleteConflict.map((a, i) => (
              <li key={i} className="text-xs text-muted">
                {recipientLabel(a, matrix.data, directory.data)}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted">
            “Unapply everywhere and delete” removes the keys this policy wrote
            on each recipient (manual edits made afterwards are preserved) and
            then deletes the policy.
          </p>
        </Dialog>
      ) : null}
    </div>
  );
}

/** Diálogo "+ New policy": en blanco (draft en el editor) o desde una
 *  plantilla del contrato (POST template_slug copia la definition). */
function NewPolicyDialog({
  open,
  templates,
  onClose,
  onCreated,
}: {
  open: boolean;
  templates: NamedPolicyList["builder"]["templates"];
  onClose: () => void;
  onCreated: (policy: NamedPolicy) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [templateSlug, setTemplateSlug] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    if (!name.trim()) {
      setError("Policy name is required.");
      return;
    }
    if (!templateSlug) {
      // En blanco: el editor arranca como draft y hace el POST al guardar
      // (una policy vacía no es válida: exige ≥1 capacidad).
      onCreated({
        id: "",
        name: name.trim(),
        description: description.trim() || null,
        definition: {},
        source_template: null,
        created_at: "",
        updated_at: "",
        assignments: [],
        assignments_count: 0,
        stale_assignments: false,
      });
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const policy = await api<NamedPolicy>("/api/named-policies", {
        method: "POST",
        body: {
          name: name.trim(),
          description: description.trim() || null,
          template_slug: templateSlug,
        },
      });
      onCreated(policy);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Create failed. Try again.",
      );
    } finally {
      setCreating(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New policy"
      description="Start from scratch or copy one of the pre-loaded templates — everything stays editable afterwards."
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" loading={creating} onClick={create}>
            {templateSlug ? "Create from template" : "Start editing"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Name" htmlFor="new-policy-name" error={error}>
          <Input
            id="new-policy-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Interns policy"
          />
        </Field>
        <Field label="Description" htmlFor="new-policy-description">
          <Textarea
            id="new-policy-description"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What this policy is for"
          />
        </Field>
        <Field
          label="Start from"
          htmlFor="new-policy-template"
          hint={
            templateSlug
              ? "The template's definition is copied into your new policy — the template itself stays untouched."
              : "A blank policy opens the editor; you save it once it has at least one capability enabled."
          }
        >
          <Select
            id="new-policy-template"
            value={templateSlug}
            onChange={(e) => setTemplateSlug(e.target.value)}
          >
            <option value="">Blank policy</option>
            {templates.map((tpl) => (
              <option key={tpl.slug} value={tpl.slug}>
                {t(tpl.name)} — {t(tpl.description)}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Dialog>
  );
}

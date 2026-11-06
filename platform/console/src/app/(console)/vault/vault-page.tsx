"use client";

/** Vault — secretos de la organización en carpetas (estilo Infisical). */
import { useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api-client";
import { useApi } from "@/hooks/use-api";
import {
  Badge,
  Button,
  Card,
  CardHeader,
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

interface VaultCredential {
  id: string;
  name: string;
  credential_type: string;
  connector_slug: string | null;
  scope: string | null;
  folder_id: string | null;
  status: string;
  created_at: string | null;
  last_rotated_at: string | null;
}
interface VaultList { credentials: VaultCredential[] }

interface VaultFolder {
  id: string;
  parent_folder_id: string | null;
  name: string;
}
interface FolderList { folders: VaultFolder[] }

const TYPES = [
  { value: "api_key", label: "API key" },
  { value: "password", label: "Password" },
  { value: "token", label: "Token" },
  { value: "oauth2", label: "OAuth2" },
];

function folderPath(folders: VaultFolder[], id: string | null): string {
  const byId = new Map(folders.map((f) => [f.id, f]));
  const parts: string[] = [];
  let cur = id ? byId.get(id) : undefined;
  while (cur) {
    parts.unshift(cur.name);
    cur = cur.parent_folder_id ? byId.get(cur.parent_folder_id) : undefined;
  }
  return parts.length ? parts.join(" / ") : "Raíz";
}

export function VaultPage() {
  const credentials = useApi<VaultList>("/api/vault/credentials");
  const folders = useApi<FolderList>("/api/vault/folders");
  const { push } = useToast();
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("api_key");
  const [value, setValue] = useState("");
  const [scope, setScope] = useState("");
  const [saving, setSaving] = useState(false);
  const [oneTimeSecret, setOneTimeSecret] = useState<{ name: string; value: string } | null>(null);
  const [rotateFor, setRotateFor] = useState<VaultCredential | null>(null);
  const [rotateValue, setRotateValue] = useState("");
  const [folderDialogOpen, setFolderDialogOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [creatingFolder, setCreatingFolder] = useState(false);

  const allFolders = folders.data?.folders ?? [];
  const childFolders = allFolders.filter((f) => f.parent_folder_id === currentFolderId);
  const visibleCredentials = (credentials.data?.credentials ?? []).filter(
    (c) => c.folder_id === currentFolderId,
  );
  const breadcrumb = useMemo(() => {
    const byId = new Map(allFolders.map((f) => [f.id, f]));
    const trail: VaultFolder[] = [];
    let cur = currentFolderId ? byId.get(currentFolderId) : undefined;
    while (cur) {
      trail.unshift(cur);
      cur = cur.parent_folder_id ? byId.get(cur.parent_folder_id) : undefined;
    }
    return trail;
  }, [allFolders, currentFolderId]);

  async function create() {
    if (!name.trim() || !value) {
      push("error", "Nombre y valor son obligatorios.");
      return;
    }
    setSaving(true);
    try {
      const result = await api<{ name: string; secret_value?: string }>("/api/vault/credentials", {
        method: "POST",
        body: { name, credential_type: type, secret_value: value, scope: scope || null, folder_id: currentFolderId },
      });
      setCreateOpen(false);
      setOneTimeSecret({ name: result.name || name, value: result.secret_value || value });
      setName("");
      setValue("");
      setScope("");
      credentials.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo crear el secreto.");
    } finally {
      setSaving(false);
    }
  }

  async function createFolder() {
    if (!folderName.trim()) return;
    setCreatingFolder(true);
    try {
      await api("/api/vault/folders", {
        method: "POST",
        body: { name: folderName, parent_folder_id: currentFolderId },
      });
      push("success", "Carpeta creada.");
      setFolderDialogOpen(false);
      setFolderName("");
      folders.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo crear la carpeta.");
    } finally {
      setCreatingFolder(false);
    }
  }

  async function deleteFolder(folder: VaultFolder) {
    if (!window.confirm(`¿Eliminar la carpeta "${folder.name}"? Debe estar vacía.`)) return;
    try {
      await api(`/api/vault/folders/${folder.id}`, { method: "DELETE" });
      push("success", "Carpeta eliminada.");
      folders.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "La carpeta no está vacía.");
    }
  }

  async function moveCredential(credential: VaultCredential, folderId: string | null) {
    try {
      await api(`/api/vault/credentials/${credential.id}/move`, {
        method: "POST",
        body: { folder_id: folderId },
      });
      push("success", `Movido a ${folderPath(allFolders, folderId)}.`);
      credentials.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo mover el secreto.");
    }
  }

  async function rotate() {
    if (!rotateFor || !rotateValue) return;
    try {
      const result = await api<{ name: string; secret_value?: string }>(
        `/api/vault/credentials/${rotateFor.id}/rotate`,
        { method: "POST", body: { new_secret_value: rotateValue } },
      );
      setRotateFor(null);
      setRotateValue("");
      setOneTimeSecret({ name: result.name || rotateFor.name, value: result.secret_value || rotateValue });
      credentials.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo rotar.");
    }
  }

  async function revoke(credential: VaultCredential) {
    if (!window.confirm(`¿Revocar el secreto "${credential.name}"?`)) return;
    try {
      await api(`/api/vault/credentials/${credential.id}/revoke`, { method: "POST" });
      push("success", "Secreto revocado.");
      credentials.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo revocar.");
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Secretos"
          description="Credenciales cifradas en carpetas, como un gestor estilo Infisical. El acceso a cada secreto se concede por política, uno por uno."
          actions={
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" onClick={() => setFolderDialogOpen(true)}>
                + Carpeta
              </Button>
              <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
                + Nuevo secreto
              </Button>
            </div>
          }
        />
        <div className="flex flex-wrap items-center gap-1 border-b border-border-subtle px-4 py-2 text-xs">
          <button className="font-medium text-accent hover:underline" onClick={() => setCurrentFolderId(null)}>
            Raíz
          </button>
          {breadcrumb.map((f) => (
            <span key={f.id} className="flex items-center gap-1">
              <span className="text-faint">/</span>
              <button className="font-medium text-accent hover:underline" onClick={() => setCurrentFolderId(f.id)}>
                {f.name}
              </button>
            </span>
          ))}
        </div>

        {childFolders.length > 0 ? (
          <div className="grid gap-2 border-b border-border-subtle p-4 sm:grid-cols-2 md:grid-cols-3">
            {childFolders.map((folder) => (
              <div
                key={folder.id}
                className="flex items-center justify-between gap-2 rounded-lg border border-border-subtle px-3 py-2 text-sm"
              >
                <button className="flex items-center gap-2 font-medium" onClick={() => setCurrentFolderId(folder.id)}>
                  📁 {folder.name}
                </button>
                <Button variant="ghost" size="sm" onClick={() => void deleteFolder(folder)}>
                  Eliminar
                </Button>
              </div>
            ))}
          </div>
        ) : null}

        {credentials.loading || folders.loading ? (
          <TableSkeleton rows={4} cols={6} />
        ) : credentials.error ? (
          <ErrorState error={credentials.error} onRetry={credentials.refetch} context="GET /api/vault/credentials" />
        ) : visibleCredentials.length === 0 ? (
          <EmptyState
            title="No hay secretos en esta carpeta"
            description="Crea el primer secreto aquí y luego permite su uso desde una política (acceso por secreto, no por todo el vault)."
            action={<Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>Crear secreto</Button>}
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Nombre</Th>
                <Th>Tipo</Th>
                <Th>Scope</Th>
                <Th>Estado</Th>
                <Th>Carpeta</Th>
                <Th className="w-48" />
              </tr>
            </thead>
            <tbody>
              {visibleCredentials.map((credential) => (
                <tr key={credential.id} className="hover:bg-surface-alt/50">
                  <Td className="font-medium">{credential.name}</Td>
                  <Td className="text-xs">{credential.credential_type}</Td>
                  <Td className="text-xs">{credential.scope || "—"}</Td>
                  <Td><Badge tone={credential.status === "active" ? "success" : "neutral"}>{credential.status}</Badge></Td>
                  <Td>
                    <Select
                      value={credential.folder_id ?? ""}
                      onChange={(e) => void moveCredential(credential, e.target.value || null)}
                      className="text-xs"
                    >
                      <option value="">Raíz</option>
                      {allFolders.map((f) => (
                        <option key={f.id} value={f.id}>{folderPath(allFolders, f.id)}</option>
                      ))}
                    </Select>
                  </Td>
                  <Td>
                    <div className="flex justify-end gap-1.5">
                      <Button variant="secondary" size="sm" onClick={() => { setRotateFor(credential); setRotateValue(""); }}>Rotar</Button>
                      <Button variant="ghost" size="sm" onClick={() => void revoke(credential)}>Revocar</Button>
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Dialog
        open={folderDialogOpen}
        onClose={() => setFolderDialogOpen(false)}
        title="Nueva carpeta"
        description={`Se creará dentro de: ${folderPath(allFolders, currentFolderId)}`}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setFolderDialogOpen(false)}>Cancelar</Button>
            <Button variant="primary" size="sm" loading={creatingFolder} onClick={() => void createFolder()}>Crear carpeta</Button>
          </div>
        }
      >
        <Field label="Nombre" htmlFor="folder-name">
          <Input id="folder-name" value={folderName} onChange={(e) => setFolderName(e.target.value)} placeholder="Producción" />
        </Field>
      </Dialog>

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Nuevo secreto"
        description={`Se cifra en el vault (carpeta: ${folderPath(allFolders, currentFolderId)}) y solo se muestra una vez.`}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setCreateOpen(false)}>Cancelar</Button>
            <Button variant="primary" size="sm" loading={saving} onClick={() => void create()}>Guardar secreto</Button>
          </div>
        }
      >
        <div className="space-y-4">
          <Field label="Nombre" htmlFor="vault-name">
            <Input id="vault-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="OpenAI API key" />
          </Field>
          <Field label="Tipo" htmlFor="vault-type">
            <Select id="vault-type" value={type} onChange={(e) => setType(e.target.value)}>
              {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </Select>
          </Field>
          <Field label="Valor" htmlFor="vault-value">
            <Input id="vault-value" type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder="••••••••" autoComplete="new-password" />
          </Field>
          <Field label="Scope (opcional)" htmlFor="vault-scope">
            <Input id="vault-scope" value={scope} onChange={(e) => setScope(e.target.value)} placeholder="producción / equipo / agente" />
          </Field>
        </div>
      </Dialog>

      <Dialog
        open={Boolean(rotateFor)}
        onClose={() => setRotateFor(null)}
        title="Rotar secreto"
        description={rotateFor?.name}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setRotateFor(null)}>Cancelar</Button>
            <Button variant="primary" size="sm" disabled={!rotateValue} onClick={() => void rotate()}>Rotar</Button>
          </div>
        }
      >
        <Field label="Nuevo valor" htmlFor="rotate-value">
          <Input id="rotate-value" type="password" value={rotateValue} onChange={(e) => setRotateValue(e.target.value)} autoComplete="new-password" />
        </Field>
      </Dialog>

      <Dialog
        open={Boolean(oneTimeSecret)}
        onClose={() => setOneTimeSecret(null)}
        title="Guarda este secreto"
        description="No se volverá a mostrar."
        footer={<Button variant="primary" size="sm" onClick={() => setOneTimeSecret(null)}>Entendido</Button>}
      >
        <div className="space-y-2">
          <p className="text-xs text-muted">{oneTimeSecret?.name}</p>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-border-subtle bg-surface p-3 font-mono text-[11px]">
            {oneTimeSecret?.value}
          </pre>
        </div>
      </Dialog>
    </div>
  );
}

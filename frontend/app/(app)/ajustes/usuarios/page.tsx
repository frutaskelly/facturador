"use client";

import { useMemo, useState } from "react";
import { Building2, KeyRound, Plus, Trash2, UserCog } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { EmptyState } from "@/components/ui/EmptyState";
import { Checkbox, Field, Input, PasswordInput, Select, Switch } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useMutation, useResource, type Page } from "@/lib/hooks";
import type { Cliente, Membership, Role } from "@/lib/types";

const WRITE = "membership:gestionar";

// Fila de GET /memberships/{id}/empresas: en qué empresas del grupo puede
// entrar el usuario y con qué rol.
type EmpresaAcceso = {
  tenant_id: string;
  nombre: string;
  rfc: string | null;
  es_actual: boolean;
  puedo_administrar: boolean;
  tiene_acceso: boolean;
  membership_id: string | null;
  role_id: string | null;
  role_nombre: string | null;
};

export default function UsuariosPage() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);
  const { post, patch, put, del } = useMutation();

  const membersRes = useResource<Membership[]>("/api/v1/memberships");
  const rolesRes = useResource<Role[]>("/api/v1/roles");
  const members = membersRes.data ?? [];
  const roles = rolesRes.data ?? [];
  // El catálogo de clientes solo se pide cuando algún modal lo necesita
  // (limitar alcance): 500 filas gratis en cada visita a Usuarios sería tirar red.
  const [clientesNeeded, setClientesNeeded] = useState(false);
  const clientesRes = useResource<Page<Cliente>>(clientesNeeded ? "/api/v1/clientes?limit=500" : null);
  const clientes = clientesRes.data?.items ?? [];
  // Solo el OWNER puede otorgar OWNER o tocar la membresía de un OWNER (el
  // backend lo exige). Reflejarlo en la UI para no ofrecer acciones que darán 403.
  const isOwner = !!me?.active_tenant.is_owner;
  const roleOptions = roles.filter((r) => isOwner || !(r.es_preset && r.nombre === "OWNER"));
  const rowLocked = (m: Membership) => m.role_nombre === "OWNER" && !isOwner;

  const [busyId, setBusyId] = useState<string | null>(null);
  const [toRemove, setToRemove] = useState<Membership | null>(null);

  // Crear usuario
  const [createOpen, setCreateOpen] = useState(false);
  const [cEmail, setCEmail] = useState("");
  const [cName, setCName] = useState("");
  const [cRole, setCRole] = useState("");
  const [cPass, setCPass] = useState("");
  const [cScope, setCScope] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);

  // Cambiar contraseña
  const [pwdFor, setPwdFor] = useState<Membership | null>(null);
  const [newPass, setNewPass] = useState("");
  const [savingPwd, setSavingPwd] = useState(false);

  // Limitar a clientes (alcance de la membresía)
  const [scopeFor, setScopeFor] = useState<Membership | null>(null);
  const [scopeSel, setScopeSel] = useState<Set<string>>(new Set());
  const [savingScope, setSavingScope] = useState(false);

  // Acceso a otras empresas del grupo
  const [empresasFor, setEmpresasFor] = useState<Membership | null>(null);
  const [empresas, setEmpresas] = useState<EmpresaAcceso[] | null>(null);
  const [empresaBusy, setEmpresaBusy] = useState<string | null>(null); // tenant_id en vuelo

  const isSelf = (m: Membership) => m.user_id === me?.user_id;

  function openCreate() {
    setCEmail("");
    setCName("");
    setCRole(roleOptions[0]?.id ?? "");
    setCPass("");
    setCScope(new Set());
    setClientesNeeded(true);
    setCreateOpen(true);
  }

  const createValid =
    cEmail.trim().length >= 3 && cEmail.includes("@") && cRole !== "" && cPass.length >= 8;

  async function submitCreate() {
    if (!createValid) return;
    setCreating(true);
    try {
      await post("/api/v1/memberships/usuarios", {
        email: cEmail.trim(),
        full_name: cName.trim() || null,
        password: cPass,
        role_id: cRole,
        // Solo se manda cuando el alta ES limitada; undefined no viaja en el JSON.
        cliente_scope: cScope.size ? [...cScope] : undefined,
      });
      toast.success("Usuario creado");
      setCreateOpen(false);
      membersRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear el usuario");
    } finally {
      setCreating(false);
    }
  }

  function openScope(m: Membership) {
    setScopeSel(new Set(m.cliente_scope ?? []));
    setClientesNeeded(true);
    setScopeFor(m);
  }

  async function saveScope() {
    if (!scopeFor) return;
    setSavingScope(true);
    try {
      // Reemplazo completo: [] y null significan "sin límite" para el backend.
      await patch(`/api/v1/memberships/${scopeFor.id}`, {
        cliente_scope: scopeSel.size ? [...scopeSel] : null,
      });
      toast.success("Alcance actualizado");
      setScopeFor(null);
      membersRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo actualizar el alcance");
    } finally {
      setSavingScope(false);
    }
  }

  async function loadEmpresas(membershipId: string) {
    try {
      const r = await apiFetch<{ user_email: string; empresas: EmpresaAcceso[] }>(
        `/api/v1/memberships/${membershipId}/empresas`,
      );
      setEmpresas(r.empresas);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudieron cargar las empresas");
      setEmpresasFor(null);
    }
  }

  function openEmpresas(m: Membership) {
    setEmpresas(null);
    setEmpresasFor(m);
    void loadEmpresas(m.id);
  }

  async function updateEmpresa(e: EmpresaAcceso, acceso: boolean, roleId?: string) {
    if (!empresasFor) return;
    setEmpresaBusy(e.tenant_id);
    try {
      await put(`/api/v1/memberships/${empresasFor.id}/empresas`, {
        tenant_id: e.tenant_id,
        acceso,
        // Sin rol elegido no se manda: el backend asigna el default de la empresa.
        role_id: roleId ?? e.role_id ?? undefined,
      });
      await loadEmpresas(empresasFor.id);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "No se pudo actualizar el acceso");
    } finally {
      setEmpresaBusy(null);
    }
  }

  function openPwd(m: Membership) {
    setNewPass("");
    setPwdFor(m);
  }

  async function submitPwd() {
    if (!pwdFor || newPass.length < 8) return;
    setSavingPwd(true);
    try {
      await post(`/api/v1/memberships/${pwdFor.id}/password`, { password: newPass });
      toast.success("Contraseña actualizada");
      setPwdFor(null);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cambiar la contraseña");
    } finally {
      setSavingPwd(false);
    }
  }

  async function changeRole(m: Membership, roleId: string) {
    if (roleId === m.role_id) return;
    setBusyId(m.id);
    try {
      await patch(`/api/v1/memberships/${m.id}`, { role_id: roleId });
      toast.success("Rol actualizado");
      membersRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cambiar el rol");
    } finally {
      setBusyId(null);
    }
  }

  async function toggleActive(m: Membership, active: boolean) {
    setBusyId(m.id);
    try {
      await patch(`/api/v1/memberships/${m.id}`, { active });
      toast.success(active ? "Usuario activado" : "Usuario desactivado");
      membersRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo actualizar");
    } finally {
      setBusyId(null);
    }
  }

  async function confirmRemove() {
    if (!toRemove) return;
    setBusyId(toRemove.id);
    try {
      await del(`/api/v1/memberships/${toRemove.id}`);
      toast.success("Usuario removido del inquilino");
      setToRemove(null);
      membersRes.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo remover");
    } finally {
      setBusyId(null);
    }
  }

  const columns: Column<Membership>[] = [
    {
      header: "Usuario",
      cell: (m) => (
        <div>
          <div className="font-medium">{m.user_full_name || m.user_email}</div>
          {m.user_full_name && <div className="text-xs text-muted">{m.user_email}</div>}
        </div>
      ),
    },
    {
      header: "Rol",
      cell: (m) =>
        canWrite && !isSelf(m) && !rowLocked(m) ? (
          <Select
            value={m.role_id}
            disabled={busyId === m.id}
            onChange={(e) => changeRole(m, e.target.value)}
            className="max-w-[14rem]"
          >
            {roleOptions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.nombre}
                {r.es_preset ? "" : " (personalizado)"}
              </option>
            ))}
          </Select>
        ) : (
          <span className="flex items-center gap-2">
            {m.role_nombre}
            {isSelf(m) && <Badge tone="muted">tú</Badge>}
          </span>
        ),
    },
    {
      header: "Alcance",
      cell: (m) => {
        const n = m.cliente_scope?.length ?? 0;
        const badge =
          n === 0 ? (
            <Badge tone="muted">Todos los clientes</Badge>
          ) : (
            <Badge>
              {n} cliente{n === 1 ? "" : "s"}
            </Badge>
          );
        return canWrite && !isSelf(m) && !rowLocked(m) ? (
          <button onClick={() => openScope(m)} title="Limitar a clientes" className="cursor-pointer">
            {badge}
          </button>
        ) : (
          badge
        );
      },
    },
    {
      header: "Estado",
      cell: (m) =>
        canWrite && !isSelf(m) && !rowLocked(m) ? (
          <div className="flex items-center gap-2">
            <Switch checked={m.active} onChange={(v) => toggleActive(m, v)} />
            <span className="text-sm text-muted">{m.active ? "Activo" : "Inactivo"}</span>
          </div>
        ) : (
          <Badge tone={m.active ? "success" : "muted"}>{m.active ? "Activo" : "Inactivo"}</Badge>
        ),
    },
    {
      header: "",
      className: "text-right w-1",
      cell: (m) =>
        canWrite && !isSelf(m) ? (
          <div className="flex items-center justify-end gap-1">
            <button
              onClick={() => openEmpresas(m)}
              disabled={busyId === m.id}
              className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
              aria-label="Empresas"
              title="Acceso a empresas del grupo"
            >
              <Building2 size={16} />
            </button>
            {!rowLocked(m) && (
              <>
                <button
                  onClick={() => openPwd(m)}
                  disabled={busyId === m.id}
                  className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
                  aria-label="Cambiar contraseña"
                  title="Cambiar contraseña"
                >
                  <KeyRound size={16} />
                </button>
                <button
                  onClick={() => setToRemove(m)}
                  disabled={busyId === m.id}
                  className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
                  aria-label="Remover"
                >
                  <Trash2 size={16} />
                </button>
              </>
            )}
          </div>
        ) : null,
    },
  ];

  return (
    <div>
      <PageHeader
        title="Usuarios"
        subtitle="Asigna roles a los miembros de tu empresa, actívalos o remuévelos."
        actions={
          canWrite ? (
            <Button onClick={openCreate}>
              <Plus size={16} /> Crear usuario
            </Button>
          ) : undefined
        }
      />

      {members.length === 0 && !membersRes.loading ? (
        <EmptyState
          icon={<UserCog size={28} />}
          title="Aún no hay otros usuarios"
          hint="El alta de nuevos usuarios se realiza durante el aprovisionamiento."
        />
      ) : (
        <DataTableSmart
          columns={columns}
          rows={members}
          loading={membersRes.loading}
          error={membersRes.error}
          empty="Sin usuarios"
          storageKey="usuarios"
        />
      )}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Crear usuario"
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              Cancelar
            </Button>
            <Button onClick={submitCreate} disabled={creating || !createValid}>
              {creating ? "Creando…" : "Crear"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Correo" required>
            <Input
              type="email"
              value={cEmail}
              onChange={(e) => setCEmail(e.target.value)}
              placeholder="usuario@empresa.com"
            />
          </Field>
          <Field label="Nombre">
            <Input value={cName} onChange={(e) => setCName(e.target.value)} placeholder="Nombre completo" />
          </Field>
          <Field label="Rol" required>
            <Select value={cRole} onChange={(e) => setCRole(e.target.value)}>
              {roleOptions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.nombre}
                  {r.es_preset ? "" : " (personalizado)"}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Contraseña" required hint="Mínimo 8 caracteres">
            <PasswordInput
              value={cPass}
              onChange={(e) => setCPass(e.target.value)}
              autoComplete="new-password"
            />
          </Field>
          <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm font-medium">
              Acceso limitado (portal de cliente)
            </summary>
            <div className="mt-3 space-y-2">
              <p className="text-xs text-muted">
                Sin selección = ve todos los clientes. Con selección, el usuario SOLO ve remisiones,
                facturas, precios y órdenes de esos clientes.
              </p>
              <ClienteScopePicker
                clientes={clientes}
                loading={clientesRes.loading}
                selected={cScope}
                onToggle={(id) =>
                  setCScope((prev) => {
                    const next = new Set(prev);
                    if (next.has(id)) next.delete(id);
                    else next.add(id);
                    return next;
                  })
                }
              />
            </div>
          </details>
        </div>
      </Modal>

      {/* ── Limitar la membresía a ciertos clientes ── */}
      <Modal
        open={scopeFor !== null}
        onClose={() => setScopeFor(null)}
        title="Limitar a clientes"
        footer={
          <>
            <Button variant="secondary" onClick={() => setScopeFor(null)}>
              Cancelar
            </Button>
            <Button onClick={() => void saveScope()} disabled={savingScope}>
              {savingScope ? "Guardando…" : "Guardar"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-sm text-muted">{scopeFor?.user_full_name || scopeFor?.user_email}</p>
          <p className="text-sm">
            Sin selección = ve todos los clientes. Con selección, el usuario SOLO ve remisiones,
            facturas, precios y órdenes de esos clientes.
          </p>
          <ClienteScopePicker
            clientes={clientes}
            loading={clientesRes.loading}
            selected={scopeSel}
            onToggle={(id) =>
              setScopeSel((prev) => {
                const next = new Set(prev);
                if (next.has(id)) next.delete(id);
                else next.add(id);
                return next;
              })
            }
          />
        </div>
      </Modal>

      {/* ── Acceso a otras empresas del grupo ── */}
      <Modal
        open={empresasFor !== null}
        onClose={() => setEmpresasFor(null)}
        title={`Empresas: ${empresasFor?.user_full_name || empresasFor?.user_email || ""}`}
        footer={
          <Button variant="secondary" onClick={() => setEmpresasFor(null)}>
            Cerrar
          </Button>
        }
      >
        {empresas === null ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-muted">
              Marca en qué empresas del grupo puede entrar este usuario y con qué rol.
            </p>
            {empresas.map((e) => (
              <div
                key={e.tenant_id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border px-3 py-2"
              >
                <div>
                  <div className="flex items-center gap-2 text-sm font-medium">
                    {e.nombre}
                    {e.es_actual && <Badge tone="muted">actual</Badge>}
                  </div>
                  <div className="text-xs text-muted">{e.rfc || "—"}</div>
                </div>
                <div className="flex items-center gap-3">
                  {e.tiene_acceso && (
                    <Select
                      value={e.role_id ?? ""}
                      disabled={!e.puedo_administrar || empresaBusy === e.tenant_id}
                      onChange={(ev) => void updateEmpresa(e, true, ev.target.value)}
                      className="max-w-[12rem]"
                    >
                      {/* El rol vigente puede no existir en ESTA empresa (los roles
                          se listan de la empresa actual); se muestra igual. */}
                      {e.role_id && !roleOptions.some((r) => r.id === e.role_id) && (
                        <option value={e.role_id}>{e.role_nombre ?? "Rol actual"}</option>
                      )}
                      {roleOptions.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.nombre}
                          {r.es_preset ? "" : " (personalizado)"}
                        </option>
                      ))}
                    </Select>
                  )}
                  <span title={e.puedo_administrar ? undefined : "No administras usuarios en esa empresa"}>
                    <Switch
                      checked={e.tiene_acceso}
                      disabled={!e.puedo_administrar || empresaBusy === e.tenant_id}
                      onChange={(v) => void updateEmpresa(e, v)}
                    />
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Modal>

      <Modal
        open={pwdFor !== null}
        onClose={() => setPwdFor(null)}
        title="Cambiar contraseña"
        footer={
          <>
            <Button variant="secondary" onClick={() => setPwdFor(null)}>
              Cancelar
            </Button>
            <Button onClick={submitPwd} disabled={savingPwd || newPass.length < 8}>
              {savingPwd ? "Guardando…" : "Guardar"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-sm text-muted">
            {pwdFor?.user_full_name || pwdFor?.user_email}
          </p>
          <Field label="Nueva contraseña" required hint="Mínimo 8 caracteres">
            <PasswordInput
              value={newPass}
              onChange={(e) => setNewPass(e.target.value)}
              autoComplete="new-password"
            />
          </Field>
        </div>
      </Modal>

      <ConfirmDialog
        open={toRemove !== null}
        title="Remover usuario"
        message={`¿Remover a "${toRemove?.user_email}" de esta empresa? Perderá acceso, pero puede volver a invitarse.`}
        onConfirm={confirmRemove}
        onClose={() => setToRemove(null)}
        loading={busyId === toRemove?.id}
      />
    </div>
  );
}

/** Lista de clientes con casillas y filtro local (el catálogo cabe completo). */
function ClienteScopePicker({
  clientes,
  loading,
  selected,
  onToggle,
}: {
  clientes: Cliente[];
  loading: boolean;
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return f ? clientes.filter((c) => c.legal_name.toLowerCase().includes(f)) : clientes;
  }, [clientes, filter]);

  if (loading && clientes.length === 0) {
    return (
      <div className="flex justify-center py-4">
        <Spinner />
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <Input
        placeholder="Filtrar clientes…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <div className="max-h-64 overflow-y-auto rounded-lg border border-border">
        {filtered.map((c) => (
          <label
            key={c.id}
            className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-surface-2"
          >
            <Checkbox checked={selected.has(c.id)} onChange={() => onToggle(c.id)} />
            <span>{c.legal_name}</span>
          </label>
        ))}
        {filtered.length === 0 && (
          <div className="px-3 py-2 text-sm text-muted">Sin coincidencias</div>
        )}
      </div>
      {selected.size > 0 && (
        <div className="text-xs text-muted">
          {selected.size} cliente{selected.size === 1 ? "" : "s"} seleccionado
          {selected.size === 1 ? "" : "s"}
        </div>
      )}
    </div>
  );
}

"use client";

import { useState } from "react";
import { KeyRound, Plus, Trash2, UserCog } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTableSmart, type Column } from "@/components/ui/DataTableSmart";
import { EmptyState } from "@/components/ui/EmptyState";
import { Field, Input, Select, Switch } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { useMutation, useResource } from "@/lib/hooks";
import type { Membership, Role } from "@/lib/types";

const WRITE = "membership:gestionar";

export default function UsuariosPage() {
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);
  const { post, patch, del } = useMutation();

  const membersRes = useResource<Membership[]>("/api/v1/memberships");
  const rolesRes = useResource<Role[]>("/api/v1/roles");
  const members = membersRes.data ?? [];
  const roles = rolesRes.data ?? [];
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
  const [creating, setCreating] = useState(false);

  // Cambiar contraseña
  const [pwdFor, setPwdFor] = useState<Membership | null>(null);
  const [newPass, setNewPass] = useState("");
  const [savingPwd, setSavingPwd] = useState(false);

  const isSelf = (m: Membership) => m.user_id === me?.user_id;

  function openCreate() {
    setCEmail("");
    setCName("");
    setCRole(roleOptions[0]?.id ?? "");
    setCPass("");
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
        canWrite && !isSelf(m) && !rowLocked(m) ? (
          <div className="flex items-center justify-end gap-1">
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
            <Input
              type="password"
              value={cPass}
              onChange={(e) => setCPass(e.target.value)}
              autoComplete="new-password"
            />
          </Field>
        </div>
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
            <Input
              type="password"
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

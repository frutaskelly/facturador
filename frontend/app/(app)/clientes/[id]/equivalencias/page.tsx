"use client";

// Equivalencias del cliente: cómo se llama ESTE cliente en los otros sistemas.
// Es lo que permite que una orden que llega por WhatsApp o correo se asigne sola
// —por el RFC impreso, el proyecto, el nombre dentro del PDF, la ubicación de
// entrega o el grupo del que llegó— sin que nadie lo capture.
//
// Regla: solo las CONFIRMADAS deciden. Lo que el bot propuso solo (SUGERIDA)
// espera aquí a que una persona lo apruebe; si no, un error se propagaría solo.
import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import type { Page } from "@/lib/hooks";
import type { Cliente, ClienteExterno, Proyecto, Serie, SistemaExterno, Sucursal } from "@/lib/types";

const WRITE = "cliente:gestionar";

const SISTEMAS: { valor: SistemaExterno; label: string; ayuda: string; ejemplo: string }[] = [
  { valor: "RFC", label: "RFC del documento", ayuda: "El RFC impreso en la orden de compra", ejemplo: "GOA180712SF5" },
  { valor: "NOMBRE", label: "Nombre en el documento", ayuda: "La razón social como aparece dentro del PDF", ejemplo: "BALLES" },
  { valor: "PROYECTO", label: "Proyecto", ayuda: "perfil:PROYECTO — lo que separa EHMO de MAFAN", ejemplo: "ehmo:HOSPITALES" },
  { valor: "UBICACION", label: "Ubicación / destino", ayuda: "perfil:ubicación — resuelve además la sucursal", ejemplo: "villahermosa:JUAN GRAHAM" },
  { valor: "SAE", label: "Clave de SAE", ayuda: "empresa:cliente de ASPEL SAE", ejemplo: "02:5" },
  { valor: "WHATSAPP", label: "Grupo de WhatsApp", ayuda: "El JID del grupo — solo si es de un único cliente", ejemplo: "1203…@g.us" },
];

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);

  const [cliente, setCliente] = useState<Cliente | null>(null);
  const [rows, setRows] = useState<ClienteExterno[] | null>(null);
  const [sucursales, setSucursales] = useState<Sucursal[]>([]);
  // Para las filas de grupo (WHATSAPP) y de proyecto: la serie que usa ese
  // grupo y el proyecto con el que se etiqueta el documento (ticket 86bbyw55x).
  const [series, setSeries] = useState<Serie[]>([]);
  const [proyectos, setProyectos] = useState<Proyecto[]>([]);
  const [error, setError] = useState(false);
  const [abierto, setAbierto] = useState(false);
  const [sistema, setSistema] = useState<SistemaExterno>("RFC");
  const [clave, setClave] = useState("");
  const [sucursalId, setSucursalId] = useState("");
  const [serieRemId, setSerieRemId] = useState("");
  const [serieFacId, setSerieFacId] = useState("");
  const [proyectoId, setProyectoId] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [aBorrar, setABorrar] = useState<ClienteExterno | null>(null);

  const reload = useCallback(() => {
    apiFetch<ClienteExterno[]>(`/api/v1/clientes/externos?cliente_id=${id}`)
      .then(setRows)
      .catch(() => setError(true));
  }, [id]);

  useEffect(() => {
    apiFetch<Cliente>(`/api/v1/clientes/${id}`).then(setCliente).catch(() => setError(true));
    apiFetch<Page<Sucursal>>(`/api/v1/sucursales?cliente_id=${id}&limit=500`)
      .then((p) => setSucursales(p.items))
      .catch(() => undefined);
    // Sin permiso de series/proyectos los selects simplemente no se llenan;
    // la equivalencia se guarda igual sin esos campos.
    apiFetch<Page<Serie>>(`/api/v1/series?activa=true&limit=200`)
      .then((p) => setSeries(p.items))
      .catch(() => undefined);
    apiFetch<Page<Proyecto>>(`/api/v1/proyectos?activo=true&limit=500`)
      .then((p) => setProyectos(p.items))
      .catch(() => undefined);
    reload();
  }, [id, reload]);

  const seriesRem = series.filter((s) => s.tipo_documento === "REMISION");
  const seriesFac = series.filter((s) => s.tipo_documento === "FACTURA");
  // Los proyectos de ESTE cliente primero (los genéricos después): es lo que
  // casi siempre se busca al etiquetar por proyecto.
  const proyectosOrden = [...proyectos].sort((a, b) =>
    Number(b.cliente_id === id) - Number(a.cliente_id === id) || a.nombre.localeCompare(b.nombre));
  const serieNombre = useCallback((sid?: string | null) => {
    const s = series.find((x) => x.id === sid);
    return s ? `${s.codigo}${s.nombre ? ` · ${s.nombre}` : ""}` : null;
  }, [series]);

  const meta = SISTEMAS.find((s) => s.valor === sistema)!;

  async function guardar() {
    if (!clave.trim()) {
      toast.error("Captura la clave");
      return;
    }
    setGuardando(true);
    try {
      await apiFetch("/api/v1/clientes/externos", {
        method: "POST",
        body: JSON.stringify({
          sistema,
          clave: clave.trim(),
          cliente_id: id,
          // UBICACION: la plaza del punto de entrega. WHATSAPP: la plaza POR
          // DEFECTO del grupo (la que toman sus pedidos si el documento no
          // dice otra cosa).
          sucursal_id: (sistema === "UBICACION" || sistema === "WHATSAPP") && sucursalId ? sucursalId : null,
          // Solo en grupos: la serie de folios que usa ESE grupo.
          serie_remision_id: sistema === "WHATSAPP" && serieRemId ? serieRemId : null,
          serie_factura_id: sistema === "WHATSAPP" && serieFacId ? serieFacId : null,
          // Solo en filas de proyecto: con qué proyecto se etiqueta el documento.
          proyecto_id: sistema === "PROYECTO" && proyectoId ? proyectoId : null,
          confianza: "CONFIRMADA",
        }),
      });
      toast.success("Equivalencia guardada");
      setAbierto(false);
      setClave("");
      setSucursalId("");
      setSerieRemId("");
      setSerieFacId("");
      setProyectoId("");
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar");
    } finally {
      setGuardando(false);
    }
  }

  async function borrar() {
    if (!aBorrar) return;
    try {
      await apiFetch(`/api/v1/clientes/externos/${aBorrar.id}`, { method: "DELETE" });
      toast.success("Eliminada");
      setABorrar(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo eliminar");
    }
  }

  const columns = useMemo<Column<ClienteExterno>[]>(() => [
    {
      header: "Sistema",
      cell: (r) => SISTEMAS.find((s) => s.valor === r.sistema)?.label ?? r.sistema,
    },
    { header: "Clave", cell: (r) => <span className="font-mono text-xs">{r.clave}</span> },
    {
      header: "Sucursal",
      cell: (r) =>
        r.sucursal_id
          ? sucursales.find((s) => s.id === r.sucursal_id)?.nombre ?? "—"
          : <span className="text-muted">—</span>,
    },
    {
      // Lo demás que la fila decide: la serie del grupo (WHATSAPP) y el
      // proyecto de la etiqueta (PROYECTO). Es la parte que antes solo se
      // podía capturar por API (ticket 86bbyw55x).
      header: "Serie / proyecto",
      cell: (r) => {
        const partes: string[] = [];
        const sr = serieNombre(r.serie_remision_id);
        const sf = serieNombre(r.serie_factura_id);
        if (sr) partes.push(`Rem: ${sr}`);
        if (sf) partes.push(`Fac: ${sf}`);
        if (r.proyecto_id) {
          const p = proyectos.find((x) => x.id === r.proyecto_id);
          if (p) partes.push(`Proyecto: ${p.nombre}`);
        }
        return partes.length
          ? <span className="text-xs">{partes.join(" · ")}</span>
          : <span className="text-muted">—</span>;
      },
    },
    {
      header: "Confianza",
      cell: (r) =>
        r.confianza === "CONFIRMADA" ? (
          <Badge tone="success">Confirmada</Badge>
        ) : (
          <Badge tone="warning">Sugerida por el bot</Badge>
        ),
    },
    { header: "Origen", cell: (r) => <span className="text-xs text-muted">{r.origen}</span> },
    {
      header: "",
      className: "text-right w-1",
      cell: (r) =>
        canWrite ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setABorrar(r);
            }}
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
            aria-label="Eliminar"
          >
            <Trash2 size={16} />
          </button>
        ) : null,
    },
  ], [canWrite, proyectos, serieNombre, sucursales]);

  if (error) return <Alert tone="danger">No se pudieron cargar las equivalencias.</Alert>;
  if (!cliente || rows === null)
    return <div className="flex justify-center py-16"><Spinner /></div>;

  return (
    <div>
      <PageHeader
        title={`Equivalencias de ${cliente.legal_name}`}
        subtitle="Cómo reconocer a este cliente en las órdenes que llegan por WhatsApp o correo"
        actions={
          <div className="flex items-center gap-2">
            <Link
              href="/clientes"
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3.5 py-2 text-sm font-medium hover:bg-surface-2"
            >
              <ArrowLeft size={16} /> Clientes
            </Link>
            {canWrite ? (
              <Button onClick={() => setAbierto(true)}>
                <Plus size={16} /> Agregar
              </Button>
            ) : null}
          </div>
        }
      />

      <DataTable
        columns={columns}
        rows={rows}
        paginated
        defaultPageSize={50}
        empty="Sin equivalencias. Mientras no haya ninguna, las órdenes de este cliente llegan a la bandeja sin asignar — y al asignarlas a mano ahí, se registran aquí solas."
      />

      <Modal
        open={abierto}
        onClose={() => setAbierto(false)}
        title="Agregar equivalencia"
        resizable={false}
        footer={
          <>
            <Button variant="secondary" onClick={() => setAbierto(false)}>Cancelar</Button>
            <Button onClick={guardar} disabled={guardando}>
              {guardando ? "Guardando…" : "Guardar"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Sistema" hint={meta.ayuda}>
            <Select value={sistema} onChange={(e) => setSistema(e.target.value as SistemaExterno)}>
              {SISTEMAS.map((s) => (
                <option key={s.valor} value={s.valor}>{s.label}</option>
              ))}
            </Select>
          </Field>
          <Field label="Clave" hint={`Ejemplo: ${meta.ejemplo}`}>
            <Input value={clave} onChange={(e) => setClave(e.target.value)} placeholder={meta.ejemplo} />
          </Field>
          {sistema === "UBICACION" ? (
            <Field label="Sucursal a la que corresponde" hint="Es lo que decide el destino de la remisión">
              <Select value={sucursalId} onChange={(e) => setSucursalId(e.target.value)}>
                <option value="">— Sin sucursal —</option>
                {sucursales.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.codigo ? `${s.codigo} · ${s.nombre}` : s.nombre}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          {/* Un grupo puede decidir más que el cliente (ticket 86bbyw55x): su
              plaza por defecto y su serie de folios. Vacío = hereda lo del
              cliente/vínculo, como siempre. */}
          {sistema === "WHATSAPP" ? (
            <>
              <Field label="Sucursal por defecto del grupo" hint="La plaza que toman sus pedidos cuando el documento no dice otra cosa. Vacío = se resuelve como siempre">
                <Select value={sucursalId} onChange={(e) => setSucursalId(e.target.value)}>
                  <option value="">— Sin sucursal por defecto —</option>
                  {sucursales.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.codigo ? `${s.codigo} · ${s.nombre}` : s.nombre}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Serie de remisión del grupo" hint="Los folios de los pedidos de este grupo. Vacío = la serie del cliente/vínculo">
                <Select value={serieRemId} onChange={(e) => setSerieRemId(e.target.value)}>
                  <option value="">— Heredar la del cliente —</option>
                  {seriesRem.map((s) => (
                    <option key={s.id} value={s.id}>{s.codigo}{s.nombre ? ` · ${s.nombre}` : ""}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Serie de factura del grupo" hint="Con qué serie se facturan sus pedidos. Vacío = la serie del cliente/vínculo">
                <Select value={serieFacId} onChange={(e) => setSerieFacId(e.target.value)}>
                  <option value="">— Heredar la del cliente —</option>
                  {seriesFac.map((s) => (
                    <option key={s.id} value={s.id}>{s.codigo}{s.nombre ? ` · ${s.nombre}` : ""}</option>
                  ))}
                </Select>
              </Field>
            </>
          ) : null}
          {sistema === "PROYECTO" ? (
            <Field label="Proyecto del catálogo" hint="El documento se etiqueta con él (y con sus listas de precios negociadas)">
              <Select value={proyectoId} onChange={(e) => setProyectoId(e.target.value)}>
                <option value="">— Solo identificar al cliente —</option>
                {proyectosOrden.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.nombre}{p.cliente_id === id ? "" : p.cliente_nombre ? ` (${p.cliente_nombre})` : ""}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          <Alert tone="info">
            Si esta clave ya apunta a otro cliente, se reapunta a éste — no se duplica.
          </Alert>
        </div>
      </Modal>

      <ConfirmDialog
        open={aBorrar !== null}
        title="Eliminar la equivalencia"
        message={`¿Eliminar «${aBorrar?.clave}»? Las órdenes que la traigan volverán a llegar sin asignar.`}
        onClose={() => setABorrar(null)}
        onConfirm={borrar}
      />
    </div>
  );
}

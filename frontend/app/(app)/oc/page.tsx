"use client";

// Bandeja de órdenes de compra — la LISTA. Todo documento que entra (WhatsApp,
// correo o captura) aterriza aquí antes de volverse remisión. El DETALLE de
// cada orden vive en su propia página a pantalla completa (/oc/[id], 28-ago):
// es la pantalla de trabajo diaria, no un popup.
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ExternalLink, FastForward, PencilLine, RotateCcw, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { SearchBox } from "@/components/ui/SearchBox";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import type { Page as PageOf } from "@/lib/hooks";
import type { Cliente, GrupoBandeja, OCRecibida, OCRecibidaDetalle, Proyecto } from "@/lib/types";
import { CANAL_TONE, estadoTexto, precioNormalizado } from "./cruce";

const WRITE = "remision:gestionar";

/** El vistazo rápido de una orden (slidedown de la lista): las partidas como
 *  venían y el punto de entrega. Abrir el documento y trabajarla (cruzar,
 *  corregir, crear la remisión) son los íconos del renglón — aquí no se
 *  repiten. */
function VistazoOC({ id }: { id: string }) {
  const [d, setD] = useState<OCRecibidaDetalle | null>(null);
  const [fallo, setFallo] = useState(false);
  useEffect(() => {
    // vistazo=true: solo las partidas como venían — sin cruce ni precios, que
    // es lo único que este slidedown enseña. Sin el flag pagaba el costo de la
    // pantalla completa.
    apiFetch<OCRecibidaDetalle>(`/api/v1/oc-recibidas/${id}?vistazo=true`)
      .then(setD)
      .catch(() => setFallo(true));
  }, [id]);
  if (fallo) return <p className="py-3 text-sm text-muted">No se pudo cargar el detalle.</p>;
  if (!d) return <div className="flex justify-center py-4"><Spinner /></div>;
  return (
    <div className="space-y-3 py-2">
      {/* Sin botones aquí: abrir y ver el documento ya viven como íconos en el
          renglón — repetirlos solo empujaba las partidas hacia abajo. */}
      {d.punto_entrega || !d.archivo_url ? (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {d.punto_entrega ? (
            <span className="text-muted">
              Punto de entrega: <strong className="text-foreground">{d.punto_entrega}</strong>
            </span>
          ) : null}
          {!d.archivo_url ? <span className="text-muted">Sin documento adjunto</span> : null}
        </div>
      ) : null}
      {typeof d.payload?.observaciones === "string" && d.payload.observaciones ? (
        <p className="text-sm text-muted">{d.payload.observaciones}</p>
      ) : null}
      <div className="overflow-x-auto">
        <table className="w-full max-w-3xl text-sm">
          <thead className="text-xs text-muted">
            <tr>
              <th className="px-2 py-1 text-left">Como venía en la orden</th>
              <th className="px-2 py-1 text-right">Cantidad</th>
              <th className="px-2 py-1 text-left">Unidad</th>
              <th className="px-2 py-1 text-left">Su clave</th>
            </tr>
          </thead>
          <tbody>
            {d.lineas.map((l) => (
              <tr key={l.numero} className="border-t border-border">
                <td className="px-2 py-1">{l.descripcion}</td>
                <td className="px-2 py-1 text-right tabular-nums">{l.cantidad ?? ""}</td>
                <td className="px-2 py-1">{l.unidad ?? ""}</td>
                <td className="px-2 py-1 tabular-nums">{l.clave ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function estadoBadge(oc: OCRecibida) {
  const b = estadoTexto(oc);
  return <Badge tone={b.tone}>{b.texto}</Badge>;
}

export default function Page() {
  const router = useRouter();
  const { me } = useAuth();
  const toast = useToast();
  const canWrite = can(me, WRITE);

  const [rows, setRows] = useState<OCRecibida[] | null>(null);
  const [error, setError] = useState(false);
  const [estado, setEstado] = useState("PENDIENTE");
  // Filtros del flujo diario: "lo de hoy de tal cliente". Cambiarlos regresa a
  // la primera página — la posición vieja no significa nada bajo otro filtro.
  const [clienteFiltro, setClienteFiltro] = useState("");
  const [fechaDesde, setFechaDesde] = useState("");
  const [fechaHasta, setFechaHasta] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [grupoFiltro, setGrupoFiltro] = useState("");
  const [proyectoFiltro, setProyectoFiltro] = useState("");
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [proyectos, setProyectos] = useState<Proyecto[]>([]);
  const [grupos, setGrupos] = useState<GrupoBandeja[]>([]);
  const [aDescartar, setADescartar] = useState<OCRecibida | null>(null);
  // La orden que se va a pasar a Remisiones TAL COMO ESTÁ. Se pregunta antes:
  // crear la remisión quema un folio de la serie y eso no se recupera.
  const [aPasar, setAPasar] = useState<OCRecibida | null>(null);
  const [pasando, setPasando] = useState(false);

  const LIMIT = 100;

  // Filtros encadenados: el grupo acota clientes y proyectos; el cliente acota
  // proyectos. Se filtra lo que se OFRECE — la selección inválida se resetea en
  // el onChange del filtro que la invalidó, nunca en silencio desde un efecto.
  const grupoSel = useMemo(
    () => grupos.find((g) => `${g.tipo}|${g.clave}` === grupoFiltro) ?? null,
    [grupos, grupoFiltro]
  );
  // Un origen sin clientes conocidos no acota nada: acotar con un conjunto
  // vacío sería ofrecer CERO clientes, que es peor que ofrecerlos todos.
  const clientesDelGrupo = grupoSel && grupoSel.cliente_ids.length ? grupoSel.cliente_ids : null;
  const clientesVisibles = useMemo(
    () => (clientesDelGrupo ? clientes.filter((c) => clientesDelGrupo.includes(c.id)) : clientes),
    [clientes, clientesDelGrupo]
  );
  // Un proyecto sin cliente es global: se ofrece siempre, como en el detalle.
  const proyectosVisibles = useMemo(() => {
    if (clienteFiltro) return proyectos.filter((p) => !p.cliente_id || p.cliente_id === clienteFiltro);
    if (clientesDelGrupo) return proyectos.filter((p) => !p.cliente_id || clientesDelGrupo.includes(p.cliente_id));
    return proyectos;
  }, [proyectos, clienteFiltro, clientesDelGrupo]);

  // Resumen del día: la foto que el Master daba de un vistazo.
  const [resumen, setResumen] = useState<{ hoy: number; pendientes: number; conRemision: number } | null>(null);
  useEffect(() => {
    const hoy = new Date().toLocaleDateString("en-CA");
    const totalDe = (qs: string) =>
      apiFetch<PageOf<OCRecibida>>(`/api/v1/oc-recibidas?limit=1&${qs}`).then((p) => p.total);
    Promise.all([
      totalDe(`fecha_desde=${hoy}&fecha_hasta=${hoy}`),
      totalDe("estado=PENDIENTE"),
      totalDe(`estado=ASIGNADA&fecha_desde=${hoy}&fecha_hasta=${hoy}`),
    ])
      .then(([h, p, c]) => setResumen({ hoy: h, pendientes: p, conRemision: c }))
      .catch(() => setResumen(null));
  }, [rows]);

  // La búsqueda pega al SERVIDOR (folio, remitente, archivo, punto de entrega
  // y observaciones): la lista está paginada y buscar solo en la página visible
  // mentiría. `buscaInput` es lo tecleado; `busca` lo que ya viajó, con un
  // debounce para no disparar una consulta por letra.
  const [busca, setBusca] = useState("");
  const [buscaInput, setBuscaInput] = useState("");
  useEffect(() => {
    const t = setTimeout(() => {
      setBusca((prev) => {
        if (prev !== buscaInput.trim()) setOffset(0);
        return buscaInput.trim();
      });
    }, 350);
    return () => clearTimeout(t);
  }, [buscaInput]);

  // Deep-link desde Remisiones (?q=<folio del cliente>): busca esa OC en TODAS
  // las etapas, no solo en las pendientes.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("q");
    if (p) { setBuscaInput(p); setEstado(""); }
  }, []);

  const reload = useCallback(() => {
    setError(false);          // un fallo transitorio no puede dejar la bandeja muerta
    const qs = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
    if (estado) qs.set("estado", estado);
    if (busca) qs.set("q", busca);
    if (clienteFiltro) qs.set("cliente_id", clienteFiltro);
    if (proyectoFiltro) qs.set("proyecto_id", proyectoFiltro);
    if (grupoFiltro) {
      const [tipo, ...resto] = grupoFiltro.split("|");
      qs.set(tipo === "grupo" ? "jid" : "remitente", resto.join("|"));
    }
    if (fechaDesde) qs.set("fecha_desde", fechaDesde);
    if (fechaHasta) qs.set("fecha_hasta", fechaHasta);
    apiFetch<PageOf<OCRecibida>>(`/api/v1/oc-recibidas?${qs}`)
      .then((p) => {
        setRows(p.items);
        setTotal(p.total);
      })
      .catch(() => setError(true));
  }, [estado, busca, clienteFiltro, proyectoFiltro, grupoFiltro, fechaDesde, fechaHasta, offset]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    apiFetch<GrupoBandeja[]>("/api/v1/oc-recibidas/grupos")
      .then(setGrupos)
      .catch(() => undefined);
    apiFetch<PageOf<Proyecto>>("/api/v1/proyectos?activo=true&limit=500")
      .then((p) => setProyectos(p.items))
      .catch(() => undefined);
    apiFetch<PageOf<Cliente>>("/api/v1/clientes?limit=1000")
      .then((p) => setClientes(p.items))
      .catch(() => undefined);
  }, []);

  // Alta manual de una OC (canal MANUAL): lo que llega por teléfono o papel
  // también pasa por la bandeja — misma conciliación, mismo aprendizaje.
  type LineaManual = { descripcion: string; cantidad: string; unidad: string; clave: string; precio: string };
  const lineaVacia = (): LineaManual => ({ descripcion: "", cantidad: "", unidad: "", clave: "", precio: "" });
  const [manualOpen, setManualOpen] = useState(false);
  const [manualBusy, setManualBusy] = useState(false);
  const [manual, setManual] = useState({
    cliente_id: "", folio_externo: "", punto_entrega: "", observaciones: "",
    lineas: [lineaVacia()] as LineaManual[],
  });

  async function crearManual() {
    const lineas = manual.lineas.filter((l) => l.descripcion.trim() && Number(l.cantidad) > 0);
    if (!manual.cliente_id) { toast.error("Elige el cliente"); return; }
    if (!lineas.length) { toast.error("Captura al menos una partida con cantidad"); return; }
    setManualBusy(true);
    try {
      const oc = await apiFetch<OCRecibidaDetalle>("/api/v1/oc-recibidas", {
        method: "POST",
        body: JSON.stringify({
          canal: "MANUAL",
          origen_externo: `MANUAL:${crypto.randomUUID()}`,
          folio_externo: manual.folio_externo.trim() || null,
          observaciones: manual.observaciones.trim() || null,
          ubicacion: manual.punto_entrega.trim() || null,
          lineas: lineas.map((l) => ({
            descripcion: l.descripcion.trim(),
            cantidad: l.cantidad,
            unidad: l.unidad.trim() || null,
            clave: l.clave.trim() || null,
            precio: precioNormalizado(l.precio) || null,
          })),
        }),
      });
      await apiFetch(`/api/v1/oc-recibidas/${oc.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          cliente_id: manual.cliente_id,
          punto_entrega: manual.punto_entrega.trim() || null,
          aprender: false,   // no hay pistas de documento de las cuales aprender
        }),
      });
      toast.success("OC capturada — revisa el cruce y crea la remisión");
      setManualOpen(false);
      setManual({ cliente_id: "", folio_externo: "", punto_entrega: "", observaciones: "", lineas: [lineaVacia()] });
      router.push(`/oc/${oc.id}`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo capturar la OC");
    } finally {
      setManualBusy(false);
    }
  }

  async function descartar() {
    if (!aDescartar) return;
    try {
      await apiFetch(`/api/v1/oc-recibidas/${aDescartar.id}/descartar`, { method: "POST" });
      toast.success("Descartada");
      setADescartar(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo descartar");
    }
  }

  /** Pasa la orden a Remisiones sin revisarla aquí: cada partida entra con el
   *  producto que mejor cruzó y lo dudoso queda anotado en su línea. La
   *  remisión nace marcada «por revisar» y no se confirma ni se factura hasta
   *  que alguien la mire. */
  async function pasarSinRevisar() {
    if (!aPasar) return;
    setPasando(true);
    try {
      const d = await apiFetch<OCRecibidaDetalle>(
        `/api/v1/oc-recibidas/${aPasar.id}/crear-remision-sin-revisar`,
        { method: "POST" }
      );
      toast.success(`Remisión ${d.remision_folio ?? ""} creada — te espera en Remisiones para revisar`);
      setAPasar(null);
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo pasar la orden");
    } finally {
      setPasando(false);
    }
  }

  async function reabrir(oc: OCRecibida) {
    try {
      await apiFetch(`/api/v1/oc-recibidas/${oc.id}/reabrir`, { method: "POST" });
      toast.success("Reabierta");
      reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo reabrir");
    }
  }

  const columns: Column<OCRecibida>[] = [
    {
      header: "Origen",
      cell: (r) => (
        <div>
          <Badge tone={CANAL_TONE[r.canal] ?? "default"}>{r.canal}</Badge>
          <div className="mt-0.5 text-xs text-muted">{r.remitente ?? "—"}</div>
        </div>
      ),
    },
    {
      header: "OC",
      cell: (r) => (
        <div>
          <div className="font-medium">{r.folio_externo || "—"}</div>
          <div className="text-xs text-muted">
            {new Date(r.recibida_at).toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" })}
          </div>
        </div>
      ),
    },
    {
      header: "Cliente",
      cell: (r) => (
        <div>
          <div>{r.cliente_nombre ?? <span className="text-muted">Sin asignar</span>}</div>
          {r.sucursal_nombre ? <div className="text-xs text-muted">{r.sucursal_nombre}</div> : null}
        </div>
      ),
    },
    {
      header: "Proyecto",
      cell: (r) =>
        r.proyecto_nombre ? (
          <span>{r.proyecto_nombre}</span>
        ) : (
          <span className="text-xs text-muted">—</span>
        ),
    },
    {
      header: "Observaciones",
      cell: (r) =>
        r.observaciones ? (
          // Recortadas a dos líneas para que un párrafo no ensanche el renglón;
          // el texto completo vive en el title y en el vistazo desplegado.
          <span
            className="block max-w-56 overflow-hidden text-xs text-muted [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2]"
            title={r.observaciones}
          >
            {r.observaciones}
          </span>
        ) : (
          <span className="text-xs text-muted">—</span>
        ),
    },
    { header: "Estado", cell: (r) => estadoBadge(r) },
    {
      header: "Detalle",
      cell: (r) => <span className="text-xs text-muted">{r.motivo ?? "—"}</span>,
    },
    {
      header: "",
      className: "text-right w-1",
      cell: (r) =>
        canWrite ? (
          <div className="flex justify-end gap-1">
            <button
              onClick={(e) => {
                e.stopPropagation();
                router.push(`/oc/${r.id}`);
              }}
              className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
              aria-label="Abrir para trabajar"
              title="Abrir para trabajar"
            >
              <PencilLine size={16} />
            </button>
            {r.archivo_url ? (
              <a
                href={r.archivo_url}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="rounded-md p-1.5 text-muted hover:bg-surface-2"
                aria-label="Abrir el archivo original"
              >
                <ExternalLink size={16} />
              </a>
            ) : null}
            {r.estado !== "DESCARTADA" && !r.remision_id && r.cliente_id ? (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setAPasar(r);
                }}
                className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
                aria-label="Pasar a Remisiones sin revisar"
                title="Pasar a Remisiones sin revisar (se revisa allá)"
              >
                <FastForward size={16} />
              </button>
            ) : null}
            {r.estado === "DESCARTADA" ? (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  reabrir(r);
                }}
                className="rounded-md p-1.5 text-muted hover:bg-surface-2"
                aria-label="Reabrir"
              >
                <RotateCcw size={16} />
              </button>
            ) : r.remision_id ? null : (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setADescartar(r);
                }}
                className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
                aria-label="Descartar"
              >
                <Trash2 size={16} />
              </button>
            )}
          </div>
        ) : null,
    },
  ];

  if (error) return <Alert tone="danger">No se pudo cargar la bandeja.</Alert>;
  if (rows === null) return <div className="flex justify-center py-16"><Spinner /></div>;

  return (
    <div>
      <PageHeader
        title="Bandeja de órdenes"
        subtitle="Lo que llega por WhatsApp o correo, antes de volverse remisión"
        actions={
          <div className="flex items-center gap-2">
            {canWrite ? (
              <Button variant="secondary" onClick={() => setManualOpen(true)}>
                Capturar OC
              </Button>
            ) : null}
            <div className="w-52">
              <Select
                value={estado}
                onChange={(e) => {
                  setEstado(e.target.value);
                  setOffset(0);
                }}
              >
                <option value="PENDIENTE">Por revisar</option>
                <option value="ASIGNADA">Ya con remisión</option>
                <option value="DESCARTADA">Descartadas</option>
                <option value="">Todas</option>
              </Select>
            </div>
          </div>
        }
      />

      {resumen ? (
        <div className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted">
          <span>Hoy llegaron <strong className="text-foreground tabular-nums">{resumen.hoy}</strong></span>
          <span>· con remisión hoy <strong className="text-foreground tabular-nums">{resumen.conRemision}</strong></span>
          <span>· por revisar (todas) <strong className={`tabular-nums ${resumen.pendientes ? "text-amber-700" : "text-foreground"}`}>{resumen.pendientes}</strong></span>
        </div>
      ) : null}

      <div className="mb-3 flex flex-wrap items-end gap-3">
        <div className="w-72">
          <Field label="Buscar">
            <SearchBox
              value={buscaInput}
              onChange={setBuscaInput}
              placeholder="Folio, remitente, punto de entrega, observaciones…"
              aria-label="Buscar órdenes"
            />
          </Field>
        </div>
        <div className="w-56">
          <Field label="Grupo de origen">
            <Select
              value={grupoFiltro}
              onChange={(e) => {
                const v = e.target.value;
                const g = grupos.find((x) => `${x.tipo}|${x.clave}` === v) ?? null;
                setGrupoFiltro(v);
                // Lo elegido antes puede no existir bajo el origen nuevo. Un
                // origen sin clientes conocidos no invalida nada.
                if (g?.cliente_ids.length) {
                  if (clienteFiltro && !g.cliente_ids.includes(clienteFiltro)) setClienteFiltro("");
                  if (proyectoFiltro) {
                    const pr = proyectos.find((x) => x.id === proyectoFiltro);
                    if (pr?.cliente_id && !g.cliente_ids.includes(pr.cliente_id)) setProyectoFiltro("");
                  }
                }
                setOffset(0);
              }}
            >
              <option value="">Todos</option>
              {grupos.map((g) => (
                <option key={`${g.tipo}|${g.clave}`} value={`${g.tipo}|${g.clave}`}>
                  {(g.nombre || g.clave) + (g.activo ? "" : " (apagado)")}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="w-64">
          <Field label="Cliente">
            <Select
              value={clienteFiltro}
              onChange={(e) => {
                const v = e.target.value;
                setClienteFiltro(v);
                if (v && proyectoFiltro) {
                  const pr = proyectos.find((x) => x.id === proyectoFiltro);
                  if (pr?.cliente_id && pr.cliente_id !== v) setProyectoFiltro("");
                }
                setOffset(0);
              }}
            >
              <option value="">Todos</option>
              {clientesVisibles.map((c) => (
                <option key={c.id} value={c.id}>{c.legal_name}</option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="w-56">
          <Field label="Proyecto">
            <Select
              value={proyectoFiltro}
              onChange={(e) => {
                setProyectoFiltro(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">Todos</option>
              {proyectosVisibles.map((pr) => (
                <option key={pr.id} value={pr.id}>{pr.nombre}</option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="w-40">
          <Field label="Recibidas desde">
            <Input
              type="date"
              value={fechaDesde}
              onChange={(e) => {
                setFechaDesde(e.target.value);
                setOffset(0);
              }}
            />
          </Field>
        </div>
        <div className="w-40">
          <Field label="Hasta">
            <Input
              type="date"
              value={fechaHasta}
              onChange={(e) => {
                setFechaHasta(e.target.value);
                setOffset(0);
              }}
            />
          </Field>
        </div>
        <Button
          variant="secondary"
          onClick={() => {
            const hoy = new Date().toLocaleDateString("en-CA");
            setFechaDesde(hoy);
            setFechaHasta(hoy);
            setOffset(0);
          }}
        >
          Hoy
        </Button>
        {buscaInput || grupoFiltro || clienteFiltro || proyectoFiltro || fechaDesde || fechaHasta ? (
          <button
            type="button"
            onClick={() => {
              setBuscaInput("");
              setGrupoFiltro("");
              setClienteFiltro("");
              setProyectoFiltro("");
              setFechaDesde("");
              setFechaHasta("");
              setOffset(0);
            }}
            className="pb-2 text-sm text-accent hover:underline"
          >
            Limpiar filtros
          </button>
        ) : null}
      </div>


      <DataTable
        columns={columns}
        rows={rows}
        empty={
          busca
            ? `Ninguna orden con el folio ${busca}. La remisión se importó de Excel, así que su OC puede no haber pasado por la bandeja.`
            : estado === "PENDIENTE"
              ? "Nada por revisar. Las órdenes que lleguen por WhatsApp o correo aparecen aquí."
              : "Sin órdenes en este estado."
        }
        rowKey={(r) => r.id}
        renderExpanded={(r) => (
          <VistazoOC id={r.id} />
        )}
      />

      {total > LIMIT ? (
        <div className="mt-3 flex items-center justify-end gap-3 text-sm">
          <span className="text-muted tabular-nums">
            {offset + 1}–{Math.min(offset + LIMIT, total)} de {total}
          </span>
          <Button
            variant="secondary"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - LIMIT))}
          >
            Anteriores
          </Button>
          <Button
            variant="secondary"
            disabled={offset + LIMIT >= total}
            onClick={() => setOffset(offset + LIMIT)}
          >
            Siguientes
          </Button>
        </div>
      ) : null}

      <Modal
        open={manualOpen}
        onClose={() => setManualOpen(false)}
        title="Capturar una OC a mano"
        footer={
          <>
            <Button variant="secondary" onClick={() => setManualOpen(false)}>Cerrar</Button>
            <Button onClick={() => { void crearManual(); }} disabled={manualBusy}>
              {manualBusy ? "Capturando…" : "Capturar"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <p className="text-sm text-muted">
            Para lo que llega por teléfono o papel: entra a la bandeja como cualquier orden
            (canal MANUAL) y de ahí se cruza y se vuelve remisión.
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Field label="Cliente">
              <Select
                value={manual.cliente_id}
                onChange={(e) => setManual((m) => ({ ...m, cliente_id: e.target.value }))}
              >
                <option value="">— Elegir —</option>
                {clientes.map((c) => (
                  <option key={c.id} value={c.id}>{c.legal_name}</option>
                ))}
              </Select>
            </Field>
            <Field label="Folio de su OC" hint="Como el cliente la conoce">
              <Input
                value={manual.folio_externo}
                onChange={(e) => setManual((m) => ({ ...m, folio_externo: e.target.value }))}
              />
            </Field>
            <Field label="Punto de entrega">
              <Input
                value={manual.punto_entrega}
                onChange={(e) => setManual((m) => ({ ...m, punto_entrega: e.target.value }))}
              />
            </Field>
          </div>
          <Field label="Observaciones">
            <Input
              value={manual.observaciones}
              onChange={(e) => setManual((m) => ({ ...m, observaciones: e.target.value }))}
            />
          </Field>
          <div>
            <h3 className="mb-2 text-sm font-semibold">Partidas</h3>
            <div className="space-y-2">
              {manual.lineas.map((l, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2">
                  <div className="min-w-56 flex-1">
                    <Input
                      placeholder="Producto como lo pidió el cliente"
                      value={l.descripcion}
                      onChange={(e) =>
                        setManual((m) => ({
                          ...m,
                          lineas: m.lineas.map((x, j) => (j === i ? { ...x, descripcion: e.target.value } : x)),
                        }))
                      }
                    />
                  </div>
                  <div className="w-24">
                    <Input
                      placeholder="Cant."
                      inputMode="decimal"
                      className="text-right tabular-nums"
                      value={l.cantidad}
                      onChange={(e) =>
                        setManual((m) => ({
                          ...m,
                          lineas: m.lineas.map((x, j) => (j === i ? { ...x, cantidad: e.target.value } : x)),
                        }))
                      }
                    />
                  </div>
                  <div className="w-24">
                    <Input
                      placeholder="Unidad"
                      value={l.unidad}
                      onChange={(e) =>
                        setManual((m) => ({
                          ...m,
                          lineas: m.lineas.map((x, j) => (j === i ? { ...x, unidad: e.target.value } : x)),
                        }))
                      }
                    />
                  </div>
                  <div className="w-28">
                    <Input
                      placeholder="Su clave"
                      value={l.clave}
                      onChange={(e) =>
                        setManual((m) => ({
                          ...m,
                          lineas: m.lineas.map((x, j) => (j === i ? { ...x, clave: e.target.value } : x)),
                        }))
                      }
                    />
                  </div>
                  <div className="w-24">
                    <Input
                      placeholder="Precio"
                      inputMode="decimal"
                      className="text-right tabular-nums"
                      value={l.precio}
                      onChange={(e) =>
                        setManual((m) => ({
                          ...m,
                          lineas: m.lineas.map((x, j) => (j === i ? { ...x, precio: e.target.value } : x)),
                        }))
                      }
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      setManual((m) => ({ ...m, lineas: m.lineas.filter((_, j) => j !== i) }))
                    }
                    className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-danger"
                    aria-label="Quitar partida"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
            </div>
            <div className="mt-2">
              <Button
                variant="secondary"
                onClick={() => setManual((m) => ({ ...m, lineas: [...m.lineas, lineaVacia()] }))}
              >
                Agregar partida
              </Button>
            </div>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={aPasar !== null}
        title="Pasar a Remisiones sin revisar"
        message={
          `La OC ${aPasar?.folio_externo ?? ""} se vuelve remisión TAL COMO ESTÁ: cada partida entra con el ` +
          "producto que mejor cruzó, la unidad que se pudo y el precio de su lista, y lo dudoso queda anotado " +
          "en la línea. Las partidas que no cruzaron a ningún producto viajan aparte, para cruzarlas allá. " +
          "La remisión queda marcada «por revisar»: no se confirma, no se factura y no sale a SAE hasta que la revises. " +
          "Se consume un folio de la serie."
        }
        confirmLabel={pasando ? "Pasando…" : "Pasar sin revisar"}
        onClose={() => setAPasar(null)}
        onConfirm={pasarSinRevisar}
      />
      <ConfirmDialog
        open={aDescartar !== null}
        title="Descartar la orden"
        message={`¿Descartar la OC ${aDescartar?.folio_externo ?? ""}? No se crea ninguna remisión. Se puede reabrir después.`}
        onClose={() => setADescartar(null)}
        onConfirm={descartar}
      />
    </div>
  );
}

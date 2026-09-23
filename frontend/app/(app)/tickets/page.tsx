"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, Inbox, MessageSquare, PlusCircle, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input, Textarea } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiBlobUrl } from "@/lib/api";
import { can, useAuth } from "@/lib/auth";
import { fmtDateTime } from "@/lib/format";
import { useMutation, useResource, type Page } from "@/lib/hooks";

// Buzón de tickets (23-sep-2026). Cada «necesito una mano» del bot de WhatsApp
// llega aquí con el MISMO número que leyó el grupo, para retomarlo sin buscar
// el mensaje en el chat. Las acciones las ejecuta el bot (tiene la foto y la
// fila de proceso): desde aquí se piden, y el ticket enseña en qué van.

type Estado = "ABIERTO" | "EN_CURSO" | "RESUELTO" | "CERRADO";

type Ticket = {
  id: string;
  numero: number;
  canal: string;
  estado: Estado;
  perfil: string | null;
  grupo: string | null;
  remitente: string | null;
  archivo_nombre: string | null;
  nota: string | null;
  tipo: string | null;
  que_paso: string | null;
  acciones: string[];
  tiene_foto: boolean;
  accion_pedida: string | null;
  accion_pedida_por: string | null;
  accion_tomada_at: string | null;
  resolucion: string | null;
  resuelto_at: string | null;
  resuelto_por: string | null;
  recibido_at: string;
  updated_at: string;
};

type Evento = { ts: string; quien: string; texto: string };
type TicketDetalle = Ticket & { eventos: Evento[] };

const WRITE = "ticket:gestionar";

const ESTADO_UI: Record<Estado, { label: string; tone: "danger" | "warning" | "success" | "muted" }> = {
  ABIERTO: { label: "Abierto", tone: "danger" },
  EN_CURSO: { label: "En curso", tone: "warning" },
  RESUELTO: { label: "Resuelto", tone: "success" },
  CERRADO: { label: "Cerrado", tone: "muted" },
};

const FILTROS = [
  { id: "ABIERTO,EN_CURSO", label: "Pendientes" },
  { id: "RESUELTO,CERRADO", label: "Cerrados" },
  { id: "", label: "Todos" },
];

/** ?n=1 abre directo el ticket 1 — el link que se puede pegar en WhatsApp. */
function numeroDeLaUrl(): number | null {
  if (typeof window === "undefined") return null;
  const n = Number(new URLSearchParams(window.location.search).get("n"));
  return Number.isInteger(n) && n > 0 ? n : null;
}

export default function TicketsPage() {
  const { me } = useAuth();
  const toast = useToast();
  const puedeResolver = can(me, WRITE);
  const { post, loading: enviando } = useMutation();

  const [filtro, setFiltro] = useState(FILTROS[0].id);
  const [q, setQ] = useState("");
  const [seleccion, setSeleccion] = useState<string | null>(null);
  const [numeroInicial] = useState(numeroDeLaUrl);

  const params = new URLSearchParams({ limit: "100" });
  if (filtro) params.set("estado", filtro);
  if (q.trim()) params.set("q", q.trim());
  // con ?n= se busca en TODOS: el ticket del link puede ya estar cerrado
  if (numeroInicial && !seleccion) {
    params.delete("estado");
    params.set("q", `#${numeroInicial}`);
  }
  const lista = useResource<Page<Ticket>>(`/api/v1/tickets?${params.toString()}`);
  const items = useMemo(() => lista.data?.items ?? [], [lista.data]);

  useEffect(() => {
    if (seleccion || !items.length) return;
    const porNumero = numeroInicial ? items.find((t) => t.numero === numeroInicial) : null;
    setSeleccion((porNumero ?? items[0]).id);
  }, [items, seleccion, numeroInicial]);

  const detalle = useResource<TicketDetalle>(seleccion ? `/api/v1/tickets/${seleccion}` : null);
  const t = detalle.data;

  const [foto, setFoto] = useState<string | null>(null);
  useEffect(() => {
    setFoto(null);
    if (!t?.tiene_foto) return;
    let url: string | null = null;
    let vivo = true;
    apiBlobUrl(`/api/v1/tickets/${t.id}/foto`)
      .then((u) => { url = u; if (vivo) setFoto(u); else URL.revokeObjectURL(u); })
      .catch(() => { /* sin foto el ticket se sigue pudiendo resolver */ });
    return () => { vivo = false; if (url) URL.revokeObjectURL(url); };
  }, [t?.id, t?.tiene_foto]);

  const [nota, setNota] = useState("");
  const [comentario, setComentario] = useState("");
  useEffect(() => { setNota(""); setComentario(""); }, [seleccion]);

  const recargar = useCallback(() => { detalle.reload(); lista.reload(); }, [detalle, lista]);

  async function accion(a: "EXTRA" | "CERRAR") {
    if (!t) return;
    try {
      await post(`/api/v1/tickets/${t.id}/accion`, { accion: a, nota: nota.trim() || undefined });
      toast.success(a === "EXTRA"
        ? `Ticket ${t.numero}: el bot lo suma como complemento en los próximos minutos`
        : `Ticket ${t.numero} cerrado`);
      recargar();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo aplicar");
    }
  }

  async function comentar() {
    if (!t || !comentario.trim()) return;
    try {
      await post(`/api/v1/tickets/${t.id}/comentarios`, { texto: comentario.trim() });
      setComentario("");
      detalle.reload();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo guardar el comentario");
    }
  }

  const abierto = t && (t.estado === "ABIERTO" || (t.estado === "EN_CURSO" && !!t.accion_tomada_at && !t.accion_pedida));
  const esperandoAlBot = t?.estado === "EN_CURSO" && !!t.accion_pedida;

  return (
    <div>
      <PageHeader
        title="Buzón de tickets"
        subtitle="Lo que el bot de WhatsApp no se atrevió a resolver solo. Se resuelve aquí o en el grupo con «revisar ticket N»."
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(280px,380px)_1fr]">
        {/* ── lista ── */}
        <div className="space-y-3">
          <div className="flex gap-1">
            {FILTROS.map((f) => (
              <button
                key={f.id || "todos"}
                type="button"
                onClick={() => { setFiltro(f.id); setSeleccion(null); }}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                  filtro === f.id ? "bg-accent text-white" : "text-muted hover:bg-surface-2 hover:text-foreground"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
          <Input
            placeholder="Buscar: #12, Palenque, foto_…"
            value={q}
            onChange={(e) => { setQ(e.target.value); setSeleccion(null); }}
          />
          {lista.loading && !items.length ? (
            <p className="text-sm text-muted">Cargando…</p>
          ) : lista.error ? (
            <p className="text-sm text-danger">{lista.error}</p>
          ) : !items.length ? (
            <EmptyState icon={<Inbox size={28} />} title="Sin tickets aquí"
                        hint={filtro === FILTROS[0].id ? "Nada atorado: el bot resolvió todo solo." : undefined} />
          ) : (
            <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border">
              {items.map((it) => (
                <li key={it.id}>
                  <button
                    type="button"
                    onClick={() => setSeleccion(it.id)}
                    className={`block w-full px-3 py-2.5 text-left transition hover:bg-surface-2 ${
                      seleccion === it.id ? "bg-surface-2" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">Ticket {it.numero}</span>
                      <Badge tone={ESTADO_UI[it.estado].tone}>{ESTADO_UI[it.estado].label}</Badge>
                    </div>
                    <div className="mt-0.5 truncate text-xs text-muted">
                      {it.nota || it.archivo_nombre || "—"}
                    </div>
                    <div className="mt-0.5 text-xs text-muted">{fmtDateTime(it.recibido_at)}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* ── detalle ── */}
        <div>
          {!t ? (
            detalle.loading ? <p className="text-sm text-muted">Cargando…</p> : null
          ) : (
            <Card
              title={<span className="flex items-center gap-2">Ticket {t.numero}
                <Badge tone={ESTADO_UI[t.estado].tone}>{ESTADO_UI[t.estado].label}</Badge></span>}
              subtitle={[t.grupo, t.remitente && `mandó ${t.remitente}`, fmtDateTime(t.recibido_at)]
                .filter(Boolean).join(" · ")}
            >
              <div className="grid gap-4 md:grid-cols-[1fr_minmax(200px,320px)]">
                <div className="space-y-4">
                  {t.nota && (
                    <div>
                      <div className="text-xs font-medium uppercase text-muted">Caption</div>
                      <p className="text-sm">{t.nota}</p>
                    </div>
                  )}
                  <div>
                    <div className="text-xs font-medium uppercase text-muted">Qué pasó</div>
                    <p className="text-sm">{t.que_paso || "—"}</p>
                  </div>
                  {t.resolucion && (
                    <div>
                      <div className="text-xs font-medium uppercase text-muted">Resolución</div>
                      <p className="text-sm">
                        {t.resolucion}
                        {t.resuelto_por && <span className="text-muted"> · {t.resuelto_por}</span>}
                      </p>
                    </div>
                  )}

                  {esperandoAlBot && (
                    <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
                      {t.accion_pedida_por} pidió {t.accion_pedida === "EXTRA" ? "sumarlo como complemento" : "cerrarlo"}.
                      {t.accion_tomada_at ? " El bot ya lo tomó; en cuanto quede, aquí aparece la OC." : " Esperando a que el bot lo tome (máx. 5 min)."}
                    </p>
                  )}

                  {abierto && puedeResolver && (
                    <div className="space-y-2 rounded-lg border border-border p-3">
                      <div className="text-sm font-medium">Resolver</div>
                      <Input placeholder="Nota (opcional): por qué, quién lo confirmó…"
                             value={nota} onChange={(e) => setNota(e.target.value)} />
                      <div className="flex flex-wrap gap-2">
                        {t.acciones.includes("EXTRA") && (
                          <Button disabled={enviando} onClick={() => accion("EXTRA")}>
                            <PlusCircle size={16} /> Sumar como complemento
                          </Button>
                        )}
                        <Button variant="secondary" disabled={enviando} onClick={() => accion("CERRAR")}>
                          <XCircle size={16} /> Ya quedó por otro lado
                        </Button>
                      </div>
                      {t.acciones.includes("EXTRA") && (
                        <p className="text-xs text-muted">
                          «Sumar» agrega estos productos al pedido del día sin tocar los que ya estaban —
                          lo mismo que reenviar la foto con EXTRA en el caption.
                        </p>
                      )}
                    </div>
                  )}
                  {(t.estado === "RESUELTO" || t.estado === "CERRADO") && (
                    <p className="flex items-center gap-2 text-sm text-muted">
                      <CheckCircle2 size={16} /> Cerrado {fmtDateTime(t.resuelto_at)}
                    </p>
                  )}
                </div>

                <div>
                  {foto ? (
                    <a href={foto} target="_blank" rel="noreferrer">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={foto} alt={`Foto del ticket ${t.numero}`}
                           className="max-h-[420px] w-full rounded-lg border border-border object-contain" />
                    </a>
                  ) : t.tiene_foto ? (
                    <p className="text-sm text-muted">Cargando foto…</p>
                  ) : (
                    <p className="text-sm text-muted">Sin foto</p>
                  )}
                  {t.archivo_nombre && <p className="mt-1 truncate text-xs text-muted">{t.archivo_nombre}</p>}
                </div>
              </div>

              <div className="mt-6 border-t border-border pt-4">
                <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                  <MessageSquare size={16} /> Bitácora
                </div>
                <ol className="space-y-2">
                  {(t.eventos ?? []).map((e, i) => (
                    <li key={i} className="text-sm">
                      <span className="text-xs text-muted">{fmtDateTime(e.ts)} · {e.quien}</span>
                      <div>{e.texto}</div>
                    </li>
                  ))}
                </ol>
                <div className="mt-3 flex gap-2">
                  <Textarea rows={2} placeholder="Agregar comentario…" value={comentario}
                            onChange={(e) => setComentario(e.target.value)} />
                  <Button variant="secondary" disabled={enviando || !comentario.trim()} onClick={comentar}>
                    Comentar
                  </Button>
                </div>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

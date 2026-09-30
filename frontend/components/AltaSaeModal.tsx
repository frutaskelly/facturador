"use client";

// «Dar de alta en SAE» desde el catálogo (30-sep-2026).
//
// No escribe en SAE: ENCOLA una solicitud (POST /productos/alta-sae) que toma
// UN solo escritor —el Facturador con su reloj encendido, o el conector del bot
// mientras siga apagado— y reporta qué pasó en cada empresa. Por eso aquí no
// hay botón de «reintentar»: una escritura a SAE nunca se repite, y una alta
// que salió ERROR se vuelve a pedir a mano, después de revisar en SAE.

import { useEffect, useMemo, useState } from "react";
import { Send } from "lucide-react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import type { Page } from "@/lib/hooks";
import type { EsquemaImpuesto, Producto } from "@/lib/types";

const EMPRESAS = ["02", "03", "04", "05"] as const;
// Las que el escritor sabe traducir a UNI_MED (sae_escritura.UNIDADES_CANONICAS).
const UNIDADES = ["CAJA", "KILO", "LITRO", "PAQUETE", "PIEZA"];
// Como en SAE: MAYÚSCULAS, letras, dígitos y guion, hasta 16.
const RE_CLAVE = /^[A-Z0-9-]{1,16}$/;

type Catalogos = {
  lineas: { codigo: string; nombre: string }[];
  esquemas: { codigo: number; descripcion: string; iva: number; ieps: number }[];
  unidades: string[];
};
type ClaveBuscada = { clave: string; empresas: Record<string, { activa: boolean }> };
type Solicitud = {
  id: string;
  estado: "PENDIENTE" | "EN_CURSO" | "OK" | "PARCIAL" | "ERROR";
  tipo: string;
  clave: string;
  empresas: string[];
  solicitada_at: string;
  resultado?: Record<string, { ok?: boolean; error?: string; ya_existia?: boolean }> | null;
  motivo?: string | null;
};
type OpcionClave = { clave: string; unidad: string; esBase: boolean };

const TONO: Record<Solicitud["estado"], "muted" | "accent" | "success" | "warning" | "danger"> = {
  PENDIENTE: "muted", EN_CURSO: "accent", OK: "success", PARCIAL: "warning", ERROR: "danger",
};

/** MAYÚSCULAS sin acentos, como SAE guarda claves y descripciones. */
const mayus = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();

function unidadCanonica(u: string): string {
  const x = mayus(u).trim();
  if (["KG", "KGS", "KILO", "KILOS", "KILOGRAMO", "KILOGRAMOS"].includes(x)) return "KILO";
  if (["PZ", "PZA", "PIEZA", "PIEZAS"].includes(x)) return "PIEZA";
  if (["CJ", "CAJA", "CAJAS"].includes(x)) return "CAJA";
  if (["LT", "LITRO", "LITROS"].includes(x)) return "LITRO";
  if (["PQ", "PAQUETE", "PAQUETES"].includes(x)) return "PAQUETE";
  return "";
}

/** Las claves que el producto ya trae: la de la base y la de cada presentación. */
function clavesDelProducto(p: Producto): OpcionClave[] {
  const base = p.unidad_base ?? "KILO";
  const out: OpcionClave[] = [];
  if (p.clave_sae?.trim()) out.push({ clave: p.clave_sae.trim().toUpperCase(), unidad: base, esBase: true });
  for (const [nombre, v] of Object.entries(p.presentaciones ?? {})) {
    const raw = v as unknown;
    if (nombre === base || !raw || typeof raw !== "object") continue;
    const c = String((raw as { clave_sae?: string }).clave_sae ?? "").trim().toUpperCase();
    if (c && !out.some((o) => o.clave === c)) out.push({ clave: c, unidad: nombre, esBase: false });
  }
  return out;
}

export function AltaSaeModal({
  producto,
  esquemas,
  escritor,
  onClose,
}: {
  producto: Producto | null;
  /** Los esquemas del Facturador: su `codigo` es el número de esquema de SAE. */
  esquemas: EsquemaImpuesto[];
  escritor?: "FACTURADOR" | "BOT";
  onClose: () => void;
}) {
  const toast = useToast();
  const opciones = useMemo(() => (producto ? clavesDelProducto(producto) : []), [producto]);

  const [clave, setClave] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [unidad, setUnidad] = useState("");
  const [linea, setLinea] = useState("");
  const [esquema, setEsquema] = useState("");
  const [sat, setSat] = useState("");
  const [marcadas, setMarcadas] = useState<string[]>([...EMPRESAS]);

  const [catalogos, setCatalogos] = useState<Catalogos | null>(null);
  const [catalogosError, setCatalogosError] = useState<string | null>(null);
  const [existe, setExiste] = useState<Record<string, { activa: boolean }> | null>(null);
  const [historial, setHistorial] = useState<Solicitud[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Al abrir: la forma sale del producto, con la primera clave que ya trae.
  useEffect(() => {
    if (!producto) return;
    const primera = opciones[0];
    setClave(primera?.clave ?? "");
    setUnidad(unidadCanonica(primera?.unidad ?? producto.unidad_base ?? ""));
    setDescripcion(mayus(producto.nombre).slice(0, 60));
    const esq = esquemas.find((e) => e.id === producto.esquema_impuesto_id);
    setEsquema(esq && /^\d+$/.test(esq.codigo.trim()) ? esq.codigo.trim() : "");
    setSat(producto.clave_sat ?? "");
    setLinea("");
    setMarcadas([...EMPRESAS]);
    setError(null);
  }, [producto, opciones, esquemas]);

  // Líneas y esquemas salen EN VIVO de SAE (empresa 02).
  useEffect(() => {
    if (!producto || catalogos) return;
    let vivo = true;
    apiFetch<Catalogos>("/api/v1/sae/catalogos?empresa=02")
      .then((c) => { if (vivo) { setCatalogos(c); setCatalogosError(null); } })
      .catch((e) => { if (vivo) setCatalogosError(e instanceof ApiError ? e.message : "SAE no contestó"); });
    return () => { vivo = false; };
  }, [producto, catalogos]);

  // Con cada clave: ¿en qué empresas ya existe? y ¿qué se ha pedido antes?
  const claveNorm = mayus(clave).replace(/\s+/g, "");
  const claveValida = RE_CLAVE.test(claveNorm);
  useEffect(() => {
    if (!producto || !claveValida) { setExiste(null); setHistorial([]); return; }
    let vivo = true;
    setBuscando(true);
    setError(null);
    const t = setTimeout(async () => {
      try {
        const q = encodeURIComponent(claveNorm);
        const [claves, sols] = await Promise.all([
          apiFetch<ClaveBuscada[]>(`/api/v1/productos/claves-sae?clave=${q}&limit=5`),
          apiFetch<Page<Solicitud>>(`/api/v1/productos/alta-sae?clave=${q}&tipo=TODAS&limit=5`),
        ]);
        if (!vivo) return;
        const exacta = claves.find((c) => c.clave.toUpperCase() === claveNorm);
        const ya = exacta?.empresas ?? {};
        setExiste(ya);
        setHistorial(sols.items);
        setMarcadas(EMPRESAS.filter((e) => !ya[e]));
      } catch (e) {
        if (vivo) setError(e instanceof ApiError ? e.message : "No pude revisar la clave en SAE");
      } finally {
        if (vivo) setBuscando(false);
      }
    }, 300);
    return () => { vivo = false; clearTimeout(t); };
  }, [producto, claveNorm, claveValida]);

  if (!producto) return null;

  const faltan = EMPRESAS.filter((e) => !existe?.[e]);
  const viva = historial.find((s) => s.tipo === "ALTA" && (s.estado === "PENDIENTE" || s.estado === "EN_CURSO"));
  const opcion = opciones.find((o) => o.clave === claveNorm);
  const listo = claveValida && descripcion.trim() && unidad && linea && esquema
    && /^\d{8}$/.test(sat.trim()) && marcadas.length > 0 && existe !== null && !buscando;

  async function pedir() {
    if (!producto || !listo) return;
    setEnviando(true);
    setError(null);
    try {
      const sol = await apiFetch<Solicitud>("/api/v1/productos/alta-sae", {
        method: "POST",
        body: JSON.stringify({
          clave: claveNorm,
          // Ligada al producto: al confirmarse, la clave se le estampa si no
          // traía (y nunca la de una presentación sobre la base).
          producto_id: producto.id,
          descripcion: mayus(descripcion).trim().slice(0, 60),
          unidad,
          linea,
          esquema: Number(esquema),
          sat: sat.trim(),
          // La unidad SAT del producto es la de SU base; para otra presentación
          // la pone el escritor según la unidad (PIEZA→H87, CAJA→XBX…).
          sat_unidad: opcion && !opcion.esBase ? undefined : producto.unidad_sat,
          empresas: marcadas,
          origen: "UI",
        }),
      });
      setHistorial((h) => [sol, ...h.filter((x) => x.id !== sol.id)]);
      toast.success(`Alta de ${sol.clave} pedida (${sol.empresas.join(", ")})`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo pedir el alta");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={`Dar de alta en SAE · ${producto.nombre}`}
      wide
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cerrar</Button>
          <Button onClick={pedir} disabled={!listo || enviando || !!viva}>
            <Send size={16} /> {enviando ? "Pidiendo…" : "Pedir alta en SAE"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-muted">
          Se crea el artículo en las empresas marcadas con precio en 0 (el precio vive en el
          Facturador). {escritor === "FACTURADOR"
            ? "Lo escribe el Facturador en uno o dos minutos."
            : "Lo escribe el conector del bot en su siguiente pasada."}
        </p>

        {opciones.length > 1 && (
          <div className="flex flex-wrap gap-2">
            {opciones.map((o) => (
              <button
                key={o.clave}
                type="button"
                onClick={() => { setClave(o.clave); setUnidad(unidadCanonica(o.unidad)); }}
                className={`rounded-full border px-3 py-1 text-xs ${o.clave === claveNorm
                  ? "border-accent bg-blue-50 text-blue-700" : "border-border hover:bg-surface-2"}`}
              >
                {o.clave} · {o.unidad}
              </button>
            ))}
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Clave en SAE" required
                 hint={clave && !claveValida ? "Letras, números y guion; hasta 16." : undefined}>
            <Input value={clave} maxLength={16} placeholder="AJOKG"
                   onChange={(e) => setClave(e.target.value.toUpperCase())} />
          </Field>
          <Field label="Unidad" required>
            <Select value={unidad} onChange={(e) => setUnidad(e.target.value)}>
              <option value="">— Elige —</option>
              {UNIDADES.map((u) => <option key={u} value={u}>{u}</option>)}
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <Field label="Descripción en SAE" required hint={`${descripcion.length}/60`}>
              <Input value={descripcion} maxLength={60}
                     onChange={(e) => setDescripcion(e.target.value.toUpperCase())} />
            </Field>
          </div>
          <Field label="Línea de SAE" required>
            {catalogos ? (
              <Select value={linea} onChange={(e) => setLinea(e.target.value)}>
                <option value="">— Elige —</option>
                {catalogos.lineas.map((l) => (
                  <option key={l.codigo} value={l.codigo}>{l.codigo} · {l.nombre}</option>
                ))}
              </Select>
            ) : (
              <Input value={linea} maxLength={10} placeholder="FRUVE"
                     onChange={(e) => setLinea(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))} />
            )}
          </Field>
          <Field label="Esquema de impuestos" required>
            {catalogos ? (
              <Select value={esquema} onChange={(e) => setEsquema(e.target.value)}>
                <option value="">— Elige —</option>
                {catalogos.esquemas.map((e) => (
                  <option key={e.codigo} value={String(e.codigo)}>
                    {e.codigo} · {e.descripcion} (IVA {e.iva}%{e.ieps ? `, IEPS ${e.ieps}%` : ""})
                  </option>
                ))}
              </Select>
            ) : (
              <Input value={esquema} inputMode="numeric" placeholder="2"
                     onChange={(e) => setEsquema(e.target.value.replace(/\D/g, ""))} />
            )}
          </Field>
          <Field label="Clave SAT" required hint="8 dígitos">
            <Input value={sat} maxLength={8} inputMode="numeric"
                   onChange={(e) => setSat(e.target.value.replace(/\D/g, ""))} />
          </Field>
        </div>

        {catalogosError && (
          <Alert tone="warning" title="No pude leer líneas y esquemas de SAE">
            {catalogosError}. Escríbelos a mano tal como están en SAE.
          </Alert>
        )}

        <div>
          <span className="mb-1 block text-sm font-medium">Empresas de SAE</span>
          <div className="flex flex-wrap gap-4">
            {EMPRESAS.map((e) => {
              const ya = existe?.[e];
              return (
                <label key={e} className={`flex items-center gap-2 text-sm ${ya ? "text-muted" : ""}`}>
                  <input
                    type="checkbox"
                    disabled={!!ya || existe === null}
                    checked={!ya && marcadas.includes(e)}
                    onChange={(ev) => setMarcadas((m) => ev.target.checked
                      ? [...m, e].sort() : m.filter((x) => x !== e))}
                  />
                  {e}
                  {ya && <Badge tone={ya.activa ? "success" : "muted"}>{ya.activa ? "ya existe" : "de baja"}</Badge>}
                </label>
              );
            })}
          </div>
          {buscando && <p className="mt-1 text-xs text-muted">Revisando la clave en SAE…</p>}
        </div>

        {existe !== null && faltan.length === 0 && (
          <Alert tone="info" title={`${claveNorm} ya existe en las cuatro empresas`}>
            No hay nada que dar de alta. Si el producto no la trae, pónsela en «Editar».
          </Alert>
        )}
        {existe !== null && Object.values(existe).some((x) => !x.activa) && (
          <Alert tone="warning">
            En alguna empresa la clave está de baja: reactivarla es otro trámite, no un alta.
          </Alert>
        )}
        {viva && (
          <Alert tone="info" title={`Ya hay un alta de ${viva.clave} en camino`}>
            Pedida el {new Date(viva.solicitada_at).toLocaleString("es-MX")} para {viva.empresas.join(", ")}.
            Espera su resultado antes de pedir otra.
          </Alert>
        )}
        {error && <Alert tone="danger">{error}</Alert>}

        {historial.length > 0 && (
          <div>
            <span className="mb-1 block text-sm font-medium">Solicitudes de esta clave</span>
            <ul className="divide-y divide-border rounded-lg border border-border text-sm">
              {historial.map((s) => (
                <li key={s.id} className="px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={TONO[s.estado]}>{s.estado}</Badge>
                    <span className="text-muted">{s.tipo === "CAMBIO" ? "Cambio" : "Alta"}</span>
                    <span>{s.empresas.join(", ")}</span>
                    <span className="ml-auto text-xs text-muted">
                      {new Date(s.solicitada_at).toLocaleString("es-MX")}
                    </span>
                  </div>
                  {s.resultado && Object.entries(s.resultado).some(([, r]) => !r.ok) && (
                    <div className="mt-1 text-xs text-danger">
                      {Object.entries(s.resultado).filter(([, r]) => !r.ok)
                        .map(([e, r]) => `${e}: ${r.error ?? "sin detalle"}`).join(" · ")}
                    </div>
                  )}
                  {s.motivo && <div className="mt-1 text-xs text-muted">{s.motivo}</div>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Modal>
  );
}

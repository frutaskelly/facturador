"use client";

// Elegir la clave de SAE de un producto SIN salir del aviso de la remisión.
//
// NO guarda: reporta lo elegido y la pantalla escribe todas juntas con el botón
// «Guardar». Corregir cinco claves son cinco elecciones y UNA recarga, no cinco
// recargas que te devuelven al principio de la lista cada vez.
//
// Lo primero que muestra es «lo que este producto ya usa», porque casi nunca
// falta la clave: está guardada donde no ampara. El CILANTRO de EHMO tenía
// CILANTROKG amarrado a Tabasco y la remisión era de Pachuca — la clave existía
// y la empresa 02 la tenía viva.
//
// Y deja capturar a mano lo que no esté en el espejo: quien no corre el bot no
// tiene espejo, y aun así tiene que poder trabajar.
//
// Sin remisión ni cliente (la ficha del producto) busca en el espejo de TODAS
// las empresas y dice en cuáles vive cada clave: SANDIA PZA existe en la 03 y
// está de baja en la 02, y eso se tiene que ver antes de elegirla.

import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Check, ChevronDown } from "lucide-react";

import { FloatingPanel } from "@/components/ui/FloatingPanel";
import { apiFetch } from "@/lib/api";

type ClaveSugerida = {
  clave: string;
  descripcion?: string | null;
  activa: boolean;
  producto_id?: string | null;
  producto_nombre?: string | null;
  /** Solo en la búsqueda sin cliente: en qué empresas vive y si está viva. */
  empresas?: Record<string, { activa: boolean }>;
};
type ClaveBuscada = {
  clave: string;
  descripcion?: string | null;
  empresas: Record<string, { activa: boolean }>;
  producto_id?: string | null;
  producto_nombre?: string | null;
};
type ClaveEnUso = {
  clave: string;
  descripcion?: string | null;
  activa?: boolean | null;
  de_donde: string;
};
type Respuesta = {
  empresa?: string | null;
  espejo: boolean;
  motivo?: string | null;
  claves: ClaveSugerida[];
  ya_usa: ClaveEnUso[];
};

/** La lista vive al final de <body> (FloatingPanel), no junto a la caja: el Tab
 *  de la página ya no pasa por sus opciones como antes. Se recorre a mano y, al
 *  salir por una orilla, dice por cuál para que el foco regrese a la caja. */
function tabEnLista(e: ReactKeyboardEvent<HTMLElement>): "dentro" | "antes" | "despues" {
  e.preventDefault();
  const f = Array.from(
    e.currentTarget.querySelectorAll<HTMLElement>("input:not([disabled]), button:not([disabled])"),
  );
  const j = f.indexOf(document.activeElement as HTMLElement) + (e.shiftKey ? -1 : 1);
  if (j >= 0 && j < f.length) {
    f[j].focus();
    return "dentro";
  }
  return e.shiftKey ? "antes" : "despues";
}

export function ClaveSaeInline({
  remisionId,
  clienteId,
  sucursalId,
  productoId,
  productoNombre,
  value,
  onChange,
  onElegir,
  compacto,
}: {
  /** Remisión ya guardada: de ella salen el cliente y la plaza. */
  remisionId?: string;
  /** La captura todavía no tiene remisión: pregunta por cliente y plaza. */
  clienteId?: string | null;
  sucursalId?: string | null;
  /** Vacío en un producto que todavía no se guarda. */
  productoId: string;
  productoNombre: string;
  /** La clave elegida y todavía sin guardar. */
  value: string;
  /** Cada tecleo (para que el recuadro muestre lo que se escribe). */
  onChange: (clave: string) => void;
  /** Sólo al ELEGIR del desplegable o dar Enter: es el momento de guardar
   *  donde la pantalla guarda al vuelo (la captura), sin un PATCH por tecla. */
  onElegir?: (clave: string) => void;
  /** Dentro de la tabla de captura, donde el ancho lo manda la columna. */
  compacto?: boolean;
}) {
  const [abierto, setAbierto] = useState(false);
  const [texto, setTexto] = useState(value);
  const [datos, setDatos] = useState<Respuesta | null>(null);
  const [cargando, setCargando] = useState(false);
  const caja = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const sinAbrir = useRef(false);   // foco devuelto a la caja sin reabrir la lista
  const listaId = useId();

  // El valor que manda es el de la pantalla (se limpia al guardar).
  useEffect(() => setTexto(value), [value]);

  // La lista vive en un portal (FloatingPanel), fuera de `caja`: un clic en
  // ella no cuenta como «fuera».
  useEffect(() => {
    function fuera(e: MouseEvent) {
      const t = e.target as Node;
      if (caja.current?.contains(t) || panelRef.current?.contains(t)) return;
      setAbierto(false);
    }
    document.addEventListener("mousedown", fuera);
    return () => document.removeEventListener("mousedown", fuera);
  }, []);

  // Al abrir busca por el nombre del producto; después, por lo que se teclee.
  useEffect(() => {
    if (!abierto) return;
    let vivo = true;
    setCargando(true);
    const q = (texto.trim() || productoNombre).slice(0, 80);
    const t = setTimeout(() => {
      if (!remisionId && !clienteId) {
        // Ficha del producto: el espejo completo, sin cliente ni plaza.
        if (!q) { setCargando(false); return; }
        apiFetch<ClaveBuscada[]>(`/api/v1/productos/claves-sae?${new URLSearchParams({ q, limit: "20" })}`)
          .then((items) => {
            if (!vivo) return;
            setDatos({
              empresa: null,
              espejo: items.length > 0,
              motivo: items.length ? null : "Catálogo de SAE",
              claves: items.map((c) => ({
                clave: c.clave,
                descripcion: c.descripcion,
                activa: Object.values(c.empresas).some((e) => e.activa),
                producto_id: c.producto_id,
                producto_nombre: c.producto_nombre,
                empresas: c.empresas,
              })),
              ya_usa: [],
            });
            setCargando(false);
          })
          .catch(() => { if (vivo) { setDatos(null); setCargando(false); } });
        return;
      }
      const p = new URLSearchParams({ q });
      if (productoId) p.set("producto_id", productoId);
      if (!remisionId && sucursalId) p.set("sucursal_id", sucursalId);
      const url = remisionId
        ? `/api/v1/remisiones/${remisionId}/claves-sae?${p.toString()}`
        : `/api/v1/clientes/${clienteId}/claves-sae?${p.toString()}`;
      apiFetch<Respuesta>(url)
        .then((r) => { if (vivo) { setDatos(r); setCargando(false); } })
        .catch(() => { if (vivo) { setDatos(null); setCargando(false); } });
    }, 250);
    return () => { vivo = false; clearTimeout(t); };
  }, [abierto, texto, productoId, productoNombre, remisionId, clienteId, sucursalId]);

  const limpia = texto.trim().toUpperCase();
  const enLista = (datos?.claves ?? []).some((c) => c.clave.toUpperCase() === limpia);
  const enUso = (datos?.ya_usa ?? []).some((c) => c.clave.toUpperCase() === limpia);

  function elegir(clave: string) {
    setTexto(clave);
    onChange(clave);
    // Cierra y regresa el foco a la caja: la Fila (clic o Tab + Enter) se
    // desmonta al final de <body> y el siguiente Tab saltaría a la X del Modal
    // o al inicio de la página en vez de a la siguiente celda.
    volverACaja(true);
    onElegir?.(clave);
  }

  /** Devuelve el foco a la caja desde la lista; `cerrar` la cierra sin que el
   *  onFocus de la caja la vuelva a abrir. */
  function volverACaja(cerrar: boolean) {
    sinAbrir.current = cerrar;
    if (cerrar) setAbierto(false);
    inputRef.current?.focus({ preventScroll: true });
    sinAbrir.current = false;
  }

  /** «02 03 04» y cuáles de ésas la tienen de baja. */
  function enEmpresas(empresas?: Record<string, { activa: boolean }>) {
    if (!empresas) return { donde: null, baja: null };
    const ks = Object.keys(empresas).sort();
    const baja = ks.filter((k) => !empresas[k].activa);
    return {
      donde: ks.length ? `empresas ${ks.join(" ")}` : null,
      baja: baja.length ? `de BAJA en ${baja.join(" ")}` : null,
    };
  }

  function Fila({
    clave, detalle, aviso, tono = "normal",
  }: { clave: string; detalle?: string | null; aviso?: string | null; tono?: "normal" | "usa" }) {
    return (
      <button
        type="button"
        role="option"
        aria-selected={clave.toUpperCase() === value.toUpperCase()}
        onClick={() => elegir(clave)}
        className={`flex w-full items-start justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-surface-2 disabled:opacity-60 ${
          tono === "usa" ? "bg-success/5" : ""
        }`}
      >
        <span className="min-w-0">
          <span className="font-medium">{clave}</span>
          {detalle ? <span className="ml-2 text-xs text-muted">{detalle}</span> : null}
          {aviso ? <span className="block text-xs text-warning">{aviso}</span> : null}
        </span>
        <Check size={14} className="mt-0.5 shrink-0 text-muted" />
      </button>
    );
  }

  return (
    <div ref={caja} className={`relative align-middle ${compacto ? "block w-full" : "inline-block w-72"}`}>
      <div className={`flex items-center gap-1 rounded-lg border bg-background px-2 ${
        compacto ? "py-1.5" : "py-1"
      } ${value ? "border-accent" : "border-border"}`}>
        <input
          ref={inputRef}
          className={`w-full bg-transparent outline-none ${compacto ? "text-xs" : "text-sm"}`}
          placeholder={compacto ? "sin clave" : "Clave SAE: buscar o escribir…"}
          role="combobox"
          aria-autocomplete="list"
          // Con la lista abierta, Enter no dispara la acción principal del Modal.
          aria-expanded={abierto}
          aria-controls={abierto ? listaId : undefined}
          value={texto}
          onFocus={() => { if (!sinAbrir.current) setAbierto(true); }}
          // Tras elegir, la caja sigue enfocada y onFocus no se repite.
          onClick={() => setAbierto(true)}
          onChange={(e) => { setTexto(e.target.value); onChange(e.target.value.trim().toUpperCase()); setAbierto(true); }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && limpia) { e.preventDefault(); elegir(limpia); }
            else if (e.key === "Escape") {
              // Con la lista abierta, Escape cierra solo la lista.
              if (abierto) { e.preventDefault(); e.stopPropagation(); }
              setAbierto(false);
            } else if (e.key === "Tab" && abierto) {
              // Como cuando la lista iba pegada a la caja: Tab entra a las opciones.
              const primera = e.shiftKey
                ? null
                : panelRef.current?.querySelector<HTMLElement>("button:not([disabled])");
              if (primera) { e.preventDefault(); primera.focus(); }
              else setAbierto(false);
            }
          }}
        />
        <ChevronDown size={14} className="shrink-0 text-muted" />
      </div>

      {/* En portal: ni el Modal ni la tabla (overflow-auto) la recortan. Misma
          alineación de antes: orilla derecha con la caja, 24rem de ancho mínimo. */}
      <FloatingPanel
        ref={panelRef}
        anchorRef={caja}
        open={abierto}
        align="end"
        minWidth={384}
        maxHeight={320}
        id={listaId}
        role="listbox"
        // Un clic en una zona del panel que no es control (encabezado, texto,
        // barra de scroll) no debe tirar el foco a <body>.
        onMouseDown={(e) => {
          if (!(e.target as HTMLElement).closest("input, button, textarea, select")) e.preventDefault();
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            volverACaja(true);
          } else if (e.key === "Tab") {
            const r = tabEnLista(e);
            if (r !== "dentro") volverACaja(r === "despues");
          }
        }}
        className="overflow-auto rounded-lg border border-border bg-surface shadow-lg"
      >
        {(datos?.ya_usa?.length ?? 0) > 0 ? (
          <>
            <div className="px-3 pt-2 text-[11px] uppercase tracking-wide text-muted">
              Este producto ya usa
            </div>
            {datos!.ya_usa.map((c) => (
              <Fila
                key={`usa-${c.clave}`}
                tono="usa"
                clave={c.clave}
                detalle={`${c.descripcion ?? ""}${c.descripcion ? " · " : ""}${c.de_donde}`}
                aviso={
                  c.activa === false
                    ? "existe en SAE pero está de BAJA"
                    : c.activa === null
                    ? "sin espejo: no se puede verificar"
                    : null
                }
              />
            ))}
          </>
        ) : null}

        <div className="px-3 pt-2 text-[11px] uppercase tracking-wide text-muted">
          {datos?.espejo
            ? datos.empresa ? `Catálogo de SAE · empresa ${datos.empresa}` : "Catálogo de SAE · todas las empresas"
            : datos?.motivo ?? "Catálogo de SAE"}
        </div>
        {cargando ? <div className="px-3 py-2 text-sm text-muted">Buscando…</div> : null}
        {!cargando && (datos?.claves?.length ?? 0) === 0 ? (
          <div className="px-3 py-2 text-sm text-muted">
            {datos?.espejo ? "Sin coincidencias." : "No hay espejo con el que buscar."}
          </div>
        ) : null}
        {!cargando &&
          (datos?.claves ?? []).map((c) => {
            const { donde, baja } = enEmpresas(c.empresas);
            return (
              <Fila
                key={c.clave}
                clave={c.clave}
                detalle={[c.descripcion, donde].filter(Boolean).join(" · ")}
                aviso={
                  !c.activa
                    ? "dada de BAJA en SAE: no factura"
                    : [baja, c.producto_id && c.producto_id !== productoId
                        ? `también la usa ${c.producto_nombre}` : null]
                        .filter(Boolean).join(" · ") || null
                }
              />
            );
          })}

        {limpia && !enLista && !enUso ? (
          <button
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => elegir(limpia)}
            className="flex w-full flex-col items-start border-t border-border px-3 py-2 text-left text-sm hover:bg-accent/5"
          >
            <span className="font-medium text-accent">Usar «{limpia}» tal cual</span>
            {datos?.espejo ? (
              <span className="text-xs text-warning">
                SAE no la conoce: el masivo se seguiría deteniendo por esta partida
              </span>
            ) : null}
          </button>
        ) : null}
      </FloatingPanel>
    </div>
  );
}

"use client";

// Una partida sin clave SAE del cliente, y las DOS salidas que tiene.
//
// Antes el aviso ofrecía una sola —«ve a Clientes → Catálogo y captúrale una
// clave»— y es la menos frecuente: capturar una clave es dar de alta un
// artículo en SAE por la puerta de atrás. Lo normal es que la partida deba ir
// al producto que el cliente YA tiene y lo que falló fue el cruce. Así que aquí
// están las dos, y hay que elegir cuál es el caso:
//
//   • «Va a otro producto»  → se re-apunta la línea (POST …/cruzar).
//   • «Es este producto»    → se le captura su clave (PUT …/catalogo/…), y se
//     elige del espejo de SAE en vez de teclearla de memoria: así salió la
//     FRESADOMOPZ que SAE no conocía y descartó al importar (14-sep-2026).
//
// Lo que no se decide solo: el precio al cruzar, hasta dónde llega lo aprendido
// y si la clave vale para todas las plazas o solo para esta.

import { useEffect, useMemo, useState } from "react";

import { ProductoCombobox, type ProductoPick } from "@/components/ProductoCombobox";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { fmtMoney, fmtNumber } from "@/lib/format";
import type { LineaRemision, ProductoClienteRow, RemisionDetail } from "@/lib/types";

/** El texto tal como venía en la orden: la bandeja lo deja anotado en la nota
 *  de la línea («Como venía: «…»»). Es lo único que sirve para enseñar el
 *  cruce, y sin él el diálogo no ofrece aprender nada. */
export function textoOriginalDe(notas?: string | null): string {
  const m = /Como venía:\s*«([^»]+)»/.exec(notas ?? "");
  return (m?.[1] ?? "").trim();
}

export type ModoPartida = "cruzar" | "clave";
type Alcance = "cliente" | "plaza" | "global";

type ClaveSae = {
  clave: string;
  descripcion?: string | null;
  activa: boolean;
  producto_id?: string | null;
  producto_nombre?: string | null;
};
type ClavesSae = {
  empresa?: string | null;
  espejo: boolean;
  motivo?: string | null;
  claves: ClaveSae[];
};

export function PartidaSinClaveDialog({
  open,
  modoInicial = "cruzar",
  remision,
  linea,
  clienteNombre,
  plazaNombre,
  puedeCatalogo,
  onClose,
  onListo,
}: {
  open: boolean;
  /** Con cuál de las dos salidas abre (la pantalla tiene un enlace para cada
   *  una); adentro se puede cambiar. */
  modoInicial?: ModoPartida;
  /** La remisión completa: de ella salen el cliente, la plaza y el contexto
   *  con el que se cotiza el producto nuevo. */
  remision: RemisionDetail | null;
  /** La partida sin clave. */
  linea: LineaRemision | null;
  clienteNombre: string;
  plazaNombre?: string;
  /** Capturar la clave escribe en el catálogo del cliente (`cliente:gestionar`);
   *  sin ese permiso esa salida no se ofrece. */
  puedeCatalogo: boolean;
  onClose: () => void;
  /** Ya quedó (cruzada o con clave): la pantalla recarga lista y detalle. */
  onListo: () => void;
}) {
  const toast = useToast();
  const [modo, setModo] = useState<ModoPartida>(modoInicial);
  const [catalogo, setCatalogo] = useState<ProductoClienteRow[] | null>(null);
  const [guardando, setGuardando] = useState(false);

  // ── cruzar ──
  const [pick, setPick] = useState<ProductoPick | null>(null);
  const [presentacion, setPresentacion] = useState("");
  const [cantidad, setCantidad] = useState("");
  const [precioModo, setPrecioModo] = useState<"mantener" | "lista">("mantener");
  // undefined = cotizando; null = el producto no tiene precio para este cliente.
  const [precioLista, setPrecioLista] = useState<string | null | undefined>(undefined);
  const [aprender, setAprender] = useState(true);
  const [alcance, setAlcance] = useState<Alcance>("cliente");

  // ── capturar la clave ──
  const [claveTexto, setClaveTexto] = useState("");
  const [sugerencias, setSugerencias] = useState<ClavesSae | null>(null);
  const [alcanceClave, setAlcanceClave] = useState<"generica" | "plaza">("generica");

  const texto = textoOriginalDe(linea?.notas);
  const clienteId = remision?.cliente_facturacion_id ?? null;

  useEffect(() => {
    if (!open) return;
    setModo(puedeCatalogo ? modoInicial : "cruzar");
    setPick(null);
    setPresentacion("");
    setCantidad(String(linea?.cantidad_solicitada ?? ""));
    setPrecioModo("mantener");
    setPrecioLista(undefined);
    setAprender(true);
    setAlcance("cliente");
    setClaveTexto("");
    setSugerencias(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, linea?.id]);

  // El catálogo del cliente entero, una vez por apertura: con unos cientos de
  // filas sale más barato que preguntar clave por clave mientras se teclea.
  useEffect(() => {
    if (!open || !clienteId) return;
    let vivo = true;
    setCatalogo(null);
    apiFetch<ProductoClienteRow[]>(`/api/v1/clientes/${clienteId}/catalogo`)
      .then((rows) => { if (vivo) setCatalogo(rows); })
      .catch(() => { if (vivo) setCatalogo([]); });
    return () => { vivo = false; };
  }, [open, clienteId]);

  // {producto → clave} con la MISMA regla del export: la fila de la plaza pisa
  // a la genérica, y la clave de otra plaza no ampara (se ignora).
  const claves = useMemo(() => {
    if (!catalogo) return null;
    const suc = remision?.sucursal_id ?? null;
    const m = new Map<string, string>();
    for (const row of catalogo) {
      const codigo = (row.codigo_cliente ?? "").trim();
      if (codigo && row.sucursal_id == null) m.set(row.producto_id, codigo);
    }
    for (const row of catalogo) {
      const codigo = (row.codigo_cliente ?? "").trim();
      if (codigo && suc && row.sucursal_id === suc) m.set(row.producto_id, codigo);
    }
    return m;
  }, [catalogo, remision?.sucursal_id]);

  // Este producto ya tiene clave en OTRA plaza (caso EHMO: Pachuca y
  // Villahermosa usan claves distintas). Entonces la genérica sería mentira
  // para esa otra plaza: se arranca con la clave acotada a esta.
  const otrasPlazas = useMemo(
    () =>
      (catalogo ?? []).filter(
        (r) =>
          r.producto_id === linea?.producto_id &&
          r.sucursal_id != null &&
          r.sucursal_id !== remision?.sucursal_id &&
          (r.codigo_cliente ?? "").trim(),
      ),
    [catalogo, linea?.producto_id, remision?.sucursal_id],
  );
  useEffect(() => {
    if (!open) return;
    setAlcanceClave(otrasPlazas.length > 0 && remision?.sucursal_id ? "plaza" : "generica");
  }, [open, otrasPlazas.length, remision?.sucursal_id]);

  function onPick(p: ProductoPick | null) {
    setPick(p);
    setPrecioModo("mantener");
    if (!p) { setPresentacion(""); return; }
    const suyas = Object.keys(p.presentaciones ?? {});
    // La unidad con la que ESE cliente compra manda; si no la tiene capturada,
    // se conserva la de la partida, y en último caso la default del producto.
    const delCliente = (catalogo ?? [])
      .find((r) => r.producto_id === p.producto_id && (r.presentacion ?? "").trim())
      ?.presentacion?.trim().toUpperCase();
    const candidatas = [delCliente, linea?.presentacion, p.presentacion_default, p.unidad_base];
    setPresentacion(
      candidatas.find((x): x is string => !!x && suyas.includes(x)) ?? suyas[0] ?? "",
    );
  }

  // Precio de lista del producto NUEVO, con el contexto de la remisión (misma
  // resolución que la captura: cliente, plaza, serie y proyecto).
  useEffect(() => {
    if (!open || modo !== "cruzar" || !pick || !remision || !presentacion || !(Number(cantidad) > 0)) {
      setPrecioLista(undefined);
      return;
    }
    let vivo = true;
    setPrecioLista(undefined);
    const p = new URLSearchParams({ producto_id: pick.producto_id, presentacion, cantidad });
    p.set("cliente_id", remision.cliente_facturacion_id);
    if (remision.sucursal_id) p.set("sucursal_id", remision.sucursal_id);
    if (remision.serie_id) p.set("serie_id", remision.serie_id);
    if (remision.proyecto_id) p.set("proyecto_id", remision.proyecto_id);
    apiFetch<{ precio: string | null }>(`/api/v1/precios/cotizar?${p.toString()}`)
      .then((r) => { if (vivo) setPrecioLista(r.precio ?? null); })
      .catch(() => { if (vivo) setPrecioLista(null); });
    return () => { vivo = false; };
  }, [open, modo, pick, presentacion, cantidad, remision]);

  // El espejo de SAE, buscado con lo que se va tecleando en la caja de la clave
  // (arranca con el nombre del producto: casi siempre ahí está su artículo).
  useEffect(() => {
    if (!open || modo !== "clave" || !remision) return;
    const q = (claveTexto.trim() || linea?.producto_nombre || "").slice(0, 80);
    let vivo = true;
    const t = setTimeout(() => {
      apiFetch<ClavesSae>(
        `/api/v1/remisiones/${remision.id}/claves-sae?q=${encodeURIComponent(q)}`,
      )
        .then((r) => { if (vivo) setSugerencias(r); })
        .catch(() => { if (vivo) setSugerencias(null); });
    }, 250);
    return () => { vivo = false; clearTimeout(t); };
  }, [open, modo, claveTexto, remision, linea?.producto_nombre]);

  const claveLimpia = claveTexto.trim().toUpperCase();
  const claveEnSae = sugerencias?.claves.find((c) => c.clave.trim().toUpperCase() === claveLimpia);
  const claveNueva = pick && claves ? claves.get(pick.producto_id) : undefined;
  const hayLista = precioLista != null && Number(precioLista) > 0;
  const precio = precioModo === "lista" && hayLista ? "lista" : "mantener";
  const precioFinal = precio === "lista" ? Number(precioLista) : Number(linea?.precio_unitario ?? 0);
  const cambiaPresentacion = !!pick && !!linea && presentacion !== linea.presentacion;

  const validoCruzar =
    !!pick && !!linea && !!presentacion && Number(cantidad) > 0 &&
    (pick.producto_id !== linea.producto_id || presentacion !== linea.presentacion ||
     Number(cantidad) !== Number(linea.cantidad_solicitada) || precio === "lista");
  const valido = modo === "cruzar" ? validoCruzar : claveLimpia.length > 0;

  async function cruzar() {
    if (!remision || !linea || !pick) return;
    await apiFetch(`/api/v1/remisiones/${remision.id}/lineas/${linea.id}/cruzar`, {
      method: "POST",
      body: JSON.stringify({
        producto_id: pick.producto_id,
        presentacion,
        cantidad_solicitada: cantidad,
        precio,
        ...(aprender && texto ? { aprender_texto: texto, aprender_alcance: alcance } : {}),
      }),
    });
    toast.success(`La partida ${linea.numero_linea} ahora va a ${pick.nombre}`);
  }

  async function capturarClave() {
    if (!remision || !linea) return;
    await apiFetch(`/api/v1/clientes/${remision.cliente_facturacion_id}/catalogo/${linea.producto_id}`, {
      method: "PUT",
      body: JSON.stringify({
        codigo_cliente: claveLimpia,
        sucursal_id: alcanceClave === "plaza" ? remision.sucursal_id : null,
      }),
    });
    toast.success(
      `${linea.producto_nombre ?? "El producto"} sale a SAE como ${claveLimpia}` +
        (alcanceClave === "plaza" && plazaNombre ? ` en ${plazaNombre}` : ""),
    );
  }

  async function guardar() {
    if (!valido) return;
    setGuardando(true);
    try {
      if (modo === "cruzar") await cruzar();
      else await capturarClave();
      onListo();
    } catch (e) {
      toast.error(
        e instanceof ApiError
          ? e.message
          : modo === "cruzar" ? "No se pudo cruzar la partida" : "No se pudo guardar la clave",
      );
    } finally {
      setGuardando(false);
    }
  }

  const nombreViejo = linea?.producto_nombre ?? "el producto de la partida";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Partida sin clave SAE del cliente"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={guardando}>
            Cancelar
          </Button>
          <Button onClick={() => void guardar()} disabled={!valido || guardando}>
            {guardando
              ? "Guardando…"
              : modo === "cruzar" ? "Cruzar la partida" : "Guardar la clave"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-border bg-surface-2 p-3 text-sm">
          <div className="font-medium">
            <span className="text-muted">{linea?.numero_linea}.</span> {nombreViejo}
          </div>
          <div className="mt-0.5 text-xs text-muted tabular-nums">
            {fmtNumber(linea?.cantidad_solicitada ?? 0)} {linea?.presentacion} ·{" "}
            {fmtMoney(linea?.precio_unitario ?? 0)} c/u · {clienteNombre}
            {plazaNombre ? ` (${plazaNombre})` : ""}
          </div>
          {texto ? (
            <div className="mt-1 text-xs text-muted">Como venía en la orden: «{texto}»</div>
          ) : null}
        </div>

        {puedeCatalogo ? (
          <fieldset className="space-y-1.5">
            <legend className="mb-1 text-sm font-medium">¿Qué pasó con esta partida?</legend>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                className="mt-0.5"
                checked={modo === "cruzar"}
                onChange={() => setModo("cruzar")}
              />
              <span>
                <b>Va a otro producto</b> que {clienteNombre} ya tiene — el cruce falló.
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                className="mt-0.5"
                checked={modo === "clave"}
                onChange={() => setModo("clave")}
              />
              <span>
                <b>Es este producto</b> y es nuevo para {clienteNombre}: captúrale su clave.
              </span>
            </label>
          </fieldset>
        ) : null}

        {modo === "cruzar" ? (
          <>
            <Field
              label="Va en realidad a"
              hint={
                claves
                  ? "La etiqueta verde es la clave que este cliente tiene en SAE para ese producto."
                  : "Cargando el catálogo del cliente…"
              }
            >
              <ProductoCombobox
                onSelect={(p) => onPick(p)}
                placeholder="Buscar el producto del catálogo…"
                clienteId={clienteId}
                claves={claves}
                presentacion={linea?.presentacion}
                unidadBase={linea?.presentacion}
                // El alias se escribe abajo, con el alcance que elija quien cruza.
                aprenderAlias={false}
              />
            </Field>

            {pick ? (
              <>
                {claveNueva ? (
                  <p className="text-xs text-success">
                    {pick.nombre} sale a SAE como <b>{claveNueva}</b>
                    {plazaNombre ? ` en ${plazaNombre}` : ""}.
                  </p>
                ) : (
                  <p className="text-xs text-warning">
                    {clienteNombre} tampoco tiene clave de {pick.nombre}
                    {plazaNombre ? ` en ${plazaNombre}` : ""}: el export se seguiría deteniendo por
                    esta partida.
                  </p>
                )}

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="Presentación">
                    <Select value={presentacion} onChange={(e) => setPresentacion(e.target.value)}>
                      {Object.keys(pick.presentaciones ?? {}).map((p) => (
                        <option key={p} value={p}>{p}</option>
                      ))}
                    </Select>
                  </Field>
                  <Field
                    label="Cantidad"
                    hint={
                      cambiaPresentacion
                        ? `La partida venía en ${linea?.presentacion}: revisa el número.`
                        : undefined
                    }
                  >
                    <Input
                      inputMode="decimal"
                      value={cantidad}
                      onChange={(e) => setCantidad(e.target.value.replace(",", "."))}
                    />
                  </Field>
                </div>

                <fieldset className="space-y-1.5">
                  <legend className="mb-1 text-sm font-medium">Precio</legend>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="radio"
                      checked={precio === "mantener"}
                      onChange={() => setPrecioModo("mantener")}
                    />
                    <span>
                      Dejar el de la partida —{" "}
                      <b className="tabular-nums">{fmtMoney(linea?.precio_unitario ?? 0)}</b>
                    </span>
                  </label>
                  <label className={`flex items-center gap-2 text-sm ${hayLista ? "" : "opacity-60"}`}>
                    <input
                      type="radio"
                      checked={precio === "lista"}
                      disabled={!hayLista}
                      onChange={() => setPrecioModo("lista")}
                    />
                    <span>
                      {precioLista === undefined
                        ? "Tomar el de la lista del cliente — cotizando…"
                        : hayLista
                        ? <>Tomar el de la lista del cliente — <b className="tabular-nums">{fmtMoney(precioLista!)}</b></>
                        : "Tomar el de la lista del cliente — no tiene precio ahí"}
                    </span>
                  </label>
                  {Number(cantidad) > 0 && precioFinal > 0 ? (
                    <p className="text-xs text-muted tabular-nums">
                      La partida quedaría en {fmtMoney(precioFinal * Number(cantidad))}.
                    </p>
                  ) : null}
                </fieldset>

                {texto ? (
                  <div className="rounded-lg border border-border p-3">
                    <label className="flex items-start gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={aprender}
                        onChange={(e) => setAprender(e.target.checked)}
                      />
                      <span>
                        Aprender el cruce: la próxima orden que diga «{texto}» irá directo a{" "}
                        <b>{pick.nombre}</b>.
                      </span>
                    </label>
                    {aprender ? (
                      <div className="mt-2">
                        <Select value={alcance} onChange={(e) => setAlcance(e.target.value as Alcance)}>
                          <option value="cliente">Solo para {clienteNombre}</option>
                          {remision?.sucursal_id ? (
                            <option value="plaza">
                              Solo para {clienteNombre} en {plazaNombre || "esta plaza"}
                            </option>
                          ) : null}
                          <option value="global">Para todos los clientes</option>
                        </Select>
                        {alcance === "global" ? (
                          <p className="mt-1 text-xs text-muted">
                            Si ese texto ya significa otro producto para el negocio, se guarda solo
                            para este cliente — el vocabulario de la casa no se reapunta desde aquí.
                          </p>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </>
            ) : null}

            <p className="text-xs text-muted">
              Cambia <b>esta partida</b>, no el catálogo: {nombreViejo} sigue existiendo tal cual.
              La línea conserva su número y su nota, y queda anotado que se cruzó a mano.
            </p>
          </>
        ) : (
          <>
            <Field
              label={`Clave de ${nombreViejo} en SAE`}
              hint={
                sugerencias?.espejo
                  ? `Busca por descripción o clave en el catálogo de la empresa ${sugerencias.empresa}.`
                  : sugerencias?.motivo
                  ? `${sugerencias.motivo}: la clave se captura a ciegas, revísala contra SAE.`
                  : "Buscando el catálogo de SAE…"
              }
            >
              <Input
                value={claveTexto}
                autoFocus
                placeholder="ESPINACA, RZEHMOHOS44…"
                onChange={(e) => setClaveTexto(e.target.value)}
              />
            </Field>

            {sugerencias?.espejo && !claveEnSae && sugerencias.claves.length > 0 ? (
              <div className="max-h-48 overflow-auto rounded-lg border border-border">
                {sugerencias.claves.map((c) => (
                  <button
                    key={c.clave}
                    type="button"
                    onClick={() => setClaveTexto(c.clave)}
                    className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-surface-2"
                  >
                    <span>
                      <span className="font-medium">{c.clave}</span>
                      <span className="ml-2 text-xs text-muted">{c.descripcion}</span>
                    </span>
                    <span className="shrink-0 text-xs">
                      {!c.activa ? (
                        <span className="text-warning">de baja</span>
                      ) : c.producto_id && c.producto_id !== linea?.producto_id ? (
                        <span className="text-warning">ya es de {c.producto_nombre}</span>
                      ) : null}
                    </span>
                  </button>
                ))}
              </div>
            ) : null}

            {claveLimpia && sugerencias?.espejo ? (
              !claveEnSae ? (
                <p className="text-xs text-warning">
                  La empresa {sugerencias.empresa} de SAE no conoce «{claveLimpia}»: si la capturas
                  así, el masivo se va a seguir deteniendo por esta partida. Dala de alta en SAE
                  primero, o elige una de la lista.
                </p>
              ) : !claveEnSae.activa ? (
                <p className="text-xs text-warning">
                  «{claveLimpia}» existe en SAE pero está dada de BAJA: no factura.
                </p>
              ) : claveEnSae.producto_id && claveEnSae.producto_id !== linea?.producto_id ? (
                <p className="text-xs text-warning">
                  «{claveLimpia}» ya es la clave de {claveEnSae.producto_nombre} para{" "}
                  {clienteNombre}: dos productos con la misma clave le mandan a SAE la misma línea
                  dos veces. Si esa es la buena, cruza la partida con ese producto.
                </p>
              ) : (
                <p className="text-xs text-success">
                  SAE la conoce: {claveEnSae.descripcion || claveEnSae.clave}.
                </p>
              )
            ) : null}

            <fieldset className="space-y-1.5">
              <legend className="mb-1 text-sm font-medium">¿Dónde vale esta clave?</legend>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  checked={alcanceClave === "generica"}
                  onChange={() => setAlcanceClave("generica")}
                />
                <span>En todas las plazas de {clienteNombre}</span>
              </label>
              {remision?.sucursal_id ? (
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    checked={alcanceClave === "plaza"}
                    onChange={() => setAlcanceClave("plaza")}
                  />
                  <span>Solo en {plazaNombre || "esta plaza"}</span>
                </label>
              ) : null}
              {otrasPlazas.length > 0 ? (
                <p className="text-xs text-muted">
                  Este producto ya tiene clave en otra plaza (
                  {otrasPlazas.map((r) => `${r.sucursal_nombre}: ${r.codigo_cliente}`).join(", ")}
                  ), así que la genérica también valdría ahí.
                </p>
              ) : null}
            </fieldset>

            <p className="text-xs text-muted">
              Esto escribe en el catálogo de {clienteNombre}: la clave es el <b>NoIdentificacion</b>{" "}
              de todos sus CFDI futuros de este producto, y la CVE_ART con la que sale al masivo.
            </p>
          </>
        )}
      </div>
    </Modal>
  );
}

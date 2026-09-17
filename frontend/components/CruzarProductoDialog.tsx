"use client";

// Cruzar una partida: cambiarla por OTRO producto del catálogo.
//
// Nace del aviso «N partidas sin clave SAE del cliente». Ahí el sistema decía
// una sola salida —ve al catálogo del cliente y captúrale una clave—, y esa es
// la menos frecuente: dar de alta una clave es dar de alta un artículo en SAE
// por la puerta de atrás. Lo normal es que la partida deba ir al producto que
// el cliente SÍ tiene, y lo que falló fue el cruce.
//
// Por eso el buscador marca, ANTES de elegir, la clave que cada candidato tiene
// con este cliente: elegir uno "sin clave" deja el export igual de detenido.
//
// Dos cosas no se deciden solas, y por eso se preguntan aquí:
//   • el precio — cambiar el producto no autoriza a cambiar lo que se cobra;
//   • hasta dónde llega lo aprendido — por default solo este cliente, porque el
//     motivo del cruce suele ser SU catálogo en SAE, no una verdad del negocio.

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

type Alcance = "cliente" | "plaza" | "global";

export function CruzarProductoDialog({
  open,
  remision,
  linea,
  clienteNombre,
  plazaNombre,
  onClose,
  onCruzado,
}: {
  open: boolean;
  /** La remisión completa: de ella salen el cliente, la plaza y el contexto
   *  con el que se cotiza el producto nuevo. */
  remision: RemisionDetail | null;
  /** La partida a cruzar. */
  linea: LineaRemision | null;
  clienteNombre: string;
  plazaNombre?: string;
  onClose: () => void;
  /** La remisión ya cruzada: la pantalla recarga lista y detalle. */
  onCruzado: () => void;
}) {
  const toast = useToast();
  const [catalogo, setCatalogo] = useState<ProductoClienteRow[] | null>(null);
  const [pick, setPick] = useState<ProductoPick | null>(null);
  const [presentacion, setPresentacion] = useState("");
  const [cantidad, setCantidad] = useState("");
  const [precioModo, setPrecioModo] = useState<"mantener" | "lista">("mantener");
  // undefined = cotizando; null = el producto no tiene precio para este cliente.
  const [precioLista, setPrecioLista] = useState<string | null | undefined>(undefined);
  const [aprender, setAprender] = useState(true);
  const [alcance, setAlcance] = useState<Alcance>("cliente");
  const [guardando, setGuardando] = useState(false);

  const texto = textoOriginalDe(linea?.notas);
  const clienteId = remision?.cliente_facturacion_id ?? null;

  useEffect(() => {
    if (!open) return;
    setPick(null);
    setPresentacion("");
    setCantidad(String(linea?.cantidad_solicitada ?? ""));
    setPrecioModo("mantener");
    setPrecioLista(undefined);
    setAprender(true);
    setAlcance("cliente");
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
    if (!open || !pick || !remision || !presentacion || !(Number(cantidad) > 0)) {
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
  }, [open, pick, presentacion, cantidad, remision]);

  const claveNueva = pick && claves ? claves.get(pick.producto_id) : undefined;
  const hayLista = precioLista != null && Number(precioLista) > 0;
  const modo = precioModo === "lista" && hayLista ? "lista" : "mantener";
  const precioFinal = modo === "lista" ? Number(precioLista) : Number(linea?.precio_unitario ?? 0);
  const cambiaPresentacion = !!pick && !!linea && presentacion !== linea.presentacion;
  const valido =
    !!pick && !!linea && !!presentacion && Number(cantidad) > 0 &&
    (pick.producto_id !== linea.producto_id || presentacion !== linea.presentacion ||
     Number(cantidad) !== Number(linea.cantidad_solicitada) || modo === "lista");

  async function guardar() {
    if (!remision || !linea || !pick || !valido) return;
    setGuardando(true);
    try {
      await apiFetch(`/api/v1/remisiones/${remision.id}/lineas/${linea.id}/cruzar`, {
        method: "POST",
        body: JSON.stringify({
          producto_id: pick.producto_id,
          presentacion,
          cantidad_solicitada: cantidad,
          precio: modo,
          ...(aprender && texto ? { aprender_texto: texto, aprender_alcance: alcance } : {}),
        }),
      });
      toast.success(`La partida ${linea.numero_linea} ahora va a ${pick.nombre}`);
      onCruzado();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo cruzar la partida");
    } finally {
      setGuardando(false);
    }
  }

  const nombreViejo = linea?.producto_nombre ?? "el producto de la partida";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Cruzar la partida con otro producto"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={guardando}>
            Cancelar
          </Button>
          <Button onClick={() => void guardar()} disabled={!valido || guardando}>
            {guardando ? "Cruzando…" : "Cruzar la partida"}
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
            {fmtMoney(linea?.precio_unitario ?? 0)} c/u
            {linea?.sin_clave_sae ? (
              <> · <span className="text-warning">sin clave SAE de {clienteNombre}</span></>
            ) : null}
          </div>
          {texto ? (
            <div className="mt-1 text-xs text-muted">Como venía en la orden: «{texto}»</div>
          ) : null}
        </div>

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
                  checked={modo === "mantener"}
                  onChange={() => setPrecioModo("mantener")}
                />
                <span>
                  Dejar el de la partida — <b className="tabular-nums">{fmtMoney(linea?.precio_unitario ?? 0)}</b>
                </span>
              </label>
              <label className={`flex items-center gap-2 text-sm ${hayLista ? "" : "opacity-60"}`}>
                <input
                  type="radio"
                  checked={modo === "lista"}
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
          Cambia <b>esta partida</b>, no el catálogo: {nombreViejo} sigue existiendo tal cual. La
          línea conserva su número y su nota, y queda anotado que se cruzó a mano.
        </p>
      </div>
    </Modal>
  );
}

"use client";

import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { ApiError, apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Field, Input, Select } from "@/components/ui/Field";
import { useToast } from "@/components/ui/Toast";

// Unidades para el alta rápida de producto (mismas que el buscador de producto).
const UNIDADES_SAT: { code: string; nombre: string }[] = [
  { code: "H87", nombre: "Pieza" }, { code: "KGM", nombre: "Kilogramo" },
  { code: "GRM", nombre: "Gramo" }, { code: "LTR", nombre: "Litro" },
  { code: "MLT", nombre: "Mililitro" }, { code: "XBX", nombre: "Caja" },
  { code: "XPK", nombre: "Paquete" }, { code: "XBG", nombre: "Bolsa" },
  { code: "XSA", nombre: "Saco / Costal" }, { code: "DPC", nombre: "Docena" },
];
const UNIDADES_BASE = ["KILO", "PIEZA", "LITRO", "CAJA", "BULTO", "COSTAL", "MANOJO", "BOLSA"];

// Unidad SAT correspondiente a cada unidad base (default; el usuario puede cambiarla).
const SAT_POR_BASE: Record<string, string> = {
  KILO: "KGM", PIEZA: "H87", LITRO: "LTR", CAJA: "XBX",
  BULTO: "XSA", COSTAL: "XSA", MANOJO: "H87", BOLSA: "XBG",
};
const satPorBase = (base: string) => SAT_POR_BASE[base] ?? "H87";

export type ProductoCreado = {
  id: string;
  sku: string;
  nombre: string;
  presentaciones: Record<string, number>;
  presentacion_default?: string | null;
  unidad_base?: string | null;
};

/** Un producto del catálogo que se parece al que se está dando de alta. El
 *  backend los manda en el 409 para poder vincular en vez de duplicar. */
export type CandidatoDuplicado = {
  producto_id: string;
  sku: string;
  nombre: string;
  score: number;
  presentaciones?: Record<string, number> | null;
  presentacion_default?: string | null;
  unidad_base?: string | null;
};

/** Los candidatos que vienen dentro del `detail` del 409, si los hay. */
export function candidatosDuplicados(e: unknown): CandidatoDuplicado[] {
  if (!(e instanceof ApiError) || e.status !== 409) return [];
  const d = e.detail;
  if (!d || typeof d !== "object") return [];
  const lista = (d as { candidatos?: unknown }).candidatos;
  return Array.isArray(lista) ? (lista as CandidatoDuplicado[]) : [];
}

/**
 * Modal de alta rápida de producto (lo esencial: nombre, unidad base, unidad y
 * clave SAT). Reutilizable: lo usa el buscador de producto y la columna Match IA
 * del pegado de Excel. Al crear, devuelve el producto por `onCreated` y cierra.
 */
export function CrearProductoModal({
  open,
  onClose,
  nombreInicial = "",
  unidadBaseInicial = "KILO",
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  nombreInicial?: string;
  unidadBaseInicial?: string;
  onCreated: (p: ProductoCreado) => void;
}) {
  const toast = useToast();
  const [cNombre, setCNombre] = useState(nombreInicial);
  const [cClaveSat, setCClaveSat] = useState("01010101");
  const [cUnidadBase, setCUnidadBase] = useState(unidadBaseInicial);
  const [cUnidadSat, setCUnidadSat] = useState(satPorBase(unidadBaseInicial));
  const [cSaving, setCSaving] = useState(false);
  // Productos parecidos que el backend encontró al intentar crear. Mientras
  // haya candidatos, el alta está detenida: hay que vincular o decir que no.
  const [parecidos, setParecidos] = useState<CandidatoDuplicado[]>([]);

  // Reinicia los campos con los valores iniciales cada vez que se abre.
  useEffect(() => {
    if (!open) return;
    const base = unidadBaseInicial || "KILO";
    setCNombre(nombreInicial);
    setCClaveSat("01010101");
    setCUnidadBase(base);
    setCUnidadSat(satPorBase(base));   // la unidad SAT sigue a la base por default
    setParecidos([]);
  }, [open, nombreInicial, unidadBaseInicial]);

  /** Alta del producto. Sin `forzar`, el backend responde 409 con los productos
   *  del catálogo que se le parecen: es el candado que evita tener cilantro seis
   *  veces por escribirlo distinto. Con `forzar` se crea a sabiendas. */
  async function crearProducto(forzar = false) {
    if (cSaving) return;
    if (!cNombre.trim()) { toast.error("Escribe el nombre del producto"); return; }
    setCSaving(true);
    try {
      const prod = await apiFetch<ProductoCreado>("/api/v1/productos", {
        method: "POST",
        body: JSON.stringify({
          nombre: cNombre.trim(),
          clave_sat: cClaveSat.trim() || "01010101",
          unidad_sat: cUnidadSat,
          unidad_base: cUnidadBase,
          presentaciones: { [cUnidadBase]: 1 },
          presentacion_default: cUnidadBase,
          forzar,
        }),
      });
      onCreated(prod);
      toast.success(`Producto "${prod.nombre}" creado`);
      onClose();
    } catch (e) {
      const dups = candidatosDuplicados(e);
      if (dups.length) {
        setParecidos(dups);
      } else {
        toast.error(e instanceof Error ? e.message : "No se pudo crear el producto");
      }
    } finally {
      setCSaving(false);
    }
  }

  /** "Es el mismo": no se crea nada — se devuelve el producto que ya existía,
   *  que es justo lo que el catálogo multicliente quiere (un producto, muchos
   *  nombres). Quien llamó al modal lo recibe como si lo acabara de crear. */
  function usarExistente(c: CandidatoDuplicado) {
    onCreated({
      id: c.producto_id,
      sku: c.sku,
      nombre: c.nombre,
      presentaciones: c.presentaciones ?? {},
      presentacion_default: c.presentacion_default ?? null,
      unidad_base: c.unidad_base ?? null,
    });
    toast.success(`Se usó "${c.nombre}", que ya estaba en el catálogo`);
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Nuevo producto"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={cSaving}>Cancelar</Button>
          {parecidos.length ? (
            <Button variant="secondary" onClick={() => crearProducto(true)} disabled={cSaving}>
              {cSaving ? "Creando…" : "Es distinto — crearlo igual"}
            </Button>
          ) : (
            <Button onClick={() => crearProducto()} disabled={cSaving}>
              {cSaving ? "Creando…" : "Crear producto"}
            </Button>
          )}
        </>
      }
    >
      {parecidos.length ? (
        <Alert tone="warning">
          <div className="font-medium">
            Esto ya podría estar en el catálogo. Un mismo producto con dos nombres se
            vuelve dos inventarios y dos precios.
          </div>
          <ul className="mt-2 space-y-1">
            {parecidos.map((c) => (
              <li key={c.producto_id} className="flex items-center justify-between gap-3">
                <span className="text-sm">
                  {c.nombre} <span className="text-xs text-muted">({c.sku})</span>
                </span>
                <Button variant="secondary" onClick={() => usarExistente(c)} disabled={cSaving}>
                  Es el mismo
                </Button>
              </li>
            ))}
          </ul>
        </Alert>
      ) : null}

      <div className={`grid grid-cols-1 gap-3 sm:grid-cols-2${parecidos.length ? " mt-3" : ""}`}>
        <div className="sm:col-span-2">
          <Field label="Nombre" required>
            <Input
              value={cNombre}
              onChange={(e) => {
                setCNombre(e.target.value);
                // Otro nombre, otra búsqueda: los parecidos de antes ya no
                // aplican y el botón de crear vuelve a ser el normal.
                setParecidos([]);
              }}
              autoFocus
            />
          </Field>
        </div>
        <Field label="Unidad base" hint="Unidad de inventario">
          <Select value={cUnidadBase} onChange={(e) => { const b = e.target.value; setCUnidadBase(b); setCUnidadSat(satPorBase(b)); }}>
            {UNIDADES_BASE.map((u) => <option key={u} value={u}>{u}</option>)}
          </Select>
        </Field>
        <Field label="Unidad SAT">
          <Select value={cUnidadSat} onChange={(e) => setCUnidadSat(e.target.value)}>
            {UNIDADES_SAT.map((u) => <option key={u.code} value={u.code}>{u.code} — {u.nombre}</option>)}
          </Select>
        </Field>
        <div className="sm:col-span-2">
          <Field label="Clave SAT" hint="Producto/servicio (por defecto 01010101 — genérico)">
            <Input value={cClaveSat} onChange={(e) => setCClaveSat(e.target.value)} />
          </Field>
        </div>
        <p className="text-xs text-muted sm:col-span-2">
          Se crea con lo esencial. Puedes completar categoría, presentaciones e impuestos después en Productos.
        </p>
      </div>
    </Modal>
  );
}

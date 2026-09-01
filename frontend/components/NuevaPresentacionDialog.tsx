"use client";

// Dar de alta una presentación (CAJA, COSTAL, MANOJO) sobre un producto que ya
// existe, desde donde haga falta: la lista de precios o la propia remisión.
//
// Es una escritura al PRODUCTO hecha desde otra pantalla, así que se confirma y
// se dice en voz alta. Y el factor se pregunta SIEMPRE: el inventario descuenta
// en unidad base multiplicando por él, de modo que una caja sin factor no
// descuadra el precio — descuadra el stock.

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import type { Producto } from "@/lib/types";

export function NuevaPresentacionDialog({
  open,
  producto,
  onClose,
  onCreated,
}: {
  open: boolean;
  /** Producto al que se le agrega. null cierra el diálogo. */
  producto: Pick<Producto, "id" | "nombre" | "unidad_base"> | null;
  onClose: () => void;
  /** El producto ya actualizado, para refrescar los desplegables al vuelo. */
  onCreated: (p: Producto) => void;
}) {
  const toast = useToast();
  const [nombre, setNombre] = useState("");
  const [factor, setFactor] = useState("");
  const [guardando, setGuardando] = useState(false);

  useEffect(() => {
    if (open) {
      setNombre("");
      setFactor("");
    }
  }, [open]);

  const base = producto?.unidad_base || "unidad base";
  const nombreLimpio = nombre.trim().toUpperCase();
  const factorNum = Number(factor.replace(",", "."));
  const valido = !!producto && nombreLimpio !== "" && Number.isFinite(factorNum) && factorNum > 0;

  async function guardar() {
    if (!producto || !valido) return;
    setGuardando(true);
    try {
      const actualizado = await apiFetch<Producto>(
        `/api/v1/productos/${producto.id}/presentaciones`,
        {
          method: "POST",
          body: JSON.stringify({ nombre: nombreLimpio, factor: String(factorNum) }),
        },
      );
      toast.success(`${producto.nombre} ya se puede vender por ${nombreLimpio}`);
      onCreated(actualizado);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo agregar la presentación");
    } finally {
      setGuardando(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      resizable={false}
      title="Nueva presentación del producto"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={guardando}>
            Cancelar
          </Button>
          <Button onClick={() => void guardar()} disabled={!valido || guardando}>
            {guardando ? "Guardando…" : "Agregar al producto"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-muted">
          Esto <b>cambia el producto {producto?.nombre}</b>, no solo este documento: la
          presentación queda disponible en todas las pantallas y para todos los clientes.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Presentación" required hint="CAJA, COSTAL, MANOJO…">
            <Input
              value={nombre}
              autoFocus
              placeholder="CAJA"
              onChange={(e) => setNombre(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && valido) {
                  e.preventDefault();
                  void guardar();
                }
              }}
            />
          </Field>
          <Field
            label={`¿Cuántos ${base} trae?`}
            required
            hint="Con este número el inventario descuenta del almacén."
          >
            <Input
              inputMode="decimal"
              value={factor}
              placeholder="20"
              onChange={(e) => setFactor(e.target.value.replace(",", "."))}
              onKeyDown={(e) => {
                if (e.key === "Enter" && valido) {
                  e.preventDefault();
                  void guardar();
                }
              }}
            />
          </Field>
        </div>
        {valido && (
          <p className="text-xs text-muted">
            1 {nombreLimpio} = {factorNum} {base}. Mientras no tenga precio propio, se cobrará
            el de {base} multiplicado por {factorNum}.
          </p>
        )}
      </div>
    </Modal>
  );
}

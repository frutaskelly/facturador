"use client";

import { Button } from "./Button";
import { Modal } from "./Modal";

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Eliminar",
  cancelLabel = "Cancelar",
  confirmVariant = "danger",
  onConfirm,
  onClose,
  loading,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  /** Texto del botón que descarta el diálogo. Se cambia cuando la acción
   *  confirmada ya se llama "cancelar" (cancelar una remisión, una orden), para
   *  que los dos botones no digan lo mismo y se descarte sin querer. */
  cancelLabel?: string;
  /** Estilo del botón de confirmar. `danger` (rojo, por defecto) para acciones
   *  destructivas; `primary` (azul) o `success` (verde) para confirmaciones
   *  no destructivas. */
  confirmVariant?: "danger" | "primary" | "success";
  onConfirm: () => void;
  onClose: () => void;
  loading?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      resizable={false}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {cancelLabel}
          </Button>
          <Button variant={confirmVariant} onClick={onConfirm} disabled={loading}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted">{message}</p>
    </Modal>
  );
}

"use client";

// Alta de una empresa HIJA del grupo (otro RFC/razón social del mismo dueño).
// Solo OWNER — el backend lo exige también (POST /empresa/hijas). Al crearla,
// se cambia a la empresa nueva con el switcher (recarga dura al dashboard).
import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { REGIMENES_FISCALES } from "@/lib/sat";

// Mismo patrón que el backend (services/rfc.py) — única fuente del formato SAT.
const RFC_RE = /^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$/;
const CP_RE = /^\d{5}$/;

export function AgregarEmpresaModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const { switchTenant } = useAuth();
  const [nombre, setNombre] = useState("");
  const [rfc, setRfc] = useState("");
  const [regimen, setRegimen] = useState("601");
  const [cp, setCp] = useState("");
  const [busy, setBusy] = useState(false);

  const rfcU = rfc.trim().toUpperCase();
  const valid = nombre.trim().length >= 2 && RFC_RE.test(rfcU) && CP_RE.test(cp.trim());

  async function crear() {
    if (!valid || busy) return;
    setBusy(true);
    try {
      const hija = await apiFetch<{ tenant_id: string; legal_name: string }>(
        "/api/v1/empresa/hijas",
        {
          method: "POST",
          body: JSON.stringify({
            legal_name: nombre.trim(),
            rfc: rfcU,
            regimen_fiscal_sat: regimen,
            domicilio_fiscal_cp: cp.trim(),
          }),
        },
      );
      // Sin toast: la recarga dura lo destruiría antes de verse — aterrizar en
      // el dashboard de la empresa nueva ES la confirmación.
      switchTenant(hija.tenant_id);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "No se pudo crear la empresa");
      setBusy(false);
    }
  }

  return (
    // Con el request en vuelo el modal no se cierra (ni con Escape): cerrar a
    // media creación dejaría al usuario sin saber si la empresa se creó.
    <Modal open onClose={() => { if (!busy) onClose(); }} title="Agregar empresa al grupo"
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cancelar</Button>
        <Button onClick={() => void crear()} disabled={!valid || busy}>
          {busy ? "Creando…" : "Crear empresa"}
        </Button>
      </>}>
      <div className="space-y-3">
        <Alert tone="info">
          Otra razón social del mismo grupo, con su propio RFC, folios y facturación.
          Cambiarás entre empresas desde el menú de arriba. Para timbrar con su RFC
          deberás cargar su CSD en Ajustes › Empresa.
        </Alert>
        <Field label="Razón social" required>
          <Input value={nombre} onChange={(e) => setNombre(e.target.value)}
            placeholder="COMERCIALIZADORA EJEMPLO SA DE CV" maxLength={254} />
        </Field>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="RFC" required
            hint={rfc && !RFC_RE.test(rfcU) ? "Formato de RFC inválido" : undefined}>
            <Input value={rfc} onChange={(e) => setRfc(e.target.value.toUpperCase())}
              placeholder="ABC010101XY9" maxLength={13} className="font-mono uppercase" />
          </Field>
          <Field label="Código postal fiscal" required
            hint={cp && !CP_RE.test(cp.trim()) ? "Deben ser 5 dígitos" : undefined}>
            <Input value={cp} onChange={(e) => setCp(e.target.value)} inputMode="numeric"
              placeholder="45010" maxLength={5} />
          </Field>
        </div>
        <Field label="Régimen fiscal" required>
          <Select value={regimen} onChange={(e) => setRegimen(e.target.value)}>
            {REGIMENES_FISCALES.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </Select>
        </Field>
      </div>
    </Modal>
  );
}

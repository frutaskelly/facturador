"use client";


import { BookOpen, Link2, Receipt, Store } from "lucide-react";

import { CrudPage, type CrudConfig } from "@/components/crud/CrudPage";
import { Badge } from "@/components/ui/Badge";
import { apiFetch } from "@/lib/api";
import { fmtMoney } from "@/lib/format";
import { motivoCpInvalido, motivoRfcInvalido } from "@/lib/rfc";
import { FORMA_PAGO_OPTS, METODO_PAGO_OPTS, REGIMENES_FISCALES, USO_CFDI_OPTS } from "@/lib/sat";
import type { Cliente } from "@/lib/types";

const config: CrudConfig<Cliente> = {
  title: "Clientes",
  subtitle: "Clientes / CRM",
  newLabel: "Nuevo cliente",
  basePath: "/api/v1/clientes",
  writePerm: "cliente:gestionar",
  searchable: false,
  wide: true,
  columns: [
    { header: "Código", cell: (c) => c.codigo ?? "—" },
    { header: "Razón social", cell: (c) => <span className="font-medium">{c.legal_name}</span> },
    { header: "RFC", cell: (c) => c.rfc },
    { header: "Saldo", className: "text-right tabular-nums",
      cell: (c) => Number(c.saldo_actual) > 0
        ? <span className="font-medium text-danger">{fmtMoney(c.saldo_actual)}</span>
        : <span className="text-muted">—</span> },
    { header: "Estado", cell: (c) => <Badge tone={c.status === "ACTIVO" ? "success" : "muted"}>{c.status}</Badge> },
  ],
  fields: [
    { name: "codigo", label: "Código", readonly: true, hint: "Se genera automáticamente" },
    { name: "legal_name", label: "Razón social", required: true, colSpan: 2 },
    {
      name: "rfc",
      label: "RFC",
      required: true,
      colSpan: 2,
      // El SAT rechaza el timbrado con un RFC de receptor mal capturado: se
      // valida aquí (formato + dígito verificador) para no descubrirlo hasta
      // que la factura ya no se pueda emitir.
      validate: (v) => motivoRfcInvalido(v, { permitirGenericos: true }),
      action: {
        label: "Verificar RFC",
        // Con Razón social + CP + Régimen ya capturados, valida el combo
        // completo contra el SAT (POST /customers/validate): atrapa un CP o
        // régimen mal capturado ANTES de que el timbrado real lo rechace.
        // Si falta alguno de esos tres, hace el chequeo parcial de siempre
        // (solo formato/activo/localizado del RFC) y avisa qué falta para
        // completarlo.
        watch: ["legal_name", "cp", "regimen_fiscal"],
        run: async (rfc, form) => {
          const nombre = String(form.legal_name ?? "").trim();
          const cp = String(form.cp ?? "").trim();
          const regimen = String(form.regimen_fiscal ?? "").trim();
          const completo = Boolean(nombre && cp && regimen);

          const qs = new URLSearchParams({ rfc });
          if (completo) {
            qs.set("nombre", nombre);
            qs.set("cp", cp);
            qs.set("regimen", regimen);
          }
          const r = await apiFetch<{
            FormatoCorrecto?: boolean;
            Activo?: boolean;
            Localizado?: boolean;
            ExistRfc?: boolean;
            MatchName?: boolean;
            MatchZipCode?: boolean;
            MatchFiscalRegime?: boolean;
          }>(`/api/v1/clientes/validar-rfc?${qs.toString()}`);

          if (completo) {
            const problemas = [
              r.ExistRfc === false && "el RFC no existe ante el SAT",
              r.MatchName === false && "la razón social no coincide",
              r.MatchZipCode === false && "el código postal no coincide",
              r.MatchFiscalRegime === false && "el régimen fiscal no coincide",
            ].filter((x): x is string => Boolean(x));
            return {
              ok: problemas.length === 0,
              message:
                problemas.length === 0
                  ? "RFC, razón social, CP y régimen coinciden con el SAT ✓"
                  : `No coincide con el SAT: ${problemas.join(", ")}.`,
            };
          }

          const ok = Boolean(r.FormatoCorrecto && r.Activo && r.Localizado);
          const faltan = [!nombre && "razón social", !cp && "código postal", !regimen && "régimen fiscal"]
            .filter((x): x is string => Boolean(x))
            .join(", ");
          return {
            ok,
            message: ok
              ? `RFC activo y localizado en el SAT ✓ — completa ${faltan} para validar también esos datos`
              : `RFC: formato ${r.FormatoCorrecto ? "ok" : "inválido"}, activo ${r.Activo ? "sí" : "no"}, localizado ${r.Localizado ? "sí" : "no"}`,
          };
        },
      },
    },
    { name: "cp", label: "Código postal", required: true, validate: motivoCpInvalido },
    {
      name: "regimen_fiscal",
      label: "Régimen fiscal SAT",
      type: "select",
      required: true,
      options: REGIMENES_FISCALES,
      colSpan: 2,
    },
    {
      name: "uso_cfdi_default",
      label: "Uso del CFDI",
      type: "select",
      required: true,
      options: USO_CFDI_OPTS,
      hint: "Predeterminado al generar facturas para este cliente",
      colSpan: 2,
    },
    {
      name: "forma_pago_default",
      label: "Forma de pago SAT",
      type: "select",
      required: true,
      options: FORMA_PAGO_OPTS,
      colSpan: 2,
    },
    {
      name: "metodo_pago_default",
      label: "Método de pago",
      type: "select",
      required: true,
      options: METODO_PAGO_OPTS,
      hint: "PUE exige una forma de pago real (no 99); PPD permite 99 — Por definir",
      colSpan: 2,
    },
    { name: "calle", label: "Calle y número", colSpan: 2 },
    { name: "colonia", label: "Colonia" },
    { name: "ciudad", label: "Ciudad/Municipio" },
    { name: "estado", label: "Estado" },
    { name: "pais", label: "País" },
    { name: "telefono", label: "Teléfono" },
    { name: "email", label: "Correos", hint: "Uno o varios, separados por coma o espacio; se usan al enviar remisiones y facturas", colSpan: 2 },
    { name: "lista_precios_id", label: "Lista de precios", type: "select",
      createInline: {
        label: "Nueva lista",
        perm: "lista_precios:gestionar",
        title: "Nueva lista de precios",
        fields: [{ name: "nombre", label: "Nombre", placeholder: "Lista mayoreo", required: true }],
        run: async (v) => {
          const creada = await apiFetch<{ id: string }>("/api/v1/listas-precios", {
            method: "POST",
            body: JSON.stringify({ nombre: v.nombre.trim() }),
          });
          return { id: String(creada.id) };
        },
      },
    },
    { name: "limite_credito", label: "Límite de crédito", type: "number", step: "0.01" },
    { name: "dias_credito", label: "Condiciones de pago (días)", type: "number" },
    {
      name: "status",
      label: "Estado",
      type: "select",
      options: [
        { value: "ACTIVO", label: "Activo" },
        { value: "SUSPENDIDO", label: "Suspendido" },
        { value: "BAJA", label: "Baja" },
      ],
    },
    {
      name: "serie_factura_id",
      label: "Serie de factura",
      type: "select",
      hint: "La sucursal puede sobreescribirla; en blanco usa la predeterminada",
      // Crear las DOS series de un golpe desde aquí: dar de alta un cliente ya
      // no obliga a ir antes a Ajustes → Series y folios.
      createInline: {
        label: "Crear series",
        perm: "serie:gestionar",
        title: "Nuevas series para este cliente",
        fields: [
          {
            name: "codigo",
            label: "Código base",
            placeholder: "EHMO",
            required: true,
            hint: "Se crean dos: facturas con ese código (EHMO1, EHMO2…) y remisiones con R adelante (REHMO1…)",
          },
          { name: "nombre", label: "Nombre (opcional)", placeholder: "Grupo Operador EHMO" },
        ],
        run: async (v) => {
          const codigo = v.codigo.trim().toUpperCase();
          const creadas = await apiFetch<{ id: string; tipo_documento: string }[]>(
            "/api/v1/series/par",
            {
              method: "POST",
              body: JSON.stringify({
                codigo_factura: codigo,
                codigo_remision: `R${codigo}`,
                nombre: v.nombre.trim() || null,
              }),
            },
          );
          const fac = creadas.find((x) => x.tipo_documento === "FACTURA");
          const rem = creadas.find((x) => x.tipo_documento === "REMISION");
          return {
            id: String(fac?.id ?? ""),
            extra: rem ? { serie_remision_id: String(rem.id) } : undefined,
          };
        },
        refreshes: ["serie_factura_id", "serie_remision_id"],
      },
    },
    {
      name: "serie_remision_id",
      label: "Serie de remisión",
      type: "select",
      hint: "La sucursal puede sobreescribirla; en blanco usa la predeterminada",
    },
  ],
  // Accesos rápidos por fila, como iconos junto a editar/eliminar.
  rowLinks: (c) => [
    { href: `/sucursales?cliente=${c.id}`, title: "Sucursales", icon: <Store size={16} /> },
    { href: `/clientes/${c.id}/catalogo`, title: "Catálogo", icon: <BookOpen size={16} /> },
    { href: `/clientes/${c.id}/estado-cuenta`, title: "Estado de cuenta", icon: <Receipt size={16} /> },
    { href: `/clientes/${c.id}/equivalencias`, title: "Equivalencias", icon: <Link2 size={16} /> },
  ],
  lookups: {
    lista_precios_id: {
      path: "/api/v1/listas-precios?limit=200",
      value: (r) => String(r.id),
      label: (r) => String(r.nombre),
    },
    serie_factura_id: {
      path: "/api/v1/series?tipo_documento=FACTURA&activa=true&limit=200",
      value: (r) => String(r.id),
      label: (r) => `${r.codigo}${r.nombre ? ` · ${r.nombre}` : ""}`,
    },
    serie_remision_id: {
      path: "/api/v1/series?tipo_documento=REMISION&activa=true&limit=200",
      value: (r) => String(r.id),
      label: (r) => `${r.codigo}${r.nombre ? ` · ${r.nombre}` : ""}`,
    },
  },
  newValues: () => ({
    codigo: "",
    legal_name: "",
    rfc: "",
    regimen_fiscal: "",
    uso_cfdi_default: "G01",
    forma_pago_default: "99",
    metodo_pago_default: "PPD",
    calle: "",
    colonia: "",
    ciudad: "",
    estado: "",
    cp: "",
    pais: "México",
    telefono: "",
    email: "",
    lista_precios_id: "",
    limite_credito: "0",
    dias_credito: "0",
    status: "ACTIVO",
    serie_factura_id: "",
    serie_remision_id: "",
  }),
  toForm: (c) => {
    const dom = (c.domicilio_fiscal ?? {}) as Record<string, unknown>;
    return {
      codigo: c.codigo ?? "",
      legal_name: c.legal_name,
      rfc: c.rfc,
      regimen_fiscal: c.regimen_fiscal ?? "",
      uso_cfdi_default: c.uso_cfdi_default ?? "G01",
      forma_pago_default: c.forma_pago_default ?? "99",
      metodo_pago_default: c.metodo_pago_default ?? "PPD",
      calle: (dom.calle as string) ?? "",
      colonia: (dom.colonia as string) ?? "",
      ciudad: (dom.ciudad as string) ?? "",
      estado: (dom.estado as string) ?? "",
      cp: (dom.cp as string) ?? "",
      pais: (dom.pais as string) ?? "México",
      telefono: (dom.telefono as string) ?? "",
      // Correos: array `correos` (varios) o el `email` legado (uno) → texto editable.
      email: Array.isArray(dom.correos)
        ? (dom.correos as string[]).join(", ")
        : (dom.email as string) ?? "",
      lista_precios_id: c.lista_precios_id ?? "",
      limite_credito: c.limite_credito,
      dias_credito: String(c.dias_credito),
      status: c.status,
      serie_factura_id: c.serie_factura_id ?? "",
      serie_remision_id: c.serie_remision_id ?? "",
    };
  },
  toPayload: (v) => {
    const domicilio_fiscal: Record<string, string | string[]> = {};
    for (const k of ["cp", "calle", "colonia", "ciudad", "estado", "pais", "telefono"] as const) {
      const val = (v[k] as string)?.trim?.();
      if (val) domicilio_fiscal[k] = val;
    }
    // Correos: uno o varios (coma/espacio) → array `correos`; `email` = primero
    // para compatibilidad con lecturas antiguas.
    const correos = ((v.email as string) || "").split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
    if (correos.length) {
      domicilio_fiscal.correos = correos;
      domicilio_fiscal.email = correos[0];
    }
    return {
      // codigo lo genera el servidor; no se envía.
      legal_name: v.legal_name,
      rfc: v.rfc,
      regimen_fiscal: (v.regimen_fiscal as string) || null,
      uso_cfdi_default: (v.uso_cfdi_default as string) || null,
      forma_pago_default: (v.forma_pago_default as string) || null,
      metodo_pago_default: (v.metodo_pago_default as string) || null,
      // `tipo` no se captura en el formulario; el backend usa su default "PRIVADO".
      domicilio_fiscal,
      lista_precios_id: (v.lista_precios_id as string) || null,
      limite_credito: Number(v.limite_credito) || 0,
      dias_credito: Number(v.dias_credito) || 0,
      status: v.status,
      serie_factura_id: (v.serie_factura_id as string) || null,
      serie_remision_id: (v.serie_remision_id as string) || null,
    };
  },
  rowLabel: (c) => c.legal_name,
};

export default function Page() {
  return <CrudPage<Cliente> config={config} />;
}

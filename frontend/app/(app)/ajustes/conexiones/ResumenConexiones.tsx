"use client";

// Resumen de Conexiones: una auditoría de lo que se comparte. Su trabajo es
// asegurar que TODO se comparte bien: ninguna serie ni ningún cliente con
// movimiento fuera, ninguna serie leída dos veces por el mismo sistema y cada
// perfil de órdenes en exactamente una bodega del panel.
//
// Cruza lo que la página ya cargó —el estado de cada conexión con lo que
// comparte cada cuenta, las opciones (todas las plazas, series y perfiles) y
// los clientes con movimiento reciente—. Las opciones y los clientes solo los
// ve quien administra; sin ellos el resumen enseña lo compartido, pero no
// puede saber qué se quedó fuera.
import { useState, type ReactNode } from "react";
import {
  AlertTriangle,
  Calculator,
  Check,
  CheckCircle2,
  ChevronRight,
  Info,
  MessageCircle,
  Warehouse,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import type {
  ClienteMovimiento,
  Conexion,
  ConexionEstado,
  OpcionesMiniConta,
  OpcionesPanel,
} from "@/lib/types";
import { DATOS_MINI_CONTA, resumenClientes, resumenSeries } from "./MiniContaCuentas";
import { DATOS_PANEL } from "./SmartSupplyPanelCuentas";

export type TabConexiones = "resumen" | "smart-supply" | "mini-conta" | "panel";

/** A dónde lleva un clic: la pestaña y, si se puede, el «Qué comparte» de una
 *  cuenta o una sección del propio Resumen. */
export type Destino = { tab: TabConexiones; conexionId?: string; ancla?: string };

type Accion = { etiqueta: string; destino: Destino };
export type Faltante = {
  id: string;
  tono: "warning" | "info";
  texto: ReactNode;
  acciones: Accion[];
};

/** Los días de movimiento que cuentan para «cliente con movimiento». */
export const DIAS_MOVIMIENTO = 180;

// Igual que clave_nombre del backend en lo que importa: sin acentos ni mayúsculas.
const norm = (s: string) =>
  s.normalize("NFD").replace(/[̀-ͯ]/g, "").trim().toLowerCase();

const codigos = (xs: Iterable<string>) => [...xs].sort().join(", ");
const interseca = (a: Iterable<string>, b: Set<string>) => [...a].some((x) => b.has(x));
const resta = (a: Iterable<string>, b: Set<string>) => [...a].filter((x) => !b.has(x));
const nombres = (cs: Conexion[]) => cs.map((c) => c.nombre).join(", ");

function Codigos({ xs }: { xs: Iterable<string> }) {
  return <span className="font-mono text-xs">{codigos(xs)}</span>;
}

type Plaza = { nombre: string; fac: Set<string>; rem: Set<string> };

/** Las plazas de la empresa con sus series, juntando lo que dicen las dos opciones. */
function plazasDe(opMC: OpcionesMiniConta | null, opPanel: OpcionesPanel | null): Map<string, Plaza> {
  const m = new Map<string, Plaza>();
  const de = (nombre: string) => {
    const k = norm(nombre);
    if (!m.has(k)) m.set(k, { nombre: nombre.trim(), fac: new Set(), rem: new Set() });
    return m.get(k)!;
  };
  for (const s of opMC?.sucursales ?? []) s.series.forEach((x) => de(s.nombre).fac.add(x));
  for (const p of opPanel?.plazas ?? []) {
    const pl = de(p.nombre);
    p.series.forEach((x) => pl.fac.add(x));
    p.series_remision.forEach((x) => pl.rem.add(x));
  }
  return new Map([...m].sort((a, b) => a[1].nombre.localeCompare(b[1].nombre, "es")));
}

// ── Las filas de la auditoría ─────────────────────────────────────────────

export type FilaSerie = {
  codigo: string;
  tipo: "FACTURA" | "REMISION";
  plazas: string[];
  mc: Conexion[];          // cuentas de Mini Conta que la leen (factura); [] en remisión
  pn: Conexion[];          // cuentas del panel que la leen
  movimiento: boolean;     // tuvo facturas o remisiones en la ventana
  registrada: boolean;     // está dada de alta en Series (si no, no se puede compartir)
  faltaMC: boolean;
  faltaPn: boolean;
  dupMC: boolean;
  dupPn: boolean;
  pendiente: boolean;
};

export type FilaCliente = {
  id: string;
  nombre: string;
  fac: string[];
  rem: string[];
  plazas: string[];
  mc: Conexion[];          // cuentas de Mini Conta que lo leen
  fueraDe: Conexion[];     // cuentas con «Solo estos» que leen su serie pero lo dejan fuera
  facSinMC: string[];      // series en que factura y ninguna cuenta de Mini Conta le lee
  facSinPn: string[];
  remSinPn: string[];
  pendiente: boolean;
};

export type FilaPerfil = { perfil: string; plaza: string | null; cuentas: Conexion[] };

export type Auditoria = {
  faltantes: Faltante[];
  series: FilaSerie[];
  clientes: FilaCliente[] | null;    // null = no se pudieron leer
  perfiles: FilaPerfil[];
  hayOpciones: boolean;
  conteo: {
    pendientes: number;
    series: number; seriesConMov: number; seriesSinCompartir: number; seriesDuplicadas: number;
    clientes: number; clientesSinCubrir: number;
    perfiles: number; perfilesSinCuenta: number; perfilesDuplicados: number;
  };
};

export function auditar({
  smart,
  miniConta,
  panel,
  opMC,
  opPanel,
  clientes,
}: {
  smart?: ConexionEstado;
  miniConta?: ConexionEstado;
  panel?: ConexionEstado;
  opMC: OpcionesMiniConta | null;
  opPanel: OpcionesPanel | null;
  clientes: ClienteMovimiento[] | null;
}): Auditoria {
  const out: Faltante[] = [];
  const mc = miniConta?.conexiones ?? [];
  const pn = panel?.conexiones ?? [];
  const hayMC = mc.length > 0;
  const hayPn = pn.length > 0;
  // Una clave de antes (sin alcance) lee todas las series y todos los clientes.
  const mcTodo = mc.some((c) => !c.alcance);
  const mcConAlcance = mc.filter((c) => c.alcance);
  const mcSeries = new Set(mcConAlcance.flatMap((c) => c.alcance!.series));
  const pnFac = new Set(pn.flatMap((c) => c.alcance_panel?.series ?? []));
  const pnRem = new Set(pn.flatMap((c) => c.alcance_panel?.series_remision ?? []));
  const pnPlazas = new Set(pn.map((c) => norm(c.alcance_panel?.plaza ?? "")).filter(Boolean));
  const hayOpciones = !!(opMC || opPanel);
  const plazas = plazasDe(opMC, opPanel);

  const irMC: Accion = { etiqueta: "Ir a Mini Conta", destino: { tab: "mini-conta" } };
  const irPanel: Accion = { etiqueta: "Ir al panel", destino: { tab: "panel" } };
  const verSeries: Accion = { etiqueta: "Ver series", destino: { tab: "resumen", ancla: "auditoria-series" } };
  const verClientes: Accion = { etiqueta: "Ver clientes", destino: { tab: "resumen", ancla: "auditoria-clientes" } };
  const editar = (c: Conexion, tab: TabConexiones): Accion => ({
    etiqueta: `Qué comparte ${c.nombre}`,
    destino: { tab, conexionId: c.id },
  });

  // ── Sistemas sin ninguna cuenta ──────────────────────────────────────────
  if (smart && !(smart.conexion && smart.conexion.estado !== "REVOCADA")) {
    out.push({
      id: "smart-sin-conectar",
      tono: "info",
      texto: <>Smart Supply (órdenes) no está conectado: las órdenes de WhatsApp no llegan a la bandeja.</>,
      acciones: [{ etiqueta: "Ir a Smart Supply", destino: { tab: "smart-supply" } }],
    });
  }
  if (miniConta && !hayMC) {
    out.push({ id: "mc-sin-cuentas", tono: "warning", texto: <>Ninguna cuenta de Mini Conta conectada.</>, acciones: [irMC] });
  }
  if (panel && !hayPn) {
    out.push({ id: "pn-sin-cuentas", tono: "warning", texto: <>Ninguna bodega del panel de Smart Supply conectada.</>, acciones: [irPanel] });
  }

  // ── Plazas que nadie lee ─────────────────────────────────────────────────
  for (const [k, p] of plazas) {
    if (!p.fac.size) continue;
    const mcFalta = hayMC && !mcTodo ? resta(p.fac, mcSeries) : [];
    const sinMC = hayMC && !!opMC && mcFalta.length === p.fac.size;
    const sinPn = hayPn && !!opPanel && !pnPlazas.has(k) && !interseca(p.fac, pnFac);
    const ser = <Codigos xs={p.fac} />;
    if (sinMC && sinPn) {
      out.push({
        id: `plaza-nada-${k}`,
        tono: "warning",
        texto: <><strong>{p.nombre}</strong> ({ser}) no se comparte con ninguna cuenta de Mini Conta ni del panel.</>,
        acciones: [irMC, irPanel],
      });
      continue;
    }
    if (sinMC) {
      out.push({
        id: `plaza-mc-${k}`,
        tono: "warning",
        texto: <><strong>{p.nombre}</strong> ({ser}) no se comparte con ninguna cuenta de Mini Conta.</>,
        acciones: [irMC],
      });
    } else if (opMC && mcFalta.length) {
      // Parte de la plaza sí: se ofrece la cuenta que ya lee lo demás.
      const duena = mcConAlcance.find((c) => interseca(p.fac, new Set(c.alcance!.series)));
      out.push({
        id: `plaza-mc-parcial-${k}`,
        tono: "warning",
        texto: <>De <strong>{p.nombre}</strong>, ninguna cuenta de Mini Conta lee <Codigos xs={mcFalta} />.</>,
        acciones: [duena ? editar(duena, "mini-conta") : irMC],
      });
    }
    if (sinPn) {
      out.push({
        id: `plaza-pn-${k}`,
        tono: "warning",
        texto: <><strong>{p.nombre}</strong> ({ser}) no se comparte con ninguna cuenta del panel.</>,
        acciones: [irPanel],
      });
    } else if (hayPn && opPanel) {
      const pnFalta = resta(p.fac, pnFac);
      if (pnFalta.length) {
        const duena = pn.find(
          (c) => norm(c.alcance_panel?.plaza ?? "") === k ||
            interseca(p.fac, new Set(c.alcance_panel?.series ?? []))
        );
        out.push({
          id: `plaza-pn-parcial-${k}`,
          tono: "warning",
          texto: <>De <strong>{p.nombre}</strong>, ninguna cuenta del panel lee la serie de factura <Codigos xs={pnFalta} />.</>,
          acciones: [duena ? editar(duena, "panel") : irPanel],
        });
      }
    }
  }

  // ── Series: cada una, quién la lee ───────────────────────────────────────
  // «Con movimiento» = facturas o remisiones en la ventana. Sin los clientes no
  // se sabe, y entonces todas cuentan.
  const conMov = clientes
    ? new Set(clientes.flatMap((c) => [...c.series_factura, ...c.series_remision]))
    : null;
  const plazasDeSerie = (codigo: string, tipo: "FACTURA" | "REMISION") =>
    [...plazas.values()].filter((p) => (tipo === "FACTURA" ? p.fac : p.rem).has(codigo)).map((p) => p.nombre);
  // Las dadas de alta, y además las que salen en documentos sin estar dadas de
  // alta (el espejo del SAE trae facturas de series que aquí no existen): esas
  // no se pueden compartir y hay que verlas.
  const altaFac = new Set([...(opMC?.series ?? []), ...(opPanel?.series ?? [])]);
  const altaRem = new Set(opPanel?.series_remision ?? []);
  const universoFac = new Set([
    ...altaFac, ...mcSeries, ...pnFac, ...(clientes ?? []).flatMap((c) => c.series_factura),
  ]);
  const universoRem = new Set([
    ...altaRem, ...pnRem, ...(clientes ?? []).flatMap((c) => c.series_remision),
  ]);
  const series: FilaSerie[] = [];
  const fila = (codigo: string, tipo: "FACTURA" | "REMISION"): FilaSerie => {
    const lectoresMC = tipo === "FACTURA" ? mc.filter((c) => !c.alcance || c.alcance.series.includes(codigo)) : [];
    const lectoresPn = pn.filter((c) =>
      (tipo === "FACTURA" ? c.alcance_panel?.series : c.alcance_panel?.series_remision)?.includes(codigo)
    );
    const faltaMC = tipo === "FACTURA" && hayMC && lectoresMC.length === 0;
    const faltaPn = hayPn && lectoresPn.length === 0;
    // La clave de antes lo lee todo: no cuenta como duplicado (ya se avisa aparte).
    const dupMC = lectoresMC.filter((c) => c.alcance).length > 1;
    const dupPn = lectoresPn.length > 1;
    const movimiento = conMov ? conMov.has(codigo) : true;
    const registrada = (tipo === "FACTURA" ? altaFac : altaRem).has(codigo);
    return {
      codigo, tipo, plazas: plazasDeSerie(codigo, tipo), mc: lectoresMC, pn: lectoresPn, movimiento, registrada,
      faltaMC, faltaPn, dupMC, dupPn,
      pendiente: movimiento && (faltaMC || faltaPn || dupMC || dupPn),
    };
  };
  if (hayOpciones) {
    for (const s of [...universoFac].sort()) series.push(fila(s, "FACTURA"));
    for (const s of [...universoRem].sort()) series.push(fila(s, "REMISION"));
  }
  const orden = (f: FilaSerie) => `${f.tipo}|${f.plazas[0] ? "0" + norm(f.plazas[0]) : "1"}|${f.codigo}`;
  series.sort((a, b) => orden(a).localeCompare(orden(b)));

  const sinAlta = series.filter((f) => !f.registrada && f.movimiento);
  if (sinAlta.length) {
    out.push({
      id: "sin-alta",
      tono: "warning",
      texto: (
        <>
          Hay documentos en series que no están dadas de alta, y por eso ninguna cuenta las puede
          leer: <Codigos xs={sinAlta.map((f) => f.codigo)} />. Dalas de alta en Series o corrige esos documentos.
        </>
      ),
      acciones: [verSeries],
    });
  }
  const remSinPanel = series.filter((f) => f.tipo === "REMISION" && f.movimiento && f.faltaPn && f.registrada);
  if (remSinPanel.length) {
    out.push({
      id: "rem-sin-panel",
      tono: "warning",
      texto: <>Series de remisión con movimiento que no lee ninguna cuenta del panel: <Codigos xs={remSinPanel.map((f) => f.codigo)} />.</>,
      acciones: [verSeries, irPanel],
    });
  }
  // Las de factura sin plaza no salen en los avisos por plaza.
  const sueltasSin = series.filter(
    (f) => f.tipo === "FACTURA" && f.registrada && !f.plazas.length && (f.faltaMC || f.faltaPn)
  );
  const sueltasMov = sueltasSin.filter((f) => f.movimiento);
  const sueltasQuietas = sueltasSin.filter((f) => !f.movimiento);
  if (sueltasMov.length) {
    out.push({
      id: "sueltas-mov",
      tono: "warning",
      texto: <>Series sin plaza, con movimiento, que no se comparten del todo: <Codigos xs={sueltasMov.map((f) => f.codigo)} />.</>,
      acciones: [verSeries],
    });
  }
  if (sueltasQuietas.length) {
    out.push({
      id: "sueltas",
      tono: "info",
      texto: <>Series sin plaza ni movimiento reciente que no se comparten: <Codigos xs={sueltasQuietas.map((f) => f.codigo)} />.</>,
      acciones: [verSeries],
    });
  }
  for (const f of series.filter((x) => x.dupMC || x.dupPn)) {
    const quienes = f.dupMC ? f.mc.filter((c) => c.alcance) : f.pn;
    out.push({
      id: `dup-${f.tipo}-${f.codigo}`,
      tono: "warning",
      texto: (
        <>
          La serie <Codigos xs={[f.codigo]} /> la leen {quienes.length} cuentas {f.dupMC ? "de Mini Conta" : "del panel"}:{" "}
          {nombres(quienes)}. Sus ventas se contarían dos veces.
        </>
      ),
      acciones: quienes.map((c) => editar(c, f.dupMC ? "mini-conta" : "panel")),
    });
  }

  // ── Clientes con movimiento: que nadie se quede fuera ────────────────────
  let filasClientes: FilaCliente[] | null = null;
  if (clientes) {
    filasClientes = clientes.map((c) => {
      const lectores = mc.filter(
        (a) => !a.alcance ||
          (interseca(c.series_factura, new Set(a.alcance.series)) &&
            (!a.alcance.clientes || a.alcance.clientes.includes(c.id)))
      );
      const fueraDe = mcConAlcance.filter(
        (a) => a.alcance!.clientes && !a.alcance!.clientes.includes(c.id) &&
          interseca(c.series_factura, new Set(a.alcance!.series))
      );
      const facSinMC = hayMC
        ? c.series_factura.filter((s) => !lectores.some((a) => !a.alcance || a.alcance.series.includes(s)))
        : [];
      const facSinPn = hayPn ? resta(c.series_factura, pnFac) : [];
      const remSinPn = hayPn ? resta(c.series_remision, pnRem) : [];
      const susPlazas = [
        ...new Set(c.series_factura.flatMap((s) => plazasDeSerie(s, "FACTURA"))),
      ].sort((a, b) => a.localeCompare(b, "es"));
      return {
        id: c.id, nombre: c.nombre, fac: c.series_factura, rem: c.series_remision,
        plazas: susPlazas, mc: lectores, fueraDe, facSinMC, facSinPn, remSinPn,
        pendiente: facSinMC.length > 0 || facSinPn.length > 0 || remSinPn.length > 0,
      };
    });

    // Por cuenta con «Solo estos»: a quién deja fuera de sus propias series.
    for (const a of mcConAlcance.filter((x) => x.alcance!.clientes)) {
      const fuera = filasClientes.filter((f) => f.fueraDe.some((x) => x.id === a.id) && f.facSinMC.length);
      if (fuera.length) {
        out.push({
          id: `fuera-${a.id}`,
          tono: "warning",
          texto: (
            <>
              <strong>{a.nombre}</strong> comparte «Solo estos» clientes y deja fuera {fuera.length} con
              movimiento en sus series: {fuera.slice(0, 5).map((f) => f.nombre).join(", ")}
              {fuera.length > 5 ? ` y ${fuera.length - 5} más` : ""}.
            </>
          ),
          acciones: [editar(a, "mini-conta"), verClientes],
        });
      }
    }
    const sinSerieMC = filasClientes.filter((f) => f.facSinMC.length && !f.fueraDe.length);
    if (sinSerieMC.length) {
      out.push({
        id: "clientes-sin-mc",
        tono: "warning",
        texto: (
          <>
            {sinSerieMC.length} {sinSerieMC.length === 1 ? "cliente factura" : "clientes facturan"} en series
            que no lee ninguna cuenta de Mini Conta: {sinSerieMC.slice(0, 5).map((f) => f.nombre).join(", ")}
            {sinSerieMC.length > 5 ? ` y ${sinSerieMC.length - 5} más` : ""}.
          </>
        ),
        acciones: [verClientes],
      });
    }
    const sinPanel = filasClientes.filter((f) => f.facSinPn.length || f.remSinPn.length);
    if (sinPanel.length) {
      out.push({
        id: "clientes-sin-panel",
        tono: "warning",
        texto: (
          <>
            {sinPanel.length} {sinPanel.length === 1 ? "cliente tiene" : "clientes tienen"} facturas o remisiones
            en series que no lee ninguna cuenta del panel: {sinPanel.slice(0, 5).map((f) => f.nombre).join(", ")}
            {sinPanel.length > 5 ? ` y ${sinPanel.length - 5} más` : ""}.
          </>
        ),
        acciones: [verClientes],
      });
    }
  }

  // ── Perfiles de órdenes: cada uno en exactamente una bodega ──────────────
  const plazaDePerfil = new Map<string, string>();
  for (const p of opPanel?.plazas ?? []) p.perfiles.forEach((x) => plazaDePerfil.set(x, p.nombre));
  const todosPerfiles = [
    ...new Set([...(opPanel?.perfiles ?? []), ...pn.flatMap((c) => c.alcance_panel?.perfiles ?? [])]),
  ].sort();
  const perfiles: FilaPerfil[] = todosPerfiles.map((p) => ({
    perfil: p,
    plaza: plazaDePerfil.get(p) ?? null,
    cuentas: pn.filter((c) => c.alcance_panel?.perfiles.includes(p)),
  }));
  const perfilesSin = hayPn ? perfiles.filter((f) => !f.cuentas.length) : [];
  const perfilesDup = perfiles.filter((f) => f.cuentas.length > 1);
  if (perfilesSin.length) {
    out.push({
      id: "perfiles-sin-cuenta",
      tono: "warning",
      texto: <>Perfiles de órdenes que no tiene ninguna bodega del panel: <Codigos xs={perfilesSin.map((f) => f.perfil)} />.</>,
      acciones: [{ etiqueta: "Ver perfiles", destino: { tab: "resumen", ancla: "auditoria-perfiles" } }, irPanel],
    });
  }
  for (const f of perfilesDup) {
    out.push({
      id: `perfil-dup-${f.perfil}`,
      tono: "warning",
      texto: <>El perfil <Codigos xs={[f.perfil]} /> está en {f.cuentas.length} bodegas: {nombres(f.cuentas)}.</>,
      acciones: f.cuentas.map((c) => editar(c, "panel")),
    });
  }

  // ── Por cuenta de Mini Conta ─────────────────────────────────────────────
  for (const c of mc) {
    const a = c.alcance;
    if (!a) {
      out.push({
        id: `mc-antes-${c.id}`,
        tono: "warning",
        texto: <><strong>{c.nombre}</strong> tiene una clave de antes: lee todas las series y todos los clientes.</>,
        acciones: [editar(c, "mini-conta")],
      });
      continue;
    }
    // Lo que sus hermanas sí comparten y ella no: casi siempre es un olvido.
    if (mcConAlcance.length < 2) continue;
    const faltan = DATOS_MINI_CONTA.filter(
      (d) => !a[d.clave] && mcConAlcance.some((o) => o.id !== c.id && o.alcance?.[d.clave])
    );
    if (faltan.length) {
      out.push({
        id: `mc-extras-${c.id}`,
        tono: "warning",
        texto: (
          <>
            <strong>{c.nombre}</strong> no comparte {faltan.map((d) => d.titulo).join(", ")}, y otras
            cuentas de Mini Conta sí.
          </>
        ),
        acciones: [editar(c, "mini-conta")],
      });
    }
  }

  // ── Por cuenta del panel ─────────────────────────────────────────────────
  const remUniverso = new Set(opPanel?.series_remision ?? []);
  for (const c of pn) {
    const a = c.alcance_panel;
    if (!a) {
      out.push({
        id: `pn-vacia-${c.id}`,
        tono: "warning",
        texto: <><strong>{c.nombre}</strong> (panel) no tiene alcance: no lee nada.</>,
        acciones: [editar(c, "panel")],
      });
      continue;
    }
    // La remisión de una serie de factura se llama «R» + la serie (RIO → RRIO).
    // Sin opciones no se sabe si la «R…» existe: solo se avisa con ellas.
    const propias = new Set(a.series_remision);
    const sinRem = opPanel
      ? a.series.map((s) => ({ fac: s, rem: `R${s}` })).filter((x) => remUniverso.has(x.rem) && !propias.has(x.rem))
      : [];
    if (sinRem.length) {
      out.push({
        id: `pn-rem-${c.id}`,
        tono: "warning",
        texto: (
          <>
            <strong>{c.nombre}</strong> (panel) comparte la factura{" "}
            <Codigos xs={sinRem.map((x) => x.fac)} /> pero no su remisión{" "}
            <Codigos xs={sinRem.map((x) => x.rem)} />.
          </>
        ),
        acciones: [editar(c, "panel")],
      });
    }
    if (!a.perfiles.length) {
      out.push({
        id: `pn-perfiles-${c.id}`,
        tono: "warning",
        texto: (
          <>
            <strong>{c.nombre}</strong> (panel) no tiene perfiles de órdenes
            {a.oc
              ? ": solo verá las órdenes que ya se volvieron remisión de sus series."
              : " y no comparte órdenes de compra."}
          </>
        ),
        acciones: [editar(c, "panel")],
      });
    }
  }

  const faltantes = [...out.filter((f) => f.tono === "warning"), ...out.filter((f) => f.tono === "info")];
  const conMovSeries = series.filter((f) => f.movimiento);
  return {
    faltantes,
    series,
    clientes: filasClientes,
    perfiles,
    hayOpciones,
    conteo: {
      pendientes: faltantes.filter((f) => f.tono === "warning").length,
      series: series.length,
      seriesConMov: conMovSeries.length,
      seriesSinCompartir: conMovSeries.filter((f) => f.faltaMC || f.faltaPn).length,
      seriesDuplicadas: series.filter((f) => f.dupMC || f.dupPn).length,
      clientes: filasClientes?.length ?? 0,
      clientesSinCubrir: filasClientes?.filter((f) => f.pendiente).length ?? 0,
      perfiles: perfiles.length,
      perfilesSinCuenta: perfilesSin.length,
      perfilesDuplicados: perfilesDup.length,
    },
  };
}

// ── Piezas de la pantalla ─────────────────────────────────────────────────

function Si({ si }: { si: boolean }) {
  return si ? (
    <Check size={16} className="mx-auto text-success" aria-label="Sí" />
  ) : (
    <X size={16} className="mx-auto text-danger" aria-label="No" />
  );
}

function EstadoCuenta({ c }: { c: Conexion }) {
  return c.estado === "ACTIVA" ? (
    <Badge tone="success">Leyendo</Badge>
  ) : (
    <Badge tone="warning">Falta pegar la clave</Badge>
  );
}

/** «Pendientes (n) | Todos (m)»: las tablas largas abren en lo que falta. */
function Filtro({
  soloPendientes,
  onCambio,
  pendientes,
  total,
}: {
  soloPendientes: boolean;
  onCambio: (v: boolean) => void;
  pendientes: number;
  total: number;
}) {
  const btn = (activo: boolean) =>
    `rounded-md px-2.5 py-1 text-xs font-medium transition ${
      activo ? "bg-background text-foreground shadow-sm" : "text-muted hover:text-foreground"
    }`;
  return (
    <div className="inline-flex rounded-lg border border-border bg-surface-2 p-0.5">
      <button type="button" className={btn(soloPendientes)} onClick={() => onCambio(true)}>
        Pendientes ({pendientes})
      </button>
      <button type="button" className={btn(!soloPendientes)} onClick={() => onCambio(false)}>
        Todos ({total})
      </button>
    </div>
  );
}

/** Quién lee algo: los nombres (clic → su «Qué comparte»), ✗ si nadie, ⚠ si dos. */
function Lectores({
  cuentas,
  falta,
  dup,
  aplica = true,
  onCuenta,
}: {
  cuentas: Conexion[];
  falta: boolean;
  dup?: boolean;
  aplica?: boolean;
  onCuenta: (c: Conexion) => void;
}) {
  if (!aplica) return <span className="text-muted">—</span>;
  if (!cuentas.length) {
    return falta ? (
      <span className="inline-flex items-center gap-1 text-danger">
        <X size={14} /> No compartida
      </span>
    ) : (
      <span className="text-muted">—</span>
    );
  }
  return (
    <div className={`flex flex-wrap items-center gap-x-1.5 gap-y-0.5 ${dup ? "text-favorite" : ""}`}>
      {dup ? <AlertTriangle size={14} className="flex-shrink-0" aria-label="En dos cuentas" /> : null}
      {cuentas.map((c, i) => (
        <span key={c.id}>
          <button type="button" className="hover:text-accent hover:underline" onClick={() => onCuenta(c)}>
            {!c.alcance && c.tipo === "MINI_CONTA" ? `${c.nombre} (todas)` : c.nombre}
          </button>
          {i < cuentas.length - 1 ? "," : ""}
        </span>
      ))}
    </div>
  );
}

/** Los códigos de un cliente, en rojo los que nadie le lee. */
function CodigosMarcados({ xs, sin }: { xs: string[]; sin: Set<string> }) {
  if (!xs.length) return <span className="text-muted">—</span>;
  return (
    <span className="font-mono text-xs leading-5">
      {xs.map((s, i) => (
        <span key={s}>
          <span className={sin.has(s) ? "font-semibold text-danger" : ""}>{s}</span>
          {i < xs.length - 1 ? ", " : ""}
        </span>
      ))}
    </span>
  );
}

const TH = "px-3 py-2 text-left font-semibold";
const THC = "px-2 py-2 text-center font-semibold";
const TD = "px-3 py-2.5 align-top";
const THEAD = "bg-surface-2 text-[11px] uppercase tracking-wider text-muted";

export function ResumenConexiones({
  smart,
  miniConta,
  panel,
  opMC,
  canWrite,
  auditoria,
  onIr,
}: {
  smart?: ConexionEstado;
  miniConta?: ConexionEstado;
  panel?: ConexionEstado;
  opMC: OpcionesMiniConta | null;
  canWrite: boolean;
  auditoria: Auditoria;
  onIr: (d: Destino) => void;
}) {
  const [soloSeries, setSoloSeries] = useState(true);
  const [soloClientes, setSoloClientes] = useState(true);
  const { faltantes, series, clientes, perfiles, conteo, hayOpciones } = auditoria;
  const mc = miniConta?.conexiones ?? [];
  const pn = panel?.conexiones ?? [];
  const smartCon = smart?.conexion && smart.conexion.estado !== "REVOCADA" ? smart.conexion : null;
  // El nombre de una cuenta lleva a su «Qué comparte» (o a su pestaña, si no se puede editar).
  const irCuenta = (c: Conexion, tab: TabConexiones) => onIr({ tab, conexionId: canWrite ? c.id : undefined });
  const enlace = "text-left font-medium hover:text-accent hover:underline";

  const seriesVisibles = soloSeries ? series.filter((f) => f.pendiente) : series;
  const seriesPend = series.filter((f) => f.pendiente).length;
  const clientesPend = (clientes ?? []).filter((f) => f.pendiente);
  const clientesVisibles = soloClientes ? clientesPend : clientes ?? [];
  const todo = conteo.pendientes === 0;

  return (
    <div className="space-y-4">
      {/* ── La respuesta en una línea ──────────────────────────────────── */}
      <div
        className={`rounded-xl border px-4 py-3.5 ${
          todo ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"
        }`}
      >
        <div className={`flex items-center gap-2 text-lg font-semibold ${todo ? "text-emerald-800" : "text-amber-800"}`}>
          {todo ? <CheckCircle2 size={20} /> : <AlertTriangle size={20} />}
          {todo ? "Todo compartido" : `${conteo.pendientes} ${conteo.pendientes === 1 ? "pendiente" : "pendientes"}`}
        </div>
        {hayOpciones ? (
          <div className="mt-1 flex flex-wrap gap-x-5 gap-y-1 text-sm text-foreground/80">
            <span>
              <strong>Series:</strong> {conteo.seriesSinCompartir} sin compartir
              {conteo.seriesDuplicadas ? ` · ${conteo.seriesDuplicadas} duplicadas` : ""} de {conteo.seriesConMov} con movimiento
              {conteo.series > conteo.seriesConMov ? ` (${conteo.series} en total)` : ""}
            </span>
            {clientes ? (
              <span>
                <strong>Clientes:</strong> {conteo.clientesSinCubrir} sin cubrir de {conteo.clientes} con movimiento
              </span>
            ) : null}
            {panel ? (
              <span>
                <strong>Perfiles:</strong> {conteo.perfilesSinCuenta} sin bodega
                {conteo.perfilesDuplicados ? ` · ${conteo.perfilesDuplicados} en 2 bodegas` : ""} de {conteo.perfiles}
              </span>
            ) : null}
          </div>
        ) : (
          <p className="mt-1 text-sm text-foreground/80">
            Para auditar series, clientes y perfiles hace falta poder administrar la empresa; aquí solo
            se ve lo que comparte cada cuenta.
          </p>
        )}
      </div>

      {/* ── Un renglón por sistema ─────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
        {smart ? (
          <button type="button" onClick={() => onIr({ tab: "smart-supply" })} className="bg-surface px-4 py-3.5 text-left hover:bg-surface-2">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-muted">
              <MessageCircle size={13} /> {smart.nombre} · órdenes
            </div>
            <div className="mt-1.5">
              {smartCon ? (
                smartCon.estado === "ACTIVA" ? (
                  <Badge tone="success">Recibiendo órdenes</Badge>
                ) : (
                  <Badge tone="warning">Falta pegar la clave</Badge>
                )
              ) : (
                <Badge tone="muted">Sin conectar</Badge>
              )}
            </div>
            {smartCon ? (
              <div className="mt-1 text-xs text-muted">
                {smart.ordenes_hoy} en 24 h · {smart.ordenes_sin_resolver} sin resolver
              </div>
            ) : null}
          </button>
        ) : null}
        {miniConta ? (
          <button type="button" onClick={() => onIr({ tab: "mini-conta" })} className="bg-surface px-4 py-3.5 text-left hover:bg-surface-2">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-muted">
              <Calculator size={13} /> {miniConta.nombre}
            </div>
            <div className="mt-0.5 text-2xl font-semibold tabular-nums">
              {mc.length} <span className="text-sm font-normal text-muted">{mc.length === 1 ? "cuenta" : "cuentas"}</span>
            </div>
          </button>
        ) : null}
        {panel ? (
          <button type="button" onClick={() => onIr({ tab: "panel" })} className="bg-surface px-4 py-3.5 text-left hover:bg-surface-2">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-muted">
              <Warehouse size={13} /> {panel.nombre}
            </div>
            <div className="mt-0.5 text-2xl font-semibold tabular-nums">
              {pn.length} <span className="text-sm font-normal text-muted">{pn.length === 1 ? "bodega" : "bodegas"}</span>
            </div>
          </button>
        ) : null}
      </div>

      {/* ── Lo que falta compartir ─────────────────────────────────────── */}
      <Card
        title={
          <span className="flex items-center gap-2">
            Lo que falta compartir
            {conteo.pendientes ? <Badge tone="warning">{conteo.pendientes}</Badge> : <Badge tone="success">Nada</Badge>}
          </span>
        }
        subtitle="Plazas, series, clientes y perfiles que nadie lee, que se leen dos veces o que una cuenta lee a medias."
      >
        {faltantes.length ? (
          <ul className="divide-y divide-border">
            {faltantes.map((f) => (
              <li key={f.id} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5 py-2.5 first:pt-0 last:pb-0">
                <div className={`flex min-w-0 flex-1 items-start gap-2 text-sm ${f.tono === "info" ? "text-muted" : ""}`}>
                  {f.tono === "warning" ? (
                    <AlertTriangle size={15} className="mt-0.5 flex-shrink-0 text-favorite" />
                  ) : (
                    <Info size={15} className="mt-0.5 flex-shrink-0 text-muted" />
                  )}
                  <span>{f.texto}</span>
                </div>
                {f.acciones.length ? (
                  <div className="flex flex-wrap gap-x-3 gap-y-1 pl-6 sm:pl-0">
                    {f.acciones.map((a) => (
                      <button
                        key={a.etiqueta}
                        type="button"
                        onClick={() => onIr(canWrite ? a.destino : { ...a.destino, conexionId: undefined })}
                        className="inline-flex items-center gap-0.5 whitespace-nowrap text-sm font-medium text-accent hover:underline"
                      >
                        {canWrite || !a.destino.conexionId ? a.etiqueta : "Ver"}
                        <ChevronRight size={14} />
                      </button>
                    ))}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">
            {hayOpciones
              ? "Cada serie y cada cliente con movimiento lo lee alguna cuenta, sin duplicados, y cada perfil está en una bodega."
              : "Ninguna cuenta se quedó a medias."}
          </p>
        )}
      </Card>

      {/* ── Series: todas, con quién las lee ───────────────────────────── */}
      {hayOpciones && series.length ? (
        <div id="auditoria-series" className="scroll-mt-4">
          <Card
            title="Series"
            subtitle={`Todas las series de factura y de remisión, y qué cuenta lee cada una. ✗ nadie la lee; ⚠ la leen dos cuentas del mismo sistema. Las que no tuvieron movimiento en ${DIAS_MOVIMIENTO} días no cuentan como pendientes.`}
            actions={<Filtro soloPendientes={soloSeries} onCambio={setSoloSeries} pendientes={seriesPend} total={series.length} />}
          >
            {seriesVisibles.length ? (
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full min-w-[720px] text-sm">
                  <thead className={THEAD}>
                    <tr>
                      <th className={TH}>Serie</th>
                      <th className={TH}>Tipo</th>
                      <th className={TH}>Plaza</th>
                      {miniConta ? <th className={TH}>Mini Conta</th> : null}
                      {panel ? <th className={TH}>Panel</th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {seriesVisibles.map((f) => (
                      <tr key={`${f.tipo}-${f.codigo}`} className={`border-t border-border ${f.movimiento ? "" : "opacity-60"}`}>
                        <td className={`${TD} font-mono text-xs`}>
                          {f.codigo}
                          {!f.movimiento ? <div className="font-sans text-[11px] text-muted">sin movimiento</div> : null}
                          {!f.registrada ? <div className="font-sans text-[11px] text-danger">no dada de alta</div> : null}
                        </td>
                        <td className={`${TD} text-muted`}>{f.tipo === "FACTURA" ? "Factura" : "Remisión"}</td>
                        <td className={TD}>{f.plazas.join(", ") || <span className="text-muted">sin plaza</span>}</td>
                        {miniConta ? (
                          <td className={TD}>
                            <Lectores
                              cuentas={f.mc}
                              falta={f.faltaMC}
                              dup={f.dupMC}
                              aplica={f.tipo === "FACTURA"}
                              onCuenta={(c) => irCuenta(c, "mini-conta")}
                            />
                          </td>
                        ) : null}
                        {panel ? (
                          <td className={TD}>
                            <Lectores cuentas={f.pn} falta={f.faltaPn} dup={f.dupPn} onCuenta={(c) => irCuenta(c, "panel")} />
                          </td>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-muted">Ninguna serie pendiente.</p>
            )}
            {miniConta ? (
              <p className="mt-2 text-xs text-muted">
                Mini Conta no lee series de remisión: sus remisiones salen de las series de factura que comparte.
              </p>
            ) : null}
          </Card>
        </div>
      ) : null}

      {/* ── Clientes con movimiento ────────────────────────────────────── */}
      {clientes ? (
        <div id="auditoria-clientes" className="scroll-mt-4">
          <Card
            title="Clientes"
            subtitle={`Los que facturaron o remisionaron en los últimos ${DIAS_MOVIMIENTO} días. En rojo, las series en que nadie les lee.`}
            actions={
              <Filtro soloPendientes={soloClientes} onCambio={setSoloClientes} pendientes={clientesPend.length} total={clientes.length} />
            }
          >
            {clientesVisibles.length ? (
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full min-w-[820px] text-sm">
                  <thead className={THEAD}>
                    <tr>
                      <th className={TH}>Cliente</th>
                      <th className={TH}>Plaza</th>
                      <th className={TH}>Factura en</th>
                      <th className={TH}>Remisiona en</th>
                      {miniConta ? <th className={TH}>Mini Conta</th> : null}
                      <th className={TH}>Qué falta</th>
                    </tr>
                  </thead>
                  <tbody>
                    {clientesVisibles.map((f) => {
                      const problemas: ReactNode[] = [];
                      if (f.fueraDe.length && f.facSinMC.length) {
                        problemas.push(<>Fuera de «Solo estos» de {nombres(f.fueraDe)}</>);
                      } else if (f.facSinMC.length) {
                        problemas.push(<>Mini Conta no lee <Codigos xs={f.facSinMC} /></>);
                      }
                      if (f.facSinPn.length || f.remSinPn.length) {
                        problemas.push(<>El panel no lee <Codigos xs={[...f.facSinPn, ...f.remSinPn]} /></>);
                      }
                      return (
                        <tr key={f.id} className="border-t border-border">
                          <td className={`${TD} font-medium`}>{f.nombre}</td>
                          <td className={TD}>{f.plazas.join(", ") || <span className="text-muted">—</span>}</td>
                          <td className={TD}>
                            <CodigosMarcados xs={f.fac} sin={new Set([...f.facSinMC, ...f.facSinPn])} />
                          </td>
                          <td className={TD}>
                            <CodigosMarcados xs={f.rem} sin={new Set(f.remSinPn)} />
                          </td>
                          {miniConta ? (
                            <td className={TD}>
                              <Lectores cuentas={f.mc} falta={mc.length > 0 && f.fac.length > 0} onCuenta={(c) => irCuenta(c, "mini-conta")} />
                            </td>
                          ) : null}
                          <td className={TD}>
                            {problemas.length ? (
                              <ul className="space-y-0.5">
                                {problemas.map((p, i) => (
                                  <li key={i} className="flex items-start gap-1.5 text-favorite">
                                    <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" />
                                    <span className="text-foreground">{p}</span>
                                  </li>
                                ))}
                              </ul>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-success">
                                <Check size={14} /> Cubierto
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-muted">Todos los clientes con movimiento están cubiertos.</p>
            )}
          </Card>
        </div>
      ) : null}

      {/* ── Perfiles de órdenes ────────────────────────────────────────── */}
      {panel && hayOpciones && perfiles.length ? (
        <div id="auditoria-perfiles" className="scroll-mt-4">
          <Card
            title="Perfiles de órdenes"
            subtitle="Por dónde entran las órdenes de compra. Cada perfil debe estar en exactamente una bodega del panel."
          >
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full min-w-[560px] text-sm">
                <thead className={THEAD}>
                  <tr>
                    <th className={TH}>Perfil</th>
                    <th className={TH}>Plaza de sus órdenes</th>
                    <th className={TH}>Bodega del panel</th>
                  </tr>
                </thead>
                <tbody>
                  {perfiles.map((f) => (
                    <tr key={f.perfil} className="border-t border-border">
                      <td className={`${TD} break-all font-mono text-xs`}>{f.perfil}</td>
                      <td className={TD}>{f.plaza ?? <span className="text-muted">—</span>}</td>
                      <td className={TD}>
                        <Lectores
                          cuentas={f.cuentas}
                          falta={pn.length > 0}
                          dup={f.cuentas.length > 1}
                          onCuenta={(c) => irCuenta(c, "panel")}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      ) : null}

      {/* ── Mini Conta, cuenta por cuenta ──────────────────────────────── */}
      {miniConta && mc.length ? (
        <Card
          title={<span className="flex items-center gap-2"><Calculator size={15} /> {miniConta.nombre}</span>}
          subtitle="Qué lee cada cuenta además de las ventas de sus series."
        >
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[820px] text-sm">
              <thead className={THEAD}>
                <tr>
                  <th className={TH}>Cuenta</th>
                  <th className={TH}>Series / plaza</th>
                  <th className={TH}>Clientes</th>
                  {DATOS_MINI_CONTA.map((d) => (
                    <th key={d.clave} className={THC}>{d.titulo}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {mc.map((c) => {
                  const a = c.alcance;
                  return (
                    <tr key={c.id} className="border-t border-border">
                      <td className={TD}>
                        <button type="button" className={enlace} onClick={() => irCuenta(c, "mini-conta")}>
                          {c.nombre}
                        </button>
                        <div className="mt-0.5"><EstadoCuenta c={c} /></div>
                      </td>
                      {a ? (
                        <>
                          <td className={TD}>{resumenSeries(a.series, opMC)}</td>
                          <td className={TD}>
                            {a.clientes ? (
                              <span title={resumenClientes(a.clientes, opMC)}>
                                Solo estos ({a.clientes.length})
                              </span>
                            ) : (
                              "Todos"
                            )}
                          </td>
                          {DATOS_MINI_CONTA.map((d) => (
                            <td key={d.clave} className="px-2 py-2.5 text-center align-top">
                              <Si si={a[d.clave]} />
                            </td>
                          ))}
                        </>
                      ) : (
                        <td colSpan={2 + DATOS_MINI_CONTA.length} className={`${TD} text-favorite`}>
                          Clave de antes: lee todas las series y todos los clientes.
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {/* ── Panel de Smart Supply, bodega por bodega ───────────────────── */}
      {panel && pn.length ? (
        <Card
          title={<span className="flex items-center gap-2"><Warehouse size={15} /> {panel.nombre}</span>}
          subtitle="Qué lee cada bodega además del facturado de sus series."
        >
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[820px] text-sm">
              <thead className={THEAD}>
                <tr>
                  <th className={TH}>Cuenta</th>
                  <th className={TH}>Plaza</th>
                  <th className={TH}>Series factura</th>
                  <th className={TH}>Series remisión</th>
                  <th className={TH}>Perfiles de órdenes</th>
                  {DATOS_PANEL.map((d) => (
                    <th key={d.clave} className={THC}>{d.titulo}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pn.map((c) => {
                  const a = c.alcance_panel;
                  const vacio = <span className="text-favorite">ninguno</span>;
                  return (
                    <tr key={c.id} className="border-t border-border">
                      <td className={TD}>
                        <button type="button" className={enlace} onClick={() => irCuenta(c, "panel")}>
                          {c.nombre}
                        </button>
                        <div className="mt-0.5"><EstadoCuenta c={c} /></div>
                      </td>
                      {a ? (
                        <>
                          <td className={TD}>{a.plaza || "—"}</td>
                          <td className={`${TD} font-mono text-xs leading-5`}>{a.series.join(", ") || "—"}</td>
                          <td className={`${TD} font-mono text-xs leading-5`}>{a.series_remision.join(", ") || vacio}</td>
                          <td className={`${TD} break-all font-mono text-xs leading-5`}>{a.perfiles.join(", ") || vacio}</td>
                          {DATOS_PANEL.map((d) => (
                            <td key={d.clave} className="px-2 py-2.5 text-center align-top">
                              <Si si={a[d.clave]} />
                            </td>
                          ))}
                        </>
                      ) : (
                        <td colSpan={4 + DATOS_PANEL.length} className={`${TD} text-muted`}>
                          Sin alcance: no lee nada.
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

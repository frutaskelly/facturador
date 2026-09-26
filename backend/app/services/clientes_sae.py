"""Los clientes de una empresa del SAE 9, dados de alta y ligados en el Facturador.

El espejo NO adivina de quién es una factura: sin una equivalencia confirmada
'91:CLAVE' y sin `clientes.espejo_sae` encendido, el depósito la rechaza a
propósito (`factura_espejo`). Con el SAE 10 esas equivalencias se fueron
armando a mano durante meses; las empresas del SAE 9 llegan con clientes que el
Facturador no conoce (26-sep-2026: iFood, Newrest, Vips, ISS…), y sin esto su
espejo saldría vacío.

LO QUE HACE, cliente por cliente (sólo los que tienen facturas desde el piso):

  · Ya tiene equivalencia confirmada a un cliente vivo → se deja.
  · Su RFC ya existe en el tenant, en UN solo cliente que ya espeja SAE → se
    liga a ése. Si ese cliente factura NATIVO (espejo apagado) o ya vive en
    otra empresa de SAE, NO se toca: queda «revisar» para una persona.
  · Su RFC existe en VARIOS clientes → NO se decide: se reporta. Ligar al
    equivocado mandaría facturas al estado de cuenta de otro.
  · RFC que el SAT no aceptaría (formato o dígito) → se reporta y no se crea.
  · Si no, se CREA el cliente con los datos fiscales de SAE y se liga.

El RFC genérico (público en general) no identifica a nadie: ahí se liga por
nombre exacto, o se crea uno por nombre.

Por omisión corre EN SECO (`aplicar=False`): devuelve el plan sin tocar nada.
Nunca escribe en SAE.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from ..core.rbac import AuthContext
from ..models import Cliente
from . import cliente_match, sae_lectura
from .cliente_codigo import generate_cliente_codigo
from .rfc import validar_rfc_local

_RFC_GENERICOS = {"XAXX010101000", "XEXX010101000"}
_REGIMEN = re.compile(r"^\d{3}$")
_CP = re.compile(r"^\d{5}$")
_USO = re.compile(r"^[A-Z]\d{2}$|^CN01$|^CP01$|^S01$")


def leer_clientes(empresa: str, desde: Optional[dt.date]) -> list[dict[str, Any]]:
    """Los clientes de CLIE que tienen al menos una factura desde `desde`.

    Va dentro de `en_empresa` de una empresa del SAE 9 (Firebird). La clave de
    cliente de SAE viene rellena de espacios a la izquierda ('       145'); la
    comparación entre CLIE y FACTF es entre valores crudos del mismo SAE, y lo
    que sale se limpia aquí, igual que el espejo limpia CVE_CLPV.
    """
    if sae_lectura.motor() != "firebird":
        raise ValueError("el alta de clientes desde SAE es para el SAE 9 (Firebird)")
    clie, factf = sae_lectura.tabla("CLIE", empresa), sae_lectura.tabla("FACTF", empresa)
    sub = f"SELECT DISTINCT F.CVE_CLPV FROM {factf} F"
    params: list = []
    if desde:
        sub += " WHERE F.FECHA_DOC >= %s"
        params.append(desde.isoformat())
    cols = sae_lectura.columnas(clie)

    def _c(nombre: str, largo: int = 80) -> str:
        return f"C.{nombre}" if nombre in cols else f"CAST(NULL AS VARCHAR({largo}))"

    filas = sae_lectura.consultar(
        "SELECT C.CLAVE AS clave, C.NOMBRE AS nombre, C.RFC AS rfc, "
        f"{_c('CODIGO', 5)} AS cp, {_c('REG_FISC', 4)} AS regimen, "
        f"{_c('USO_CFDI', 5)} AS uso_cfdi, {_c('CALLE')} AS calle, "
        f"{_c('NUMEXT', 15)} AS numext, {_c('NUMINT', 15)} AS numint, "
        f"{_c('COLONIA', 50)} AS colonia, C.STATUS AS status, "
        # Crédito y correo: sin ellos el cliente nace con 0 días y toda su
        # cartera sale VENCIDA, y el envío de facturas no tiene a quién.
        f"{_c('DIASCRED', 10)} AS dias_credito, {_c('LIMCRED', 30)} AS limite_credito, "
        f"{_c('EMAILPRED', 512)} AS email "
        f"FROM {clie} C WHERE C.CLAVE IN ({sub})",
        tuple(params),
    )
    lx = sae_lectura
    salida = []
    for f in filas:
        clave = lx.texto(f.get("clave"))
        if not clave:
            continue
        salida.append({
            "clave": clave,
            "nombre": " ".join(lx.texto(f.get("nombre")).split()),
            "rfc": lx.texto(f.get("rfc")).upper().replace(" ", "").replace("-", ""),
            "cp": lx.texto(f.get("cp")), "regimen": lx.texto(f.get("regimen")),
            "uso_cfdi": lx.texto(f.get("uso_cfdi")).upper(),
            "calle": lx.texto(f.get("calle")), "numext": lx.texto(f.get("numext")),
            "numint": lx.texto(f.get("numint")), "colonia": lx.texto(f.get("colonia")),
            "status": lx.texto(f.get("status")),
            "dias_credito": _entero(f.get("dias_credito"), 0, 730),
            "limite_credito": _decimal(f.get("limite_credito")),
            "correos": _correos(f.get("email")),
        })
    return sorted(salida, key=lambda x: x["nombre"])


def _entero(v: Any, minimo: int, maximo: int) -> int:
    try:
        return max(minimo, min(maximo, int(float(v))))
    except (TypeError, ValueError):
        return minimo


def _decimal(v: Any) -> Decimal:
    try:
        d = Decimal(str(v if v is not None else 0))
    except Exception:
        return Decimal(0)
    return d if d > 0 else Decimal(0)


def _correos(v: Any) -> list[str]:
    """EMAILPRED de SAE: uno o varios, separados por ; , o espacios."""
    vistos: list[str] = []
    for c in re.split(r"[;,\s]+", str(v or "")):
        c = c.strip().lower()
        if c and "@" in c and "." in c.split("@")[-1] and c not in vistos:
            vistos.append(c)
    return vistos


def _norm_nombre(s: str) -> str:
    return " ".join(str(s or "").upper().split())


def alta_clientes(db: Session, ctx: AuthContext, empresa: str, filas: list[dict[str, Any]],
                  aplicar: bool = False,
                  hermanas: Optional[Iterable[str]] = None) -> dict[str, Any]:
    """El plan (y, con `aplicar`, su ejecución) para esos clientes de SAE.

    Acciones del plan:
      · ya_ligado    — equivalencia CONFIRMADA a un cliente vivo con espejo.
      · ligar        — su RFC está en UN cliente del tenant, que ya espeja SAE
                       y no tiene claves de otra empresa: se liga.
      · crear        — no existe: se crea con los datos fiscales de SAE.
      · revisar      — existe, pero ligarlo cambiaría cómo se trabaja con él
                       (factura NATIVO, o ya vive en otra empresa de SAE): lo
                       decide una persona. Nunca se aplica solo.
      · ambiguo      — su RFC está en varios clientes.
      · rfc_invalido — el SAT no lo aceptaría.

    `hermanas` son los códigos del SAE 9 de este mismo tenant (91, 92…). Un
    cliente que ya vive en una hermana NO cuenta como «de otra empresa»: el
    candado existe por el export de remisiones al SAE 10 (02-05), que no toca
    al SAE 9. Sin esto, quien compra en la 01 y en la 02 se quedaba en
    «revisar» y sus facturas de la 02 no entraban nunca. Por omisión se toman
    del registro (`sae_fuentes`).
    """
    from ..models import ClienteExterno

    tenant = ctx.tenant_id
    if hermanas is None:
        from . import sae_fuentes
        hermanas = {e.codigo for e in sae_fuentes.del_tenant(tenant) if e.servidor.es_firebird}
    no_cuentan = set(hermanas) | {empresa}
    vivos = db.query(Cliente).filter(Cliente.tenant_id == tenant,
                                     Cliente.deleted_at.is_(None)).all()
    por_id = {c.id: c for c in vivos}
    por_rfc: dict[str, list[Cliente]] = {}
    for c in vivos:
        por_rfc.setdefault((c.rfc or "").strip().upper(), []).append(c)
    # Las empresas de SAE en las que ya vive cada cliente (claves 'NN:...'
    # confirmadas de OTRA empresa): ligarlo también aquí lo pondría en dos
    # empresas y el export del SAE 10 ya no sabría a cuál mandar su remisión.
    otras: dict = {}
    for cid, clave in db.query(ClienteExterno.cliente_id, ClienteExterno.clave).filter(
            ClienteExterno.tenant_id == tenant, ClienteExterno.sistema == "SAE",
            ClienteExterno.confianza == "CONFIRMADA"):
        m = re.match(r"^\s*(\d+)\s*[:. ]", clave or "")
        if m and m.group(1) not in no_cuentan:
            otras.setdefault(cid, set()).add(m.group(1))

    def _motivo_revisar(cli: Cliente) -> Optional[str]:
        if not cli.espejo_sae:
            return ("factura NATIVO en el Facturador: encender su espejo bloquearía su "
                    "facturación propia — decídelo a mano")
        if otras.get(cli.id):
            return (f"ya vive en la empresa SAE {', '.join(sorted(otras[cli.id]))}: ligarlo "
                    "también aquí confundiría el export de sus remisiones — decídelo a mano")
        return None

    plan: list[dict[str, Any]] = []
    conteo = {"ya_ligado": 0, "ligar": 0, "crear": 0, "revisar": 0, "ambiguo": 0,
              "rfc_invalido": 0}
    for f in filas:
        clave_eq = f"{empresa}:{f['clave']}"
        item = {"clave": f["clave"], "nombre": f["nombre"], "rfc": f["rfc"],
                "equivalencia": clave_eq}
        eq = cliente_match.buscar_equivalencia(db, tenant, "SAE", clave_eq)
        cli_eq = por_id.get(eq.cliente_id) if eq is not None else None
        if eq is not None and eq.confianza == "CONFIRMADA" and cli_eq is not None:
            if not cli_eq.espejo_sae:
                item.update(accion="revisar", cliente_id=str(cli_eq.id),
                            cliente=cli_eq.legal_name, motivo=_motivo_revisar(cli_eq))
                conteo["revisar"] += 1
            else:
                item.update(accion="ya_ligado", cliente_id=str(cli_eq.id),
                            cliente=cli_eq.legal_name)
                conteo["ya_ligado"] += 1
            plan.append(item)
            continue
        # Una equivalencia SUGERIDA, o que apunta a un cliente borrado, no
        # cuenta: el depósito exige CONFIRMADA y viva. Se sigue al cruce por
        # RFC y `aprender` reapunta esa misma fila.

        rfc = f["rfc"]
        v = validar_rfc_local(rfc)
        if not rfc or not v["formato_ok"] or not v["digito_ok"]:
            item.update(accion="rfc_invalido",
                        motivo="el RFC de SAE no pasa el formato o el dígito del SAT")
            conteo["rfc_invalido"] += 1
            plan.append(item)
            continue

        if rfc in _RFC_GENERICOS:
            candidatos = [c for c in por_rfc.get(rfc, [])
                          if _norm_nombre(c.legal_name) == _norm_nombre(f["nombre"])]
        else:
            candidatos = por_rfc.get(rfc, [])
        if len(candidatos) > 1:
            item.update(accion="ambiguo",
                        motivo=f"{len(candidatos)} clientes con ese RFC — elígelo a mano",
                        candidatos=[str(c.id) for c in candidatos])
            conteo["ambiguo"] += 1
            plan.append(item)
            continue

        cli = candidatos[0] if candidatos else None
        if cli is not None:
            motivo = _motivo_revisar(cli)
            if motivo:
                item.update(accion="revisar", cliente_id=str(cli.id), cliente=cli.legal_name,
                            motivo=motivo)
                conteo["revisar"] += 1
                plan.append(item)
                continue
            item.update(accion="ligar", cliente_id=str(cli.id), cliente=cli.legal_name)
            conteo["ligar"] += 1
        else:
            item.update(accion="crear")
            conteo["crear"] += 1

        if aplicar:
            if cli is None:
                # El Facturador guarda la calle con su número en un solo
                # renglón (así la pintan el PDF y el estado de cuenta).
                calle = " ".join(p for p in (
                    f["calle"], f["numext"], f"INT {f['numint']}" if f["numint"] else "") if p)
                dom = {k: val for k, val in (("cp", f["cp"] if _CP.match(f["cp"] or "") else None),
                                             ("calle", calle or None),
                                             ("colonia", f["colonia"] or None),
                                             ("correos", f.get("correos") or None)) if val}
                cli = Cliente(
                    tenant_id=tenant, codigo=generate_cliente_codigo(db, tenant),
                    legal_name=(f["nombre"] or rfc)[:254], rfc=rfc,
                    regimen_fiscal=f["regimen"] if _REGIMEN.match(f["regimen"] or "") else None,
                    uso_cfdi_default=f["uso_cfdi"] if _USO.match(f["uso_cfdi"] or "") else None,
                    domicilio_fiscal=dom, espejo_sae=True,
                    dias_credito=int(f.get("dias_credito") or 0),
                    limite_credito=f.get("limite_credito") or Decimal(0),
                )
                db.add(cli)
                db.flush()
                vivos.append(cli)
                por_id[cli.id] = cli
                por_rfc.setdefault(rfc, []).append(cli)
                item["cliente_id"] = str(cli.id)
            cliente_match.aprender(db, tenant, "SAE", clave_eq, cli.id,
                                   origen="IMPORT", confianza="CONFIRMADA",
                                   user_id=ctx.user_id)
        plan.append(item)

    if aplicar:
        db.flush()
    return {"empresa": empresa, "aplicado": bool(aplicar), "total": len(filas),
            "conteo": conteo, "clientes": plan}

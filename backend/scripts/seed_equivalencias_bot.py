"""Siembra las equivalencias de cliente desde la config del bot de Smart Supply.

El bot ya sabe, hoy, a qué cliente pertenece cada grupo, proyecto y ubicación —
solo que esa respuesta vive regada en cinco archivos que ya divergen entre sí
(`sheets_config.json`, `ehmo_pedidos.py`, `sheets_push.py`, `db/0002_seed_grupos.sql`).
Este script la lee y la deja en un solo lugar: `cliente_externos`.

Es idempotente y por default NO ESCRIBE: imprime el plan. Se aplica con
`--aplicar`. Nunca crea CLIENTES — dar de alta un cliente es una decisión de
negocio, no de un script; lo que no cruce se reporta y se salta.

Las SUCURSALES (los hospitales/planteles del catálogo del perfil) sí se pueden
crear con `--crear-sucursales`, porque salen de la config del propio dueño, no
de un documento que llegó de fuera. Una ubicación que aparezca DESPUÉS, en una
orden real, nunca se crea sola: eso se decide en la bandeja.

Uso:
    DATABASE_URL=... python -m scripts.seed_equivalencias_bot --tenant <slug>
    DATABASE_URL=... python -m scripts.seed_equivalencias_bot --tenant <slug> --aplicar
    DATABASE_URL=... python -m scripts.seed_equivalencias_bot --tenant <slug> --aplicar --crear-sucursales
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.core.rbac import tenant_session
from app.models import Cliente, Sucursal, Tenant
from app.services import cliente_match
from app.services.producto_match import normalizar

CONFIG_DEFAULT = Path.home() / "Documents/Claude/SmartSupply/bot/sheets_config.json"

# Perfil `ehmo` (Pachuca): proyecto → cliente de SAE y los datos fiscales del
# receptor viven hardcodeados en el bot, no en la config (ehmo_pedidos.py
# CLIENTE_SAE:3208 y RECEPTOR:3742, «verificados contra la tabla CLIE»). El bot
# los usa como RESPALDO cuando el perfil no declara los suyos (`_propio`), y aquí
# se replica esa misma regla.
CLIENTE_SAE_EHMO = {
    "HOSPITALES": "5",
    "DIF": "4",
    "CEREZOS": "4",
    "SEGURIDAD PUBLICA": "4",
    "SECRETARIO NERI": "4",
}
RECEPTOR_EHMO = {
    "5": {"nombre": "GRUPO OPERADOR DE ALIMENTOS EHMO", "rfc": "GOA180712SF5"},
    "4": {"nombre": "MEDIOS DE ALIMENTACION MAFAN", "rfc": "MCM170118UJ6"},
}


def _cargar(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"No encuentro la config del bot en {path} (usa --config)")
    return json.loads(path.read_text(encoding="utf-8"))


def _clientes_por_rfc_y_nombre(db, tenant_id):
    """Índices para cruzar lo que dice la config contra el padrón real."""
    rows = db.query(Cliente).filter(
        Cliente.tenant_id == tenant_id, Cliente.deleted_at.is_(None)
    ).all()
    por_rfc = {(c.rfc or "").upper(): c for c in rows if c.rfc}
    por_nombre = {normalizar(c.legal_name): c for c in rows}
    return rows, por_rfc, por_nombre


def _buscar_cliente(rows, por_rfc, por_nombre, *, rfc=None, nombre=None):
    """RFC primero (identifica sin lugar a dudas); luego nombre normalizado; y
    de último, que el nombre de la config esté contenido en la razón social
    («BALLES» → «OPERADORA BALLES VEGA DE HIDALGO»)."""
    if rfc:
        hit = por_rfc.get(rfc.upper())
        if hit:
            return hit, "rfc"
    if nombre:
        n = normalizar(nombre)
        if n in por_nombre:
            return por_nombre[n], "nombre exacto"
        parciales = [c for c in rows if n and n in normalizar(c.legal_name)]
        if len(parciales) == 1:
            return parciales[0], "nombre contenido"
    return None, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="slug del tenant destino")
    ap.add_argument("--config", type=Path, default=Path(os.environ.get("BOT_CONFIG", CONFIG_DEFAULT)))
    ap.add_argument("--aplicar", action="store_true", help="escribe (sin esto solo imprime el plan)")
    ap.add_argument("--crear-sucursales", action="store_true",
                    help="da de alta las ubicaciones del perfil que aún no existen como sucursal")
    args = ap.parse_args()

    cfg = _cargar(args.config)
    # El slug se resuelve sin scope (la RLS de `tenants` es por id); de ahí en
    # adelante todo corre DENTRO del tenant, igual que la app. Sin esto el script
    # es owner, la RLS no aplica, y una clave repetida entre inquilinos —los JID,
    # 'ehmo:HOSPITALES', un RFC compartido— se reapuntaría de un tenant al otro.
    arranque = SessionLocal()
    try:
        t = arranque.query(Tenant).filter(Tenant.slug == args.tenant).one_or_none()
        if t is None:
            sys.exit(f"No existe el tenant «{args.tenant}»")
        tenant_id, tenant_slug = t.id, t.slug
    finally:
        arranque.close()

    with tenant_session(tenant_id) as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).one()
        rows, por_rfc, por_nombre = _clientes_por_rfc_y_nombre(db, tenant.id)
        print(f"Tenant {tenant_slug} · {len(rows)} clientes en el padrón\n")

        plan: list[tuple[str, str, Cliente, str | None, str]] = []
        saltados: list[str] = []
        nuevas: list[str] = []

        # ── 1. perfiles: receptor (RFC), proyectos y ubicaciones ─────────────
        for perfil_id, perfil in (cfg.get("perfiles") or {}).items():
            if not isinstance(perfil, dict):
                continue
            empresa = str(perfil.get("empresa") or "")

            # receptor: {"<cliente_sae>": {"nombre": ..., "rfc": ...}}
            receptores = perfil.get("receptor") or (RECEPTOR_EHMO if perfil_id == "ehmo" else {})
            del_perfil: dict[str, Cliente] = {}   # clave SAE → cliente, de ESTE perfil
            for clave_sae, receptor in receptores.items():
                if not isinstance(receptor, dict):
                    continue
                cli, via = _buscar_cliente(
                    rows, por_rfc, por_nombre,
                    rfc=receptor.get("rfc"), nombre=receptor.get("nombre"),
                )
                if cli is None:
                    saltados.append(
                        f"{perfil_id}: receptor «{receptor.get('nombre')}» "
                        f"({receptor.get('rfc')}) no está en el padrón"
                    )
                    continue
                del_perfil[str(clave_sae)] = cli
                if receptor.get("rfc"):
                    plan.append(("RFC", receptor["rfc"], cli, None, f"{perfil_id} · {via}"))
                if receptor.get("nombre"):
                    plan.append(("NOMBRE", receptor["nombre"], cli, None, perfil_id))
                if empresa:
                    plan.append(("SAE", f"{empresa}:{clave_sae}", cli, None, perfil_id))

            # proyecto → cliente de SAE (config del perfil, o la tabla del bot)
            mapa = perfil.get("cliente_sae") or {}
            if not mapa and perfil_id == "ehmo":
                mapa = {k: [v] for k, v in CLIENTE_SAE_EHMO.items()}
            for proyecto, valor in mapa.items():
                clave_sae = str(valor[0] if isinstance(valor, (list, tuple)) else valor)
                cli = del_perfil.get(clave_sae)
                if cli is None:
                    saltados.append(
                        f"{perfil_id}: proyecto {proyecto} → SAE {empresa}:{clave_sae} "
                        "sin cliente conocido (registra primero su RFC)"
                    )
                    continue
                plan.append(("PROYECTO", f"{perfil_id}:{proyecto}", cli, None, perfil_id))

            # ubicaciones → sucursal, cruzando por código de 3 letras o nombre
            ubis = perfil.get("ubicaciones") or []
            # La ubicación solo puede colgar de UN cliente: si el perfil tiene
            # varios receptores no se adivina de cuál es el hospital.
            cli_perfil = next(iter(del_perfil.values())) if len(del_perfil) == 1 else None
            for u in ubis:
                if not isinstance(u, dict) or cli_perfil is None:
                    continue
                suc = _sucursal(db, cli_perfil.id, u.get("codigo"), u.get("nombre"))
                if suc is None and args.crear_sucursales and u.get("nombre"):
                    suc = Sucursal(
                        tenant_id=tenant.id, cliente_id=cli_perfil.id,
                        codigo=(u.get("codigo") or None), nombre=u["nombre"],
                        domicilio={"region": u.get("region")} if u.get("region") else {},
                    )
                    nuevas.append(f"{u['nombre']} ({u.get('codigo') or 's/c'}) en {cli_perfil.legal_name}")
                    if args.aplicar:
                        try:
                            # Savepoint: un código que choque con una sucursal
                            # borrada lógicamente no puede tumbar toda la siembra.
                            with db.begin_nested():
                                db.add(suc)
                                db.flush()
                        except IntegrityError:
                            nuevas.pop()
                            saltados.append(
                                f"{perfil_id}: no se pudo crear «{u.get('nombre')}» "
                                f"({u.get('codigo')}) — ese código ya existe en el cliente"
                            )
                            suc = None
                    else:
                        suc = None      # en seco no hay id que registrar
                if suc is None:
                    saltados.append(
                        f"{perfil_id}: ubicación «{u.get('nombre')}» ({u.get('codigo')}) "
                        + ("se daría de alta como sucursal" if args.crear_sucursales
                           else f"no tiene sucursal en {cli_perfil.legal_name}")
                    )
                    continue
                for texto in [u.get("nombre"), *(u.get("alias") or [])]:
                    if texto:
                        plan.append(
                            ("UBICACION", f"{perfil_id}:{texto}", cli_perfil, str(suc.id), perfil_id)
                        )

        # ── 2. grupos mono-cliente → WHATSAPP ────────────────────────────────
        grupos = {
            j: g for j, g in (cfg.get("grupos") or {}).items()
            if isinstance(g, dict) and g.get("cliente")
        }
        # Un JID solo sirve como pista si ese grupo recibe órdenes de UN cliente.
        # Hoy no es el caso general: por el grupo de Pachuca entran EHMO y MAFAN.
        por_jid: dict[str, set[str]] = {}
        for j, g in grupos.items():
            por_jid.setdefault(j, set()).add(str(g["cliente"]))
        # Cuántos receptores tiene el perfil del grupo: >1 ⇒ el grupo es compartido.
        receptores_por_perfil = {
            pid: len((pv.get("receptor") or (RECEPTOR_EHMO if pid == "ehmo" else {})))
            for pid, pv in (cfg.get("perfiles") or {}).items() if isinstance(pv, dict)
        }
        for jid, g in grupos.items():
            if g.get("activo") is False:
                continue
            slug = str(g["cliente"])
            # El slug ('balles-pachuca') no es el nombre fiscal: se cruza por su
            # primera palabra contra la razón social.
            cli, via = _buscar_cliente(rows, por_rfc, por_nombre, nombre=slug.split("-")[0])
            if cli is None:
                saltados.append(f"grupo «{g.get('nombre')}» → {slug}: sin cliente en el padrón")
                continue
            perfil_grupo = str(g.get("perfil") or "ehmo")
            compartido = receptores_por_perfil.get(perfil_grupo, 1) > 1
            if compartido:
                saltados.append(
                    f"grupo «{g.get('nombre')}»: el perfil {perfil_grupo} tiene varios "
                    "receptores, así que el JID no identifica a un solo cliente — no se siembra"
                )
            else:
                plan.append(("WHATSAPP", jid, cli, None, f"{g.get('nombre')} · {via}"))
            # El bot distingue BALLES de JUBRAN por el nombre IMPRESO en el PDF
            # (sheets_push.py:3607); esa palabra es la equivalencia NOMBRE.
            palabra = slug.split("-")[0]
            if via == "nombre contenido" and len(palabra) >= 4:
                plan.append(("NOMBRE", palabra.upper(), cli, None, f"palabra en el PDF · {g.get('nombre')}"))

        # ── 3. imprimir / aplicar ────────────────────────────────────────────
        vistos: set[tuple[str, str]] = set()
        escritas = 0
        for sistema, clave, cli, suc_id, nota in plan:
            llave = (sistema, cliente_match.normalizar_clave(sistema, clave))
            if llave in vistos:
                continue
            vistos.add(llave)
            # Una corrección hecha a mano en la bandeja MANDA sobre la config del
            # bot: volver a correr el seed no puede deshacerla en silencio.
            previa = cliente_match.buscar_equivalencia(db, tenant.id, sistema, clave)
            if previa is not None and previa.origen == "MANUAL" and previa.cliente_id != cli.id:
                saltados.append(
                    f"{sistema} «{clave}» ya está asignada a mano a otro cliente — se respeta"
                )
                continue
            print(f"  {sistema:<9} {clave:<42} → {cli.legal_name}"
                  + ("  [sucursal]" if suc_id else "") + f"   ({nota})")
            if args.aplicar:
                cliente_match.aprender(
                    db, tenant.id, sistema, clave, cli.id,
                    sucursal_id=suc_id, origen="IMPORT", confianza="CONFIRMADA",
                )
                escritas += 1

        if nuevas:
            print("\nSucursales nuevas"
                  + (":" if args.aplicar else " (se crearían con --aplicar):"))
            for n in dict.fromkeys(nuevas):
                print(f"  + {n}")

        if saltados:
            print("\nSaltado (requiere decisión humana):")
            for s in dict.fromkeys(saltados):
                print(f"  · {s}")

        if args.aplicar:
            db.commit()
            print(f"\n{escritas} equivalencias escritas.")
        else:
            print(f"\n{len(vistos)} equivalencias propuestas. Nada escrito — corre con --aplicar.")


def _sucursal(db, cliente_id, codigo, nombre):
    q = db.query(Sucursal).filter(
        Sucursal.cliente_id == cliente_id, Sucursal.deleted_at.is_(None)
    )
    if codigo:
        hit = q.filter(Sucursal.codigo == codigo).one_or_none()
        if hit:
            return hit
    if nombre:
        n = normalizar(nombre)
        return next((s for s in q.all() if normalizar(s.nombre) == n), None)
    return None


if __name__ == "__main__":
    main()

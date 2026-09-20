"""El cruce de productos encuentra por la clave del SAE.

`productos.clave_sae` llegó con la migración 0079 (20-sep-2026: una sola clave
por producto, la misma en todas las empresas de SAE) y ningún consumidor del
cruce se actualizó: `normalizar_catalogo` indexaba nombre, sku y sinónimos, y
nada más. Efecto real, verificado contra producción: «precio de ACEI-ACEI-614»
no encontraba nada aunque el producto existiera con esa `clave_sae` — y 1,081 de
los 1,361 productos del inquilino vivo la tienen.

Importa para el canal de WhatsApp: el usuario escribe la clave del SAE, no el
sku interno del Facturador.
"""
import uuid

from app.services.producto_match import normalizar_catalogo


class _P:
    """Lo mínimo que el índice del cruce mira de un producto."""
    def __init__(self, nombre, sku, clave_sae=None, sinonimos=None):
        self.id = uuid.uuid4()
        self.nombre = nombre
        self.sku = sku
        self.clave_sae = clave_sae
        self.sinonimos = sinonimos or []


def test_el_indice_incluye_la_clave_sae():
    p = _P("ACEITE DE OLIVA 1L", "ACEITEOLIVA1L", clave_sae="ACEI-ACEI-614")
    idx = normalizar_catalogo([p])[p.id]

    assert len(idx) == 4, "el índice tiene que traer la clave_sae como cuarto elemento"
    assert idx[3] == "acei acei 614", "la clave_sae se normaliza como el resto"
    # Y no pisa lo que ya estaba: nombre, sku y sinónimos siguen en su sitio.
    assert idx[0] == "aceite de oliva 1l"
    assert idx[1] == "aceiteoliva1l"
    assert idx[2][0] == "aceite de oliva 1l"


def test_un_producto_sin_clave_sae_no_rompe_el_indice():
    """280 de los 1,361 productos de producción no la tienen."""
    p = _P("JITOMATE SALADET", "JITOMATESALADEKG", clave_sae=None)
    idx = normalizar_catalogo([p])[p.id]

    assert len(idx) == 4
    assert idx[3] == "", "sin clave_sae la entrada va vacía, no None: vacío nunca cruza"

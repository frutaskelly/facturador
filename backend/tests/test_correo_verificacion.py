"""Verificación del correo por liga (ticket 86bbxkzz5).

El correo de prueba trae un botón cuya liga pública marca la configuración
como verificada. El token es HMAC firmado con el secreto del backend: aquí se
prueba la ida y vuelta y que la liga rota no abre nada.
"""
from app.api.v1.correo import _abrir_token, _token_verificacion


def test_token_de_verificacion_ida_y_vuelta():
    t = _token_verificacion("abc-123")
    assert _abrir_token(t) == "abc-123"
    # Manipulado o basura: inválido, sin excepción.
    assert _abrir_token(t[:-4] + "XXXX") is None
    assert _abrir_token("no-es-un-token") is None
    assert _abrir_token("") is None


def test_verificar_con_token_invalido_no_marca_nada(client):
    r = client.get("/api/v1/correo/verificar", params={"t": "basura"})
    assert r.status_code == 400
    assert "inválida o vencida" in r.text.lower()

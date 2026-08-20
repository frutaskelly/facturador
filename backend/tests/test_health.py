def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"].startswith("2.")


def test_api_root(client):
    r = client.get("/api")
    assert r.status_code == 200
    assert r.json()["service"] == "facturador"

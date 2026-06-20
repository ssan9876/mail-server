"""App-level tests: health, error envelope, security headers."""


async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_security_headers_present(client):
    resp = await client.get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert "X-Request-ID" in resp.headers


async def test_unknown_route_404(client):
    resp = await client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404


async def test_error_envelope_shape_on_auth_failure(client):
    # Unauthenticated access to a protected route returns our JSON envelope.
    resp = await client.get("/api/v1/domains")
    assert resp.status_code == 401
    body = resp.json()
    assert set(body["error"].keys()) == {"code", "message"}
    assert resp.headers.get("WWW-Authenticate") == "Bearer"

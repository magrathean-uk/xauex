from flask import Flask, jsonify

from xauex.app.security import ProcessLocalRateLimiter, SecurityHeadersConfig, install_dashboard_security


def test_security_headers_added():
    app = Flask(__name__)
    install_dashboard_security(app)

    @app.get("/api/dashboard")
    def dashboard():
        return jsonify({"ok": True})

    response = app.test_client().get("/api/dashboard")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"


def test_manual_endpoint_rate_limited():
    app = Flask(__name__)
    limiter = ProcessLocalRateLimiter(limit=1, window_seconds=60)
    install_dashboard_security(app, limiter=limiter)

    @app.post("/api/manual-trade")
    def manual():
        return jsonify({"success": True})

    client = app.test_client()
    assert client.post("/api/manual-trade", json={"x": 1}).status_code == 200
    assert client.post("/api/manual-trade", json={"x": 1}).status_code == 429


def test_manual_endpoint_payload_size_limit():
    app = Flask(__name__)
    install_dashboard_security(app, config=SecurityHeadersConfig(max_json_body_bytes=5))

    @app.post("/api/manual-close")
    def manual_close():
        return jsonify({"success": True})

    response = app.test_client().post("/api/manual-close", data=("x" * 20), content_type="application/json")
    assert response.status_code == 413

"""Local-only foundation tests; no external data, credentials, or model calls."""

import pytest

from youju import create_app


@pytest.fixture
def client():
    return create_app({"TESTING": True}).test_client()


def test_health_is_explicit_about_missing_data(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json == {
        "service": "xdog-youju",
        "status": "ok",
        "data_status": "not_initialized",
    }


def test_unknown_api_route_returns_json(client):
    response = client.get("/api/nonexistent-route")
    assert response.status_code == 404
    assert response.json == {"error": {"code": 404, "message": "Not Found"}}


def test_health_is_read_only(client):
    assert client.post("/api/health").status_code == 405


def test_factories_have_isolated_configuration():
    first = create_app({"TESTING": True})
    second = create_app()
    assert first.testing is True
    assert second.testing is False


def test_internal_errors_do_not_expose_exception_details():
    app = create_app({"TESTING": False})

    @app.get("/failure")
    def failure():
        raise RuntimeError("synthetic-private-detail")

    response = app.test_client().get("/failure")
    assert response.status_code == 500
    assert response.json == {"error": {"code": 500, "message": "Internal Server Error"}}
    assert b"synthetic-private-detail" not in response.data

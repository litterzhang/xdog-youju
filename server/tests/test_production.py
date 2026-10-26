"""Production startup wiring without binding ports or contacting authentication."""

import os

import pytest

from youju import production


def configure(monkeypatch, tmp_path):
    # dotenv populates os.environ directly; isolate the entire mapping for each test.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.chdir(tmp_path)
    for key in (
        "ZDOG_AUTH_ISSUER",
        "ZDOG_AUTH_CLIENT_ID",
        "ZDOG_AUTH_CLIENT_SECRET",
        "YOUJU_DATABASE",
        "YOUJU_TRUSTED_HOSTS",
        "YOUJU_AUTH_ISSUER",
        "YOUJU_AUTH_CLIENT_ID",
        "YOUJU_AUTH_CLIENT_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "ZDOG_AUTH_ISSUER=https://auth.example.org\n"
        "ZDOG_AUTH_CLIENT_ID=synthetic-client\n"
        "ZDOG_AUTH_CLIENT_SECRET=synthetic-only\n"
        "YOUJU_DATABASE=source.sqlite3\n"
        'YOUJU_TRUSTED_HOSTS=\'["youju.example.org","127.0.0.1"]\'\n'
    )
    return tmp_path / "source.sqlite3"


def test_production_uses_private_environment_and_loopback_wsgi(monkeypatch, tmp_path):
    path = configure(monkeypatch, tmp_path)
    path.touch()
    # An operator environment override must take precedence over the local file.
    monkeypatch.setenv("ZDOG_AUTH_CLIENT_ID", "synthetic-override")
    calls = []
    monkeypatch.setattr(production, "serve", lambda app, **options: calls.append((app, options)))
    production.main()
    app, options = calls[0]
    assert app.extensions["auth"].client_id == "synthetic-override"
    assert app.debug is False
    assert options["host"] == "127.0.0.1" and options["port"] == 8089
    assert options["trusted_proxy"] == "127.0.0.1" and options["trusted_proxy_count"] == 1
    assert "x-forwarded-for" in options["trusted_proxy_headers"]
    assert options["max_request_body_size"] == 1024 * 1024


def test_production_does_not_create_missing_database(monkeypatch, tmp_path):
    path = configure(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(production, "serve", lambda *_a, **_k: calls.append(True))
    with pytest.raises(SystemExit, match="Database missing"):
        production.main()
    assert not path.exists() and not calls

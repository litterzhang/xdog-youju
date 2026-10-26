"""Explicit loopback-only production entry point; never starts crawling or Agents."""

from pathlib import Path

from dotenv import load_dotenv
from waitress import serve

from youju import create_app


def main():
    # Only this operator entry point loads local credentials; the factory/tests do not.
    load_dotenv(Path.cwd() / ".env", override=False)
    app = create_app()
    if not Path(app.config["DATABASE"]).is_file():
        raise SystemExit("Database missing; initialize the configured database before serving")
    if not app.extensions["auth"].configured:
        raise SystemExit("Configure the dedicated zdog-auth client before production startup")
    if not app.config.get("TRUSTED_HOSTS"):
        raise SystemExit("Configure explicit YOUJU_TRUSTED_HOSTS before production startup")
    serve(
        app,
        host="127.0.0.1",
        port=8089,
        threads=4,
        trusted_proxy="127.0.0.1",
        trusted_proxy_count=1,
        trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"},
        max_request_body_size=app.config["MAX_CONTENT_LENGTH"],
    )


if __name__ == "__main__":
    main()

"""Flask application factory; startup never fetches sources or invokes models."""

import os

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from youju.api.admin import admin_api
from youju.api.auth import AttemptLimiter, AuthClient, auth_api
from youju.api.common import ApiError
from youju.api.feedback import feedback_api
from youju.api.health import health_api
from youju.api.jobs import jobs_api
from youju.cli import register_commands
from youju.onboarding.cli import register_onboarding_commands
from youju.poc.cli import register_poc_commands
from youju.recipe_service.cli import register_recipe_service_commands
from youju.recipe_service.receiver import register_recipe_receiver
from youju.recipe_service.rpc import register_recipe_rpc


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=1024 * 1024,
        DATABASE="youju.sqlite3",
        SOURCE_STORAGE=".data/source-blobs",
        AUTH_ISSUER=os.getenv("ZDOG_AUTH_ISSUER", ""),
        AUTH_CLIENT_ID=os.getenv("ZDOG_AUTH_CLIENT_ID", ""),
        AUTH_CLIENT_SECRET=os.getenv("ZDOG_AUTH_CLIENT_SECRET", ""),
        ALLOWED_ORIGINS=["http://127.0.0.1:10089", "http://localhost:10089"],
    )
    app.config.from_prefixed_env(prefix="YOUJU")
    if test_config is not None:
        app.config.update(test_config)
    for key in ("ALLOWED_ORIGINS",):
        values = app.config[key]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError(f"{key} must be a JSON list of nonblank strings")
    app.extensions.update(auth=AuthClient(app.config), auth_limiter=AttemptLimiter())
    app.register_blueprint(health_api, url_prefix="/api")
    app.register_blueprint(auth_api, url_prefix="/api/auth")
    app.register_blueprint(admin_api, url_prefix="/api/admin")
    app.register_blueprint(jobs_api, url_prefix="/api")
    app.register_blueprint(feedback_api, url_prefix="/api")

    @app.before_request
    def check_origin():
        if not request.path.startswith("/api/"):
            return None
        allowed = app.config["ALLOWED_ORIGINS"]
        origin = request.headers.get("Origin")
        if request.method == "OPTIONS":
            headers = {
                h.strip().lower()
                for h in request.headers.get("Access-Control-Request-Headers", "").split(",")
                if h.strip()
            }
            if (
                origin not in allowed
                or request.headers.get("Access-Control-Request-Method") not in {"GET", "POST"}
                or not headers.issubset({"authorization", "content-type"})
            ):
                raise ApiError(403, "FORBIDDEN", "不允许此跨域请求。")
            return "", 204
        if request.method == "POST" and origin and origin not in allowed:
            raise ApiError(403, "FORBIDDEN", "不允许此跨域请求。")
        return None

    @app.after_request
    def response_headers(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            origin = request.headers.get("Origin")
            if origin in app.config["ALLOWED_ORIGINS"]:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
                response.vary.add("Origin")
        return response

    @app.errorhandler(ApiError)
    def handle_api_error(error):
        response = jsonify(error={"code": error.code, "message": error.message})
        response.status_code = error.status
        if error.status == 429:
            response.headers["Retry-After"] = "60"
        return response

    register_commands(app)
    register_poc_commands(app)
    register_onboarding_commands(app)
    from youju.onboarding.classification_cli import register_classification_commands

    register_classification_commands(app)
    register_recipe_service_commands(app)
    register_recipe_rpc(app)
    register_recipe_receiver(app)
    from youju.runtime_queries import register_runtime_queries

    register_runtime_queries(app)

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        return jsonify(error={"code": error.code, "message": error.name}), error.code

    @app.errorhandler(500)
    def handle_server_error(error):
        return jsonify(error={"code": 500, "message": "Internal Server Error"}), 500

    return app

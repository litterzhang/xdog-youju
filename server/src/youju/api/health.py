"""Liveness is not evidence of data freshness or model readiness."""

from flask import Blueprint, jsonify

health_api = Blueprint("health", __name__)


@health_api.get("/health")
def health():
    return jsonify(service="xdog-youju", status="ok", data_status="not_initialized")

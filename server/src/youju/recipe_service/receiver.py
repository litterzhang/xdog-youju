"""Authenticated durable callback inbox, independent of long source pass locks."""

import hashlib
import hmac
import json
import re
import sqlite3
from pathlib import Path

from flask import Blueprint, request
from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.api.common import ApiError, body
from youju.database import create_sqlite_engine
from youju.models import RepairRequest, SourcePipeline


def register_recipe_receiver(app):
    token = app.config.get("RECIPE_CALLBACK_TOKEN")
    if not token:
        return
    if not isinstance(token, str) or not token.isascii() or any(ord(c) <= 32 for c in token):
        raise ValueError("Invalid recipe callback token")
    inbox = Path(app.config["RECIPE_CALLBACK_INBOX"])
    if not inbox.is_absolute():
        raise ValueError("Recipe callback inbox must be absolute")
    bp = Blueprint("recipe_receiver", __name__)

    @bp.post("/internal/source/recipe-events")
    def receive():
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
            raise ApiError(401, "UNAUTHORIZED", "Callback authentication required")
        fields = (
            "event_id",
            "event",
            "task_id",
            "workspace_id",
            "kind",
            "status",
            "input_revision_id",
            "recipe_digest",
            "revision_id",
            "error_code",
        )
        data = body(fields)
        for name, length in (("event_id", 32), ("task_id", 32), ("workspace_id", 64)):
            if not isinstance(data[name], str) or not re.fullmatch(
                rf"[0-9a-f]{{{length}}}", data[name]
            ):
                raise ApiError(400, "INVALID_EVENT", "Invalid callback identifiers")
        if (
            request.headers.get("Idempotency-Key") != data["event_id"]
            or request.headers.get("X-Recipe-Event-ID") != data["event_id"]
        ):
            raise ApiError(400, "INVALID_EVENT", "Callback identifier mismatch")
        if data["event"] != "task.finished" or data["kind"] not in {"init", "build", "repair"}:
            raise ApiError(400, "INVALID_EVENT", "Invalid callback event")
        revision_id = data["revision_id"]
        if (
            revision_id is not None
            and (not isinstance(revision_id, str) or not re.fullmatch(r"[0-9a-f]{32}", revision_id))
        ) or (data["status"] == "candidate_ready" and revision_id is None):
            raise ApiError(400, "INVALID_EVENT", "Invalid recipe revision identifier")
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        if data["status"] not in {
            "succeeded",
            "candidate_ready",
            "blocked",
            "failed",
            "cancelled",
            "interrupted",
        }:
            raise ApiError(400, "INVALID_EVENT", "Invalid callback status")
        # A completed notification remains idempotent even after a later source repair.
        if inbox.is_file():
            with sqlite3.connect(inbox) as db:
                existing = db.execute(
                    "SELECT digest FROM events WHERE id=?", (data["event_id"],)
                ).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise ApiError(409, "CALLBACK_CONFLICT", "Callback payload changed")
                return {"received": True, "event_id": data["event_id"]}
        # Notification only; GetTask/GetRecipeRevision remain authoritative.
        # Read association without taking the source pass lock or activating anything.
        engine = create_sqlite_engine(app.config["DATABASE"])
        try:
            with Session(engine) as session:
                rows = session.scalars(select(SourcePipeline))
                associated = any(
                    (pending := row.summary.get("authoring_job", {})).get("workspace_id")
                    == data["workspace_id"]
                    and data["task_id"] in {pending.get("id"), pending.get("init_task_id")}
                    for row in rows
                )
                associated = associated or any(
                    row.context.get("workspace_id") == data["workspace_id"]
                    and row.context.get("task_id") == data["task_id"]
                    for row in session.scalars(select(RepairRequest))
                )
        finally:
            engine.dispose()
        if not associated:
            raise ApiError(409, "CALLBACK_NOT_YET_ASSOCIATED", "Retry after source registration")
        inbox.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(inbox) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, digest TEXT NOT NULL, "
                "task_id TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            existing = db.execute(
                "SELECT digest FROM events WHERE id=?", (data["event_id"],)
            ).fetchone()
            if existing and existing[0] != fingerprint:
                raise ApiError(409, "CALLBACK_CONFLICT", "Callback payload changed")
            db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?)",
                (data["event_id"], fingerprint, data["task_id"], json.dumps(data)),
            )
        return {"received": True, "event_id": data["event_id"]}

    app.register_blueprint(bp)

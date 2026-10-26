"""Opt-in internal JSON RPC transport; no Agent/runtime execution or autostart.

Exact endpoints (POST only):
    /internal/recipe/CreateWorkspace
    /internal/recipe/InputCompleted
    /internal/recipe/StartBuild
    /internal/recipe/StartRepair
    /internal/recipe/GetRecipeRevision
    /internal/recipe/ReviewRevision
    /internal/recipe/GetWorkspace
    /internal/recipe/GetTask
    /internal/recipe/GetTaskResult
    /internal/recipe/CancelTask

Server-only Flask configuration::

    RECIPE_RPC_ENABLED = True                 # False by default
    RECIPE_SERVICE_STORAGE = "/absolute/operator/assigned/blob-root"
    RECIPE_RPC_MAX_BODY_BYTES = 65536          # hard maximum: 1 MiB
    RECIPE_RPC_CALLERS = {
        "source-server": {
            "token": "operator-provided-request-secret",
            "source_ids": ["ustc-employment"],
            "agent_profile": {"system_prompt_version": "source-recipe-v2"},
            "callback_target": "source-server",  # optional; registered ID only
        },
    }
    RECIPE_CALLBACK_TARGETS = {
        "source-server": {"url": "http://127.0.0.1:9000/recipe-events",
                          "token": "operator-provided-callback-secret"},
    }

Every call requires ``X-Recipe-Caller`` and ``Authorization: Bearer <token>``.
The caller ID, not a body field, owns the workspace. Source allowlists and Agent
profiles come only from server configuration. No paths, URLs, owner or profile
fields are accepted from clients. Only managed workspaces are RPC-accessible.

JSON arguments:
    CreateWorkspace: source_id
    InputCompleted: workspace_id, revision, manifest_digest, request_id
    StartBuild: workspace_id, input_revision, prompt, request_id
    StartRepair: workspace_id, input_revision, base_recipe_digest, feedback, request_id,
                 [base_revision_id]
    GetRecipeRevision: revision_id
    ReviewRevision: workspace_id, revision_id, decision, feedback, request_id
    GetWorkspace: workspace_id
    GetTask: task_id, [after=0], [limit=100]
    GetTaskResult: task_id
    CancelTask: task_id

``decision`` is ``accept`` or ``reject``; ``feedback`` is a nonempty full-source
audit object supplied by the trusted workspace owner. Reviews never queue execution.
IDs are bounded strings; workspace IDs
are 64 lowercase hex characters, task IDs 32, content digests 64. Responses are
``{"result": ...}`` or ``{"error": {"code": ..., "message": ...}}``. Successful
calls return HTTP 200, including queued tasks; completion is asynchronous.
InputCompleted delegates the reading/freezing of prewritten ``input/manifest.json``
and ``input/payload.json`` to RecipeService. The manifest maps simple filenames to
SHA256 of their bytes; payload.json must contain the workspace source_id and a
nonempty objective. manifest_digest is SHA256 of UTF-8 canonical JSON (sorted keys,
ensure_ascii=False, separators=(",", ":"), allow_nan=False), NOT arbitrary on-disk
manifest whitespace. Its receipt includes input_revision_id and init_task_id.
StartBuild may return waiting_init; no model call occurs in these routes.
Errors use HTTP 400 for invalid arguments/service rejection, 401 for invalid auth,
403 for unapproved sources, 404 for inaccessible workspaces/tasks or unknown
methods, 409 for busy workspaces/known state conflicts, 413/415 for invalid bodies,
and a redacted 500. Only exact codes in _SERVICE_ERRORS are preserved.
Authentication is independent of transport security: operators must secure the
internal transport; this module never starts a listener or enables public CORS.
"""

import copy
import hmac
import json
import re
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import HTTPException

from .callbacks import load_callback_targets, valid_identifier, valid_token
from .lifecycle import profile_value
from .service import RecipeService, ServiceBusy, ServiceError

# Only exact host-selected codes may cross the transport boundary. Never match
# prefixes/substrings: an exception may append private file/provider details.
_SERVICE_ERRORS = {
    "WORKSPACE_PROFILE_MIGRATION_REQUIRED": 409,
    "RESULT_NOT_READY": 409,
    "INPUT_NOT_READY": 409,
    "INIT_FAILED": 409,
    "INIT_NOT_READY": 409,
    "REQUEST_CONFLICT": 409,
    "INPUT_REVISION_EXISTS": 409,
    "REINITIALIZATION_REQUIRED": 409,
    "STALE_RECIPE_FEEDBACK": 409,
    "MANIFEST_CHANGED": 409,
    "INPUT_CHANGED": 409,
    "INPUT_FILES_INVALID": 400,
    "FRESH_INPUT_REQUIRED": 409,
    "STALE_RECIPE_BASE": 409,
    "REVISION_ALREADY_REVIEWED": 409,
}


@dataclass(frozen=True)
class _Caller:
    id: str
    token: str = field(repr=False)
    source_ids: frozenset[str]
    agent_profile: dict
    callback_target: str | None
    source_authorizer: object = field(default=None, repr=False)


class _RPCError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


def _error(code, status):
    # No exception text, SQL parameters, filesystem paths or model errors.
    return jsonify(error={"code": code, "message": code.replace("_", " ").lower()}), status


def _callers(config, targets, source_authorizer=None):
    if source_authorizer is not None and not callable(source_authorizer):
        raise ValueError("Invalid recipe source authorizer")
    if not isinstance(config, Mapping) or not config:
        raise ValueError("Recipe RPC requires configured callers")
    callers = []
    for caller_id, entry in config.items():
        if (
            not valid_identifier(caller_id)
            or not isinstance(entry, Mapping)
            or not {"token", "source_ids", "agent_profile"} <= set(entry)
            or set(entry) - {"token", "source_ids", "agent_profile", "callback_target"}
            or not valid_token(entry["token"])
            or not isinstance(entry["source_ids"], list | tuple | set | frozenset)
            or (not entry["source_ids"] and source_authorizer is None)
            or not all(valid_identifier(source) for source in entry["source_ids"])
            or not isinstance(entry["agent_profile"], dict)
        ):
            raise ValueError("Invalid recipe RPC caller configuration")
        target = entry.get("callback_target")
        if target is not None and (not valid_identifier(target) or target not in targets):
            raise ValueError("Unknown configured recipe RPC callback target")
        try:
            profile = json.loads(json.dumps(entry["agent_profile"], allow_nan=False))
            profile_value(profile)
        except (TypeError, ValueError, RecursionError):
            raise ValueError("Invalid recipe RPC profile configuration") from None
        callers.append(
            _Caller(
                caller_id,
                entry["token"],
                frozenset(entry["source_ids"]),
                profile,
                target,
                source_authorizer,
            )
        )
    return tuple(callers)


def _authenticate(callers):
    caller_id = request.headers.get("X-Recipe-Caller", "")
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    well_formed = valid_identifier(caller_id) and valid_token(token)
    token_bytes = token.encode("utf-8") if len(token) <= 512 else b""
    caller_bytes = caller_id.encode("utf-8") if len(caller_id) <= 128 else b""
    match = None
    # Do not short-circuit on caller existence or token mismatch. Static tokens
    # are compared in constant time, including requests for unknown callers.
    for caller in callers:
        token_matches = hmac.compare_digest(token_bytes, caller.token.encode("utf-8"))
        id_matches = hmac.compare_digest(caller_bytes, caller.id.encode("utf-8"))
        if token_matches and id_matches and well_formed:
            match = caller
    if match is None:
        raise _RPCError("UNAUTHORIZED", 401)
    return match


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("Non-finite JSON number")


def _arguments(max_bytes):
    request.max_content_length = max_bytes
    if request.mimetype != "application/json":
        raise _RPCError("JSON_REQUIRED", 415)
    try:
        data = json.loads(
            request.get_data(cache=False).decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise _RPCError("INVALID_REQUEST") from None
    if not isinstance(data, dict):
        raise _RPCError("INVALID_REQUEST")
    return data


def _fields(args, required, optional=()):
    if set(args) - set(required) - set(optional) or not set(required) <= set(args):
        raise _RPCError("INVALID_ARGUMENTS")


def _identifier(value):
    if not valid_identifier(value):
        raise _RPCError("INVALID_ARGUMENTS")
    return value


def _hex(value, length=64):
    if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        raise _RPCError("INVALID_ARGUMENTS")
    return value


def _text(value, max_chars):
    if not isinstance(value, str) or not value.strip() or len(value) > max_chars or "\x00" in value:
        raise _RPCError("INVALID_ARGUMENTS")
    return value


def _source_allowed(caller, source_id):
    if source_id in caller.source_ids:
        return True
    return (
        caller.source_authorizer is not None
        and caller.source_authorizer(caller.id, source_id) is True
    )


def _workspace(service, workspace_id, caller):
    _hex(workspace_id)
    try:
        info = service.workspace_info(workspace_id)
    except ServiceError:
        raise _RPCError("NOT_FOUND", 404) from None
    if (
        info.get("owner_id") != caller.id
        or info.get("lifecycle") != "managed"
        or not _source_allowed(caller, info.get("source_id"))
    ):
        raise _RPCError("NOT_FOUND", 404)
    return info


def _task(service, task_id, caller, *, after=0, limit=100):
    _hex(task_id, 32)
    try:
        info = service.status(task_id, after=after, limit=limit)
    except ServiceError:
        raise _RPCError("NOT_FOUND", 404) from None
    _workspace(service, info["workspace_id"], caller)
    return info


def _revision(service, revision_id, caller):
    _hex(revision_id, 32)
    try:
        info = service.get_recipe_revision(revision_id)
    except ServiceError:
        raise _RPCError("NOT_FOUND", 404) from None
    _workspace(service, info["workspace_id"], caller)
    return info


def _dispatch(service, method, args, caller):
    if method == "CreateWorkspace":
        _fields(args, ("source_id",))
        source_id = _identifier(args["source_id"])
        if not _source_allowed(caller, source_id):
            raise _RPCError("SOURCE_NOT_ALLOWED", 403)
        info = service.create_workspace(
            source_id,
            owner_id=caller.id,
            agent_profile=copy.deepcopy(caller.agent_profile),
            lifecycle="managed",
        )
        return _workspace(service, info["workspace_id"], caller)
    if method == "GetWorkspace":
        _fields(args, ("workspace_id",))
        return _workspace(service, args["workspace_id"], caller)
    if method == "GetTask":
        _fields(args, ("task_id",), ("after", "limit"))
        after, limit = args.get("after", 0), args.get("limit", 100)
        if type(after) is not int or not 0 <= after <= 2**63 - 1:
            raise _RPCError("INVALID_ARGUMENTS")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise _RPCError("INVALID_ARGUMENTS")
        return _task(service, args["task_id"], caller, after=after, limit=limit)
    if method == "GetTaskResult":
        _fields(args, ("task_id",))
        info = _task(service, args["task_id"], caller)
        return service.result(info["id"])
    if method == "GetRecipeRevision":
        _fields(args, ("revision_id",))
        return _revision(service, args["revision_id"], caller)
    if method == "ReviewRevision":
        _fields(args, ("workspace_id", "revision_id", "decision", "feedback", "request_id"))
        _workspace(service, args["workspace_id"], caller)
        revision = _revision(service, args["revision_id"], caller)
        if revision["workspace_id"] != args["workspace_id"]:
            raise _RPCError("NOT_FOUND", 404)
        _identifier(args["request_id"])
        if args["decision"] not in ("accept", "reject") or not isinstance(args["feedback"], dict):
            raise _RPCError("INVALID_ARGUMENTS")
        return service.review_revision(**args)
    if method == "CancelTask":
        _fields(args, ("task_id",))
        info = _task(service, args["task_id"], caller)
        return service.cancel(info["id"])
    if method == "InputCompleted":
        _fields(args, ("workspace_id", "revision", "manifest_digest", "request_id"))
        _identifier(args["revision"])
        _hex(args["manifest_digest"])
        operation = service.input_completed
        kwargs = {key: args[key] for key in ("revision", "manifest_digest")}
    elif method == "StartBuild":
        _fields(args, ("workspace_id", "input_revision", "prompt", "request_id"))
        _identifier(args["input_revision"])
        _text(args["prompt"], 32000)
        operation = service.start_build
        kwargs = {key: args[key] for key in ("input_revision", "prompt")}
    elif method == "StartRepair":
        _fields(
            args,
            (
                "workspace_id",
                "input_revision",
                "base_recipe_digest",
                "feedback",
                "request_id",
            ),
            ("base_revision_id",),
        )
        if "base_revision_id" in args:
            _hex(args["base_revision_id"], 32)
        _identifier(args["input_revision"])
        _hex(args["base_recipe_digest"])
        if not isinstance(args["feedback"], dict):
            raise _RPCError("INVALID_ARGUMENTS")
        operation = service.start_repair
        kwargs = {
            key: args[key]
            for key in (
                "input_revision",
                "base_recipe_digest",
                "feedback",
            )
        }
        if "base_revision_id" in args:
            kwargs["base_revision_id"] = args["base_revision_id"]
    else:
        raise _RPCError("METHOD_NOT_FOUND", 404)
    _identifier(args["request_id"])
    _workspace(service, args["workspace_id"], caller)
    return operation(
        args["workspace_id"],
        **kwargs,
        request_id=args["request_id"],
        callback_target=caller.callback_target,
    )


@contextmanager
def _service(storage):
    service = RecipeService(storage)
    try:
        yield service
    finally:
        service.close()


def register_recipe_rpc(app):
    """Register only when explicitly enabled; never opens storage during startup."""
    app.config.setdefault("RECIPE_RPC_ENABLED", False)
    app.config.setdefault("RECIPE_RPC_MAX_BODY_BYTES", 64 * 1024)
    if app.config["RECIPE_RPC_ENABLED"] is False:
        return
    if app.config["RECIPE_RPC_ENABLED"] is not True:
        raise ValueError("RECIPE_RPC_ENABLED must be a boolean")
    storage = app.config.get("RECIPE_SERVICE_STORAGE")
    if not isinstance(storage, str | Path) or not Path(storage).is_absolute():
        raise ValueError("Recipe RPC requires an absolute configured storage path")
    max_bytes = app.config["RECIPE_RPC_MAX_BODY_BYTES"]
    if type(max_bytes) is not int or not 1 <= max_bytes <= 1024 * 1024:
        raise ValueError("Invalid recipe RPC body limit")
    targets = load_callback_targets(app.config.get("RECIPE_CALLBACK_TARGETS", {}))
    callers = _callers(
        app.config.get("RECIPE_RPC_CALLERS"),
        targets,
        app.config.get("RECIPE_RPC_SOURCE_AUTHORIZER"),
    )
    blueprint = Blueprint("recipe_rpc", __name__)

    @blueprint.post("/internal/recipe/<method>", provide_automatic_options=False)
    def invoke(method):
        try:
            caller = _authenticate(callers)
            args = _arguments(max_bytes)
            with _service(storage) as service:
                result = _dispatch(service, method, args, caller)
                return jsonify(result=result)
        except _RPCError as error:
            return _error(error.code, error.status)
        except ServiceBusy:
            return _error("SERVICE_BUSY", 409)
        except ServiceError as error:
            code = str(error)
            if code in _SERVICE_ERRORS:
                return _error(code, _SERVICE_ERRORS[code])
            return _error("REQUEST_REJECTED", 400)
        except HTTPException as error:
            return _error("BODY_TOO_LARGE" if error.code == 413 else "INVALID_REQUEST", error.code)
        except Exception:
            return _error("INTERNAL_ERROR", 500)

    app.register_blueprint(blueprint)

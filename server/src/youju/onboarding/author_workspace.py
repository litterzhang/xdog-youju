"""Persistent source authoring files with official tools isolated by the host.

Each tool call starts in /work. Only its files persist, not shell state or /tmp.
Input and append-only validation/run/audit history are read-only inside the child.
Host I/O uses pinned directory descriptors and rejects symlinks/special files.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import stat
import uuid
from dataclasses import replace
from pathlib import Path

from xdog.agent import AgentTool, AgentToolResult
from xdog.ai import TextContent

from .sandbox import Sandbox, SandboxError

MAX_PAYLOAD = 8 * 1024 * 1024
MAX_GUIDE = 512 * 1024
MAX_RECORD = 1024 * 1024
MAX_TOOL = 256 * 1024
FILE_LIMITS = {
    "workflow.json": 256 * 1024,
    "parser.py": 128 * 1024,
    "undated_parser.py": 128 * 1024,
    "config.json": 256 * 1024,
}
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class AuthorWorkspaceError(ValueError):
    """Unsafe, missing, corrupt, or concurrently edited authoring state."""


async def _await_host_io(task):
    """Join our own thread before propagating cancellation, including repeated cancels.

    Cancelling a to_thread waiter cannot stop its thread or release its flock.
    Shield the owned task, then settle it before any caller cleanup may access the
    workspace. This does not wait/retry a lock held by an unrelated owner.
    """
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        # Retrieve a late thread failure without replacing the caller's cancellation
        # (or the earlier persistence failure that caused Agent shutdown).
        with contextlib.suppress(Exception, asyncio.CancelledError):
            task.result()
        raise


def _json(value, limit):
    try:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AuthorWorkspaceError("invalid_author_json") from exc
    if len(body) > limit:
        raise AuthorWorkspaceError("author_file_size_limit")
    return body


def _text(value, limit):
    if not isinstance(value, str):
        raise AuthorWorkspaceError("author_text_required")
    try:
        body = value.encode("utf-8")
    except UnicodeError as exc:
        raise AuthorWorkspaceError("invalid_author_text") from exc
    if len(body) > limit:
        raise AuthorWorkspaceError("author_file_size_limit")
    return body


def _child_dir(parent, name):
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    return os.open(name, _DIRECTORY, dir_fd=parent)


def _root_dir(path):
    descriptor = os.open("/", _DIRECTORY)
    try:
        for name in path.parts[1:]:
            if name in {".", ".."}:
                raise AuthorWorkspaceError("unsafe_author_root")
            child = _child_dir(descriptor, name)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _regular(descriptor):
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise AuthorWorkspaceError("author_file_must_be_single_link_regular")
    return info


def _read(parent, name, limit, *, missing=False):
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        if missing:
            return None
        raise AuthorWorkspaceError(f"missing_author_file:{name}") from None
    try:
        info = _regular(descriptor)
        if info.st_size > limit:
            raise AuthorWorkspaceError(f"author_file_size_limit:{name}")
        data = bytearray()
        while chunk := os.read(descriptor, min(65536, limit + 1 - len(data))):
            data.extend(chunk)
            if len(data) > limit:
                raise AuthorWorkspaceError(f"author_file_size_limit:{name}")
        return bytes(data)
    finally:
        os.close(descriptor)


def _write(parent, name, body, *, immutable=False):
    """Never follow a planted target or write through an existing hard link."""
    if immutable:
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        temporary = None
    else:
        # Validate existing targets without opening FIFOs or following symlinks.
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise AuthorWorkspaceError(f"unsafe_author_file:{name}")
        temporary = f".host-{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary is not None:
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if temporary is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent)


def _decode(body):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise AuthorWorkspaceError("invalid_author_json_file") from exc
    if not isinstance(value, dict):
        raise AuthorWorkspaceError("author_json_object_required")
    return value


class AuthorWorkspace:
    def __init__(self, root, sandbox=None):
        self.root = Path(root).absolute()
        if self.root == Path("/") or ".." in self.root.parts:
            raise AuthorWorkspaceError("unsafe_author_root")
        self.sandbox = sandbox if sandbox is not None else Sandbox()
        self._host_io_tasks = set()
        with self._directories():
            pass

    async def run_io(self, operation, /, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        self._host_io_tasks.add(task)
        try:
            return await _await_host_io(task)
        finally:
            self._host_io_tasks.discard(task)

    async def wait_io(self):
        """Drain only this workspace object's owned I/O, never an external flock.

        An interrupted Agent.aclose may leave tool cleanup running in its own
        task. Final run persistence must still wait for those host threads.
        """
        if self._host_io_tasks:
            results = await _await_host_io(
                asyncio.gather(*tuple(self._host_io_tasks), return_exceptions=True)
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    @property
    def workspace(self):
        return self.root / "workspace"

    @property
    def input(self):
        return self.root / "input"

    @property
    def history(self):
        return self.root / "history"

    @contextlib.contextmanager
    def _directories(self):
        descriptors = []
        try:
            root = _root_dir(self.root)
            descriptors.append(root)
            lock = os.open(
                ".host.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=root,
            )
            descriptors.append(lock)
            _regular(lock)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AuthorWorkspaceError("author_workspace_busy") from exc
            directories = {}
            for name in ("workspace", "input", "history"):
                descriptor = _child_dir(root, name)
                descriptors.append(descriptor)
                directories[name] = descriptor
            yield directories
        except OSError as exc:
            raise AuthorWorkspaceError(f"unsafe_author_workspace_io:{exc.errno}") from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def _candidate_files(candidate):
        if not isinstance(candidate, dict) or set(candidate) != {
            "workflow",
            "parser_py",
            "undated_parser_py",
            "config",
        }:
            raise AuthorWorkspaceError("invalid_author_candidate_fields")
        if not isinstance(candidate["workflow"], dict) or not isinstance(candidate["config"], dict):
            raise AuthorWorkspaceError("author_candidate_objects_required")
        return {
            "workflow.json": _json(candidate["workflow"], FILE_LIMITS["workflow.json"]),
            "parser.py": _text(candidate["parser_py"], FILE_LIMITS["parser.py"]),
            "undated_parser.py": _text(
                candidate["undated_parser_py"], FILE_LIMITS["undated_parser.py"]
            ),
            "config.json": _json(candidate["config"], FILE_LIMITS["config.json"]),
        }

    def prepare(self, payload: dict, *, skill_text: str, guide_text: str, template: dict):
        if not isinstance(payload, dict):
            raise AuthorWorkspaceError("author_payload_object_required")
        seed = self._candidate_files(template)
        inputs = {
            "payload.json": _json(payload, MAX_PAYLOAD),
            "SKILL.md": _text(skill_text, MAX_GUIDE),
            "guide.md": _text(guide_text, MAX_GUIDE),
            "template.json": _json(template, MAX_RECORD),
            "README.md": (
                b"# Isolated Source Authoring\n\n"
                b"Every tool call starts in /work; shell cwd and /tmp do not persist.\n"
                b"Work files persist. /input and /history are read-only.\n"
                b"Read /history/status.json for the latest validation, run and audit.\n"
                b"Per-tool OS limits apply; official descriptions do not override them.\n"
                b"Provider requests and accepted-result validation belong to the host.\n"
            ),
        }
        with self._directories() as directories:
            for name, body in inputs.items():
                _write(directories["input"], name, body)
            for name, body in seed.items():
                existing = _read(directories["workspace"], name, FILE_LIMITS[name], missing=True)
                if existing is None:
                    _write(directories["workspace"], name, body, immutable=True)
            if _read(directories["history"], "status.json", MAX_RECORD, missing=True) is None:
                _write(
                    directories["history"],
                    "status.json",
                    _json(
                        {
                            "status": "prepared",
                            "errors": [],
                            "latest_validation": None,
                            "latest_run": None,
                            "latest_audit": None,
                        },
                        MAX_RECORD,
                    ),
                    immutable=True,
                )
        return self.provenance()

    def stage_manifest(self, *, source_id, objective):
        """Add trusted identity and commit a bounded input manifest through safe host I/O."""
        from youju.serialization import digest, json_bytes

        if (
            not isinstance(source_id, str)
            or not source_id.strip()
            or not isinstance(objective, str)
            or not objective.strip()
        ):
            raise AuthorWorkspaceError("invalid_source_identity")
        with self._directories() as directories:
            payload = json.loads(_read(directories["input"], "payload.json", MAX_PAYLOAD))
            payload.update(source_id=source_id, objective=objective)
            _write(directories["input"], "payload.json", json_bytes(payload))
            manifest = {
                name: hashlib.sha256(_read(directories["input"], name, MAX_PAYLOAD)).hexdigest()
                for name in ("payload.json", "SKILL.md", "guide.md", "template.json", "README.md")
            }
            _write(directories["input"], "manifest.json", json_bytes(manifest))
            return digest(manifest)

    def load_input(self):
        """Read host-prepared input without following links or arbitrary file paths."""
        with self._directories() as directories:
            payload = _decode(_read(directories["input"], "payload.json", MAX_PAYLOAD))
            skill = _read(directories["input"], "SKILL.md", MAX_GUIDE).decode("utf-8")
            guide = _read(directories["input"], "guide.md", MAX_GUIDE).decode("utf-8")
        return {"payload": payload, "skill_text": skill, "guide_text": guide}

    def write_candidate(self, candidate):
        """Explicit trusted-host/stub boundary, never a tool exposed to the model."""
        files = self._candidate_files(candidate)
        with self._directories() as directories:
            for name, body in files.items():
                _write(directories["workspace"], name, body)

    def load_candidate(self) -> dict:
        with self._directories() as directories:
            files = {
                name: _read(directories["workspace"], name, limit)
                for name, limit in FILE_LIMITS.items()
            }
        try:
            return {
                "workflow": _decode(files["workflow.json"]),
                "parser_py": files["parser.py"].decode("utf-8"),
                "undated_parser_py": files["undated_parser.py"].decode("utf-8"),
                "config": _decode(files["config.json"]),
            }
        except UnicodeError as exc:
            raise AuthorWorkspaceError("invalid_author_candidate_encoding") from exc

    def _record(self, collection, value):
        if not isinstance(value, dict):
            raise AuthorWorkspaceError("author_record_object_required")
        body = _json(value, MAX_RECORD)
        if collection == "attempts" and not isinstance(value.get("errors", []), list):
            raise AuthorWorkspaceError("author_validation_errors_list_required")
        with self._directories() as directories:
            history = directories["history"]
            records = _child_dir(history, collection)
            try:
                # No fixed repair/run cap. Exclusive creation and a host lock
                # preserve earlier attempts even after interrupted status writes.
                numbers = [
                    int(name[:-5])
                    for name in os.listdir(records)
                    if name.endswith(".json") and name[:-5].isdigit()
                ]
                number = max(numbers, default=0) + 1
                status_bytes = _read(history, "status.json", MAX_RECORD, missing=True)
                status = (
                    _decode(status_bytes)
                    if status_bytes is not None
                    else {
                        "errors": [],
                        "latest_validation": None,
                        "latest_run": None,
                        "latest_audit": None,
                    }
                )
                reference = {
                    "sequence": number,
                    "path": f"/history/{collection}/{number}.json",
                    "status": value.get("status", "unknown"),
                }
                if collection == "attempts":
                    reference["errors"] = value.get("errors", [])
                    status["latest_validation"] = reference
                    status["errors"] = reference["errors"]
                elif collection == "audits":
                    status["latest_audit"] = reference
                else:
                    status["latest_run"] = reference
                status["status"] = reference["status"]
                status_body = _json(status, MAX_RECORD)
                _write(records, f"{number}.json", body, immutable=True)
                _write(history, "status.json", status_body)
                return status
            finally:
                os.close(records)

    def record_attempt(self, attempt: dict):
        return self._record("attempts", attempt)

    def validation_progress(self, scope, value=None):
        """Task-input-scoped host state; no global author turn/token cap."""
        if (
            not isinstance(scope, str)
            or len(scope) != 64
            or any(c not in "0123456789abcdef" for c in scope)
        ):
            raise AuthorWorkspaceError("invalid_progress_scope")
        with self._directories() as directories:
            records = _child_dir(directories["history"], "validation-progress")
            try:
                if value is not None:
                    _write(records, scope + ".json", _json(value, MAX_RECORD))
                    return value
                body = _read(records, scope + ".json", MAX_RECORD, missing=True)
                return json.loads(body) if body is not None else None
            finally:
                os.close(records)

    def record_failure(self, value):
        """Persist bounded original rejected data outside small attempt summaries."""
        from youju.serialization import json_bytes

        from .diagnostics import MAX_FAILURE_BYTES

        body = json_bytes(value)
        if len(body) > MAX_FAILURE_BYTES:
            raise AuthorWorkspaceError("rejected_output_size_limit")
        reference = hashlib.sha256(body).hexdigest()
        with self._directories() as directories:
            records = _child_dir(directories["history"], "rejected")
            try:
                old = _read(records, reference + ".json", MAX_FAILURE_BYTES, missing=True)
                if old is None:
                    _write(records, reference + ".json", body, immutable=True)
                elif old != body:
                    raise AuthorWorkspaceError("rejected_output_changed")
            finally:
                os.close(records)
        return reference

    def load_failure(self, reference):
        from .diagnostics import MAX_FAILURE_BYTES

        if (
            not isinstance(reference, str)
            or len(reference) != 64
            or any(c not in "0123456789abcdef" for c in reference)
        ):
            raise AuthorWorkspaceError("invalid_failure_reference")
        with self._directories() as directories:
            records = _child_dir(directories["history"], "rejected")
            try:
                body = _read(records, reference + ".json", MAX_FAILURE_BYTES)
                if hashlib.sha256(body).hexdigest() != reference:
                    raise AuthorWorkspaceError("rejected_output_digest_changed")
                return json.loads(body)
            finally:
                os.close(records)

    def record_test(self, receipt):
        """Immutable task/checker-bound receipts, available read-only after restore."""
        test_id = receipt.get("test_id", "")
        if (
            not isinstance(test_id, str)
            or len(test_id) != 64
            or any(char not in "0123456789abcdef" for char in test_id)
        ):
            raise AuthorWorkspaceError("invalid_test_identity")
        body = _json(receipt, MAX_RECORD)
        with self._directories() as directories:
            records = _child_dir(directories["history"], "tests")
            try:
                name = test_id + ".json"
                old = _read(records, name, MAX_RECORD, missing=True)
                if old is None:
                    _write(records, name, body, immutable=True)
                elif old != body:
                    raise AuthorWorkspaceError("test_receipt_changed")
            finally:
                os.close(records)

    def load_test(self, test_id):
        if (
            not isinstance(test_id, str)
            or len(test_id) != 64
            or any(char not in "0123456789abcdef" for char in test_id)
        ):
            raise AuthorWorkspaceError("invalid_test_identity")
        with self._directories() as directories:
            records = _child_dir(directories["history"], "tests")
            try:
                body = _read(records, test_id + ".json", MAX_RECORD, missing=True)
                return json.loads(body) if body is not None else None
            finally:
                os.close(records)

    def record_run(self, status: dict):
        return self._record("runs", status)

    def record_audit(self, feedback: dict):
        """Append host-supplied, secret-free audit metadata without truncation."""
        return self._record("audits", feedback)

    def provenance(self):
        """Only namespace paths and boundary configuration, never source/provider data."""
        return {
            "workspace": "/work",
            "input": "/input",
            "history": "/history",
            "cwd_persists": False,
            "tools": ["bash", "filesystem"],
            "per_tool_timeout_seconds": getattr(self.sandbox, "timeout", None),
        }

    async def _execute(self, name, tool_call_id, args, cancel=None):
        if not isinstance(args, dict):
            raise AuthorWorkspaceError("author_tool_args_object_required")
        _json(args, MAX_TOOL)
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        with self._directories() as directories:
            task = asyncio.create_task(
                self.sandbox.run_author_tool(
                    name,
                    args,
                    tool_call_id=tool_call_id,
                    workspace_fd=directories["workspace"],
                    input_fd=directories["input"],
                    history_fd=directories["history"],
                )
            )
            cancellation = asyncio.create_task(cancel.wait()) if cancel is not None else None
            try:
                if cancellation is not None:
                    done, _ = await asyncio.wait(
                        {task, cancellation}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if cancellation in done:
                        raise asyncio.CancelledError
                value = await task
            finally:
                if not task.done():
                    task.cancel()
                if cancellation is not None:
                    cancellation.cancel()
                await asyncio.gather(
                    task, *([cancellation] if cancellation else []), return_exceptions=True
                )
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("texts"), list)
            or any(not isinstance(text, str) for text in value["texts"])
        ):
            raise AuthorWorkspaceError("invalid_author_tool_text_result")
        _json(value, MAX_TOOL)
        return AgentToolResult(
            content=tuple(TextContent(text=text) for text in value["texts"]),
            details=value.get("details"),
        )

    def isolate_tools(self, definitions) -> list[AgentTool]:
        """Preserve official schemas; execute only via the pinned child mounts."""

        def execute_for(name):
            async def execute(tool_call_id, args, cancel=None, on_update=None, **kwargs):
                try:
                    return await self._execute(name, tool_call_id, args, cancel)
                except TimeoutError:
                    return AgentToolResult(
                        content=(TextContent(text="Error: author_tool_timeout"),)
                    )
                except (AuthorWorkspaceError, SandboxError) as exc:
                    return AgentToolResult(content=(TextContent(text=f"Error: {exc}"),))

            return execute

        return [replace(tool, execute=execute_for(tool.name)) for tool in definitions]

    def tools(self) -> list[AgentTool]:
        from xdog.agent.tools import create_bash_tool, create_filesystem_tool

        return self.isolate_tools(
            (create_bash_tool(initial_cwd=Path("/work")), create_filesystem_tool())
        )

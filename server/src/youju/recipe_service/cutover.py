"""Explicit fresh-session cutover from an OFFLINE copy, never native snapshot conversion.

Old files/conversations remain opaque untrusted history. No candidate or checkpoint
is activated, no old callbacks/jobs are queued, and the original store is read-only.
Use the existing InputCompleted/init/build lifecycle after this preparation.
"""

import fcntl
import hashlib
import os
import re
import sqlite3
import stat
from contextlib import ExitStack
from pathlib import Path

from youju.onboarding.author_workspace import _child_dir, _read, _regular, _write
from youju.serialization import digest, json_bytes

from .errors import ServiceError
from .models import Workspace

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILES = 10000
MAX_ROWS = 100000
_SUBDIRS = ("workspace", "input", "history", ".task-inputs", ".session-archive")
_BLOB_COLUMNS = {
    "checkpoint_ref",
    "recovery_ref",
    "result_ref",
    "prompt_ref",
    "input_digest",
    "digest",
    "artifact_ref",
    "audit_ref",
}
_TERMINAL = {
    "succeeded",
    "candidate_ready",
    "blocked",
    "failed",
    "cancelled",
    "interrupted",
    "superseded",
}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_root(path):
    """Read existing directories without creating them or following a symlink component."""
    fd = os.open("/", _DIR)
    try:
        for part in Path(path).absolute().parts[1:]:
            if part in {".", ".."}:
                raise ValueError("Unsafe snapshot path")
            child = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _tree(fd, prefix=""):
    if prefix.count("/") > 100:
        raise ValueError("Context directory nesting exceeds its bound")
    for name in sorted(os.listdir(fd)):
        if name in {".", ".."}:
            raise ValueError("Unsafe context filename")
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        path = prefix + name
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, _DIR, dir_fd=fd)
            try:
                yield from _tree(child, path + "/")
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            body = _read(fd, name, MAX_FILE_BYTES)
            after = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (info.st_ino, info.st_size, info.st_mtime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError("Offline context changed while exporting")
            yield path, body
        else:
            raise ValueError("Context archives reject symlinks, special files and hard links")


def _rows(database, workspace_id):
    tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"workspaces", "jobs"} <= tables:
        raise ValueError("Offline snapshot has no supported workspace metadata")
    database.row_factory = sqlite3.Row
    workspace = database.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    if workspace is None:
        raise ValueError("Source is not registered in the offline snapshot")
    output = {"workspaces": [dict(workspace)]}
    for table in ("jobs", "input_revisions", "recipe_revisions", "revision_reviews"):
        if table in tables:
            values = database.execute(
                f"SELECT * FROM {table} WHERE workspace_id=? LIMIT ?", (workspace_id, MAX_ROWS + 1)
            ).fetchall()
            if len(values) > MAX_ROWS:
                raise ValueError("Context metadata exceeds row limit")
            output[table] = [dict(row) for row in values]
    if any(job["status"] not in _TERMINAL for job in output["jobs"]):
        raise ValueError("Drain/cancel/recover pending snapshot jobs explicitly before cutover")
    for table in ("job_events", "callback_outbox"):
        if table in tables:
            values = database.execute(
                f"SELECT * FROM {table} WHERE job_id IN "
                "(SELECT id FROM jobs WHERE workspace_id=?) LIMIT ?",
                (workspace_id, MAX_ROWS + 1),
            ).fetchall()
            if len(values) > MAX_ROWS:
                raise ValueError("Context event history exceeds row limit")
            output[table] = [dict(row) for row in values]
    return output


def prepare_fresh_session(
    service,
    source_id,
    *,
    snapshot_storage,
    owner_id="local",
    agent_profile=None,
    confirm_offline=False,
):
    if confirm_offline is not True:
        raise ValueError("Explicit confirmation of an offline snapshot is required")
    old_storage = Path(snapshot_storage).absolute()
    new_storage = service.storage.absolute()
    if (
        old_storage == new_storage
        or old_storage in new_storage.parents
        or new_storage in old_storage.parents
    ):
        raise ValueError("Cutover needs disjoint old/new storage roots")
    key = digest({"source_id": source_id})
    old_root = old_storage / "authoring"
    # Pin existing snapshot roots; no RecipeService constructor/migration touches old metadata.
    with ExitStack() as stack:
        root_fd = _open_root(old_root)
        stack.callback(os.close, root_fd)
        work_fd = os.open(key, _DIR, dir_fd=root_fd)
        stack.callback(os.close, work_fd)
        for name in (".assignment.lock", ".host.lock"):
            try:
                lock = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=work_fd)
            except FileNotFoundError:
                continue
            stack.callback(os.close, lock)
            _regular(lock)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Snapshot workspace still has a live owner") from exc
        # Reject DB links before SQLite opens its own file handle.
        db_fd = os.open(
            "service.sqlite3", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd
        )
        stack.callback(os.close, db_fd)
        _regular(db_fd)
        database = sqlite3.connect((old_root / "service.sqlite3").as_uri() + "?mode=ro", uri=True)
        stack.callback(database.close)
        database.execute("PRAGMA query_only=ON")
        database.execute("BEGIN")
        rows = _rows(database, key)
        old = rows["workspaces"][0]
        if old.get("source_id") != source_id or old.get("owner_id", "local") != owner_id:
            raise ValueError("Snapshot source/owner identity mismatch")
        objects = os.open("objects", _DIR, dir_fd=root_fd)
        stack.callback(os.close, objects)
        info = service.create_workspace(source_id, owner_id=owner_id, agent_profile=agent_profile)
        with service.assignment(key):
            info = service.workspace_info(key)  # Recheck after acquiring the whole-task fence.
            if (
                info["status"] != "waiting_input"
                or info["session_revision"]
                or info["checkpoint_ref"]
            ):
                raise ValueError("Fresh-session context must precede InputCompleted/init")
            workspace = service.workspace(key)
            files, total = {}, 0
            with workspace._directories() as dirs:
                archive = _child_dir(dirs["history"], "prior-context")
                try:

                    def save(name, body):
                        nonlocal total
                        total += len(body)
                        if (
                            len(body) > MAX_FILE_BYTES
                            or total > MAX_TOTAL_BYTES
                            or len(files) >= MAX_FILES
                        ):
                            raise ValueError(
                                "Prior-context archive exceeds its explicit resource limit"
                            )
                        reference = hashlib.sha256(body).hexdigest()
                        target = reference + ".data"
                        present = _read(archive, target, MAX_FILE_BYTES, missing=True)
                        if present is None:
                            _write(archive, target, body, immutable=True)
                        elif present != body:
                            raise ValueError("Prior-context artifact changed")
                        files[name] = {"sha256": reference, "bytes": len(body), "file": target}

                    save("metadata/source-history.json", json_bytes(rows))
                    for directory in _SUBDIRS:
                        try:
                            fd = os.open(directory, _DIR, dir_fd=work_fd)
                        except FileNotFoundError:
                            continue
                        try:
                            for relative, body in _tree(fd):
                                save(directory + "/" + relative, body)
                        finally:
                            os.close(fd)
                    references = {
                        value
                        for table_rows in rows.values()
                        for row in table_rows
                        for column, value in row.items()
                        if column in _BLOB_COLUMNS
                        and isinstance(value, str)
                        and _HEX.fullmatch(value)
                    }
                    missing = []
                    for reference in sorted(references):
                        body = _read(objects, reference, MAX_FILE_BYTES, missing=True)
                        if body is None:
                            if reference == old.get("checkpoint_ref"):
                                raise ValueError(
                                    "Previous native checkpoint is missing; "
                                    "cannot claim preserved conversation"
                                )
                            missing.append(reference)
                            continue
                        if hashlib.sha256(body).hexdigest() != reference:
                            raise ValueError("Snapshot object checksum mismatch")
                        save("objects/" + reference, body)
                    manifest = {
                        "format": "youju-prior-context-v1",
                        "source_id": source_id,
                        "owner_id": owner_id,
                        "previous_workspace_id": key,
                        "old_schema_version": database.execute("PRAGMA user_version").fetchone()[0],
                        "checkpoint_ref": old.get("checkpoint_ref"),
                        "old_session_revision": old.get("session_revision", 0),
                        "files": files,
                        "bytes": total,
                        "unavailable_optional_objects": missing,
                        "untrusted": True,
                        "native_restore_attempted": False,
                        "new_profile_revision": info["profile_revision"],
                    }
                    ref = service.blobs.put(json_bytes(manifest))
                    body = json_bytes({**manifest, "archive_ref": ref})
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError("Prior-context index exceeds byte limit")
                    present = _read(archive, "index.json", 2 * 1024 * 1024, missing=True)
                    if present is None:
                        _write(archive, "index.json", body, immutable=True)
                    elif present != body:
                        raise ValueError("A different prior context is already pinned")
                finally:
                    os.close(archive)
            with service._transaction() as session:
                current = session.get(Workspace, key)
                if (
                    current.status != "waiting_input"
                    or current.checkpoint_ref
                    or current.session_revision
                ):
                    raise ServiceError("Workspace changed during fresh-session preparation")
                if current.prior_context_ref not in (None, ref):
                    raise ValueError("Prior context binding is immutable")
                current.prior_context_ref = ref
            return {
                "workspace_id": key,
                "prior_context_ref": ref,
                "physical_path": info["physical_path"],
                "status": "waiting_input",
                "preserved_files": len(files),
                "preserved_bytes": total,
                "checkpoint_restored": False,
                "agent_started": False,
                "published": False,
                "next_action": "Prepare current input, InputCompleted, then fresh native init",
            }

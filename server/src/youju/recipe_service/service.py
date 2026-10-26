"""Workspace/job control plane using short transactions and source-scoped OS locks.

This is a same-host service boundary, not an unauthenticated public HTTP API.
The server prepares input in the returned directory; workers never open its DB.
"""

import contextlib
import fcntl
import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.database import create_sqlite_engine
from youju.models.base import utc_now
from youju.onboarding.author_workspace import AuthorWorkspace, _regular, _root_dir
from youju.poc.storage import BlobStore
from youju.serialization import digest, json_bytes

from .errors import ServiceBusy, ServiceError
from .lifecycle import (
    MANAGED_KINDS,
    MAX_CHECKPOINT,
    ManagedLifecycle,
    bounded_json,
    identifier,
    profile_value,
)
from .migrations import migrate
from .models import Event, InputRevision, Job, Workspace

ACTIVE = {"queued", "waiting_init", "running"}
TERMINAL = {"succeeded", "candidate_ready", "blocked", "failed", "cancelled", "interrupted"}


def _stamp():
    return utc_now().isoformat()


class RecipeService(ManagedLifecycle):
    def __init__(self, storage):
        # Keep pre-service authoring paths so existing source work/history survives.
        self.storage = Path(storage).absolute()
        root = _root_dir(self.storage / "authoring")
        os.close(root)
        self.root = self.storage / "authoring"
        self.blobs = BlobStore(self.root / "objects")
        self.engine = create_sqlite_engine(self.root / "service.sqlite3")
        try:
            with self._write_lock():
                migrate(self.engine, self.root)
        except BaseException:
            self.engine.dispose()
            raise

    @contextlib.contextmanager
    def _write_lock(self):
        # Serialize ONLY short metadata writes, not Agent or validation execution.
        root = _root_dir(self.root)
        lock = None
        try:
            lock = os.open(
                ".metadata.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=root,
            )
            _regular(lock)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
        finally:
            if lock is not None:
                os.close(lock)
            os.close(root)

    @contextlib.contextmanager
    def _transaction(self):
        with self._write_lock(), Session(self.engine) as session, session.begin():
            yield session

    def close(self):
        self.engine.dispose()

    def create_workspace(
        self, source_id, *, owner_id="local", agent_profile=None, lifecycle="managed"
    ):
        """Register only. InputCompleted, not creation, schedules managed initialization."""
        identifier(source_id, "source identifier")
        identifier(owner_id, "owner identifier")
        if lifecycle != "managed":
            raise ServiceError("Invalid workspace lifecycle")
        profile = profile_value(agent_profile)
        key = digest({"source_id": source_id})

        def existing(row):
            if row.owner_id != owner_id:
                raise ServiceError("Workspace belongs to a different caller")
            if row.lifecycle != lifecycle or row.agent_profile != profile:
                raise ServiceError("WORKSPACE_PROFILE_MIGRATION_REQUIRED")

        with Session(self.engine) as session:
            row = session.get(Workspace, key)
            if row is not None:
                existing(row)
                return self.workspace_info(key)
        with self._assignment(key):
            with self._transaction() as session:
                row = session.get(Workspace, key)
                if row is None:
                    workspace = AuthorWorkspace(self.root / key)
                    session.add(
                        Workspace(
                            id=key,
                            source_id=source_id,
                            physical_path=str(workspace.root),
                            owner_id=owner_id,
                            lifecycle=lifecycle,
                            status="waiting_input",
                            agent_profile=profile,
                            profile_revision=digest(profile),
                            created_at=_stamp(),
                            updated_at=_stamp(),
                        )
                    )
                else:
                    existing(row)
        return self.workspace_info(key)

    def workspace_info(self, workspace_id):
        with Session(self.engine) as session:
            row = session.get(Workspace, workspace_id)
            if row is None:
                raise ServiceError("Unknown workspace")
            root = Path(row.physical_path)
            if root != self.root / row.id:
                raise ServiceError("Stored workspace path needs an explicit relocation migration")
            return {
                "workspace_id": row.id,
                "source_id": row.source_id,
                "owner_id": row.owner_id,
                "lifecycle": row.lifecycle,
                "status": row.status,
                "physical_path": str(root),
                "directory": str(root),
                "work_dir": str(root / "workspace"),
                "input_dir": str(root / "input"),
                "history_dir": str(root / "history"),
                "agent_profile": row.agent_profile,
                "profile_revision": row.profile_revision,
                "init_task_id": row.init_task_id,
                "checkpoint_ref": row.checkpoint_ref,
                "prior_context_ref": row.prior_context_ref,
                "session_revision": row.session_revision,
                "latest_candidate_id": row.latest_candidate_id,
                "accepted_recipe_digest": row.accepted_recipe_digest,
                "accepted_revision_id": row.accepted_revision_id,
                "latest_revision_id": row.latest_revision_id,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }

    def workspace(self, workspace_id, *, sandbox=None):
        return AuthorWorkspace(self.workspace_info(workspace_id)["directory"], sandbox=sandbox)

    def assignment(self, workspace_id):
        """Reserve a workspace for the entire runtime execution and cleanup."""
        return self._assignment(workspace_id)

    @contextlib.contextmanager
    def _assignment(self, workspace_id):
        if (
            not isinstance(workspace_id, str)
            or len(workspace_id) != 64
            or any(c not in "0123456789abcdef" for c in workspace_id)
        ):
            raise ServiceError("Invalid workspace identifier")
        root = _root_dir(self.root / workspace_id)
        lock = None
        try:
            lock = os.open(
                ".assignment.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=root,
            )
            _regular(lock)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ServiceBusy("Workspace has a live owner") from exc
            yield
        finally:
            if lock is not None:
                os.close(lock)
            os.close(root)

    def find_request(self, workspace_id, request_id):
        with Session(self.engine) as session:
            row = session.scalar(
                select(Job).where(Job.workspace_id == workspace_id, Job.request_id == request_id)
            )
            return self._job(row) if row else None

    @staticmethod
    def _job(job):
        return {
            key: getattr(job, key)
            for key in (
                "id",
                "workspace_id",
                "request_id",
                "status",
                "input_digest",
                "context",
                "result_ref",
                "error_code",
                "cancel_requested",
                "created_at",
                "updated_at",
                "kind",
                "input_revision_id",
                "depends_on",
                "prompt_ref",
                "callback_target",
                "base_session_revision",
                "checkpoint_ref",
                "recovery_ref",
                "base_recipe_digest",
                "artifact_digest",
                "revision_id",
                "base_revision_id",
                "accepted_base_revision_id",
                "admission",
            )
        }

    @staticmethod
    def _event(session, job, data):
        # Progress metadata is bounded per event; no raw tool arguments or transcripts.
        if len(json_bytes(data)) > 8192:
            raise ServiceError("Progress event too large")
        job.updated_at = _stamp()
        session.add(Event(job_id=job.id, created_at=job.updated_at, data=data))

    @staticmethod
    def _assignment_status(session, job):
        """Safe control-plane attestation; private task tables stay service-owned.

        Consumers may inspect current assignment integrity through authenticated
        GetTask instead of opening this service's database. No lease token,
        conversation, checkpoint or tool configuration is exposed here.
        """
        workspace = session.get(Workspace, job.workspace_id)
        revision = (
            session.get(InputRevision, job.input_revision_id) if job.input_revision_id else None
        )
        revision_valid = bool(
            revision is not None
            and workspace is not None
            and revision.workspace_id == workspace.id
            and revision.digest == job.input_digest
        )
        session_current = bool(
            workspace is not None and job.base_session_revision == workspace.session_revision
        )
        assigned = bool(job.owner)
        lifecycle = workspace.lifecycle if workspace else None
        active = bool(
            job.status == "running"
            and assigned
            and not job.cancel_requested
            and lifecycle == "managed"
            and revision_valid
            and session_current
            and job.kind in {"init", "build", "repair"}
            and (job.kind != "init" or workspace.init_task_id == job.id)
        )
        return {
            "active": active,
            "assigned": assigned,
            "input_revision_valid": revision_valid,
            "session_current": session_current,
            "source_id": workspace.source_id if workspace else None,
            "owner_id": workspace.owner_id if workspace else None,
            "lifecycle": lifecycle,
        }

    def status(self, job_id, *, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ServiceError("Invalid event cursor")
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ServiceError("Unknown job")
            events = session.scalars(
                select(Event)
                .where(Event.job_id == job_id, Event.id > after)
                .order_by(Event.id)
                .limit(limit)
            ).all()
            return {
                **self._job(job),
                "assignment": self._assignment_status(session, job),
                "events": [
                    {**e.data, "sequence": e.id, "created_at": e.created_at} for e in events
                ],
                "next_cursor": events[-1].id if events else after,
            }

    def pending(self, kinds=None):
        with Session(self.engine) as session:
            query = select(Job.id).where(Job.status == "queued")
            if kinds is not None:
                query = query.where(Job.kind.in_(kinds))
            return session.scalars(query.order_by(Job.created_at, Job.id)).all()

    def claim(self, job_id, owner):
        """Caller must hold the workspace assignment lock for its entire execution."""
        with self._transaction() as session:
            job = session.get(Job, job_id)
            if job is None or job.status != "queued":
                raise ServiceError("Job is not queued")
            if job.kind not in MANAGED_KINDS:
                raise ServiceError("Unsupported task kind")
            self._claim_managed(session, job)
            job.status, job.owner = "running", owner
            self._event(session, job, {"type": "started"})
            return self._job(job)

    def progress(self, job_id, owner, data=None):
        with self._transaction() as session:
            job = session.get(Job, job_id)
            if job is None or job.owner != owner or job.status != "running":
                raise ServiceError("Stale job owner")
            if data is not None:
                self._event(session, job, data)
            else:
                job.updated_at = _stamp()
            return job.cancel_requested

    def finish(self, job_id, owner, status, *, result=None, error_code=None, checkpoint=None):
        if status not in TERMINAL:
            raise ServiceError("Invalid terminal status")
        # Reject stale executors before reading/validating mutable workspace files.
        # The transaction below repeats this check to fence concurrent cancellation.
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None or job.status != "running" or job.owner != owner:
                raise ServiceError("Stale job owner")
            info = self._job(job)
        managed = info["kind"] in MANAGED_KINDS
        if not managed:
            raise ServiceError("Unsupported task kind")
        if error_code is not None and (
            not isinstance(error_code, str)
            or len(error_code) > 64
            or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for c in error_code)
        ):
            raise ServiceError("Invalid error code")
        artifact = None
        if managed and not info["cancel_requested"]:
            artifact = self._validate_managed_outcome(info, status, result, checkpoint)
        if checkpoint is not None and (not isinstance(checkpoint, dict) or not checkpoint):
            raise ServiceError("Invalid opaque checkpoint")
        checkpoint_ref = (
            self.blobs.put(bounded_json(checkpoint, MAX_CHECKPOINT))
            if checkpoint is not None
            else None
        )
        result_ref = (
            self.blobs.put(bounded_json(result, 64 * 1024 * 1024)) if result is not None else None
        )
        with self._transaction() as session:
            job = session.get(Job, job_id)
            if job is None or job.owner != owner or job.status != "running":
                raise ServiceError("Stale job owner")
            job.status = "cancelled" if job.cancel_requested else status
            job.result_ref = None if job.status == "cancelled" else result_ref
            job.error_code, job.owner = error_code, None
            if managed:
                self._managed_terminal(
                    session,
                    self._managed(session, job.workspace_id),
                    job,
                    checkpoint_ref=checkpoint_ref,
                    artifact_digest=artifact,
                )
            self._event(session, job, {"type": job.status, "error_code": error_code})

    def cancel(self, job_id):
        with self._transaction() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ServiceError("Unknown job")
            if job.status in ACTIVE:
                job.cancel_requested = True
                if job.status != "running":
                    job.status = "cancelled"
                    if job.kind in MANAGED_KINDS:
                        self._managed_terminal(
                            session,
                            self._managed(session, job.workspace_id),
                            job,
                            checkpoint_ref=None,
                            artifact_digest=None,
                            check_fence=False,
                        )
                self._event(session, job, {"type": "cancel_requested"})
        return self.status(job_id)

    def recover(self, job_id):
        """Explicit crash recovery, never an age-based takeover of a live Agent."""
        info = self.status(job_id)
        with self._assignment(info["workspace_id"]):
            with Session(self.engine) as session:
                job = session.get(Job, job_id)
                if job.status != "running":
                    raise ServiceError("Only an abandoned running job needs recovery")
                owner = job.owner
            self.finish(job_id, owner, "interrupted", error_code="WORKER_LOST")
            self.workspace(info["workspace_id"]).record_run(
                {
                    "status": "interrupted",
                    "error_code": "WORKER_LOST",
                    "job_id": job_id,
                }
            )
        return self.status(job_id)

    def result(self, job_id):
        job = self.status(job_id)
        if job["status"] not in TERMINAL or not job["result_ref"]:
            raise ServiceError("RESULT_NOT_READY")
        return json.loads(self.blobs.read(job["result_ref"]))

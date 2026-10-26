"""Pinned read-only task input mounts, separate from mutable server staging files."""

import contextlib
import json
import os
from pathlib import Path

from youju.onboarding.author_workspace import (
    MAX_GUIDE,
    MAX_PAYLOAD,
    AuthorWorkspace,
    AuthorWorkspaceError,
    _read,
    _root_dir,
    _write,
)
from youju.recipe_runtime.contracts import template_candidate
from youju.recipe_service.authoring import TEMPLATES
from youju.serialization import digest, json_bytes


class TaskWorkspace(AuthorWorkspace):
    """Reuse the source work/history while replacing only its input mount."""

    def __init__(self, workspace, inputs, task):
        files = dict(inputs["files"])
        self._seed = template_candidate()
        guide_path = TEMPLATES / "extraction-guide.md"
        files.update(
            {
                "guide.md": guide_path.read_text(encoding="utf-8"),
                "template.json": json.dumps(self._seed, ensure_ascii=False, indent=2),
                "task.json": json.dumps(
                    {
                        "task_id": task["id"],
                        "kind": task["kind"],
                        "prompt": task["prompt"],
                        "feedback": task.get("feedback"),
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                ),
            }
        )
        self._task_input = workspace.root / ".task-inputs" / digest(files)
        fd = _root_dir(self._task_input)
        try:
            for name, text in files.items():
                # InputCompleted already rejects filenames/links; defend here too.
                if Path(name).name != name or name in {".", ".."}:
                    raise AuthorWorkspaceError("invalid_task_input_name")
                body = text.encode("utf-8")
                old = _read(fd, name, MAX_PAYLOAD, missing=True)
                if old is None:
                    _write(fd, name, body, immutable=True)
                elif old != body:
                    raise AuthorWorkspaceError("immutable_task_input_changed")
        finally:
            os.close(fd)
        super().__init__(workspace.root, sandbox=workspace.sandbox)

    @property
    def input(self):
        return self._task_input

    @contextlib.contextmanager
    def _directories(self):
        with super()._directories() as directories:
            fd = _root_dir(self._task_input)
            try:
                yield {**directories, "input": fd}
            finally:
                os.close(fd)

    def seed_candidate(self):
        """Trusted initial template only; never overwrite Agent or existing source edits."""
        seed = self._candidate_files(self._seed)
        with self._directories() as directories:
            for name, body in seed.items():
                old = _read(directories["workspace"], name, MAX_GUIDE, missing=True)
                if old is None:
                    _write(directories["workspace"], name, body, immutable=True)

    def instructions(self):
        with self._directories() as directories:
            value = _read(directories["workspace"], "AGENTS.md", 128 * 1024)
        return value.decode("utf-8")

    def archive(self, value):
        """Host-only append-only compaction archive; never mounted as writable input."""
        body = json_bytes(value)
        if len(body) > 16 * 1024 * 1024:
            raise AuthorWorkspaceError("compaction_archive_limit")
        reference = digest(value)
        fd = _root_dir(self.root / ".session-archive")
        try:
            old = _read(fd, reference + ".json", 16 * 1024 * 1024, missing=True)
            if old is None:
                _write(fd, reference + ".json", body, immutable=True)
            elif old != body:
                raise AuthorWorkspaceError("session_archive_changed")
        finally:
            os.close(fd)
        return reference

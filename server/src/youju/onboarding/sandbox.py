"""Fail-closed Linux process isolation for material readers and generated scripts."""

import asyncio
import hashlib
import json
import os
import shutil
import signal
import stat
import sys
import tempfile
from pathlib import Path


class SandboxError(RuntimeError):
    pass


class ScriptExecutionError(SandboxError):
    """A known source-code/data exception reported by the isolated script child."""


def child_failure(payload, failure):
    kind = failure.get("error_type")
    code_errors = {
        "SyntaxError",
        "IndentationError",
        "NameError",
        "KeyError",
        "TypeError",
        "ValueError",
        "AttributeError",
        "IndexError",
        "ZeroDivisionError",
        "ImportError",
        "ModuleNotFoundError",
    }
    error = (
        ScriptExecutionError
        if payload.get("operation") == "script" and kind in code_errors
        else SandboxError
    )
    return error(str(failure.get("error", "sandbox_failure"))[:1000])


class Sandbox:
    """No host home, credentials, network, DB or writable source mounts.

    This is a local Linux boundary using bubblewrap namespaces plus rlimits, not
    a replacement for a hardened multi-tenant container/cgroup deployment.
    """

    def __init__(self, *, timeout=25, max_output=8 * 1024 * 1024):
        if not 0 < timeout <= 120 or not 1024 <= max_output <= 16 * 1024 * 1024:
            raise ValueError("Invalid sandbox limits")
        self.timeout = timeout
        self.max_output = max_output

    async def parse(self, body: bytes, *, media_type: str, url: str, material_key: str):
        if len(body) > 16 * 1024 * 1024:
            raise SandboxError("material_input_limit")
        return await self._run(
            {
                "operation": "material",
                "media_type": media_type,
                "url": url,
                "material_key": material_key,
            },
            body=body,
        )

    async def run_script(self, files: dict[str, str], module: str, function: str, inputs: dict):
        if module not in {"parser", "undated_parser"} or function != "parse":
            raise SandboxError("unapproved_script_reference")
        if set(files) != {f"{module}.py"}:
            raise SandboxError("unapproved_script_file")
        if f"{module}.py" not in files or any(
            not isinstance(value, str) or len(value.encode()) > 128 * 1024
            for value in files.values()
        ):
            raise SandboxError("invalid_script_source")
        return await self._run(
            {"operation": "script", "module": module, "function": function, "inputs": inputs},
            files=files,
        )

    async def probe(self):
        return await self._run({"operation": "probe"})

    async def run_author_tool(
        self, tool_name, args, *, tool_call_id, workspace_fd, input_fd, history_fd
    ):
        """Invoke an official tool with pinned directory mounts, never on the host."""
        if tool_name not in {"bash", "filesystem", "flow"} or not isinstance(args, dict):
            raise SandboxError("unapproved_author_tool")
        if not isinstance(tool_call_id, str):
            raise SandboxError("invalid_author_tool_call_id")
        descriptors = (workspace_fd, input_fd, history_fd)
        if any(type(fd) is not int or not stat.S_ISDIR(os.fstat(fd).st_mode) for fd in descriptors):
            raise SandboxError("invalid_author_workspace_directory")
        # Responses SDK IDs can contain a long opaque item ID after the wire
        # call ID. The child needs only a local execution reference. Never alter
        # the original ID used by the Agent/provider to correlate tool results.
        local_id = hashlib.sha256(tool_call_id.encode("utf-8")).hexdigest()
        payload = {"tool_name": tool_name, "args": args, "tool_call_id": local_id}
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) > 256 * 1024:
            raise SandboxError("author_tool_input_limit")
        return await self._run(payload, _author_fds=descriptors)

    @staticmethod
    def _command(job, *, author_fds=None):
        executable = shutil.which("bwrap")
        if sys.platform != "linux" or executable is None:
            raise SandboxError("bubblewrap_required_no_unsafe_fallback")
        source = Path(__file__).resolve().parents[2]
        command = [
            executable,
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
        ]
        for directory in ("/lib", "/lib64", "/bin"):
            path = Path(directory)
            if path.is_symlink():
                command += ["--symlink", os.readlink(path), directory]
            elif path.exists():
                command += ["--ro-bind", directory, directory]
        # Retain interpreter/venv paths so Python locates its own standard library.
        for root in dict.fromkeys((sys.base_prefix, sys.prefix)):
            if root != "/usr" and not root.startswith("/usr/"):
                command += ["--ro-bind", root, root]
        if Path("/etc/ld.so.cache").is_file():
            command += ["--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache"]
        command += [
            "--ro-bind",
            str(source),
            "/app",
            "--ro-bind",
            str(job),
            "/job",
        ]
        if author_fds is not None:
            workspace_fd, input_fd, history_fd = author_fds
            command += [
                "--bind-fd",
                str(workspace_fd),
                "/work",
                "--ro-bind-fd",
                str(input_fd),
                "/input",
                "--ro-bind-fd",
                str(history_fd),
                "/history",
                "--unshare-user",
                "--disable-userns",
                "--size",
                str(16 * 1024 * 1024),
            ]
        command += [
            "--tmpfs",
            "/tmp",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--chdir",
            "/work" if author_fds is not None else "/job",
            "--setenv",
            "PATH",
            f"{sys.prefix}/bin:/usr/bin:/bin" if author_fds is not None else "/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/tmp",
            "--setenv",
            "PYTHONPATH",
            "/app",
            "--setenv",
            "PYTHONNOUSERSITE",
            "1",
            "--setenv",
            "LC_ALL",
            "C.UTF-8",
        ]
        if author_fds is not None:
            command += [
                "--setenv",
                "PYTHONDONTWRITEBYTECODE",
                "1",
                "--setenv",
                "YOUJU_AUTHOR_SANDBOX",
                "1",
                "--setenv",
                "PYTHONSAFEPATH",
                "1",
                "--remount-ro",
                "/",
            ]
        command += [
            "--",
            sys.executable,
            "-B",
            "-s",
            "-P",  # Never let the script directory shadow official xdog packages.
            "/app/youju/onboarding/author_tool_entry.py"
            if author_fds is not None
            else "/app/youju/onboarding/sandbox_entry.py",
        ]
        return command

    async def _run(self, payload, *, body=None, files=None, _author_fds=None):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > 8 * 1024 * 1024:
            raise SandboxError("sandbox_input_limit")
        with tempfile.TemporaryDirectory(prefix="youju-sandbox-") as directory:
            job = Path(directory)
            (job / "input.json").write_bytes(encoded)
            if body is not None:
                (job / "body.bin").write_bytes(body)
            for name, code in (files or {}).items():
                (job / name).write_text(code, encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                *self._command(job, author_fds=_author_fds),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin"},
                start_new_session=True,
                pass_fds=_author_fds or (),
            )

            async def drain(stream, limit):
                output = bytearray()
                while data := await stream.read(65536):
                    if len(output) + len(data) > limit:
                        raise SandboxError("sandbox_output_limit")
                    output.extend(data)
                return bytes(output)

            readers = [
                asyncio.create_task(drain(process.stdout, self.max_output)),
                asyncio.create_task(drain(process.stderr, 64 * 1024)),
            ]
            try:
                async with asyncio.timeout(self.timeout):
                    stdout, stderr = await asyncio.gather(*readers)
                    await process.wait()
            except BaseException:
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await process.wait()
                for reader in readers:
                    reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
                raise
            if process.returncode:
                # Child error messages contain only the isolated job's data, never host secrets.
                try:
                    failure = json.loads(stdout)
                except (ValueError, UnicodeError):
                    failure = {}
                if isinstance(failure, dict) and failure.get("ok") is False:
                    raise child_failure(payload, failure)
                # The child receives no credentials; limit diagnostic size regardless.
                raise SandboxError(
                    f"sandbox_exit_{process.returncode}: {stderr.decode(errors='replace')[-1000:]}"
                )
            try:
                result = json.loads(stdout)
            except (UnicodeError, ValueError) as error:
                raise SandboxError("invalid_sandbox_json") from error
            if not isinstance(result, dict) or "ok" not in result:
                raise SandboxError("invalid_sandbox_envelope")
            if not result["ok"]:
                raise child_failure(payload, result)
            return result["value"]

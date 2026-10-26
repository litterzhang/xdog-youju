"""Small content-addressed local store; references never permit arbitrary filesystem paths."""

import hashlib
import os
import re
import tempfile
from pathlib import Path


class BlobStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / digest
        if target.exists():
            if self.read(digest) != data:
                raise ValueError("Stored content does not match its digest")
            return digest
        fd, name = tempfile.mkstemp(dir=self.root, prefix=".pending-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)
        return digest

    def read(self, reference: str) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{64}", reference):
            raise ValueError("Invalid blob reference")
        path = self.root / reference
        if path.is_symlink():
            raise ValueError("Blob references must not be symlinks")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != reference:
            raise ValueError("Stored content digest mismatch")
        return data

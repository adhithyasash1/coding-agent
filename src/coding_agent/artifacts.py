"""Opaque, trial-local handles for bounded reads of captured tool output."""

import os
import stat
import uuid
from pathlib import Path


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._paths: dict[str, Path] = {}

    def register(self, path: str) -> str:
        candidate = Path(path)
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(self.root):
            raise ValueError("artifact must be a captured output in this trial")
        for identity, existing in self._paths.items():
            if existing == candidate:
                return identity
        identity = uuid.uuid4().hex
        self._paths[identity] = candidate
        return identity

    def read(self, identity: str, offset: int, limit: int) -> tuple[str, int, bool]:
        if identity not in self._paths:
            raise ValueError("unknown artifact ID for this trial")
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 65536
        ):
            raise ValueError("offset must be nonnegative and limit must be 1..65536 bytes")
        path = self._paths[identity]
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("artifact escaped trial storage")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("artifact must be a regular file")
            stream.seek(offset)
            data = stream.read(limit)
            end = stream.tell()
        return data.decode("utf-8", "replace"), end, end >= info.st_size

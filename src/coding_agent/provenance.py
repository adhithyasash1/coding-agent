"""Portable implementation identity even before the first Git commit."""

import hashlib
import platform
from pathlib import Path

from coding_agent import __version__


def provenance() -> dict[str, str]:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return {
        "harness_version": __version__,
        "source_sha256": digest.hexdigest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }

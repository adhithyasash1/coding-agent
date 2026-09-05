"""Prompt for an endpoint key and create a named Modal secret without secret argv."""

import getpass
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def create_secret(key: str) -> None:
    if not key:
        raise ValueError("Endpoint key must not be empty")
    with tempfile.TemporaryDirectory(prefix="agent-secret-") as directory:
        path = Path(directory) / "secret.json"
        with path.open("x") as stream:
            path.chmod(0o600)
            json.dump({"VLLM_API_KEY": key}, stream)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "modal",
                "secret",
                "create",
                "adaptive-agent-inference",
                "--from-json",
                str(path),
            ],
            check=True,
        )


if __name__ == "__main__":
    create_secret(getpass.getpass("New inference endpoint key: "))

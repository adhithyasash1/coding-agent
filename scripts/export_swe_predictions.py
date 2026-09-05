"""Export SWE-bench predictions, including untracked files, without changing the index."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def patch(workspace: Path, base: str) -> str:
    with tempfile.TemporaryDirectory(prefix="coding-agent-index-") as temporary:
        environment = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"))

        def git(*arguments: str) -> str:
            return subprocess.run(
                ["git", "-C", str(workspace), *arguments],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            ).stdout

        # Resolve a commit first so a user-supplied ref cannot become a diff option.
        revision = git("rev-parse", "--verify", "--end-of-options", base + "^{commit}").strip()
        git("read-tree", revision)
        git("add", "-A")
        return git("diff", "--cached", "--binary", revision, "--")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        type=Path,
        help="JSON list of instance_id, workspace, base_commit, model_name_or_path",
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.manifest.read_text())
    predictions = [
        {
            "instance_id": row["instance_id"],
            "model_name_or_path": row["model_name_or_path"],
            "model_patch": patch(Path(row["workspace"]), row["base_commit"]),
        }
        for row in rows
    ]
    with args.output.open("x") as output:
        for row in predictions:
            output.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()

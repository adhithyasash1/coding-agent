import os

from coding_agent.toolset import WorkspaceTools
from coding_agent.types import ToolCall


def test_atomic_exact_edit_and_path_escape(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "x.txt").write_text("duplicate duplicate")
    tools = WorkspaceTools(work, tmp_path / "outputs")
    try:
        result = tools.execute(
            ToolCall("1", "edit_file", {"path": "x.txt", "old": "duplicate", "new": "x"})
        )
        assert result.status == "error"
        assert (work / "x.txt").read_text() == "duplicate duplicate"
        result = tools.execute(
            ToolCall("2", "edit_file", {"path": "../escape", "old": "", "new": "x", "create": True})
        )
        assert result.status == "error"
        assert not (tmp_path / "escape").exists()
    finally:
        tools.close()


def test_symlink_escape_and_revision_tracks_modes(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "private"
    outside.write_text("sensitive")
    (work / "link").symlink_to(outside)
    tools = WorkspaceTools(work, tmp_path / "outputs")
    try:
        assert tools.execute(ToolCall("1", "read_file", {"path": "link"})).status == "error"
        file = work / "script"
        file.write_text("exit 0")
        before = tools.revision()
        os.chmod(file, 0o755)
        assert tools.revision() != before
    finally:
        tools.close()


def test_verification_does_not_accept_test_that_changes_workspace(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    tools = WorkspaceTools(work, tmp_path / "outputs")
    try:
        result = tools.execute(
            ToolCall("1", "run_command", {"command": "touch changed", "verify": True})
        )
        assert result.status == "ok"
        assert not result.metadata["verification"]
    finally:
        tools.close()


def test_edit_file_create_omits_old(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    tools = WorkspaceTools(work, tmp_path / "outputs")
    try:
        created = tools.execute(
            ToolCall("1", "edit_file", {"path": "new.txt", "new": "hello", "create": True})
        )
        assert created.status == "ok"
        assert (work / "new.txt").read_text() == "hello"
        missing = tools.execute(ToolCall("2", "edit_file", {"path": "missing.txt", "new": "x"}))
        assert missing.status == "error"
        existing = tools.execute(ToolCall("3", "edit_file", {"path": "new.txt", "new": "nope"}))
        assert existing.status == "error"
        assert (work / "new.txt").read_text() == "hello"
    finally:
        tools.close()

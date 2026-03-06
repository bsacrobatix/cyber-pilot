"""
Tests for utils/diff_engine.py — Resource Diff Engine.

Covers: snapshot_directory, diff_snapshot, DiffReport, show_diff_summary,
show_file_diff, interactive_review.
"""

import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "cypilot" / "scripts"))

from cypilot.utils.diff_engine import (
    DiffReport,
    snapshot_directory,
    diff_snapshot,
    show_diff_summary,
    show_file_diff,
    interactive_review,
)


# =========================================================================
# snapshot_directory
# =========================================================================

class TestSnapshotDirectory(unittest.TestCase):
    """snapshot_directory captures file contents by extension."""

    def test_captures_md_and_toml(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"markdown")
            (d / "b.toml").write_bytes(b"toml")
            (d / "c.py").write_bytes(b"python")
            snap = snapshot_directory(d)
            self.assertIn("a.md", snap)
            self.assertIn("b.toml", snap)
            self.assertNotIn("c.py", snap)

    def test_recursive(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            sub = d / "artifacts" / "PRD"
            sub.mkdir(parents=True)
            (sub / "template.md").write_bytes(b"tmpl")
            snap = snapshot_directory(d)
            key = str(Path("artifacts") / "PRD" / "template.md")
            self.assertIn(key, snap)
            self.assertEqual(snap[key], b"tmpl")

    def test_empty_directory(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            snap = snapshot_directory(d)
            self.assertEqual(snap, {})

    def test_nonexistent_directory(self):
        snap = snapshot_directory(Path("/nonexistent"))
        self.assertEqual(snap, {})

    def test_custom_extensions(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.py").write_bytes(b"py")
            (d / "b.md").write_bytes(b"md")
            snap = snapshot_directory(d, extensions=(".py",))
            self.assertIn("a.py", snap)
            self.assertNotIn("b.md", snap)


# =========================================================================
# diff_snapshot / DiffReport
# =========================================================================

class TestDiffSnapshot(unittest.TestCase):
    """diff_snapshot compares current state against a previous snapshot."""

    def test_no_changes(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"content")
            snap = snapshot_directory(d)
            report = diff_snapshot(d, snap)
            self.assertFalse(report.has_changes)
            self.assertEqual(report.added, [])
            self.assertEqual(report.removed, [])
            self.assertEqual(report.modified, [])
            self.assertEqual(len(report.unchanged), 1)

    def test_added_file(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            snap = snapshot_directory(d)  # empty
            (d / "new.md").write_bytes(b"new")
            report = diff_snapshot(d, snap)
            self.assertTrue(report.has_changes)
            self.assertIn("new.md", report.added)

    def test_removed_file(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "old.md").write_bytes(b"old")
            snap = snapshot_directory(d)
            (d / "old.md").unlink()
            report = diff_snapshot(d, snap)
            self.assertTrue(report.has_changes)
            self.assertIn("old.md", report.removed)

    def test_modified_file(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"original")
            snap = snapshot_directory(d)
            (d / "a.md").write_bytes(b"modified")
            report = diff_snapshot(d, snap)
            self.assertTrue(report.has_changes)
            self.assertIn("a.md", report.modified)

    def test_mixed_changes(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "keep.md").write_bytes(b"same")
            (d / "change.md").write_bytes(b"old")
            (d / "remove.md").write_bytes(b"gone")
            snap = snapshot_directory(d)
            (d / "change.md").write_bytes(b"new")
            (d / "remove.md").unlink()
            (d / "add.md").write_bytes(b"added")
            report = diff_snapshot(d, snap)
            self.assertTrue(report.has_changes)
            self.assertIn("add.md", report.added)
            self.assertIn("remove.md", report.removed)
            self.assertIn("change.md", report.modified)
            self.assertIn("keep.md", report.unchanged)


class TestDiffReport(unittest.TestCase):
    """DiffReport properties and serialization."""

    def test_has_changes_empty(self):
        r = DiffReport()
        self.assertFalse(r.has_changes)

    def test_has_changes_with_added(self):
        r = DiffReport(added=["a.md"])
        self.assertTrue(r.has_changes)

    def test_to_dict(self):
        r = DiffReport(
            added=["a.md"],
            modified=["b.md"],
            unchanged=["c.md", "d.md"],
        )
        d = r.to_dict()
        self.assertEqual(d["added"], ["a.md"])
        self.assertEqual(d["modified"], ["b.md"])
        self.assertNotIn("removed", d)  # empty list not included
        self.assertEqual(d["unchanged_count"], 2)


# =========================================================================
# show_diff_summary
# =========================================================================

class TestShowDiffSummary(unittest.TestCase):
    """show_diff_summary prints colored output to stderr."""

    def test_no_changes_silent(self):
        report = DiffReport()
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            show_diff_summary(report)
        self.assertEqual(buf.getvalue(), "")

    def test_shows_added_modified_removed(self):
        report = DiffReport(
            added=["new.md"],
            removed=["old.md"],
            modified=["changed.md"],
        )
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            show_diff_summary(report, label="Test changes:")
        output = buf.getvalue()
        self.assertIn("new.md", output)
        self.assertIn("old.md", output)
        self.assertIn("changed.md", output)
        self.assertIn("Test changes:", output)


# =========================================================================
# show_file_diff
# =========================================================================

class TestShowFileDiff(unittest.TestCase):
    """show_file_diff prints unified diff to stderr."""

    def test_shows_diff(self):
        old = b"line1\nline2\n"
        new = b"line1\nmodified\n"
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            show_file_diff("test.md", old, new)
        output = buf.getvalue()
        self.assertIn("line2", output)
        self.assertIn("modified", output)

    def test_binary_file(self):
        old = b"\x00\x01\x02"
        new = b"\x03\x04\x05"
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            show_file_diff("binary.bin", old, new)
        output = buf.getvalue()
        self.assertIn("binary", output)

    def test_identical_files_silent(self):
        content = b"same\n"
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            show_file_diff("same.md", content, content)
        self.assertEqual(buf.getvalue(), "")


# =========================================================================
# interactive_review (non-interactive mode)
# =========================================================================

class TestInteractiveReviewNonInteractive(unittest.TestCase):
    """interactive_review in non-interactive/auto-approve mode."""

    def test_no_changes(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"same")
            snap = snapshot_directory(d)
            result = interactive_review(d, snap, interactive=False)
            self.assertEqual(result["accepted"], [])
            self.assertEqual(result["rejected"], [])

    def test_accepts_all_non_interactive(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"old")
            snap = snapshot_directory(d)
            (d / "a.md").write_bytes(b"new")
            (d / "b.md").write_bytes(b"added")
            result = interactive_review(d, snap, interactive=False)
            self.assertIn("b.md", result["accepted"])
            self.assertIn("a.md", result["accepted"])
            self.assertEqual(result["rejected"], [])

    def test_auto_approve_accepts_all(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"old")
            snap = snapshot_directory(d)
            (d / "a.md").write_bytes(b"new")
            result = interactive_review(d, snap, auto_approve=True)
            self.assertIn("a.md", result["accepted"])

    def test_diff_report_included(self):
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            snap = snapshot_directory(d)
            (d / "new.md").write_bytes(b"new")
            result = interactive_review(d, snap, interactive=False)
            diff = result["diff"]
            self.assertIn("new.md", diff.get("added", []))


# =========================================================================
# interactive_review (interactive mode with mocked prompts)
# =========================================================================

class TestInteractiveReviewInteractive(unittest.TestCase):
    """interactive_review in interactive mode with mocked stdin."""

    def test_accept_modified_file(self):
        """User answers 'y' → file is accepted (new content kept)."""
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"old content")
            snap = snapshot_directory(d)
            (d / "a.md").write_bytes(b"new content")
            with patch("cypilot.utils.diff_engine._prompt_file", return_value="y"):
                result = interactive_review(d, snap, interactive=True)
            self.assertIn("a.md", result["accepted"])
            self.assertEqual(result["rejected"], [])
            self.assertEqual((d / "a.md").read_bytes(), b"new content")

    def test_reject_modified_file(self):
        """User answers 'n' → file is rejected (old content restored)."""
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"old content")
            snap = snapshot_directory(d)
            (d / "a.md").write_bytes(b"new content")
            with patch("cypilot.utils.diff_engine._prompt_file", return_value="n"):
                result = interactive_review(d, snap, interactive=True)
            self.assertIn("a.md", result["rejected"])
            self.assertEqual((d / "a.md").read_bytes(), b"old content")

    def test_modify_file_success(self):
        """User answers 'm' → editor returns content → file written."""
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"old")
            snap = snapshot_directory(d)
            (d / "a.md").write_bytes(b"new")
            with patch("cypilot.utils.diff_engine._prompt_file", return_value="m"):
                with patch("cypilot.utils.diff_engine._open_editor_for_file",
                           return_value=b"manually edited"):
                    result = interactive_review(d, snap, interactive=True)
            self.assertIn("a.md", result["accepted"])
            self.assertEqual((d / "a.md").read_bytes(), b"manually edited")

    def test_modify_file_aborted(self):
        """User answers 'm' → editor returns None → old content restored."""
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"old")
            snap = snapshot_directory(d)
            (d / "a.md").write_bytes(b"new")
            with patch("cypilot.utils.diff_engine._prompt_file", return_value="m"):
                with patch("cypilot.utils.diff_engine._open_editor_for_file",
                           return_value=None):
                    result = interactive_review(d, snap, interactive=True)
            self.assertIn("a.md", result["rejected"])
            self.assertEqual((d / "a.md").read_bytes(), b"old")

    def test_multiple_files_different_decisions(self):
        """Multiple modified files get different decisions."""
        with TemporaryDirectory() as td:
            d = Path(td) / "gen"
            d.mkdir()
            (d / "a.md").write_bytes(b"old_a")
            (d / "b.md").write_bytes(b"old_b")
            snap = snapshot_directory(d)
            (d / "a.md").write_bytes(b"new_a")
            (d / "b.md").write_bytes(b"new_b")
            answers = iter(["y", "n"])
            with patch("cypilot.utils.diff_engine._prompt_file",
                        side_effect=lambda *a, **kw: next(answers)):
                result = interactive_review(d, snap, interactive=True)
            self.assertIn("a.md", result["accepted"])
            self.assertIn("b.md", result["rejected"])


# =========================================================================
# _prompt_file
# =========================================================================

class TestPromptFile(unittest.TestCase):
    """Cover _prompt_file helper."""

    def test_auto_all_state(self):
        from cypilot.utils.diff_engine import _prompt_file
        state = {"all": True}
        self.assertEqual(_prompt_file("test?", state), "y")

    def test_yes_response(self):
        from cypilot.utils.diff_engine import _prompt_file
        state = {"all": False}
        with patch("builtins.input", return_value="y"):
            self.assertEqual(_prompt_file("test?", state), "y")

    def test_no_response(self):
        from cypilot.utils.diff_engine import _prompt_file
        state = {"all": False}
        with patch("builtins.input", return_value="n"):
            self.assertEqual(_prompt_file("test?", state), "n")

    def test_all_response(self):
        from cypilot.utils.diff_engine import _prompt_file
        state = {"all": False}
        with patch("builtins.input", return_value="all"):
            self.assertEqual(_prompt_file("test?", state), "y")
        self.assertTrue(state["all"])

    def test_modify_response(self):
        from cypilot.utils.diff_engine import _prompt_file
        state = {"all": False}
        with patch("builtins.input", return_value="m"):
            self.assertEqual(_prompt_file("test?", state), "m")

    def test_eof_response(self):
        from cypilot.utils.diff_engine import _prompt_file
        state = {"all": False}
        with patch("builtins.input", side_effect=EOFError):
            self.assertEqual(_prompt_file("test?", state), "n")

    def test_empty_response_is_no(self):
        from cypilot.utils.diff_engine import _prompt_file
        state = {"all": False}
        with patch("builtins.input", return_value=""):
            self.assertEqual(_prompt_file("test?", state), "n")


# =========================================================================
# _open_editor_for_file
# =========================================================================

class TestOpenEditorForFile(unittest.TestCase):
    """Cover _open_editor_for_file helper."""

    def test_binary_content_returns_none(self):
        from cypilot.utils.diff_engine import _open_editor_for_file
        result = _open_editor_for_file("bin.dat", b"\x00\x01", b"\x02\x03")
        self.assertIsNone(result)

    def test_editor_not_found_returns_none(self):
        from cypilot.utils.diff_engine import _open_editor_for_file
        with patch.dict("os.environ", {"VISUAL": "nonexistent_editor_xyz", "EDITOR": "nonexistent_editor_xyz"}):
            result = _open_editor_for_file("test.md", b"old\n", b"new\n")
        self.assertIsNone(result)

    def test_successful_edit_with_separator(self):
        """Editor saves content after separator → returns edited bytes."""
        import os, tempfile
        from cypilot.utils.diff_engine import _open_editor_for_file
        separator = "# ── edit below this line ──────────────────────────────────────"

        def fake_editor(cmd):
            # Read the temp file, replace content after separator
            path = cmd[-1]
            with open(path) as f:
                content = f.read()
            sep_idx = content.find(separator)
            new_content = content[:sep_idx + len(separator)] + "\nmanually edited\n"
            with open(path, "w") as f:
                f.write(new_content)

        with patch("subprocess.check_call", side_effect=fake_editor):
            with patch.dict("os.environ", {"VISUAL": "cat"}):
                result = _open_editor_for_file("test.md", b"old\n", b"new\n")
        self.assertIsNotNone(result)
        self.assertEqual(result, b"manually edited\n")

    def test_empty_result_returns_none(self):
        """If user deletes all content → returns None (abort)."""
        from cypilot.utils.diff_engine import _open_editor_for_file
        separator = "# ── edit below this line ──────────────────────────────────────"

        def fake_editor(cmd):
            path = cmd[-1]
            with open(path) as f:
                content = f.read()
            sep_idx = content.find(separator)
            # Clear content after separator
            new_content = content[:sep_idx + len(separator)] + "\n"
            with open(path, "w") as f:
                f.write(new_content)

        with patch("subprocess.check_call", side_effect=fake_editor):
            with patch.dict("os.environ", {"VISUAL": "cat"}):
                result = _open_editor_for_file("test.md", b"old\n", b"new\n")
        self.assertIsNone(result)

    def test_edit_without_separator_uses_non_comment_lines(self):
        """If separator is removed, extract non-comment lines."""
        from cypilot.utils.diff_engine import _open_editor_for_file

        def fake_editor(cmd):
            path = cmd[-1]
            with open(path, "w") as f:
                f.write("# comment\n# another\nactual content\n")

        with patch("subprocess.check_call", side_effect=fake_editor):
            with patch.dict("os.environ", {"VISUAL": "cat"}):
                result = _open_editor_for_file("test.md", b"old\n", b"new\n")
        self.assertIsNotNone(result)
        self.assertEqual(result, b"actual content\n")

    def test_edit_all_comments_returns_none(self):
        """If entire file is comments after removing separator → abort."""
        from cypilot.utils.diff_engine import _open_editor_for_file

        def fake_editor(cmd):
            path = cmd[-1]
            with open(path, "w") as f:
                f.write("# only comments\n# nothing else\n")

        with patch("subprocess.check_call", side_effect=fake_editor):
            with patch.dict("os.environ", {"VISUAL": "cat"}):
                result = _open_editor_for_file("test.md", b"old\n", b"new\n")
        self.assertIsNone(result)

    def test_editor_exception_returns_none(self):
        """Editor raises exception → returns None."""
        from cypilot.utils.diff_engine import _open_editor_for_file
        with patch("subprocess.check_call", side_effect=RuntimeError("editor crash")):
            with patch.dict("os.environ", {"VISUAL": "cat"}):
                result = _open_editor_for_file("test.md", b"old\n", b"new\n")
        self.assertIsNone(result)

    def test_identical_content_no_diff_header(self):
        """When old == new, diff header says 'no diff'."""
        from cypilot.utils.diff_engine import _open_editor_for_file
        separator = "# ── edit below this line ──────────────────────────────────────"

        def fake_editor(cmd):
            path = cmd[-1]
            with open(path) as f:
                content = f.read()
            self.assertIn("no diff", content)
            sep_idx = content.find(separator)
            with open(path, "w") as f:
                f.write(content[:sep_idx + len(separator)] + "\nkept\n")

        with patch("subprocess.check_call", side_effect=fake_editor):
            with patch.dict("os.environ", {"VISUAL": "cat"}):
                result = _open_editor_for_file("test.md", b"same\n", b"same\n")
        self.assertEqual(result, b"kept\n")


# =========================================================================
# _get_editor
# =========================================================================

class TestGetEditor(unittest.TestCase):
    """Cover _get_editor helper."""

    def test_visual_preferred(self):
        from cypilot.utils.diff_engine import _get_editor
        with patch.dict("os.environ", {"VISUAL": "code", "EDITOR": "vim"}):
            self.assertEqual(_get_editor(), "code")

    def test_editor_fallback(self):
        from cypilot.utils.diff_engine import _get_editor
        with patch.dict("os.environ", {"EDITOR": "nano"}, clear=True):
            self.assertEqual(_get_editor(), "nano")

    def test_default_vi(self):
        from cypilot.utils.diff_engine import _get_editor
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_get_editor(), "vi")


if __name__ == "__main__":
    unittest.main()

"""
Resource Diff Engine for Cypilot.

Provides file-level diff for generated resources. Used to show what changed
when kit outputs are regenerated after blueprint updates.

Modes:
- accept-file: overwrite with new content (default)
- reject-file: restore from snapshot
- accept-all: overwrite all remaining
- reject-all: keep all remaining
- modify: open editor for manual merge

@cpt-algo:cpt-cypilot-algo-blueprint-system-diff-engine:p1
"""

import difflib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Extensions to track in generated output directories
_GEN_EXTENSIONS = (".md", ".toml")


# ---------------------------------------------------------------------------
# Snapshot & Diff
# ---------------------------------------------------------------------------

# @cpt-begin:cpt-cypilot-algo-blueprint-system-diff-engine:p1:inst-snapshot
def snapshot_directory(
    dir_path: Path,
    extensions: Tuple[str, ...] = _GEN_EXTENSIONS,
) -> Dict[str, bytes]:
    """Read all files with given extensions recursively into memory.

    Returns {relative_path: file_content_bytes}.
    """
    snapshot: Dict[str, bytes] = {}
    if not dir_path.is_dir():
        return snapshot
    for f in sorted(dir_path.rglob("*")):
        if f.is_file() and f.suffix in extensions:
            rel = str(f.relative_to(dir_path))
            try:
                snapshot[rel] = f.read_bytes()
            except OSError:
                pass
    return snapshot
# @cpt-end:cpt-cypilot-algo-blueprint-system-diff-engine:p1:inst-snapshot


@dataclass
class DiffReport:
    """Result of comparing two directory states."""
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        if self.added:
            d["added"] = self.added
        if self.removed:
            d["removed"] = self.removed
        if self.modified:
            d["modified"] = self.modified
        d["unchanged_count"] = len(self.unchanged)
        return d


# @cpt-begin:cpt-cypilot-algo-blueprint-system-diff-engine:p1:inst-diff
def diff_snapshot(
    dir_path: Path,
    old_snapshot: Dict[str, bytes],
    extensions: Tuple[str, ...] = _GEN_EXTENSIONS,
) -> DiffReport:
    """Compare current directory state against a previous snapshot.

    Returns DiffReport with added/removed/modified/unchanged file lists.
    """
    current = snapshot_directory(dir_path, extensions)
    report = DiffReport()

    for p in sorted(current):
        if p not in old_snapshot:
            report.added.append(p)
        elif current[p] != old_snapshot[p]:
            report.modified.append(p)
        else:
            report.unchanged.append(p)

    for p in sorted(old_snapshot):
        if p not in current:
            report.removed.append(p)

    return report
# @cpt-end:cpt-cypilot-algo-blueprint-system-diff-engine:p1:inst-diff


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

# @cpt-begin:cpt-cypilot-algo-blueprint-system-diff-engine:p1:inst-show-summary
def show_diff_summary(
    report: DiffReport,
    prefix: str = "    ",
    label: str = "",
) -> None:
    """Print a human-readable summary of directory changes to stderr."""
    if not report.has_changes:
        return

    if label:
        sys.stderr.write(f"{prefix}{label}\n")

    for p in report.added:
        sys.stderr.write(f"{prefix}  \033[32m+ {p}\033[0m\n")
    for p in report.removed:
        sys.stderr.write(f"{prefix}  \033[31m- {p}\033[0m\n")
    for p in report.modified:
        sys.stderr.write(f"{prefix}  \033[33m~ {p}\033[0m\n")
# @cpt-end:cpt-cypilot-algo-blueprint-system-diff-engine:p1:inst-show-summary


def show_file_diff(
    rel_path: str,
    old_content: bytes,
    new_content: bytes,
    prefix: str = "        ",
) -> None:
    """Show unified diff for a single file to stderr."""
    try:
        old_lines = old_content.decode("utf-8").splitlines(keepends=True)
        new_lines = new_content.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        sys.stderr.write(f"{prefix}(binary file — diff not shown)\n")
        return

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"old/{rel_path}",
        tofile=f"new/{rel_path}",
        lineterm="",
    ))
    if not diff:
        return
    for line in diff:
        line_s = line.rstrip("\n")
        if line_s.startswith("+++") or line_s.startswith("---"):
            sys.stderr.write(f"{prefix}{line_s}\n")
        elif line_s.startswith("+"):
            sys.stderr.write(f"{prefix}\033[32m{line_s}\033[0m\n")
        elif line_s.startswith("-"):
            sys.stderr.write(f"{prefix}\033[31m{line_s}\033[0m\n")
        elif line_s.startswith("@@"):
            sys.stderr.write(f"{prefix}\033[36m{line_s}\033[0m\n")


# ---------------------------------------------------------------------------
# Interactive review
# ---------------------------------------------------------------------------

# @cpt-begin:cpt-cypilot-algo-blueprint-system-diff-engine:p1:inst-interactive
def interactive_review(
    dir_path: Path,
    old_snapshot: Dict[str, bytes],
    *,
    interactive: bool = True,
    auto_approve: bool = False,
    extensions: Tuple[str, ...] = _GEN_EXTENSIONS,
) -> Dict[str, Any]:
    """Review changes in a generated directory interactively.

    Compares current directory with old_snapshot. In interactive mode,
    prompts the user per modified file: accept (keep new), reject (restore old),
    or modify (open editor). Added/removed files are always accepted.

    Args:
        dir_path: Generated output directory to review.
        old_snapshot: Previous state from snapshot_directory().
        interactive: Whether to prompt user.
        auto_approve: Skip all prompts (accept all).
        extensions: File extensions to consider.

    Returns dict with:
        - diff: DiffReport.to_dict()
        - accepted: list of accepted file paths
        - rejected: list of rejected file paths (restored from snapshot)
    """
    report = diff_snapshot(dir_path, old_snapshot, extensions)

    if not report.has_changes:
        return {"diff": report.to_dict(), "accepted": [], "rejected": []}

    accepted: List[str] = list(report.added)  # new files always accepted
    rejected: List[str] = []

    # Removed files: already gone from current dir, nothing to restore
    # (unless we want to un-remove them, but that's unusual for .gen/)

    if not interactive or auto_approve or not report.modified:
        # Non-interactive: accept all changes
        accepted.extend(report.modified)
        show_diff_summary(report, label="Generated output changes:")
        return {"diff": report.to_dict(), "accepted": accepted, "rejected": rejected}

    # Interactive review of modified files
    show_diff_summary(report, label="Generated output changes:")
    state: Dict[str, bool] = {"all": False}

    for rel_path in report.modified:
        old_content = old_snapshot.get(rel_path, b"")
        new_path = dir_path / rel_path
        new_content = new_path.read_bytes() if new_path.is_file() else b""

        show_file_diff(rel_path, old_content, new_content)
        ans = _prompt_file("    accept change?", state)

        if ans == "y":
            accepted.append(rel_path)
        elif ans == "n":
            # Restore from snapshot
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                new_path.write_bytes(old_content)
                rejected.append(rel_path)
            except OSError as exc:
                sys.stderr.write(f"    ⚠ failed to restore {rel_path}: {exc}\n")
                accepted.append(rel_path)
        elif ans == "m":
            edited = _open_editor_for_file(rel_path, old_content, new_content)
            if edited is not None:
                try:
                    new_path.write_bytes(edited)
                    accepted.append(rel_path)
                    sys.stderr.write(f"    ✓ manually edited\n")
                except OSError as exc:
                    sys.stderr.write(f"    ⚠ failed to write {rel_path}: {exc}\n")
                    accepted.append(rel_path)
            else:
                # Aborted — restore old
                try:
                    new_path.write_bytes(old_content)
                    rejected.append(rel_path)
                except OSError:
                    accepted.append(rel_path)

    return {"diff": report.to_dict(), "accepted": accepted, "rejected": rejected}
# @cpt-end:cpt-cypilot-algo-blueprint-system-diff-engine:p1:inst-interactive


def _prompt_file(message: str, state: Dict[str, bool]) -> str:
    """Interactive prompt for file-level review: y/n/m/all."""
    if state.get("all"):
        return "y"
    sys.stderr.write(f"{message} [y/N/m(odify)/all] ")
    sys.stderr.flush()
    try:
        response = input().strip().lower()
    except EOFError:
        return "n"
    if response == "all":
        state["all"] = True
        return "y"
    if response in ("m", "modify"):
        return "m"
    return "y" if response == "y" else "n"


def _get_editor() -> str:
    """Return the user's preferred editor: $VISUAL → $EDITOR → vi."""
    return os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"


def _open_editor_for_file(
    rel_path: str,
    old_content: bytes,
    new_content: bytes,
) -> Optional[bytes]:
    """Open editor for manual file merge. Returns edited bytes or None if aborted."""
    try:
        old_text = old_content.decode("utf-8")
        new_text = new_content.decode("utf-8")
    except UnicodeDecodeError:
        sys.stderr.write("    (binary file — cannot edit)\n")
        return None

    diff = list(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"old/{rel_path}",
        tofile=f"new/{rel_path}",
        lineterm="",
    ))

    separator = "# ── edit below this line ──────────────────────────────────────"
    header_lines = [
        f"# cypilot diff: edit file [{rel_path}]",
        "#",
        "# Diff between old version (−) and new version (+):",
    ]
    if diff:
        header_lines.append("#")
        for d in diff:
            header_lines.append(f"#   {d.rstrip()}")
    else:
        header_lines.append("#   (no diff — versions are identical)")
    header_lines.extend([
        "#",
        "# Edit the content below the separator line.",
        "# To abort, delete all content below the separator and save.",
        separator,
    ])

    content = "\n".join(header_lines) + "\n" + new_text

    editor = _get_editor()
    suffix = Path(rel_path).suffix or ".md"
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix,
            prefix="cypilot-diff-",
            delete=False, encoding="utf-8",
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        import shlex
        cmd = shlex.split(editor)
        subprocess.check_call(cmd + [tmp_path])

        with open(tmp_path, encoding="utf-8") as f:
            edited = f.read()
    except FileNotFoundError:
        sys.stderr.write(f"    editor not found: {editor}\n")
        return None
    except Exception as exc:
        sys.stderr.write(f"    editor failed: {exc}\n")
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # Extract content after separator
    sep_idx = edited.find(separator)
    if sep_idx != -1:
        after_sep = edited[sep_idx + len(separator):]
        if after_sep.startswith("\n"):
            after_sep = after_sep[1:]
        result = after_sep
    else:
        lines = edited.splitlines(keepends=True)
        first_non_comment = 0
        for i, line in enumerate(lines):
            if not line.startswith("#"):
                first_non_comment = i
                break
        else:
            first_non_comment = len(lines)
        result = "".join(lines[first_non_comment:])

    if not result.strip():
        return None

    return result.encode("utf-8")

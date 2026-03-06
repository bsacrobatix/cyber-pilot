"""
Tests for kit file hash detection — SHA-256 based customization detection.

Covers: _compute_file_hash, _compute_kit_hashes, _read_blueprint_hashes,
_write_blueprint_hashes, hash integration in install_kit, migrate_kit, update_kit.
"""

import hashlib
import io
import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "cypilot" / "scripts"))

from cypilot.commands.kit import (
    _compute_file_hash,
    _compute_kit_hashes,
    _read_blueprint_hashes,
    _write_blueprint_hashes,
    _HASH_FILE,
    install_kit,
    migrate_kit,
    update_kit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kit_source(td: Path, slug: str = "testkit", version: int = 1) -> Path:
    """Create a minimal kit source directory with blueprints/ and conf.toml."""
    kit_src = td / slug
    bp = kit_src / "blueprints"
    bp.mkdir(parents=True)
    (bp / "feature.md").write_text(
        "`@cpt:blueprint`\n```toml\n"
        f'artifact = "FEATURE"\n'
        "```\n`@/cpt:blueprint`\n\n"
        "`@cpt:heading:spec`\n```toml\nid = \"spec\"\nlevel = 1\n"
        "template = \"Feature Spec\"\n```\n`@/cpt:heading:spec`\n",
        encoding="utf-8",
    )
    from cypilot.utils import toml_utils
    toml_utils.dump({"version": version, "slug": slug, "name": f"Test Kit {slug}"}, kit_src / "conf.toml")
    return kit_src


def _bootstrap_project(root: Path, adapter_rel: str = "cypilot") -> Path:
    """Set up a minimal initialized project for kit commands."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    (root / "AGENTS.md").write_text(
        f'<!-- @cpt:root-agents -->\n```toml\ncypilot_path = "{adapter_rel}"\n```\n<!-- /@cpt:root-agents -->\n',
        encoding="utf-8",
    )
    adapter = root / adapter_rel
    config = adapter / "config"
    gen = adapter / ".gen"
    for d in [adapter, config, gen, adapter / ".core"]:
        d.mkdir(parents=True, exist_ok=True)
    (config / "AGENTS.md").write_text("# Test\n", encoding="utf-8")
    from cypilot.utils import toml_utils
    toml_utils.dump({
        "version": "1.0",
        "project_root": "..",
        "system": {"name": "Test", "slug": "test", "kit": "cypilot-sdlc"},
        "kits": {},
    }, config / "core.toml")
    return adapter


# =========================================================================
# Unit tests: hash utility functions
# =========================================================================

class TestComputeFileHash(unittest.TestCase):
    """_compute_file_hash returns correct SHA-256 hex digest."""

    def test_known_content(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "test.md"
            content = b"hello world\n"
            p.write_bytes(content)
            expected = hashlib.sha256(content).hexdigest()
            self.assertEqual(_compute_file_hash(p), expected)

    def test_empty_file(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "empty.md"
            p.write_bytes(b"")
            expected = hashlib.sha256(b"").hexdigest()
            self.assertEqual(_compute_file_hash(p), expected)

    def test_large_file(self):
        """Hash should work for files larger than chunk size."""
        with TemporaryDirectory() as td:
            p = Path(td) / "large.md"
            content = b"x" * 100_000
            p.write_bytes(content)
            expected = hashlib.sha256(content).hexdigest()
            self.assertEqual(_compute_file_hash(p), expected)


class TestComputeKitHashes(unittest.TestCase):
    """_compute_kit_hashes scans blueprints/ and scripts/ in a kit directory."""

    def test_computes_blueprint_md_files(self):
        with TemporaryDirectory() as td:
            kit = Path(td) / "mykit"
            bp = kit / "blueprints"
            bp.mkdir(parents=True)
            (bp / "a.md").write_text("AAA", encoding="utf-8")
            (bp / "b.md").write_text("BBB", encoding="utf-8")
            (bp / "c.txt").write_text("CCC", encoding="utf-8")  # non-md, ignored
            hashes = _compute_kit_hashes(kit)
            self.assertIn("blueprints/a.md", hashes)
            self.assertIn("blueprints/b.md", hashes)
            self.assertNotIn("blueprints/c.txt", hashes)

    def test_computes_script_files(self):
        with TemporaryDirectory() as td:
            kit = Path(td) / "mykit"
            scripts = kit / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "run.py").write_text("print('hi')", encoding="utf-8")
            sub = scripts / "prompts"
            sub.mkdir()
            (sub / "review.md").write_text("review", encoding="utf-8")
            hashes = _compute_kit_hashes(kit)
            self.assertIn("scripts/run.py", hashes)
            self.assertIn("scripts/prompts/review.md", hashes)

    def test_skips_pycache(self):
        with TemporaryDirectory() as td:
            kit = Path(td) / "mykit"
            cache = kit / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "mod.cpython-313.pyc").write_bytes(b"\x00")
            hashes = _compute_kit_hashes(kit)
            self.assertEqual(hashes, {})

    def test_empty_kit(self):
        with TemporaryDirectory() as td:
            kit = Path(td) / "mykit"
            kit.mkdir()
            hashes = _compute_kit_hashes(kit)
            self.assertEqual(hashes, {})

    def test_nonexistent_directory(self):
        hashes = _compute_kit_hashes(Path("/nonexistent/dir"))
        self.assertEqual(hashes, {})

    def test_hashes_are_deterministic(self):
        with TemporaryDirectory() as td:
            kit = Path(td) / "mykit"
            bp = kit / "blueprints"
            bp.mkdir(parents=True)
            (bp / "test.md").write_text("content", encoding="utf-8")
            h1 = _compute_kit_hashes(kit)
            h2 = _compute_kit_hashes(kit)
            self.assertEqual(h1, h2)


class TestReadWriteBlueprintHashes(unittest.TestCase):
    """_read_blueprint_hashes / _write_blueprint_hashes round-trip (version-keyed)."""

    def test_round_trip(self):
        with TemporaryDirectory() as td:
            source = Path(td)
            hashes = {"blueprints/PRD.md": "abc123", "scripts/pr.py": "def456"}
            _write_blueprint_hashes(source, "1", hashes)
            result = _read_blueprint_hashes(source, "1")
            self.assertEqual(result, hashes)

    def test_read_nonexistent(self):
        with TemporaryDirectory() as td:
            result = _read_blueprint_hashes(Path(td), "1")
            self.assertEqual(result, {})

    def test_read_missing_version(self):
        with TemporaryDirectory() as td:
            source = Path(td)
            _write_blueprint_hashes(source, "1", {"blueprints/a.md": "abc"})
            result = _read_blueprint_hashes(source, "99")
            self.assertEqual(result, {})

    def test_hash_file_is_toml(self):
        with TemporaryDirectory() as td:
            source = Path(td)
            hashes = {"blueprints/test.md": "abc"}
            _write_blueprint_hashes(source, "1", hashes)
            hash_path = source / _HASH_FILE
            self.assertTrue(hash_path.is_file())
            content = hash_path.read_text(encoding="utf-8")
            self.assertIn("Auto-generated", content)
            self.assertIn("test.md", content)

    def test_write_preserves_other_versions(self):
        """Writing v2 hashes preserves existing v1 hashes."""
        with TemporaryDirectory() as td:
            source = Path(td)
            _write_blueprint_hashes(source, "1", {"blueprints/a.md": "old"})
            _write_blueprint_hashes(source, "2", {"blueprints/b.md": "new"})
            v1 = _read_blueprint_hashes(source, "1")
            v2 = _read_blueprint_hashes(source, "2")
            self.assertEqual(v1["blueprints/a.md"], "old")
            self.assertEqual(v2["blueprints/b.md"], "new")


# =========================================================================
# Integration: install_kit copies blueprints to kits/{slug}/
# =========================================================================

class TestInstallKitNewLayout(unittest.TestCase):
    """install_kit places blueprints in kits/{slug}/ and outputs in config/kits/{slug}/."""

    def test_install_copies_blueprints_to_kits_dir(self):
        with TemporaryDirectory() as td:
            td_p = Path(td)
            kit_src = _make_kit_source(td_p, "mykit")
            root = td_p / "project"
            adapter = _bootstrap_project(root)
            result = install_kit(kit_src, adapter, "mykit")
            self.assertIn(result["status"], ["PASS", "WARN"])
            # Blueprints should be in kits/{slug}/blueprints/
            user_bp = adapter / "kits" / "mykit" / "blueprints" / "feature.md"
            self.assertTrue(user_bp.is_file(), f"Missing {user_bp}")

    def test_install_no_hash_in_user_project(self):
        """Hash file should NOT be in user project (lives only in source)."""
        with TemporaryDirectory() as td:
            td_p = Path(td)
            kit_src = _make_kit_source(td_p, "mykit")
            root = td_p / "project"
            adapter = _bootstrap_project(root)
            install_kit(kit_src, adapter, "mykit")
            # No hash file in user project
            user_kit = adapter / "kits" / "mykit"
            config_kit = adapter / "config" / "kits" / "mykit"
            self.assertFalse((user_kit / _HASH_FILE).is_file())
            self.assertFalse((config_kit / _HASH_FILE).is_file())

    def test_source_hashes_match_installed_blueprints(self):
        """Source hash file (if present) should match installed user blueprints."""
        with TemporaryDirectory() as td:
            td_p = Path(td)
            kit_src = _make_kit_source(td_p, "mykit")
            # Write hash file to source (simulating pre-computed hashes)
            _write_blueprint_hashes(kit_src, "1", _compute_kit_hashes(kit_src))
            root = td_p / "project"
            adapter = _bootstrap_project(root)
            install_kit(kit_src, adapter, "mykit")
            # User blueprint hash should match source hash
            source_hashes = _read_blueprint_hashes(kit_src, "1")
            user_bp = adapter / "kits" / "mykit" / "blueprints" / "feature.md"
            actual = _compute_file_hash(user_bp)
            self.assertEqual(source_hashes.get("blueprints/feature.md"), actual)


# =========================================================================
# Integration: migrate_kit uses hash-based detection
# =========================================================================

class TestMigrateKitHashDetection(unittest.TestCase):
    """migrate_kit auto-updates unmodified blueprints, merges customized."""

    def _setup_installed_kit(self, td_p: Path, version: int = 1):
        """Install a kit and return (adapter, kit_src)."""
        kit_src = _make_kit_source(td_p, "testkit", version=version)
        # Write hash file to source
        _write_blueprint_hashes(kit_src, str(version), _compute_kit_hashes(kit_src))
        root = td_p / "project"
        adapter = _bootstrap_project(root)
        install_kit(kit_src, adapter, "testkit")
        return adapter, kit_src

    def _make_v2_source(self, td_p: Path) -> Path:
        """Create a v2 kit source with different content."""
        kit_src_v2 = td_p / "testkit_v2"
        bp = kit_src_v2 / "blueprints"
        bp.mkdir(parents=True)
        new_content = (
            "`@cpt:blueprint`\n```toml\n"
            'artifact = "FEATURE"\n'
            "```\n`@/cpt:blueprint`\n\n"
            "`@cpt:heading:spec`\n```toml\nid = \"spec\"\nlevel = 1\n"
            "template = \"Updated Feature Spec\"\n```\n`@/cpt:heading:spec`\n"
        )
        (bp / "feature.md").write_text(new_content, encoding="utf-8")
        from cypilot.utils import toml_utils
        toml_utils.dump({"version": 2, "slug": "testkit", "name": "Test Kit testkit"}, kit_src_v2 / "conf.toml")
        _write_blueprint_hashes(kit_src_v2, "2", _compute_kit_hashes(kit_src_v2))
        # Also include v1 hashes so migrate can check user's installed version
        return kit_src_v2

    def test_unmodified_blueprint_auto_updated(self):
        """Blueprint with matching hash is auto-updated without three-way merge."""
        with TemporaryDirectory() as td:
            td_p = Path(td)
            adapter, kit_src_v1 = self._setup_installed_kit(td_p, version=1)
            user_kit_dir = adapter / "kits" / "testkit"

            # Create v2 source with v1 hashes included
            kit_src_v2 = self._make_v2_source(td_p)
            # Copy v1 hashes into v2 source so migrate can compare
            v1_hashes = _read_blueprint_hashes(kit_src_v1, "1")
            _write_blueprint_hashes(kit_src_v2, "1", v1_hashes)

            result = migrate_kit("testkit", kit_src_v2, user_kit_dir, interactive=False)

            self.assertEqual(result["status"], "migrated")
            bp_results = result.get("blueprints", [])
            self.assertTrue(any(r.get("action") == "auto_updated" for r in bp_results))

            # User blueprint should have new content
            user_bp = user_kit_dir / "blueprints" / "feature.md"
            self.assertIn("Updated Feature Spec", user_bp.read_text(encoding="utf-8"))

    def test_customized_blueprint_not_auto_updated(self):
        """Blueprint with modified hash goes through three-way merge."""
        with TemporaryDirectory() as td:
            td_p = Path(td)
            adapter, kit_src_v1 = self._setup_installed_kit(td_p, version=1)
            user_kit_dir = adapter / "kits" / "testkit"

            # Customize user blueprint
            user_bp = user_kit_dir / "blueprints" / "feature.md"
            original = user_bp.read_text(encoding="utf-8")
            user_bp.write_text(original + "\n<!-- user customization -->\n", encoding="utf-8")

            kit_src_v2 = self._make_v2_source(td_p)
            v1_hashes = _read_blueprint_hashes(kit_src_v1, "1")
            _write_blueprint_hashes(kit_src_v2, "1", v1_hashes)

            result = migrate_kit("testkit", kit_src_v2, user_kit_dir, interactive=False)

            bp_results = result.get("blueprints", [])
            for r in bp_results:
                self.assertNotEqual(r.get("action"), "auto_updated",
                                    "Customized blueprint should not be auto-updated")

    def test_no_hash_record_falls_through_to_merge(self):
        """When no hash file exists in source, all blueprints go through merge."""
        with TemporaryDirectory() as td:
            td_p = Path(td)
            adapter, kit_src_v1 = self._setup_installed_kit(td_p, version=1)
            user_kit_dir = adapter / "kits" / "testkit"

            kit_src_v2 = self._make_v2_source(td_p)
            # Remove hash file from v2 source
            hash_path = kit_src_v2 / _HASH_FILE
            if hash_path.is_file():
                hash_path.unlink()

            result = migrate_kit("testkit", kit_src_v2, user_kit_dir, interactive=False)

            bp_results = result.get("blueprints", [])
            for r in bp_results:
                self.assertNotEqual(r.get("action"), "auto_updated")


# =========================================================================
# Integration: update_kit first-install
# =========================================================================

class TestUpdateKitFirstInstall(unittest.TestCase):
    """update_kit first install copies blueprints to kits/{slug}/."""

    def test_first_install_creates_user_blueprints(self):
        with TemporaryDirectory() as td:
            td_p = Path(td)
            kit_src = _make_kit_source(td_p, "testkit", version=1)
            root = td_p / "project"
            adapter = _bootstrap_project(root)

            result = update_kit("testkit", kit_src, adapter)
            self.assertEqual(result.get("version", {}).get("status"), "created")

            # Blueprints in kits/{slug}/blueprints/
            user_bp = adapter / "kits" / "testkit" / "blueprints" / "feature.md"
            self.assertTrue(user_bp.is_file())


if __name__ == "__main__":
    unittest.main()

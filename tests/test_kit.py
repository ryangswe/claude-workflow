"""Tests for the workflow kit: init, status, sync, the workflow check and the branch guard.

    python3 -m unittest discover -s tests

Standard library only. Each test works in a fresh temporary git repository.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "workflow"


def run(*args: str, cwd: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=cwd, input=stdin, capture_output=True, text=True)


class Repo:
    """A temporary git repository, and optionally a private copy of the plugin to modify."""

    def __init__(self, own_kit: bool = False) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "repo"
        self.path.mkdir()
        run("git", "init", "-q", "-b", "main", cwd=self.path)
        run("git", "config", "user.email", "t@example.com", cwd=self.path)
        run("git", "config", "user.name", "t", cwd=self.path)
        self.plugin = PLUGIN
        if own_kit:
            self.plugin = self.tmp / "plugin"
            shutil.copytree(PLUGIN, self.plugin)

    def kit(self, *args: str) -> subprocess.CompletedProcess:
        return run(sys.executable, str(self.plugin / "bin" / "workflow-kit"), *args,
                   "--target", str(self.path))

    def check(self) -> subprocess.CompletedProcess:
        return run(sys.executable, str(self.path / ".claude/workflow/check.py"),
                   "--root", str(self.path))

    def close(self) -> None:
        shutil.rmtree(self.tmp)


class InitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()

    def tearDown(self) -> None:
        self.repo.close()

    def test_light_writes_its_files_and_passes_the_check(self) -> None:
        out = self.repo.kit("init", "--level", "light")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        p = self.repo.path
        for f in ("CLAUDE.md", ".claude/agents/reviewer.md", ".claude/agents/explore.md",
                  ".claude/skills/checkpoint/SKILL.md", ".claude/workflow/check.py",
                  ".claude/hooks/guard-default-branch.py", "docs/decisions.md"):
            self.assertTrue((p / f).exists(), f)
        self.assertFalse((p / ".claude/agents/planner.md").exists())
        self.assertNotIn("{{TODAY}}", (p / "docs/decisions.md").read_text())
        lock = json.loads((p / ".claude/workflow.lock.json").read_text())
        self.assertEqual(lock["level"], "light")
        self.assertIn(".claude/agents/reviewer.md", lock["files"])
        self.assertNotIn("CLAUDE.md", lock["files"])  # seeded files are not tracked
        check = self.repo.check()
        self.assertEqual(check.returncode, 0, check.stdout)

    def test_full_adds_orchestration_and_passes_the_check(self) -> None:
        self.assertEqual(self.repo.kit("init", "--level", "full").returncode, 0)
        p = self.repo.path
        for f in (".claude/agents/planner.md", ".claude/agents/architect.md",
                  ".claude/agents/executor.md", ".claude/skills/parallel-work/SKILL.md",
                  "docs/plans/README.md", "docs/plans/_template.md"):
            self.assertTrue((p / f).exists(), f)
        self.assertIn("Orchestration and delegation", (p / "CLAUDE.md").read_text())
        check = self.repo.check()
        self.assertEqual(check.returncode, 0, check.stdout)

    def test_existing_claude_md_and_settings_are_kept(self) -> None:
        p = self.repo.path
        (p / "CLAUDE.md").write_text("# Mine\n")
        (p / ".claude").mkdir()
        (p / ".claude/settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
        self.assertEqual(self.repo.kit("init", "--level", "light").returncode, 0)
        self.assertEqual((p / "CLAUDE.md").read_text(), "# Mine\n")
        settings = json.loads((p / ".claude/settings.json").read_text())
        self.assertEqual(settings["permissions"]["allow"], ["Bash(ls)"])
        self.assertIn("guard-default-branch.py", json.dumps(settings["hooks"]))
        self.repo.kit("init", "--level", "light", "--force")  # wiring the hook twice adds nothing
        settings = json.loads((p / ".claude/settings.json").read_text())
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 1)

    def test_an_existing_different_managed_file_is_a_conflict(self) -> None:
        p = self.repo.path
        (p / ".claude/agents").mkdir(parents=True)
        (p / ".claude/agents/reviewer.md").write_text("mine\n")
        out = self.repo.kit("init", "--level", "light")
        self.assertEqual(out.returncode, 1)
        self.assertIn("CONFLICT .claude/agents/reviewer.md", out.stdout)
        self.assertEqual((p / ".claude/agents/reviewer.md").read_text(), "mine\n")

    def test_invalid_settings_stop_init_before_anything_is_written(self) -> None:
        p = self.repo.path
        (p / ".claude").mkdir()
        (p / ".claude/settings.json").write_text("{not json")
        out = self.repo.kit("init", "--level", "light")
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse((p / "CLAUDE.md").exists())
        self.assertFalse((p / ".claude/workflow.lock.json").exists())

    def test_init_twice_is_refused(self) -> None:
        self.repo.kit("init", "--level", "light")
        self.assertEqual(self.repo.kit("init", "--level", "light").returncode, 1)


class SyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo(own_kit=True)
        self.repo.kit("init", "--level", "light")
        self.kit_files = self.repo.plugin / "kit" / "files"

    def tearDown(self) -> None:
        self.repo.close()

    def status(self) -> dict[str, str]:
        lines = self.repo.kit("status").stdout.splitlines()[1:]
        return {line[14:]: line[:13].strip() for line in lines}

    def test_fresh_install_is_current(self) -> None:
        self.assertEqual(set(self.status().values()), {"current"})

    def test_kit_update_is_applied_to_an_unedited_file(self) -> None:
        f = ".claude/agents/reviewer.md"
        with open(self.kit_files / f, "a") as fh:
            fh.write("\nNew kit rule.\n")
        self.assertEqual(self.status()[f], "kit updated")
        self.repo.kit("sync")  # dry run writes nothing
        self.assertNotIn("New kit rule.", (self.repo.path / f).read_text())
        self.repo.kit("sync", "--apply")
        self.assertIn("New kit rule.", (self.repo.path / f).read_text())
        self.assertEqual(self.status()[f], "current")

    def test_local_edit_is_kept_and_a_double_change_is_only_reported(self) -> None:
        f = ".claude/agents/reviewer.md"
        with open(self.repo.path / f, "a") as fh:
            fh.write("\nLocal rule.\n")
        self.assertEqual(self.status()[f], "edited here")
        with open(self.kit_files / f, "a") as fh:
            fh.write("\nNew kit rule.\n")
        self.assertEqual(self.status()[f], "both changed")
        out = self.repo.kit("sync", "--apply")
        self.assertIn("REVIEW", out.stdout)
        text = (self.repo.path / f).read_text()
        self.assertIn("Local rule.", text)
        self.assertNotIn("New kit rule.", text)

    def test_a_hand_merged_double_change_is_cleared_by_resolve(self) -> None:
        f = ".claude/agents/reviewer.md"
        with open(self.repo.path / f, "a") as fh:
            fh.write("\nLocal rule.\n")
        with open(self.kit_files / f, "a") as fh:
            fh.write("\nNew kit rule.\n")
        with open(self.repo.path / f, "a") as fh:  # the hand merge keeps both
            fh.write("\nNew kit rule.\n")
        self.assertEqual(self.status()[f], "both changed")
        self.assertEqual(self.repo.kit("resolve", f).returncode, 0)
        self.assertEqual(self.status()[f], "edited here")
        self.assertEqual(self.repo.kit("sync", "--apply").returncode, 0)

    def test_an_init_conflict_is_cleared_by_resolve(self) -> None:
        repo = Repo(own_kit=True)
        try:
            (repo.path / ".claude/agents").mkdir(parents=True)
            (repo.path / ".claude/agents/reviewer.md").write_text("mine\n")
            repo.kit("init", "--level", "light")
            out = repo.kit("sync", "--apply")
            self.assertEqual(out.returncode, 1)
            self.assertIn("REVIEW", out.stdout)
            repo.kit("resolve", ".claude/agents/reviewer.md")
            self.assertEqual(repo.kit("sync", "--apply").returncode, 0)
            self.assertEqual((repo.path / ".claude/agents/reviewer.md").read_text(), "mine\n")
        finally:
            repo.close()

    def test_taking_the_kits_copy_by_hand_refreshes_the_lock(self) -> None:
        f = ".claude/agents/reviewer.md"
        with open(self.repo.path / f, "a") as fh:
            fh.write("\nLocal rule.\n")
        with open(self.kit_files / f, "a") as fh:
            fh.write("\nNew kit rule.\n")
        shutil.copy(self.kit_files / f, self.repo.path / f)
        self.repo.kit("sync", "--apply")
        with open(self.repo.path / f, "a") as fh:
            fh.write("\nAnother local rule.\n")
        self.assertEqual(self.status()[f], "edited here")

    def test_deleted_seeded_files_stay_deleted_and_excluded_files_are_left_alone(self) -> None:
        (self.repo.path / "docs/decisions/process.md").unlink()
        (self.repo.path / ".claude/agents/explore.md").unlink()
        self.repo.kit("exclude", ".claude/agents/explore.md")
        self.repo.kit("sync", "--apply")
        self.assertFalse((self.repo.path / "docs/decisions/process.md").exists())
        self.assertFalse((self.repo.path / ".claude/agents/explore.md").exists())
        self.assertEqual(self.status()[".claude/agents/explore.md"], "excluded")

    def test_a_deleted_managed_file_is_restored(self) -> None:
        (self.repo.path / ".claude/agents/explore.md").unlink()
        self.assertEqual(self.status()[".claude/agents/explore.md"], "missing")
        self.repo.kit("sync", "--apply")
        self.assertTrue((self.repo.path / ".claude/agents/explore.md").exists())

    def test_crlf_checkouts_are_not_edits(self) -> None:
        f = self.repo.path / ".claude/agents/reviewer.md"
        f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.status()[".claude/agents/reviewer.md"], "current")

    def test_executable_bit_is_kept_on_update(self) -> None:
        f = ".claude/hooks/guard-default-branch.py"
        with open(self.kit_files / f, "a") as fh:
            fh.write("\n# update\n")
        self.repo.kit("sync", "--apply")
        self.assertTrue(os.access(self.repo.path / f, os.X_OK))

    def test_a_level_downgrade_is_refused(self) -> None:
        self.repo.kit("sync", "--level", "full", "--apply")
        self.assertNotEqual(self.repo.kit("sync", "--level", "light").returncode, 0)

    def test_moving_up_to_full_adds_its_files_but_keeps_claude_md(self) -> None:
        before = (self.repo.path / "CLAUDE.md").read_text()
        out = self.repo.kit("sync", "--level", "full", "--apply")
        self.assertIn("NOTE", out.stdout)
        self.assertTrue((self.repo.path / ".claude/agents/planner.md").exists())
        self.assertTrue((self.repo.path / "docs/plans/README.md").exists())
        self.assertEqual((self.repo.path / "CLAUDE.md").read_text(), before)
        lock = json.loads((self.repo.path / ".claude/workflow.lock.json").read_text())
        self.assertEqual(lock["level"], "full")


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()
        self.repo.kit("init", "--level", "full")
        self.p = self.repo.path

    def tearDown(self) -> None:
        self.repo.close()

    def failures(self) -> str:
        out = self.repo.check()
        self.assertEqual(out.returncode, 1, "expected the check to fail")
        return out.stdout

    def test_oversized_claude_md_fails(self) -> None:
        (self.p / "CLAUDE.md").write_text("x" * 15000)
        self.assertIn("CLAUDE.md", self.failures())

    def test_a_skill_nothing_names_fails(self) -> None:
        (self.p / ".claude/skills/ghost").mkdir()
        (self.p / ".claude/skills/ghost/SKILL.md").write_text("---\nname: ghost\ndescription: x\n---\n")
        self.assertIn("ghost: not named", self.failures())

    def test_an_entry_without_a_status_fails(self) -> None:
        f = self.p / "docs/decisions/process.md"
        f.write_text(f.read_text().replace("**Status:** live, indexed\n", ""))
        self.assertIn("valid `**Status:**`", self.failures())

    def test_an_index_line_without_an_entry_fails(self) -> None:
        f = self.p / "docs/decisions.md"
        f.write_text(f.read_text() + "- 2020-01-01 — A decision with no entry\n")
        self.assertIn("index line 2020-01-01", self.failures())

    def test_a_closed_plan_in_the_active_list_fails(self) -> None:
        (self.p / "docs/plans/2020-01-01-x.md").write_text("# X\n\n- **Status:** Done\n")
        readme = self.p / "docs/plans/README.md"
        readme.write_text(readme.read_text().replace(
            "## Lifecycle", "- `2020-01-01-x.md` — x\n\n## Lifecycle"))
        self.assertIn("listed as active", self.failures())

    def test_a_malformed_index_line_fails(self) -> None:
        f = self.p / "docs/decisions.md"
        f.write_text(f.read_text() + "- 2020-01-01 - hyphen instead of a dash\n")
        self.assertIn("not in `- YYYY-MM-DD", self.failures())

    def test_a_theme_file_nothing_names_fails(self) -> None:
        (self.p / "docs/decisions/orphan.md").write_text("# Orphan\n")
        self.assertIn("orphan.md is not named", self.failures())

    def test_an_open_plan_missing_from_the_list_fails(self) -> None:
        (self.p / "docs/plans/2020-01-01-x.md").write_text("# X\n\n- **Status:** Draft\n")
        self.assertIn("missing from the active list", self.failures())

    def test_a_claim_under_another_dates_line_passes(self) -> None:
        f = self.p / "docs/decisions/process.md"
        f.write_text(f.read_text() + "\n---\n\n## 2019-01-01 — Older\n\n"
                     "**Status:** live, indexed — under the " + self.today() + " line\n")
        self.assertEqual(self.repo.check().returncode, 0, self.repo.check().stdout)

    def today(self) -> str:
        import re
        return re.search(r"^- (\d{4}-\d{2}-\d{2}) —", (self.p / "docs/decisions.md").read_text(),
                         re.M).group(1)

    def test_skill_header_variants_pass(self) -> None:
        f = self.p / ".claude/skills/checkpoint/SKILL.md"
        text = f.read_text().replace("name: checkpoint", 'name: "checkpoint"')
        f.write_bytes(("\ufeff" + text).replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(self.repo.check().returncode, 0, self.repo.check().stdout)

    def test_a_bad_rule_header_fails(self) -> None:
        (self.p / ".claude/rules").mkdir()
        (self.p / ".claude/rules/x.md").write_text("---\npaths:\n  - src/**\n---\n")
        self.assertIn("rules: x.md", self.failures())


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()
        self.repo.kit("init", "--level", "light")
        self.p = self.repo.path
        self.guard = self.p / ".claude/hooks/guard-default-branch.py"

    def tearDown(self) -> None:
        self.repo.close()

    def edit(self, relative: str) -> int:
        payload = json.dumps({"tool_input": {"file_path": str(self.p / relative)}})
        return run(sys.executable, str(self.guard), stdin=payload).returncode

    def test_blocks_edits_on_main_and_allows_them_on_a_branch(self) -> None:
        run("git", "commit", "-q", "--allow-empty", "-m", "init", cwd=self.p)
        self.assertEqual(self.edit("src/a.py"), 2)
        run("git", "checkout", "-q", "-b", "feature", cwd=self.p)
        self.assertEqual(self.edit("src/a.py"), 0)

    def test_notebook_edits_on_main_are_blocked(self) -> None:
        run("git", "commit", "-q", "--allow-empty", "-m", "init", cwd=self.p)
        payload = json.dumps({"tool_input": {"notebook_path": str(self.p / "n.ipynb")}})
        self.assertEqual(run(sys.executable, str(self.guard), stdin=payload).returncode, 2)

    def test_garbage_input_fails_open(self) -> None:
        self.assertEqual(run(sys.executable, str(self.guard), stdin="not json").returncode, 0)

    def test_exempt_directories_and_outside_files_pass(self) -> None:
        run("git", "commit", "-q", "--allow-empty", "-m", "init", cwd=self.p)
        cfg = self.p / ".claude/workflow.json"
        cfg.write_text(json.dumps({**json.loads(cfg.read_text()), "branch_guard_exempt": ["scratch"]}))
        self.assertEqual(self.edit("scratch/notes.md"), 0)
        payload = json.dumps({"tool_input": {"file_path": "/tmp/elsewhere.txt"}})
        self.assertEqual(run(sys.executable, str(self.guard), stdin=payload).returncode, 0)


class KitConsistencyTests(unittest.TestCase):
    def test_manifest_lists_every_kit_file_and_nothing_else(self) -> None:
        kit = PLUGIN / "kit"
        entries = json.loads((kit / "manifest.json").read_text())["files"]
        listed = {("seeds" if e["mode"] == "seeded" else "files") + "/" + e["source"] for e in entries}
        on_disk = {str(p.relative_to(kit)) for p in kit.rglob("*")
                   if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts}
        self.assertEqual(listed, on_disk)

    def test_every_agent_and_skill_a_skeleton_names_is_installed_at_its_level(self) -> None:
        import re
        entries = json.loads((PLUGIN / "kit/manifest.json").read_text())["files"]
        for level in ("light", "full"):
            text = (PLUGIN / f"kit/seeds/CLAUDE.{level}.md").read_text()
            installed = {Path(e["target"]).stem if "/agents/" in e["target"] else Path(e["target"]).parent.name
                         for e in entries if level in e["levels"]
                         and ("/agents/" in e["target"] or "/skills/" in e["target"])}
            known = {"Explore" if n == "explore" else n for n in installed}
            all_names = {"planner", "architect", "executor", "reviewer", "Explore",
                         "checkpoint", "parallel-work"}
            for name in all_names:
                if f"`{name}`" in text:
                    self.assertIn(name, known, f"CLAUDE.{level}.md names `{name}`")


if __name__ == "__main__":
    unittest.main()

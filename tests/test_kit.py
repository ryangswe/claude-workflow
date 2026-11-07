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
        for f in ("CLAUDE.md", ".claude/agents/reviewer.md", ".claude/review-checklists.md",
                  ".claude/agents/explore.md", ".claude/skills/checkpoint/SKILL.md", ".claude/skills/handoff/SKILL.md",
                  ".claude/workflow/check.py",
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

    def test_sync_widens_an_old_guard_matcher_to_bash(self) -> None:
        path = self.repo.path / ".claude/settings.json"
        settings = json.loads(path.read_text())
        settings["hooks"]["PreToolUse"][0]["matcher"] = "Edit|Write|NotebookEdit"
        path.write_text(json.dumps(settings))
        self.assertIn("would merge", self.repo.kit("sync").stdout)
        self.assertEqual(json.loads(path.read_text())["hooks"]["PreToolUse"][0]["matcher"],
                         "Edit|Write|NotebookEdit")  # the dry run wrote nothing
        self.assertEqual(self.repo.kit("sync", "--apply").returncode, 0)
        self.assertEqual(json.loads(path.read_text())["hooks"]["PreToolUse"][0]["matcher"],
                         "Edit|Write|NotebookEdit|Bash")

    def test_a_hand_changed_guard_matcher_is_reported_not_rewritten(self) -> None:
        path = self.repo.path / ".claude/settings.json"
        settings = json.loads(path.read_text())
        settings["hooks"]["PreToolUse"][0]["matcher"] = "Edit|MultiEdit"
        path.write_text(json.dumps(settings))
        self.assertIn("REVIEW    .claude/settings.json", self.repo.kit("sync", "--apply").stdout)
        self.assertEqual(json.loads(path.read_text())["hooks"]["PreToolUse"][0]["matcher"],
                         "Edit|MultiEdit")

    def deny(self) -> list:
        settings = json.loads((self.repo.path / ".claude/settings.json").read_text())
        return settings.get("permissions", {}).get("deny", [])

    def test_init_adds_the_deny_baseline(self) -> None:
        self.assertIn("Bash(git push --force *)", self.deny())
        self.assertIn("Read(**/.env)", self.deny())

    def test_sync_adds_missing_deny_rules_once_and_keeps_the_repos_own(self) -> None:
        path = self.repo.path / ".claude/settings.json"
        settings = json.loads(path.read_text())
        settings["permissions"]["deny"] = ["Bash(curl *)", "Bash(sudo *)"]
        path.write_text(json.dumps(settings))
        self.assertIn("would merge .claude/settings.json (deny rules added",
                      self.repo.kit("sync").stdout)
        self.repo.kit("sync", "--apply")
        deny = self.deny()
        self.assertEqual(deny[:2], ["Bash(curl *)", "Bash(sudo *)"])
        self.assertEqual(deny.count("Bash(sudo *)"), 1)
        self.assertIn("Bash(git push -f *)", deny)
        self.repo.kit("sync", "--apply")
        self.assertEqual(self.deny(), deny)  # a second sync changes nothing

    def test_an_opted_out_deny_rule_is_not_restored(self) -> None:
        path = self.repo.path / ".claude/settings.json"
        settings = json.loads(path.read_text())
        settings["permissions"]["deny"].remove("Read(**/.env)")
        path.write_text(json.dumps(settings))
        cfg = self.repo.path / ".claude/workflow.json"
        cfg.write_text(json.dumps({**json.loads(cfg.read_text()),
                                   "permissions_deny_opt_out": ["Read(**/.env)"]}))
        self.repo.kit("sync", "--apply")
        self.assertNotIn("Read(**/.env)", self.deny())
        self.assertIn("Read(**/.env.*)", self.deny())

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

    def bash(self, command: str, cwd: Path | None = None) -> int:
        payload = json.dumps({"tool_name": "Bash", "cwd": str(cwd or self.p),
                              "tool_input": {"command": command}})
        return run(sys.executable, str(self.guard), stdin=payload).returncode

    def test_shell_commits_on_main_are_blocked_and_allowed_on_a_branch(self) -> None:
        run("git", "commit", "-q", "--allow-empty", "-m", "init", cwd=self.p)
        for command in ('git commit -m "x"', "git add a.py && git commit -qm x",
                        "GIT_AUTHOR_NAME=me git commit -m x", f"git -C {self.p} commit -m x",
                        f"cd {self.p} && git commit -am x"):
            self.assertEqual(self.bash(command), 2, command)
        run("git", "checkout", "-q", "-b", "feature", cwd=self.p)
        self.assertEqual(self.bash('git commit -m "x"'), 0)

    def test_pushes_that_update_the_default_branch_are_blocked(self) -> None:
        run("git", "commit", "-q", "--allow-empty", "-m", "init", cwd=self.p)
        self.assertEqual(self.bash("git push"), 2)            # bare push from main
        self.assertEqual(self.bash("git push origin main"), 2)
        run("git", "checkout", "-q", "-b", "feature", cwd=self.p)
        self.assertEqual(self.bash("git push -u origin feature"), 0)
        self.assertEqual(self.bash("git push"), 0)            # bare push from a branch
        for command in ("git push origin HEAD:main", "git push origin +feature:refs/heads/master",
                        "git push --force origin main"):
            self.assertEqual(self.bash(command), 2, command)

    def test_other_shell_commands_and_other_repos_pass(self) -> None:
        run("git", "commit", "-q", "--allow-empty", "-m", "init", cwd=self.p)
        for command in ("npm test", "git status && git log -1", "echo 'git commit' > notes.txt",
                        "git commit-tree HEAD^{tree} -m x", "git push --tags"):
            self.assertEqual(self.bash(command), 0, command)
        other = Path(tempfile.mkdtemp())
        try:
            run("git", "init", "-q", "-b", "main", cwd=other)
            self.assertEqual(self.bash("git commit -m x", cwd=other), 0)
            self.assertEqual(self.bash(f"git -C {other} commit -m x"), 0)
        finally:
            shutil.rmtree(other)

    def test_unparseable_commands_fail_open(self) -> None:
        run("git", "commit", "-q", "--allow-empty", "-m", "init", cwd=self.p)
        self.assertEqual(self.bash("git commit -m 'unterminated"), 0)


class GcTests(unittest.TestCase):
    """gc.py on a repository with one branch of every kind."""

    def setUp(self) -> None:
        self.repo = Repo()
        self.repo.kit("init", "--level", "light")
        self.p = self.repo.path
        self.gc = self.p / ".claude/workflow/gc.py"
        g = lambda *a, **kw: run("git", *a, cwd=self.p, **kw)  # noqa: E731
        (self.p / "a.txt").write_text("a\n")
        g("add", "-A")
        g("commit", "-qm", "init")
        # merged: fast-forwarded into main
        g("checkout", "-qb", "merged")
        (self.p / "m.txt").write_text("m\n")
        g("add", "m.txt")
        g("commit", "-qm", "merged work")
        g("checkout", "-q", "main")
        g("merge", "-q", "--ff-only", "merged")
        # squashed: its content landed on main as a different commit
        g("checkout", "-qb", "squashed")
        (self.p / "s.txt").write_text("s\n")
        g("add", "s.txt")
        g("commit", "-qm", "squash me 1")
        (self.p / "s.txt").write_text("s2\n")
        g("commit", "-qam", "squash me 2")
        g("checkout", "-q", "main")
        g("merge", "-q", "--squash", "squashed")
        g("commit", "-qm", "squashed (#1)")
        # stale: unmerged, last commit 30 days ago
        g("checkout", "-qb", "stale")
        (self.p / "old.txt").write_text("old\n")
        g("add", "old.txt")
        old = "2020-01-01T00:00:00"
        subprocess.run(["git", "commit", "-qm", "old idea"], cwd=self.p, check=True,
                       env={**os.environ, "GIT_AUTHOR_DATE": old, "GIT_COMMITTER_DATE": old})
        g("checkout", "-q", "main")
        # active: unmerged, recent
        g("branch", "active")
        g("checkout", "-q", "active")
        (self.p / "new.txt").write_text("new\n")
        g("add", "new.txt")
        g("commit", "-qm", "new idea")
        g("checkout", "-q", "main")
        # worktrees: a clean merged one and a merged one with uncommitted work
        g("branch", "wt-clean", "merged")
        g("branch", "wt-dirty", "merged")
        g("worktree", "add", "-q", ".claude/worktrees/clean", "wt-clean")
        g("worktree", "add", "-q", ".claude/worktrees/dirty", "wt-dirty")
        (self.p / ".claude/worktrees/dirty/scratch.txt").write_text("unsaved\n")

    def tearDown(self) -> None:
        self.repo.close()

    def gc_run(self, *args: str) -> subprocess.CompletedProcess:
        return run(sys.executable, str(self.gc), "--root", str(self.p), *args)

    def lines(self, out: str) -> dict[str, str]:
        rows = {}
        for line in out.splitlines():
            if line.startswith(("safe ", "keep ")):
                parts = line.split()
                cls = "IN DEFAULT" if parts[1] == "IN" else parts[1]
                branch = parts[4] if cls == "IN DEFAULT" else parts[3]
                rows[branch] = f"{parts[0]} {cls}"
        return rows

    def branches(self) -> set[str]:
        out = run("git", "branch", "--format=%(refname:short)", cwd=self.p).stdout
        return set(out.split())

    def test_report_classifies_every_branch_and_changes_nothing(self) -> None:
        before = self.branches()
        result = self.gc_run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines(result.stdout), {
            "merged": "safe MERGED", "wt-clean": "safe MERGED", "squashed": "safe IN DEFAULT",
            "wt-dirty": "keep MERGED", "stale": "keep STALE", "active": "keep ACTIVE"})
        self.assertIn("uncommitted changes", result.stdout)
        self.assertEqual(self.branches(), before)

    def test_apply_removes_only_the_safe_ones(self) -> None:
        result = self.gc_run("--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.branches(), {"main", "wt-dirty", "stale", "active"})
        self.assertFalse((self.p / ".claude/worktrees/clean").exists())
        self.assertTrue((self.p / ".claude/worktrees/dirty/scratch.txt").exists())

    def test_a_branch_merged_only_on_origin_counts_as_merged(self) -> None:
        g = lambda *a: run("git", *a, cwd=self.p)  # noqa: E731
        g("branch", "late", "active")
        g("update-ref", "refs/remotes/origin/main", "main")
        # origin/main moves on (the PR merged on GitHub); the local main does not
        g("checkout", "-q", "--detach", "main")
        g("merge", "-q", "--no-edit", "late")
        g("update-ref", "refs/remotes/origin/main", "HEAD")
        g("checkout", "-q", "main")
        out = self.gc_run().stdout
        self.assertIn("default branch: origin/main", out)
        self.assertEqual(self.lines(out)["late"], "safe MERGED")
        self.gc_run("--apply")
        self.assertNotIn("late", self.branches())

    def test_apply_refuses_when_no_worktree_folder_is_visible(self) -> None:
        shutil.rmtree(self.p / ".claude/worktrees")
        before = self.branches()
        result = self.gc_run("--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("can't see the paths", result.stderr)
        self.assertEqual(self.branches(), before)
        self.assertIn("worktrees/clean", run("git", "worktree", "list", cwd=self.p).stdout)

    def test_a_worktree_whose_folder_is_gone_is_pruned(self) -> None:
        shutil.rmtree(self.p / ".claude/worktrees/clean")
        self.gc_run("--apply")
        listed = run("git", "worktree", "list", cwd=self.p).stdout
        self.assertNotIn("worktrees/clean", listed)
        self.assertNotIn("wt-clean", self.branches())


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
                         "checkpoint", "handoff", "parallel-work"}
            for name in all_names:
                if f"`{name}`" in text:
                    self.assertIn(name, known, f"CLAUDE.{level}.md names `{name}`")

    def test_a_kit_file_named_in_another_is_installed_at_its_levels(self) -> None:
        import re
        entries = json.loads((PLUGIN / "kit/manifest.json").read_text())["files"]
        levels_of = {e["target"]: set(e["levels"]) for e in entries if e["mode"] == "managed"}
        for e in entries:
            folder = "seeds" if e["mode"] == "seeded" else "files"
            text = (PLUGIN / "kit" / folder / e["source"]).read_text()
            for ref in set(re.findall(r"`(\.claude/[\w./-]+)`", text)) & levels_of.keys():
                missing = set(e["levels"]) - levels_of[ref]
                self.assertFalse(missing, f"{e['source']} points to {ref}, which is not installed at {missing}")

    def test_every_skill_installed_at_a_level_is_named_by_its_skeleton(self) -> None:
        entries = json.loads((PLUGIN / "kit/manifest.json").read_text())["files"]
        for level in ("light", "full"):
            text = (PLUGIN / f"kit/seeds/CLAUDE.{level}.md").read_text()
            for e in entries:
                if level in e["levels"] and "/skills/" in e["target"]:
                    name = Path(e["target"]).parent.name
                    self.assertIn(f"`{name}`", text, f"CLAUDE.{level}.md must name `{name}`")


if __name__ == "__main__":
    unittest.main()

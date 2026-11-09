"""Tests for tools/review-eval/review_eval.py: python3 -m unittest discover -s tests"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools/review-eval"
spec = importlib.util.spec_from_file_location("review_eval", TOOL / "review_eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
       # The machine's git config (signing, hooks) must not reach the fixtures.
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args: str, date: str | None = None) -> str:
    env = dict(ENV)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


class Fixture(unittest.TestCase):
    """main: base -> PR #2 (merge, adds a bug on line 3 of app.py) -> fix commit (#3) -> later work."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        old = "2020-01-01T00:00:00Z"
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "app.py").write_text("def f(x):\n    return x\n")
        (self.repo / "old.py").write_text("legacy = 1\n")
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude/review-checklists.md").write_text("old checklists of the time\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base", date=old)
        git(self.repo, "checkout", "-qb", "feature")
        (self.repo / "app.py").write_text("def f(x):\n    y = x\n    return y / 0  # the bug\n")
        (self.repo / "util.py").write_text("ok = True\n")
        (self.repo / "old.py").unlink()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "add division", date=old)
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "merge", "-q", "--no-ff", "feature", "-m",
            "Merge pull request #2 from me/feature\n\nAdd division", date=old)
        self.merge = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "app.py").write_text("def f(x):\n    y = x\n    return y  # fixed\n")
        git(self.repo, "commit", "-qam", "Fix the division by zero from #2 (#3)", date=old)
        self.fix = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "later.py").write_text("SECRET_FUTURE = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "Later work (#4)", date=old)
        # The reviewer under test, as a config would install it
        self.cfgdir = self.tmp / "configs"
        self.cfgdir.mkdir()
        (self.cfgdir / "reviewer.md").write_text("---\nname: reviewer\nmodel: opus\n---\nReview.\n")
        (self.cfgdir / "checklists.md").write_text("new checklists under test\n")
        self.config = {"name": "T", "files": {".claude/agents/reviewer.md": "reviewer.md",
                                              ".claude/review-checklists.md": "checklists.md"},
                       "frontmatter": {"model": "sonnet"}}
        self.case = {"id": "1", "set": "pilot", "pr": 2, "merge": self.merge, "title": "Add division",
                     "anchors": [{"file": "app.py", "start": 3, "end": 3, "kind": "lines",
                                  "checked": True}]}

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)


class SnapshotTests(Fixture):
    def snapshot(self) -> Path:
        dest = self.tmp / "snap"
        ev.build_snapshot(self.repo, self.case, self.config, self.cfgdir, dest)
        return dest

    def test_the_snapshot_holds_base_and_pr_and_nothing_later(self) -> None:
        s = self.snapshot()
        self.assertEqual(git(s, "rev-list", "--all", "--count"), "2")
        self.assertEqual(git(s, "for-each-ref", "--format=%(refname)"), "refs/heads/main")
        self.assertEqual(git(s, "remote"), "")
        self.assertFalse((s / "later.py").exists())
        self.assertIn("y / 0", (s / "app.py").read_text())  # the bug, before its fix
        log = git(s, "log", "--all", "--format=%s%n%b")
        self.assertNotIn("#3", log)
        self.assertNotIn("Fix", log)

    def test_the_diff_under_review_is_exactly_the_prs(self) -> None:
        s = self.snapshot()
        self.assertEqual(sorted(git(s, "diff", "--name-only", "HEAD^", "HEAD").split()),
                         ["app.py", "old.py", "util.py"])
        self.assertFalse((s / "old.py").exists())  # a file the PR deleted is gone

    def test_the_reviewer_under_test_replaces_the_repos_and_is_not_in_the_diff(self) -> None:
        s = self.snapshot()
        self.assertEqual((s / ".claude/review-checklists.md").read_text(), "new checklists under test\n")
        self.assertIn("model: sonnet", (s / ".claude/agents/reviewer.md").read_text())
        self.assertNotIn(".claude", git(s, "diff", "--name-only", "HEAD^", "HEAD"))

    def test_no_unreachable_objects_and_no_reuse_of_a_folder(self) -> None:
        s = self.snapshot()
        self.assertEqual(git(s, "fsck", "--unreachable", "--no-reflogs"), "")
        with self.assertRaises(RuntimeError):
            ev.build_snapshot(self.repo, self.case, self.config, self.cfgdir, s)

    def test_a_null_file_removes_it(self) -> None:
        self.config["files"][".claude/review-checklists.md"] = None
        self.assertFalse((self.snapshot() / ".claude/review-checklists.md").exists())

    def test_a_same_size_rewrite_with_matching_stat_data_is_still_committed(self) -> None:
        # Both commits share a date, so git archive gives both versions the same mtime. With
        # checkStat=minimal and no ctime, git compares only that mtime and the size, so a stale
        # index would call the rewritten file unchanged and drop it from the PR commit.
        (self.repo / "same.txt").write_text("aaaa\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "before", date="2020-01-01T00:00:00Z")
        base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "same.txt").write_text("bbbb\n")
        git(self.repo, "commit", "-qam", "Rewrite (#5)", date="2020-01-01T00:00:00Z")
        case = {**self.case, "base": base, "merge": git(self.repo, "rev-parse", "HEAD")}
        stat = {"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.checkStat",
                "GIT_CONFIG_VALUE_0": "minimal", "GIT_CONFIG_KEY_1": "core.trustCtime",
                "GIT_CONFIG_VALUE_1": "false"}
        dest = self.tmp / "snap"
        with unittest.mock.patch.dict(os.environ, stat):
            ev.build_snapshot(self.repo, case, self.config, self.cfgdir, dest)
        self.assertEqual(git(dest, "show", "HEAD:same.txt"), "bbbb")
        self.assertEqual(git(dest, "diff", "--name-only", "HEAD^", "HEAD"), "same.txt")


class ReviewFileTests(Fixture):
    def test_the_reviewer_gets_the_diff_message_and_base_files_without_git(self) -> None:
        snap = self.tmp / "snap"
        ev.build_snapshot(self.repo, self.case, self.config, self.cfgdir, snap)
        ev.write_review_files(snap)
        diff = (snap / ".review/pr.diff").read_text()
        self.assertIn("+    return y / 0", diff)
        self.assertIn("old.py", diff)
        self.assertIn("Add division", (snap / ".review/commit.txt").read_text())
        self.assertEqual((snap / ".review/base/app.py").read_text(), "def f(x):\n    return x\n")
        self.assertEqual((snap / ".review/base/old.py").read_text(), "legacy = 1\n")  # deleted file
        self.assertFalse((snap / ".review/base/util.py").exists())  # new file: no base version
        self.assertNotIn(".review", git(snap, "diff", "--name-only", "HEAD^", "HEAD"))

    def test_the_users_git_config_does_not_shape_the_diff(self) -> None:
        snap = self.tmp / "snap"
        ev.build_snapshot(self.repo, self.case, self.config, self.cfgdir, snap)
        cfg = self.tmp / "user.gitconfig"
        cfg.write_text("[color]\n\tui = always\n[diff]\n\tnoprefix = true\n")
        with unittest.mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(cfg)}):
            ev.write_review_files(snap)
        diff = (snap / ".review/pr.diff").read_text()
        self.assertTrue(diff.startswith("diff --git a/"), diff[:40])
        self.assertNotIn("\x1b[", diff)

    def test_symlinks_that_point_outside_are_replaced_and_inside_ones_kept(self) -> None:
        snap = self.tmp / "snap"
        ev.build_snapshot(self.repo, self.case, self.config, self.cfgdir, snap)
        (self.tmp / "secret.txt").write_text("fix\n")
        os.symlink(self.tmp / "secret.txt", snap / "out-abs")
        os.symlink("../secret.txt", snap / "out-rel")
        os.symlink("app.py", snap / "in")
        replaced = sorted(ev.neutralise_symlinks(snap))
        self.assertEqual(replaced, ["out-abs", "out-rel"])
        self.assertFalse((snap / "out-abs").is_symlink())
        self.assertNotIn("fix", (snap / "out-rel").read_text().split("->")[0])
        self.assertTrue((snap / "in").is_symlink())

    def test_odd_paths_non_utf8_files_and_type_changes(self) -> None:
        git(self.repo, "checkout", "-q", "-b", "odd", self.merge + "^1")
        (self.repo / "café.py").write_text("a = 1\n")
        (self.repo / "tab\tname.py").write_text("b = 1\n")
        (self.repo / "latin.txt").write_bytes("na\xefve\n".encode("latin-1"))
        (self.repo / "becomes_link.txt").write_text("plain\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        (self.repo / "café.py").write_text("a = 2\n")
        (self.repo / "tab\tname.py").write_text("b = 2\n")
        (self.repo / "latin.txt").write_bytes("caf\xe9\n".encode("latin-1"))
        (self.repo / "becomes_link.txt").unlink()
        (self.repo / "becomes_link.txt").symlink_to("café.py")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "pr")
        case = dict(self.case, merge=git(self.repo, "rev-parse", "HEAD"))
        snap = self.tmp / "odd-snap"
        ev.build_snapshot(self.repo, case, self.config, self.cfgdir, snap)
        ev.write_review_files(snap)
        base = snap / ".review/base"
        self.assertEqual((base / "café.py").read_text(), "a = 1\n")
        self.assertEqual((base / "tab\tname.py").read_text(), "b = 1\n")
        self.assertEqual((base / "latin.txt").read_bytes(), "na\xefve\n".encode("latin-1"))
        self.assertEqual((base / "becomes_link.txt").read_text(), "plain\n")
        self.assertIn("café.py", (snap / ".review/pr.diff").read_text(encoding="utf-8", errors="replace"))


class AnchorTests(Fixture):
    def test_the_fixed_line_is_found_where_the_pr_wrote_it(self) -> None:
        anchors = ev.draft_anchors(self.repo, self.merge, self.fix)
        self.assertEqual(anchors, [{"file": "app.py", "start": 3, "end": 3, "kind": "lines",
                                    "checked": False}])

    def test_tests_are_skipped_unless_asked_and_insertions_give_regions(self) -> None:
        git(self.repo, "checkout", "-q", "-b", "pr5", self.merge + "^1")
        (self.repo / "app_test.py").write_text("assert True\n")
        (self.repo / "app.py").write_text("def f(x):\n    z = x\n    return z\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "pr5")
        intro = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "app_test.py").write_text("assert False\n")
        (self.repo / "app.py").write_text("def f(x):\n    z = x\n    check(z)\n    return z\n")
        git(self.repo, "commit", "-qam", "fix: check z")
        fix = git(self.repo, "rev-parse", "HEAD")
        anchors = ev.draft_anchors(self.repo, intro, fix)
        self.assertEqual({a["file"] for a in anchors}, {"app.py"})
        self.assertEqual({a["kind"] for a in anchors}, {"region"})
        with_tests = ev.draft_anchors(self.repo, intro, fix, include_tests=True)
        self.assertIn("app_test.py", {a["file"] for a in with_tests})

    def pr_and_fix(self, before: str, pr: str, between: str | None, fixed: str) -> tuple[str, str]:
        git(self.repo, "checkout", "-q", "-b", "dup", self.merge + "^1")
        (self.repo / "m.py").write_text(before)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "before")
        (self.repo / "m.py").write_text(pr)
        git(self.repo, "commit", "-qam", "pr")
        intro = git(self.repo, "rev-parse", "HEAD")
        if between is not None:
            (self.repo / "m.py").write_text(between)
            git(self.repo, "commit", "-qam", "unrelated")
        (self.repo / "m.py").write_text(fixed)
        git(self.repo, "commit", "-qam", "fix")
        return intro, git(self.repo, "rev-parse", "HEAD")

    def test_a_duplicated_line_anchors_only_the_copy_the_fix_changed(self) -> None:
        pr = "def a():\n    return None\n" + "x = 1\n" * 30 + "def b():\n    return None\n"
        fixed = pr.replace("def b():\n    return None", "def b():\n    return 0")
        intro, fix = self.pr_and_fix("", pr, None, fixed)
        self.assertEqual([(a["start"], a["end"]) for a in ev.draft_anchors(self.repo, intro, fix)],
                         [(34, 34)])

    def test_lines_shifted_by_later_commits_map_back_to_the_pr(self) -> None:
        pr = "a = 1\nbug = 2\nc = 3\n"
        between = "# header\n# more\n" + pr  # pushes the bug down two lines before the fix
        intro, fix = self.pr_and_fix("", pr, between, between.replace("bug = 2", "bug = 0"))
        self.assertEqual([(a["start"], a["end"]) for a in ev.draft_anchors(self.repo, intro, fix)],
                         [(2, 2)])

    def test_a_removed_line_that_looks_like_a_diff_header_is_not_a_header(self) -> None:
        pr = 'q = """\n-- do a thing\n"""\nr = 1\n'
        intro, fix = self.pr_and_fix("", pr, None, 'q = """\n"""\nr = 2\n')
        self.assertEqual([(a["file"], a["start"], a["end"])
                          for a in ev.draft_anchors(self.repo, intro, fix)], [("m.py", 2, 4)])

    def test_lines_the_pr_did_not_write_give_no_anchor(self) -> None:
        # A fix of a line from the base commit says nothing about PR #2.
        (self.repo / "app.py").write_text("def g(x):\n    y = x\n    return y  # fixed\n")
        git(self.repo, "commit", "-qam", "rename")
        self.assertEqual(ev.draft_anchors(self.repo, self.merge, git(self.repo, "rev-parse", "HEAD")), [])


class ControlTests(Fixture):
    def test_fixed_and_case_prs_are_never_controls(self) -> None:
        ctl = ev.pick_controls(self.repo, [], count=10, min_age_days=0, min_lines=0)
        self.assertNotIn(2, [c["pr"] for c in ctl])  # a later commit fixes #2
        self.assertIn(4, [c["pr"] for c in ctl])
        ctl = ev.pick_controls(self.repo, [{"pr": 4}], count=10, min_age_days=0, min_lines=0)
        self.assertNotIn(4, [c["pr"] for c in ctl])


class GradeTests(unittest.TestCase):
    case = {"id": "1", "set": "pilot", "anchors": [{"file": "app.py", "start": 30, "end": 32}]}

    def f(self, sev: str, file: str = "app.py", line: int = 31) -> dict:
        return {"severity": sev, "file": file, "line_start": line, "line_end": line}

    def test_outcomes(self) -> None:
        g = lambda fs: ev.grade_run(self.case, fs)["outcome"]  # noqa: E731
        self.assertEqual(g([self.f("major")]), "catch")
        self.assertEqual(g([self.f("critical", line=42)]), "catch")  # within 10 lines
        self.assertEqual(g([self.f("major", line=43)]), "miss")
        self.assertEqual(g([self.f("minor")]), "seen")
        self.assertEqual(g([self.f("major", file="other.py")]), "miss")
        self.assertEqual(g([]), "miss")
        self.assertEqual(g(None), "unparsed")

    def test_a_case_counts_as_caught_on_a_majority_of_runs(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            cases = [{"id": "two", "set": "pilot", "anchors": self.case["anchors"]},
                     {"id": "one", "set": "pilot", "anchors": self.case["anchors"]}]
            for cid, caught in (("two", 2), ("one", 1)):
                for n in range(3):
                    fs = [self.f("major")] if n < caught else []
                    d = tmp / "X" / cid
                    d.mkdir(parents=True, exist_ok=True)
                    (d / f"run-{n + 1}.json").write_text(json.dumps(
                        {"case": cid, "config": "X", "findings": fs, "seconds": 1}))
            table = ev.report(cases, tmp)
            self.assertIn("| two | pilot | 2/3 |", table)
            self.assertIn("| X | 1 | n/a | 6 |", table)  # only "two" counts as caught
        finally:
            shutil.rmtree(tmp)

    def write_runs(self, tmp: Path, findings_by_run: list[list[dict]], cid: str = "1") -> None:
        d = tmp / "X" / cid
        d.mkdir(parents=True, exist_ok=True)
        for n, fs in enumerate(findings_by_run, 1):
            (d / f"run-{n}.json").write_text(json.dumps(
                {"case": cid, "config": "X", "findings": fs, "seconds": 1, "started": f"t{n}"}))

    def test_matches_lists_the_deciding_finding_the_other_severe_ones_and_a_verdict_line(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            elsewhere = {**self.f("major"), "file": "other.py", "title": "names the bug elsewhere"}
            self.write_runs(tmp, [[self.f("major"), elsewhere], [elsewhere]])
            out = ev.matches([{**self.case, "set": "pilot"}], tmp)
            first, second = out.strip().split("\n\n")
            self.assertIn("X case 1 run 1: catch", first)
            self.assertIn("matched: [major] app.py", first)
            self.assertIn("severe:  [major] other.py", first)
            self.assertIn("X case 1 run 2: miss", second)
            line = json.loads(second.splitlines()[-1])
            self.assertEqual(line, {"config": "X", "case": "1", "run": 2, "started": "t2",
                                    "verdict": "miss"})
        finally:
            shutil.rmtree(tmp)

    def test_report_uses_a_verdict_only_for_the_run_it_was_made_on(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            case = {**self.case, "set": "pilot"}
            self.write_runs(tmp, [[], [], [self.f("major")]])
            self.assertIn("| 1 | pilot | 1/3 |", ev.report([case], tmp))
            self.assertIn("3 of 3 case runs are graded by line only", ev.report([case], tmp))
            vfile = tmp / "verdicts.jsonl"
            vfile.write_text("// checked by hand\n" + "\n".join(json.dumps(v) for v in [
                {"config": "X", "case": "1", "run": 1, "started": "t1", "verdict": "catch"},
                {"config": "X", "case": "1", "run": 2, "started": "redone", "verdict": "catch"},
                {"config": "X", "case": "1", "run": 3, "started": "t3", "verdict": "miss"}]) + "\n")
            table = ev.report([case], tmp, verdicts=ev.load_verdicts(vfile))
            self.assertIn("| 1 | pilot | 1/3 |", table)  # run 1 now a catch, run 3 a miss, run 2 lapsed
            self.assertIn("1 of 3 case runs are graded by line only", table)
            vfile.write_text(json.dumps({"config": "X", "case": "1", "run": 1, "verdict": "maybe"}))
            with self.assertRaises(SystemExit):
                ev.load_verdicts(vfile)
        finally:
            shutil.rmtree(tmp)

    def test_controls_count_severe_findings(self) -> None:
        r = ev.grade_run({"id": "c1", "set": "control"}, [self.f("major"), self.f("minor")])
        self.assertEqual(r, {"outcome": "control", "severe": 1})

    def test_parse_findings(self) -> None:
        text = ("report...\n```json\n[{\"severity\": \"Major\", \"file\": \"./app.py\", "
                "\"line_start\": \"3\", \"title\": \"x\"}]\n```\n")
        self.assertEqual(ev.parse_findings(text), [{"severity": "major", "file": "app.py",
                                                    "line_start": 3, "line_end": 3, "title": "x"}])
        self.assertEqual(ev.parse_findings("```json\n{\"findings\": []}\n```"), [])
        self.assertIsNone(ev.parse_findings("no json here"))
        self.assertEqual(ev.parse_findings("```json\n[{\"file\": \"/tmp/review-eval-x1/repo/a/b.py\"}]\n```")
                         [0]["file"], "a/b.py")


class EndToEndTests(Fixture):
    def test_run_then_report_with_a_fake_reviewer(self) -> None:
        fake = self.tmp / "fake.py"
        fake.write_text(
            "import json, subprocess, sys\n"
            "head = subprocess.run(['git', 'rev-list', '--all', '--count'], capture_output=True,"
            " text=True).stdout.strip()\n"
            "finding = {'severity': 'Major', 'file': 'app.py', 'line_start': 3, 'line_end': 3,"
            " 'title': 'division by zero (saw ' + head + ' commits)'}\n"
            "text = 'report\\n```json\\n' + json.dumps([finding]) + '\\n```'\n"
            "print(json.dumps({'result': text, 'total_cost_usd': 0.25}))\n")
        self.config.update({"output": "claude-json", "command": [sys.executable, str(fake), "{brief}"]})
        cases = self.tmp / "cases.jsonl"
        cases.write_text(json.dumps(self.case) + "\n")
        cfg = self.cfgdir / "t.json"
        cfg.write_text(json.dumps(self.config))
        out = self.tmp / "runs"
        argv = ["run", "--repo", str(self.repo), "--cases", str(cases), "--config", str(cfg),
                "--runs", "3", "--out", str(out), "--skip-probe"]
        self.assertEqual(ev.main(argv), 0)
        rec = json.loads((out / "T/1/run-1.json").read_text())
        self.assertIn("saw 2 commits", rec["findings"][0]["title"])
        self.assertEqual(rec["cost_usd"], 0.25)
        before = (out / "T/1/run-1.json").stat().st_mtime_ns
        self.assertEqual(ev.main(argv), 0)  # resumable: finished runs are not redone
        self.assertEqual((out / "T/1/run-1.json").stat().st_mtime_ns, before)
        self.case["anchors"][0]["checked"] = False
        cases.write_text(json.dumps(self.case) + "\n")
        shutil.rmtree(out)
        self.assertEqual(ev.main(argv), 1)  # unchecked anchors refuse to run
        self.assertFalse(out.exists())
        self.assertEqual(ev.main(argv + ["--allow-unchecked"]), 0)
        table = ev.report(ev.load_cases(cases), out)
        self.assertIn("| 1 | pilot | 3/3 |", table)
        self.assertIn("| T | 1 | n/a | 3 | 0 | 0.25 |", table)
        self.assertEqual(list(Path(tempfile.gettempdir()).glob("review-eval-*/repo")), [])


class FailureTests(Fixture):
    def run_with(self, script: str, runs: int = 1) -> tuple[int, Path]:
        fake = self.tmp / "fake.py"
        fake.write_text(script)
        self.config.update({"output": "claude-json",
                            "command": [sys.executable, str(fake), "{agents_file}", "{model}",
                                        "{effort}"]})
        cases = self.tmp / "cases.jsonl"
        cases.write_text(json.dumps(self.case) + "\n")
        cfg = self.cfgdir / "t.json"
        cfg.write_text(json.dumps(self.config))
        out = self.tmp / "runs"
        code = ev.main(["run", "--repo", str(self.repo), "--cases", str(cases), "--config", str(cfg),
                        "--runs", str(runs), "--out", str(out), "--skip-probe"])
        return code, out / "T" / "1"

    def test_a_failed_run_is_an_error_not_a_miss_and_three_stop_the_run(self) -> None:
        code, d = self.run_with("import json, sys\nprint(json.dumps({'is_error': True, "
                                "'result': 'usage limit reached'}))\nsys.exit(1)\n", runs=5)
        self.assertEqual(code, 2)
        self.assertEqual(sorted(p.name for p in d.iterdir()),
                         ["error-1.json", "error-2.json", "error-3.json"])
        self.assertNotIn("| 1 |", ev.report([self.case], d.parent.parent))

    def test_a_budget_stop_counts_as_a_finished_run(self) -> None:
        code, d = self.run_with("import json, sys\nprint(json.dumps({'is_error': True, "
                                "'subtype': 'error_max_budget_usd', 'result': 'partial'}))\n"
                                "sys.exit(1)\n")
        self.assertEqual(code, 0)
        self.assertEqual([p.name for p in d.iterdir()], ["run-1.json"])

    # A fake claude for the probe: finds the brief among its arguments, does what `act` says, and
    # prints stream-json events: one tool_result per attempt, then the result with four findings.
    FAKE = (
        "import json, os, re, sys\n"
        "brief = next(a for a in sys.argv[1:] if 'isolation check' in a)\n"
        "secret = re.search(r'Read tool on (\\S+);', brief).group(1)\n"
        "folder = os.path.dirname(secret)\n"
        "results = []\n"
        "{act}\n"
        "for r in results:\n"
        "    print(json.dumps({{'type': 'user', 'message': {{'content': [{{'type': 'tool_result', "
        "'content': r}}]}}}}))\n"
        "fs = [{{'severity': 'Minor', 'file': 'probe', 'line_start': 1, 'title': t}} for t in "
        "('read: done', 'link: done', 'glob: done', 'grep: done')]\n"
        "print(json.dumps({{'type': 'result', 'is_error': False, 'total_cost_usd': 0.01, "
        "'result': '```json\\n' + json.dumps(fs) + '\\n```'}}))\n")

    def probe_with(self, act: str, output: str = "claude-json") -> tuple[bool, Path]:
        fake = self.tmp / "probe_fake.py"
        fake.write_text(self.FAKE.format(act=act))
        cfg = dict(self.config, output=output,
                   command=[sys.executable, str(fake), "--output-format", "json", "{brief}"])
        out = self.tmp / "probe-runs"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = ev.probe(self.repo, self.case, cfg, self.cfgdir, out)
        self.said = buf.getvalue()
        return ok, out / "T" / "probe.json"

    def test_a_tool_that_reads_the_secret_fails_the_probe_even_if_the_model_is_quiet(self) -> None:
        ok, marker = self.probe_with("results.append(open(secret).read())")
        self.assertFalse(ok)
        self.assertIn("file contents", self.said)
        self.assertFalse(marker.exists())

    def test_a_directory_listing_fails_the_probe(self) -> None:
        ok, _ = self.probe_with("results.append(' '.join(os.listdir(folder)))")
        self.assertFalse(ok)
        self.assertIn("a directory listing", self.said)

    def test_a_grep_that_returns_only_file_names_fails_the_probe(self) -> None:
        ok, _ = self.probe_with(
            "results.append('\\n'.join(os.path.join(folder, n) for n in os.listdir(folder) "
            "if 'PROBE' in open(os.path.join(folder, n)).read()))")
        self.assertFalse(ok)
        self.assertIn("a directory listing", self.said)

    def test_a_run_whose_tools_returned_nothing_fails_closed(self) -> None:
        ok, _ = self.probe_with("pass")
        self.assertFalse(ok)
        self.assertIn("did not finish, use its tools", self.said)

    def test_the_one_argument_output_format_is_switched_too(self) -> None:
        fake = self.tmp / "probe_fake.py"
        fake.write_text(self.FAKE.format(act="results += ['denied'] * 3"))
        cfg = dict(self.config, output="claude-json",
                   command=[sys.executable, str(fake), "--output-format=json", "{brief}"])
        self.assertTrue(ev.probe(self.repo, self.case, cfg, self.cfgdir, self.tmp / "r"))
        cfg["command"] = [sys.executable, str(fake), "{brief}"]
        self.assertFalse(ev.probe(self.repo, self.case, cfg, self.cfgdir, self.tmp / "r"))

    def test_a_probe_that_never_ran_fails_closed(self) -> None:
        fake = self.tmp / "dead.py"
        fake.write_text("import sys\nsys.exit(1)\n")
        cfg = dict(self.config, output="claude-json",
                   command=[sys.executable, str(fake), "--output-format", "json", "{brief}"])
        self.assertFalse(ev.probe(self.repo, self.case, cfg, self.cfgdir, self.tmp / "r"))

    def test_only_claude_configs_can_be_probed(self) -> None:
        ok, _ = self.probe_with("results.append('denied')", output="last-message")
        self.assertFalse(ok)
        self.assertIn("only inspect tool results for claude", self.said)

    def test_a_clean_probe_passes_and_unlocks_run_for_that_config_only(self) -> None:
        ok, marker = self.probe_with("results += ['denied'] * 4")
        self.assertTrue(ok, self.said)
        self.assertTrue(marker.exists())
        cases = self.tmp / "cases.jsonl"
        cases.write_text(json.dumps(self.case) + "\n")
        cfg = self.cfgdir / "t.json"
        fake = self.tmp / "probe_fake.py"
        command = [sys.executable, str(fake), "--output-format", "json", "{brief}"]
        cfg.write_text(json.dumps(dict(self.config, output="claude-json", command=command)))
        argv = ["run", "--repo", str(self.repo), "--cases", str(cases), "--config", str(cfg),
                "--runs", "1", "--out", str(marker.parent.parent)]
        self.assertEqual(ev.main(argv), 0)
        cfg.write_text(json.dumps(dict(self.config, output="claude-json", max_budget_usd=9,
                                       command=command)))
        self.assertEqual(ev.main(argv), 1)  # the config changed, so its probe no longer counts

    def test_the_reviewer_model_and_effort_reach_the_command(self) -> None:
        code, d = self.run_with(
            "import json, sys\nagents = json.load(open(sys.argv[1]))['reviewer']\n"
            "t = agents['model'] + '/' + sys.argv[2] + '/' + sys.argv[3] + '/' + agents['prompt'].strip()\n"
            "print(json.dumps({'result': '```json\\n' + json.dumps([{'severity': 'Minor', "
            "'file': 'x', 'line_start': 1, 'title': t}]) + '\\n```', 'modelUsage': {'m': 1}}))\n")
        rec = json.loads((d / "run-1.json").read_text())
        self.assertEqual(rec["findings"][0]["title"], "sonnet/sonnet//Review.")
        self.assertEqual(rec["model_usage"], {"m": 1})


if __name__ == "__main__":
    unittest.main()

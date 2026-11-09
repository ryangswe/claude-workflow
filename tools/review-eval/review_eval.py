#!/usr/bin/env python3
"""Reviewer eval: does a reviewer flag a bug that later had to be fixed?

Each case is a PR that introduced a bug a later commit fixed. A run rebuilds the repository as it
stood when that PR was open, in a fresh folder with exactly two commits (the PR's base and the PR)
and no later history, installs the reviewer under test, asks it to review the last commit, and
keeps the findings it lists. A run *catches* the case when a Critical or Major finding names the
case's anchor file within `--tolerance` lines of the anchor. Controls are PRs nothing later fixed;
their findings measure noise.

    review_eval.py anchors  --repo R --intro <merge> --fix <commit>      draft a case's anchors
    review_eval.py controls --repo R --cases C [--count 10]              pick control PRs
    review_eval.py snapshot --repo R --cases C --case ID --config F --out DIR
    review_eval.py probe    --repo R --cases C --case ID --config F [--out runs]
    review_eval.py run      --repo R --cases C --config F [--set pilot] [--runs 3] [--out runs]
    review_eval.py report   --cases C [--out runs] [--tolerance 10]

Standard library only, Python 3.9+. See README.md beside this file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

SEVERE = {"critical", "major"}
FIXED_ENV = {
    "GIT_AUTHOR_NAME": "eval", "GIT_AUTHOR_EMAIL": "eval@example.invalid",
    "GIT_COMMITTER_NAME": "eval", "GIT_COMMITTER_EMAIL": "eval@example.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    # The user's git config must not reach snapshot commits (signing prompts, hooks paths).
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}
DEFAULT_BRIEF = (
    "Review the change {title} (PR #{pr}). The working tree is the repository after the change. "
    "You have no shell, so git is not available: the full diff is in .review/pr.diff, the commit "
    "message in .review/commit.txt, and the version before the change of every file it modified or "
    "deleted is under .review/base/. Leave .review/ out of your findings. {plan}"
    "Do not change any file.\n\n"
    "After your usual report, end with a fenced ```json block holding a list of your findings, "
    "one object each: {{\"severity\": \"Critical|Major|Minor\", \"file\": \"path from the "
    "repository root\", \"line_start\": n, \"line_end\": n, \"title\": \"...\"}}. Lines are in the "
    "file as it stands after the change. Use [] when there are none."
)


# ---------------------------------------------------------------------------------------- git
def git(repo, *args: str, check: bool = True, env: dict | None = None) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def git_bytes(repo, *args: str, env: dict | None = None) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       env={**os.environ, **(env or {})})
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def default_ref(repo) -> str:
    for ref in ("origin/main", "origin/master", "main", "master"):
        if git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).strip():
            return ref
    raise RuntimeError("no main or master branch")


def pr_of(subject: str, body: str) -> tuple[int | None, str]:
    """PR number and title from a merge commit ("Merge pull request #N ...") or a squash ("T (#N)")."""
    m = re.match(r"Merge pull request #(\d+) ", subject)
    if m:
        title = next((line.strip() for line in body.splitlines() if line.strip()), subject)
        return int(m.group(1)), title
    m = re.search(r"\(#(\d+)\)\s*$", subject)
    if m:
        return int(m.group(1)), subject[:m.start()].strip()
    return None, subject


# ---------------------------------------------------------------------------------- snapshot
def _extract(repo, rev: str, dest: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(git_bytes(repo, "archive", "--format=tar", rev))) as t:
        if sys.version_info >= (3, 12):
            t.extractall(dest, filter="tar")
        else:
            t.extractall(dest)


def _clear(dest: Path) -> None:
    for p in dest.iterdir():
        if p.name == ".git":
            continue
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()


def _overlay(dest: Path, config: dict, config_dir: Path) -> None:
    for target, source in (config.get("files") or {}).items():
        path = dest / target
        if source is None:
            if path.exists():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        text = (config_dir / source).read_text(encoding="utf-8")
        if target.endswith(".claude/agents/reviewer.md") and config.get("frontmatter"):
            text = set_frontmatter(text, config["frontmatter"])
        path.write_text(text, encoding="utf-8")


def set_frontmatter(text: str, values: dict) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise ValueError("reviewer file has no frontmatter")
    lines = m.group(1).split("\n")
    for key, value in values.items():
        for i, line in enumerate(lines):
            if line.startswith(f"{key}:"):
                lines[i] = f"{key}: {value}"
                break
        else:
            lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n" + text[m.end():]


def build_snapshot(repo, case: dict, config: dict, config_dir: Path, dest: Path) -> None:
    """dest gets a repo with two commits, base then PR, the reviewer under test in both, no history."""
    merge = case["merge"]
    base = case.get("base") or git(repo, "rev-parse", f"{merge}^1").strip()
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError(f"{dest} exists and is not empty; a snapshot needs a new folder")
    dest.mkdir(parents=True, exist_ok=True)
    git(dest, "init", "-q", "-b", "main", env=FIXED_ENV)
    _extract(repo, base, dest)
    _overlay(dest, config, config_dir)
    git(dest, "add", "-A", "-f", env=FIXED_ENV)
    git(dest, "commit", "-q", "--no-verify", "-m", "Base", env=FIXED_ENV)
    _clear(dest)
    _extract(repo, merge, dest)
    _overlay(dest, config, config_dir)
    # Both versions can carry the same mtime and size, so git's stat cache would call a rewritten
    # file unchanged. An empty index makes the add hash every file again.
    git(dest, "read-tree", "--empty", env=FIXED_ENV)
    git(dest, "add", "-A", "-f", env=FIXED_ENV)
    git(dest, "commit", "-q", "--no-verify", "--allow-empty",
        "-m", f"{case.get('title', '')} (PR #{case.get('pr', '?')})", env=FIXED_ENV)
    git(dest, "reflog", "expire", "--expire=now", "--all", env=FIXED_ENV)
    git(dest, "gc", "-q", "--prune=now", env=FIXED_ENV)  # no unreachable objects either


def write_review_files(snap: Path) -> None:
    """What the reviewer would get from git, as files, so it needs no shell: the diff, the commit
    message and the base version of every changed file. Untracked, so not part of the diff."""
    review = snap / ".review"
    review.mkdir()
    (review / "pr.diff").write_bytes(git_bytes(
        snap, "-c", "core.quotePath=false", "diff", "--no-ext-diff", "--no-textconv", "--no-color",
        "--src-prefix=a/", "--dst-prefix=b/", "HEAD^", "HEAD", env=FIXED_ENV))
    (review / "commit.txt").write_bytes(git_bytes(snap, "log", "-1", "--format=%B", env=FIXED_ENV))
    changed = git_bytes(snap, "diff", "--name-only", "-z", "--diff-filter=MDT", "--no-renames",
                        "HEAD^", "HEAD", env=FIXED_ENV).split(b"\0")
    for raw in filter(None, changed):
        path = raw.decode("utf-8", errors="surrogateescape")
        dest = review / "base" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if git(snap, "ls-tree", "HEAD^", "--", path, env=FIXED_ENV).startswith("120000"):
            dest.write_bytes(b"symlink -> " + git_bytes(snap, "show", f"HEAD^:{path}", env=FIXED_ENV)
                             + b"\n")
        else:
            dest.write_bytes(git_bytes(snap, "show", f"HEAD^:{path}", env=FIXED_ENV))


def neutralise_symlinks(snap: Path) -> list[str]:
    """Replace each symlink in the working tree that resolves outside the snapshot with a text file
    naming its target, so no file tool can follow one out. Returns the paths replaced."""
    root = os.path.realpath(snap)
    replaced = []
    for dirpath, dirnames, filenames in os.walk(snap):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                continue
            target = os.path.realpath(path)
            if target == root or target.startswith(root + os.sep):
                continue
            link = os.readlink(path)
            os.unlink(path)
            Path(path).write_text(f"symlink -> {link} (points outside the snapshot; replaced for "
                                  "the eval)\n", encoding="utf-8")
            replaced.append(os.path.relpath(path, snap))
    return replaced


# ----------------------------------------------------------------------------------- anchors
def _hunks(repo, old: str, new: str, path: str | None = None) -> dict[str, list[tuple]]:
    """Per old-side path: (old_start, old_count, new_start, new_count) of each -U0 hunk."""
    out: dict[str, list[tuple]] = {}
    args = ["diff", "-U0", "--no-renames", "--no-ext-diff", old, new] + (["--", path] if path else [])
    current, header = None, False
    for line in git(repo, *args).splitlines():
        if line.startswith("diff --git "):
            current, header = None, True
            continue
        if header and line.startswith("--- "):
            current = None if line[4:] == "/dev/null" else line[6:]
            continue
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            header = False
            if current:
                out.setdefault(current, []).append(
                    (int(m.group(1)), int(m.group(2) or 1) if m.group(2) != "0" else 0,
                     int(m.group(3)), int(m.group(4) or 1) if m.group(4) != "0" else 0))
    return out


def _new_to_old(hunks: list[tuple], n: int) -> int | None:
    """Line n of the new side as a line of the old side, or None if a hunk changed it."""
    shift = 0
    for old_start, old_count, new_start, new_count in hunks:
        first_new = new_start if new_count else new_start + 1
        if n < first_new:
            break
        if new_count and n < new_start + new_count:
            return None
        shift += old_count - new_count
    return n + shift


NOT_SOURCE = re.compile(r"(^|/)(docs?|tests?|__tests__|fixtures?)/|\.(test|spec)\.[^/]+$|"
                        r"_test\.[^/]+$|(^|/)test_[^/]+$|\.md$")


def draft_anchors(repo, intro: str, fix: str, context: int = 3,
                  include_tests: bool = False) -> list[dict]:
    """Anchors at the intro commit for the lines a later fix changed that the intro PR wrote.

    Lines the fix deleted or changed give `lines` anchors; a hunk that only inserts gives a
    `region` around the insertion point. Tests and docs are skipped unless include_tests, since a
    fix adds tests while the bug sits in source. Every anchor is a draft for a person to check.
    """
    parents = git(repo, "rev-list", "--parents", "-n1", intro).split()
    pr_commits = set(git(repo, "rev-list", f"{parents[1]}..{intro}").split()) if len(parents) > 2 \
        else {intro}
    anchors = []
    for path, hunks in _hunks(repo, f"{fix}^", fix).items():
        if not include_tests and NOT_SOURCE.search(path):
            continue
        exists = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{intro}:{path}"],
                                capture_output=True).returncode == 0
        if not exists:
            continue  # the file did not exist at the intro commit under this name
        # How lines at fix^ map back to the intro commit, through everything in between.
        between = _hunks(repo, intro, f"{fix}^", path).get(path, [])
        hits: dict[str, list[int]] = {"lines": [], "region": []}
        for old_start, old_count, _, _ in hunks:
            lo, hi = (old_start, old_start + old_count - 1) if old_count else \
                (max(1, old_start - context + 1), old_start + context)
            blame = git(repo, "blame", "-l", "-s", "-L", f"{lo},{hi}", f"{fix}^", "--", path,
                        check=False)
            for offset, row in enumerate(blame.splitlines()):
                if row.split()[0].lstrip("^") not in pr_commits:
                    continue
                at_intro = _new_to_old(between, lo + offset)
                if at_intro is not None:
                    hits["lines" if old_count else "region"].append(at_intro)
        for kind in ("lines", "region"):
            for a, b in _clusters(sorted(set(hits[kind]))):
                anchors.append({"file": path, "start": a, "end": b, "kind": kind,
                                "checked": False})
    return anchors


def _clusters(lines: list[int], gap: int = 10) -> list[tuple[int, int]]:
    groups: list[list[int]] = []
    for n in lines:
        if groups and n - groups[-1][-1] <= gap:
            groups[-1].append(n)
        else:
            groups.append([n])
    return [(g[0], g[-1]) for g in groups]


# ---------------------------------------------------------------------------------- controls
def pick_controls(repo, cases: list[dict], count: int = 10, min_age_days: int = 30,
                  min_lines: int = 100, max_lines: int = 1800) -> list[dict]:
    ref = default_ref(repo)
    excluded = {c.get("pr") for c in cases} | {p for c in cases for p in c.get("also_pr", [])}
    log = git(repo, "log", ref, "--format=%B%x00")
    for m in re.finditer(r"(?i)\b(?:fix|fixes|fixed|revert|regress\w*|broke\w*)\b[^\x00]*?#(\d+)", log):
        excluded.add(int(m.group(1)))
    cutoff = time.time() - min_age_days * 86400
    pool = []
    rows = git(repo, "log", ref, "--first-parent", "--format=%H%x1f%ct%x1f%s%x1f%b%x1e")
    for row in rows.split("\x1e"):
        parts = row.strip("\n").split("\x1f")
        if len(parts) < 4:
            continue
        sha, ct, subject, body = parts
        pr, title = pr_of(subject, body)
        if pr is None or pr in excluded or int(ct) > cutoff:
            continue
        stat = git(repo, "diff", "--numstat", f"{sha}^1", sha, check=False).splitlines()
        files = [s.split("\t") for s in stat if s.count("\t") == 2]
        code = [f for f in files if not (f[2].startswith("docs/") or f[2].endswith(".md")
                                          or f[2].endswith("package-lock.json"))]
        size = sum(int(a) + int(d) for a, d, _ in code if a.isdigit() and d.isdigit())
        if code and min_lines <= size <= max_lines:
            pool.append({"pr": pr, "merge": sha, "title": title, "lines": size})
    pool.sort(key=lambda c: c["pr"])
    step = max(1, len(pool) // count) if pool else 1
    picked = pool[::step][:count]
    return [{"id": f"c{i + 1}", "set": "control", **c} for i, c in enumerate(picked)]


# --------------------------------------------------------------------------------- run, grade
def parse_findings(text: str) -> list[dict] | None:
    """The last fenced json block that is a list of findings (or {"findings": [...]}); None if none."""
    for block in reversed(re.findall(r"```json\s*\n(.*?)```", text, re.S)):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict):
            data = data.get("findings")
        if isinstance(data, list):
            out = []
            for f in data:
                if not isinstance(f, dict):
                    continue
                try:
                    start = int(f.get("line_start") or f.get("line") or 0)
                    end = int(f.get("line_end") or start)
                except (TypeError, ValueError):
                    start = end = 0
                out.append({"severity": str(f.get("severity", "")).strip().lower(),
                            "file": _relpath(str(f.get("file", "")).strip()),
                            "line_start": start, "line_end": max(start, end),
                            "title": str(f.get("title", ""))[:200]})
            return out
    return None


def _relpath(path: str) -> str:
    """A path from the repository root, as findings sometimes give ./x or an absolute snapshot path."""
    m = re.search(r"/review-eval-[^/]+/repo/(.*)$", path)
    if m:
        path = m.group(1)
    while path.startswith("./"):
        path = path[2:]
    return path


def grade_run(case: dict, findings: list[dict] | None, tolerance: int = 10) -> dict:
    if findings is None:
        return {"outcome": "unparsed", "severe": 0}
    severe = sum(1 for f in findings if f["severity"] in SEVERE)
    if case.get("set") == "control":
        return {"outcome": "control", "severe": severe}
    outcome, match = "miss", None
    for f in findings:
        for a in case.get("anchors", []):
            if f["file"] != a["file"] or not f["line_start"]:
                continue
            if f["line_start"] <= a["end"] + tolerance and f["line_end"] >= a["start"] - tolerance:
                if f["severity"] in SEVERE:
                    return {"outcome": "catch", "severe": severe, "match": f}
                if match is None:
                    outcome, match = "seen", f
    return {"outcome": outcome, "severe": severe, "match": match}


# A line match says where a finding points, not what it says: a finding about something else can land
# near an anchor, and a finding that names the bug can point where the fix went instead. A person
# reads each graded run (`matches`) and records a verdict; `report` uses it in place of the grade.
VERDICTS = {"catch", "seen", "miss"}


def load_verdicts(path) -> dict[tuple[str, str, int], dict]:
    """Verdicts by (config, case, run). Each line: config, case, run, started (the run record's,
    so a verdict lapses when its run is redone), verdict (catch, seen or miss) and an optional note."""
    out = {}
    if not path:
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        v = json.loads(line)
        if v.get("verdict") not in VERDICTS:
            raise SystemExit(f"verdict must be one of {sorted(VERDICTS)}: {line}")
        out[(v["config"], str(v["case"]), int(v["run"]))] = v
    return out


def _run_number(path: Path) -> int:
    return int(path.stem.split("-")[1])


def _describe(f: dict) -> str:
    return f"[{f['severity']}] {f['file']}:{f['line_start']}-{f['line_end']} {f.get('title', '')}"


def matches(cases: list[dict], runs_dir: Path, tolerance: int = 10, config: str | None = None) -> str:
    """For each finished run of a case with anchors: its grade, the finding that decided it, and its
    other severe findings (one may name the bug while pointing elsewhere), then a verdict line to
    copy into the verdicts file and correct."""
    blocks = []
    order = {c["id"]: i for i, c in enumerate(cases)}
    for f in sorted(runs_dir.glob("*/*/run-*.json"), key=lambda p: (p.parent.parent.name,
                    order.get(p.parent.name, len(order)), p.parent.name, _run_number(p))):
        rec = json.loads(f.read_text(encoding="utf-8"))
        case = next((c for c in cases if c["id"] == rec["case"]), None)
        if case is None or case.get("set") == "control" or (config and rec["config"] != config):
            continue
        g = grade_run(case, rec.get("findings"), tolerance)
        n = _run_number(f)
        lines = [f"{rec['config']} case {rec['case']} run {n}: {g['outcome']}"]
        if g.get("match"):
            lines.append(f"  matched: {_describe(g['match'])}")
        for other in rec.get("findings") or []:
            if other["severity"] in SEVERE and other is not g.get("match"):
                lines.append(f"  severe:  {_describe(other)}")
        verdict = g["outcome"] if g["outcome"] in VERDICTS else "miss"
        lines.append("  " + json.dumps({"config": rec["config"], "case": rec["case"], "run": n,
                                        "started": rec.get("started"), "verdict": verdict}))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _fill(template, values: dict):
    if isinstance(template, list):
        return [_fill(t, values) for t in template]
    return template.format(**values)


def run_one(repo, case: dict, config: dict, config_dir: Path, out_file: Path,
            keep: bool = False, setup=None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="review-eval-"))
    snap = tmp / "repo"
    record: dict = {"case": case["id"], "config": config["name"],
                    "started": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        build_snapshot(repo, case, config, config_dir, snap)
        write_review_files(snap)
        record["symlinks_replaced"] = neutralise_symlinks(snap)
        if setup:
            setup(snap)
        plan = f"The plan it implements is at {case['plan']}. " if case.get("plan") else ""
        brief = config.get("brief", DEFAULT_BRIEF).format(
            title=case.get("title", ""), pr=case.get("pr", "?"), plan=plan)
        reviewer = (config_dir / config["files"][".claude/agents/reviewer.md"]).read_text(
            encoding="utf-8") if ".claude/agents/reviewer.md" in config.get("files", {}) else ""
        reviewer_body = re.sub(r"^---\n.*?\n---\n", "", reviewer, flags=re.S)
        last = tmp / "last-message.txt"
        front = config.get("frontmatter") or {}
        installed = snap / ".claude/agents/reviewer.md"
        agents_file = tmp / "agents.json"
        if installed.exists():  # the reviewer as installed, passed explicitly with --agents
            text = installed.read_text(encoding="utf-8")
            meta = dict(re.findall(r"^(\w+):\s*(.*)$", re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1),
                                   re.M))
            agents_file.write_text(json.dumps({"reviewer": {
                "description": meta.get("description", "reviewer"),
                "prompt": re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S),
                "tools": config.get("tools", ["Read", "Glob", "Grep"]),
                "model": front.get("model", meta.get("model", "inherit"))}}), encoding="utf-8")
        values = {"brief": brief, "snapshot": str(snap), "reviewer_body": reviewer_body,
                  "budget": str(config.get("max_budget_usd", 5)), "last_message": str(last),
                  "agents_file": str(agents_file), "model": str(front.get("model", "")),
                  "effort": str(front.get("effort", ""))}
        cmd = _fill(config["command"], values)
        t0 = time.time()
        try:
            env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            r = subprocess.run(cmd, cwd=snap, capture_output=True, text=True, env=env,
                               stdin=subprocess.DEVNULL, timeout=config.get("timeout_s", 1800))
            stdout, code = r.stdout, r.returncode
            record["stderr_tail"] = r.stderr[-2000:]
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            code = "timeout"
        record.update({"exit": code, "seconds": round(time.time() - t0, 1)})
        text = stdout
        if config.get("output") == "claude-json":
            try:
                data = json.loads(stdout)
                text = data.get("result", "")
                record["cost_usd"] = data.get("total_cost_usd")
                record["usage"] = data.get("usage")
                record["model_usage"] = data.get("modelUsage")
                record["is_error"] = bool(data.get("is_error"))
                record["subtype"] = data.get("subtype")
            except ValueError:
                pass
        elif config.get("output") == "claude-stream":
            record["transcript"] = stdout  # every event, tool results included
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "result":
                    text = event.get("result", "")
                    record["cost_usd"] = event.get("total_cost_usd")
                    record["model_usage"] = event.get("modelUsage")
                    record["is_error"] = bool(event.get("is_error"))
                    record["subtype"] = event.get("subtype")
        elif config.get("output") == "last-message" and last.exists():
            text = last.read_text(encoding="utf-8", errors="replace")
        record["text"] = text
        record["findings"] = parse_findings(text)
    finally:
        if keep:
            record["snapshot"] = str(snap)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    # An infrastructure failure (the tool erred, a limit hit) is not a review: it is kept as
    # error-N.json, which the report ignores and the next run retries. A timeout or a budget stop
    # is the reviewer's own outcome and counts.
    budget_stop = str(record.get("subtype", "")).startswith("error_max_budget")
    record["infra_error"] = (record.get("exit") not in (0, "timeout") or record.get("is_error")) \
        and not budget_stop and record.get("findings") is None
    if record["infra_error"]:
        out_file = out_file.with_name(out_file.name.replace("run-", "error-"))
    out_file.parent.mkdir(parents=True, exist_ok=True)
    part = out_file.with_suffix(".json.part")
    part.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(part, out_file)
    return record


def cli_version(config: dict) -> str:
    try:
        r = subprocess.run([config["command"][0], "--version"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30)
        return r.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired, IndexError, KeyError):
        return "unknown"


def config_digest(config: dict, config_dir: Path) -> str:
    """Config, its installed files, this tool's code and the reviewer CLI's version: a change to
    any of them can change what the reviewer can reach, so a probe only counts for all four."""
    import hashlib
    h = hashlib.sha256(json.dumps(config, sort_keys=True).encode())
    for source in sorted(v for v in (config.get("files") or {}).values() if v):
        h.update((config_dir / source).read_bytes())
    h.update(Path(__file__).read_bytes())
    h.update(cli_version(config).encode())
    return h.hexdigest()[:16]


def probe(repo, case: dict, config: dict, config_dir: Path, runs_dir: Path) -> bool:
    """Try to make the configured reviewer read outside the snapshot, and check every tool result.

    A secret file sits outside the snapshot, and a second secret is only in the name of a file
    beside it, which a directory listing or a Grep in any output mode would show. (Symlinks that
    point out of a snapshot are replaced before every run, so none is planted.) The run uses
    stream-json, so the check reads what the tools returned, not what the model says.
    Passes only if the run finished, reported each attempt, and neither secret appears anywhere;
    then writes runs/<config>/probe.json, which `run` requires."""
    if config.get("output") != "claude-json":
        print("PROBE FAILED: probe can only inspect tool results for claude configs; this config's "
              "isolation is unproven")
        return False
    secret_dir = Path(tempfile.mkdtemp(prefix="review-eval-probe-"))
    token, name_token = "PROBE-" + os.urandom(6).hex(), "PROBENAME-" + os.urandom(6).hex()
    secret = secret_dir / "fix.txt"
    secret.write_text(token + "\n", encoding="utf-8")
    # Contains the Grep pattern too, so even a Grep that returns only file names lists it.
    (secret_dir / f"{name_token}.txt").write_text("PROBE listed\n", encoding="utf-8")
    brief = (f"This is an isolation check, not a review. Try each of these and report what "
             f"happened: (1) use the Read tool on {secret}; (2) use Glob with the pattern * in "
             f"{secret_dir}; (3) use Grep for PROBE in {secret_dir} with output_mode content; (4) "
             f"if you have a shell, run cat {secret}. End with a fenced ```json block holding one "
             "finding per attempt: "
             "{{\"severity\": \"Minor\", \"file\": \"probe\", \"line_start\": 1, \"title\": "
             "\"<attempt>: <what happened>\"}}.")
    command, rewritten = [], False
    for i, part in enumerate(config["command"]):
        if part == "json" and i and config["command"][i - 1] == "--output-format":
            command += ["stream-json", "--verbose"]  # stream-json in print mode needs --verbose
            rewritten = True
        elif part == "--output-format=json":
            command += ["--output-format=stream-json", "--verbose"]
            rewritten = True
        else:
            command.append(part)
    if not rewritten:
        print("PROBE FAILED: the config's command has no --output-format json to switch to "
              "stream-json, so tool results can't be inspected")
        shutil.rmtree(secret_dir, ignore_errors=True)
        return False
    probe_config = dict(config, brief=brief, command=command, output="claude-stream")

    out = secret_dir / "probe-run.json"
    try:
        rec = run_one(repo, case, probe_config, config_dir, out)
        everything = json.dumps({k: v for k, v in rec.items() if k != "snapshot"})
        leaked = [t for t in (token, name_token) if t in everything]
        saw_tools = '"tool_result"' in (rec.get("transcript") or "")
        finished = rec.get("exit") == 0 and not rec.get("is_error") and saw_tools and \
            rec.get("findings") is not None and len(rec["findings"]) >= 3
        print(rec.get("text") or "(no text)")
        print(f"\nexit {rec.get('exit')}, cost {rec.get('cost_usd')}, "
              f"models {list(rec.get('model_usage') or {})}")
        if leaked:
            print("PROBE FAILED: a tool returned something from outside the snapshot "
                  f"({'file contents' if token in leaked else 'a directory listing'})")
        elif not finished:
            print("PROBE FAILED: the run did not finish, use its tools and report its attempts, so "
                  "isolation is unproven")
        else:
            print("PROBE PASSED: no tool result contained anything from outside the snapshot")
            marker = runs_dir / config["name"] / "probe.json"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"config": config_digest(config, config_dir),
                                          "cli": cli_version(config), "models": rec.get("model_usage"),
                                          "at": dt.datetime.now(dt.timezone.utc).isoformat()}),
                              encoding="utf-8")
        return finished and not leaked
    finally:
        shutil.rmtree(secret_dir, ignore_errors=True)


def load_cases(path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("//")]


def load_config(path) -> tuple[dict, Path]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")), p.parent


def report(cases: list[dict], runs_dir: Path, tolerance: int = 10,
           verdicts: dict[tuple[str, str, int], dict] | None = None) -> str:
    results: dict[tuple[str, str], list[dict]] = {}
    meta: dict[str, dict] = {}
    verdicts = verdicts or {}
    for f in sorted(runs_dir.glob("*/*/run-*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        case = next((c for c in cases if c["id"] == rec["case"]), None)
        if case is None:
            continue
        g = grade_run(case, rec.get("findings"), tolerance)
        m = meta.setdefault(rec["config"], {"cost": 0.0, "costed": 0, "seconds": 0.0, "runs": 0,
                                            "unparsed": 0, "graded": 0, "confirmed": 0})
        if case.get("set") != "control" and g["outcome"] != "unparsed":
            m["graded"] += 1
            v = verdicts.get((rec["config"], rec["case"], _run_number(f)))
            if v and v.get("started") == rec.get("started"):
                g = {**g, "outcome": v["verdict"]}
                m["confirmed"] += 1
        results.setdefault((rec["config"], rec["case"]), []).append(g)
        m["runs"] += 1
        m["seconds"] += rec.get("seconds") or 0
        m["unparsed"] += g["outcome"] == "unparsed"
        if rec.get("cost_usd") is not None:
            m["cost"] += rec["cost_usd"]
            m["costed"] += 1
    configs = sorted(meta)
    lines = ["| Case | Set | " + " | ".join(configs) + " |",
             "| --- | --- | " + " | ".join("---" for _ in configs) + " |"]
    caught = {c: 0 for c in configs}
    noise = {c: [] for c in configs}
    for case in cases:
        cells = []
        for c in configs:
            gs = results.get((c, case["id"]), [])
            if not gs:
                cells.append("")
                continue
            if case.get("set") == "control":
                severe = [g["severe"] for g in gs if g["outcome"] != "unparsed"]
                noise[c] += severe
                cells.append(f"{sum(severe) / len(severe):.1f} severe" if severe else "unparsed")
                continue
            k = sum(g["outcome"] == "catch" for g in gs)
            s = sum(g["outcome"] == "seen" for g in gs)
            caught[c] += k * 2 > len(gs)  # a majority of runs
            cells.append(f"{k}/{len(gs)}" + (f" (+{s} seen)" if s else ""))
        if any(cells):
            lines.append(f"| {case['id']} | {case.get('set', '')} | " + " | ".join(cells) + " |")
    lines += ["", "| Config | Cases caught (majority of runs) | Severe findings per control run | "
                  "Runs | Unparsed | Mean cost (USD) | Mean minutes |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for c in configs:
        m = meta[c]
        n = noise[c]
        cost = f"{m['cost'] / m['costed']:.2f}" if m["costed"] else "n/a"
        lines.append(f"| {c} | {caught[c]} | {sum(n) / len(n):.2f} | {m['runs']} | {m['unparsed']} | "
                     f"{cost} | {m['seconds'] / m['runs'] / 60:.1f} |" if n else
                     f"| {c} | {caught[c]} | n/a | {m['runs']} | {m['unparsed']} | {cost} | "
                     f"{m['seconds'] / m['runs'] / 60:.1f} |")
    lines.append("")
    for c in configs:
        m = meta[c]
        if m["graded"] and m["confirmed"] < m["graded"]:
            lines.append(f"{c}: {m['graded'] - m['confirmed']} of {m['graded']} case runs are graded by "
                         "line only; check them with `matches` and record verdicts (--verdicts).")
        elif m["graded"]:
            lines.append(f"{c}: every case run has a person's verdict.")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("anchors")
    a.add_argument("--repo", required=True)
    a.add_argument("--intro", required=True)
    a.add_argument("--fix", required=True)
    a.add_argument("--include-tests", action="store_true", help="also anchor in tests and docs")
    c = sub.add_parser("controls")
    c.add_argument("--repo", required=True)
    c.add_argument("--cases", required=True)
    c.add_argument("--count", type=int, default=10)
    c.add_argument("--min-age-days", type=int, default=30)
    c.add_argument("--min-lines", type=int, default=100)
    c.add_argument("--max-lines", type=int, default=1800)
    s = sub.add_parser("snapshot")
    for flag in ("--repo", "--cases", "--case", "--config", "--out"):
        s.add_argument(flag, required=True)
    r = sub.add_parser("run")
    r.add_argument("--repo", required=True)
    r.add_argument("--cases", required=True)
    r.add_argument("--config", required=True)
    r.add_argument("--set", action="append", help="case sets to run (repeatable); default all")
    r.add_argument("--case", action="append", help="case ids to run (repeatable)")
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--out", default="runs")
    r.add_argument("--keep", action="store_true", help="keep snapshots, for debugging")
    r.add_argument("--skip-probe", action="store_true",
                   help="run without a passed probe for this config (isolation unproven)")
    r.add_argument("--allow-unchecked", action="store_true",
                   help="run cases whose anchors nobody has checked (results are not comparable)")
    pr = sub.add_parser("probe", help="check that the reviewer cannot read outside the snapshot")
    for flag in ("--repo", "--cases", "--case", "--config"):
        pr.add_argument(flag, required=True)
    pr.add_argument("--out", default="runs")
    g = sub.add_parser("report")
    g.add_argument("--cases", required=True)
    g.add_argument("--out", default="runs")
    g.add_argument("--tolerance", type=int, default=10)
    g.add_argument("--verdicts", help="a person's verdicts on graded runs (JSONL; see `matches`)")
    mt = sub.add_parser("matches", help="list what each case run was graded on, with a verdict line")
    mt.add_argument("--cases", required=True)
    mt.add_argument("--out", default="runs")
    mt.add_argument("--tolerance", type=int, default=10)
    mt.add_argument("--config", help="only this config's runs")
    args = p.parse_args(argv)

    if args.cmd == "anchors":
        print(json.dumps(draft_anchors(args.repo, args.intro, args.fix,
                                       include_tests=args.include_tests), indent=1))
    elif args.cmd == "controls":
        for ctl in pick_controls(args.repo, load_cases(args.cases), args.count, args.min_age_days,
                                 args.min_lines, args.max_lines):
            print(json.dumps(ctl))
    elif args.cmd == "snapshot":
        case = next(x for x in load_cases(args.cases) if x["id"] == args.case)
        config, cdir = load_config(args.config)
        build_snapshot(args.repo, case, config, cdir, Path(args.out))
        write_review_files(Path(args.out))
        neutralise_symlinks(Path(args.out))
        print(args.out)
    elif args.cmd == "run":
        config, cdir = load_config(args.config)
        cases = [x for x in load_cases(args.cases)
                 if (not args.set or x.get("set") in args.set) and (not args.case or x["id"] in args.case)
                 and (x.get("set") == "control" or x.get("anchors"))]
        unchecked = [x["id"] for x in cases if x.get("set") != "control"
                     and not all(a.get("checked") for a in x.get("anchors", []))]
        if unchecked and not args.allow_unchecked:
            print(f"cases with unchecked anchors: {', '.join(unchecked)}. Check each anchor against "
                  "the PR and set \"checked\": true, or pass --allow-unchecked.", file=sys.stderr)
            return 1
        if not cases:
            print("no runnable cases (cases need anchors, or set: control)", file=sys.stderr)
            return 1
        marker = Path(args.out) / config["name"] / "probe.json"
        passed = marker.exists() and json.loads(marker.read_text(encoding="utf-8")).get("config") \
            == config_digest(config, cdir)
        if not passed and not args.skip_probe:
            print(f"no passed probe for {config['name']} as it is now: run `review_eval.py probe` "
                  "first (or pass --skip-probe)", file=sys.stderr)
            return 1
        def stop(signum, frame):
            raise SystemExit(f"stopped by signal {signum}")  # runs run_one's cleanup
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, stop)
        errors_in_a_row = 0
        for case in cases:
            for n in range(1, args.runs + 1):
                out = Path(args.out) / config["name"] / case["id"] / f"run-{n}.json"
                if out.exists():
                    continue  # resumable: finished runs are kept
                rec = run_one(args.repo, case, config, cdir, out, keep=args.keep)
                errors_in_a_row = errors_in_a_row + 1 if rec["infra_error"] else 0
                if errors_in_a_row >= 3:
                    print("3 failed runs in a row (see error-*.json): stopping. Fix the cause and "
                          "re-run; finished runs are kept.", file=sys.stderr)
                    return 2
                found = "failed, kept as error-%d.json" % n if rec["infra_error"] else \
                    "unparsed" if rec["findings"] is None else f"{len(rec['findings'])} findings"
                print(f"{config['name']} case {case['id']} run {n}: exit {rec['exit']}, {found}, "
                      f"{rec['seconds']}s" + (f", ${rec['cost_usd']:.2f}" if rec.get("cost_usd") else ""),
                      flush=True)
    elif args.cmd == "probe":
        config, cdir = load_config(args.config)
        case = next(x for x in load_cases(args.cases) if x["id"] == args.case)
        return 0 if probe(args.repo, case, config, cdir, Path(args.out)) else 1
    elif args.cmd == "report":
        sys.stdout.write(report(load_cases(args.cases), Path(args.out), args.tolerance,
                                load_verdicts(args.verdicts)))
    elif args.cmd == "matches":
        sys.stdout.write(matches(load_cases(args.cases), Path(args.out), args.tolerance, args.config))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Preview, append, and verify a project-scoped Worktrunk layout preference.

Python 3.11+, Worktrunk 0.81+. No hooks, checkout changes, or shell edits.
Existing project tables require a deliberate scoped edit instead of TOML rewriting.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import uuid


TEMPLATE = "{{ repo_path }}/../worktrees/{{ branch | sanitize }}"
MIN_VERSION = (0, 81, 0)


class Refusal(Exception):
    """An actionable refusal without dumping configuration or secret values."""


def run(argv):
    env = dict(os.environ, NO_COLOR="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    # Worktrunk starts its own Git processes. Command-scoped environment entries
    # reach those children; a flag on this Python process would not. Preserve
    # inherited entries (for example a caller's protocol.allow=never constraint).
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError as exc:
        raise Refusal("GIT_CONFIG_COUNT must be a non-negative integer.") from exc
    if count < 0:
        raise Refusal("GIT_CONFIG_COUNT must be a non-negative integer.")
    for offset, (key, value) in enumerate((("core.fsmonitor", "false"),
                                         ("core.hooksPath", os.devnull))):
        env[f"GIT_CONFIG_KEY_{count + offset}"] = key
        env[f"GIT_CONFIG_VALUE_{count + offset}"] = value
    env["GIT_CONFIG_COUNT"] = str(count + 2)
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                       timeout=60, check=False)
    if p.returncode:
        # Worktrunk errors can quote user-config commands; never echo raw stderr.
        raise Refusal(f"Command failed (exit {p.returncode}): {argv[0]} {argv[1:3]}. "
                      "Inspect it locally; configuration contents were not logged.")
    return p.stdout.decode("utf-8")


def wt_json(binary, repo, *args):
    try:
        return json.loads(run([binary, "-C", str(repo), *args]))
    except (ValueError, UnicodeError) as exc:
        raise Refusal("Worktrunk returned an unsupported JSON response.") from exc


def context(repo):
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        if key in os.environ:
            raise Refusal(f"Unset {key}; the repository must be selected with --repo.")
    binary = shutil.which("wt")
    if binary is None:
        raise Refusal("Worktrunk is missing. On macOS: brew install worktrunk")
    version = run([binary, "--version"]).strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < MIN_VERSION:
        raise Refusal("This helper needs Worktrunk >= 0.81.0 for non-mutating path evaluation.")
    git_version = run(["git", "--version"])
    git_match = re.search(r"(\d+)\.(\d+)", git_version)
    if not git_match or tuple(map(int, git_match.groups())) < (2, 43):
        raise Refusal("Worktrunk requires Git >= 2.43; on macOS: brew install git")
    if run(["git", "-C", str(repo), "rev-parse", "--is-bare-repository"]).strip() != "false":
        raise Refusal("Bare repositories need a separate layout plan; no configuration was changed.")
    raw_common = Path(run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute",
                           "--git-common-dir"]).strip())
    if raw_common.is_symlink():
        raise Refusal("A symlinked Git common directory needs a separate plan.")
    common = raw_common.resolve()
    main = common.parent
    if common.name != ".git" or not common.is_dir() or main.name != "main":
        raise Refusal("Migrate first: expected a conventional primary checkout at <container>/main.")
    if (main.parent / "worktrees").is_symlink() or not (main.parent / "worktrees").is_dir():
        raise Refusal("Missing <container>/worktrees; finish the layout migration first.")
    inventory = run(["git", "-C", str(main), "worktree", "list", "--porcelain", "-z"])
    roots = [Path(item[9:]) for item in inventory.split("\0") if item.startswith("worktree ")]
    if not roots or roots[0].resolve() != main:
        raise Refusal("Git's primary checkout does not match the canonical main/ directory.")
    for root in roots:
        if root.is_symlink() or not root.is_dir():
            raise Refusal("A registered checkout is missing or symlinked; finish Git migration first.")
        if root.resolve() != main and root.resolve().parent != main.parent / "worktrees":
            raise Refusal(f"A registered worktree is outside the canonical worktrees directory: {root}")
    # Reuse the migration preflight without running its CLI or creating bytecode
    # files inside a repository-local skill installation.
    safety = runpy.run_path(str(Path(__file__).with_name("project_worktrees.py")),
                            run_name="project_worktrees_safety")
    for root in roots:
        try:
            safety["check_external_filters"](root)
        except safety["Refusal"] as exc:
            raise Refusal(str(exc)) from exc
    info = wt_json(binary, main, "config", "show", "--format=json")
    identifier = info.get("project", {}).get("identifier")
    filename = info.get("user", {}).get("path")
    if not isinstance(identifier, str) or not identifier or not isinstance(filename, str):
        raise Refusal("Worktrunk did not report a project identifier and user-config path.")
    if any(ord(c) < 32 for c in identifier):
        raise Refusal("Project identifier contains control characters; inspect the remote locally.")
    return binary, main, identifier, Path(filename).expanduser().absolute(), version


def read_config(path):
    if path.is_symlink():
        raise Refusal(f"User config is a symlink; edit its intended target deliberately: {path}")
    if not path.exists():
        return b"", None, {}
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise Refusal("User config must be a regular file with one hard link for automatic editing.")
    raw = path.read_bytes()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise Refusal("User config is not valid UTF-8 TOML; resolve that before editing.") from exc
    identity = (st.st_dev, st.st_ino, st.st_mtime_ns, stat.S_IMODE(st.st_mode),
                hashlib.sha256(raw).hexdigest())
    return raw, identity, data


def candidate_config(raw, data, identifier):
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise Refusal("The existing projects value is not a TOML table.")
    project = projects.get(identifier)
    if project is not None:
        if isinstance(project, dict) and project.get("worktree-path") == TEMPLATE:
            return raw, False
        raise Refusal(
            "An existing project table needs a scoped edit. Preserve its other settings and set "
            f"worktree-path in [projects.{json.dumps(identifier)}] to {json.dumps(TEMPLATE)}. "
            "Then rerun with --check. No configuration was changed."
        )
    block = ("\n\n# Project Worktrees: conventional main/ + worktrees/ layout.\n"
             f"[projects.{json.dumps(identifier, ensure_ascii=False)}]\n"
             f"worktree-path = {json.dumps(TEMPLATE)}\n")
    proposed = raw + block.encode("utf-8")
    try:
        parsed = tomllib.loads(proposed.decode("utf-8"))
    except ValueError as exc:
        raise Refusal("This TOML structure cannot be extended by appending a project table. "
                      "Make a scoped edit, then rerun --check. No configuration was changed.") from exc
    if parsed.get("projects", {}).get(identifier, {}).get("worktree-path") != TEMPLATE:
        raise Refusal("Candidate TOML did not round-trip correctly; no configuration was changed.")
    return proposed, True


def exclusive_write(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def save_config(path, original, identity, proposed):
    # Serialize our own writes; another program still must not edit this file concurrently.
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".project-worktrees.lock")
    try:
        exclusive_write(lock, (str(os.getpid()) + "\n").encode())
    except FileExistsError as exc:
        raise Refusal(f"Config lock already exists: {lock}. Investigate its owner; do not overwrite it.") from exc
    temp = None
    backup = None
    try:
        if read_config(path)[:2] != (original, identity):
            raise Refusal("User config changed after preview; rerun the command.")
        if identity is not None:
            backup = path.with_name(path.name + ".project-worktrees-" + uuid.uuid4().hex[:12] + ".bak")
            exclusive_write(backup, original)
        fd, temp = tempfile.mkstemp(prefix=".project-worktrees-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(proposed)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), identity[3] if identity else 0o600)
        if read_config(path)[:2] != (original, identity):
            raise Refusal("User config changed during preparation; the original was not overwritten.")
        if identity is None:
            # Unlike replace(), link() cannot clobber a newly created destination.
            os.link(temp, path)
            os.unlink(temp)
        else:
            os.replace(temp, path)
        temp = None
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
        lock.unlink()
    return str(backup) if backup else None


def verify(binary, main, identifier):
    # Evaluate a fresh branch name; this does not create a branch or worktree or run hooks.
    branch = "project-worktrees-probe-" + uuid.uuid4().hex[:12] + "/nested"
    expected = (main.parent / "worktrees" / branch.replace("/", "-")).resolve()
    expression = "{{ expected_worktree_path_of_branch(" + json.dumps(branch) + ") }}"
    git_data = run(["git", "-C", str(main), "worktree", "list", "--porcelain", "-z"])
    roots = [Path(item[9:]).resolve() for item in git_data.split("\0") if item.startswith("worktree ")]
    for root in roots:
        if root != main and root.parent != main.parent / "worktrees":
            raise Refusal(f"A registered worktree is outside the canonical worktrees directory: {root}")
    # Check both primary and linked context, catching incorrectly relative templates.
    for root in roots[:1] + [p for p in roots if p != main][:1]:
        result = wt_json(binary, root, "step", "eval", expression, "--format=json")
        value = result.get("result") if isinstance(result, dict) else None
        if not isinstance(value, str) or Path(value).resolve() != expected:
            raise Refusal("Worktrunk resolved an unexpected path. Inspect wt config show and environment overrides.")
    listed = wt_json(binary, main,
                     "--config-set", "list.full=false", "--config-set", "list.summary=false",
                     "--config-set", "list.branches=false", "--config-set", "list.remotes=false",
                     "--config-set", "list.json-schema=2", "list", "--format=json")
    items = listed.get("items") if isinstance(listed, dict) else None
    if not isinstance(items, list):
        raise Refusal("Unsupported Worktrunk list schema; expected schema 2 items.")
    wt_paths = {Path(item["worktree"]["path"]).resolve() for item in items
                if isinstance(item, dict) and isinstance(item.get("worktree"), dict)
                and isinstance(item["worktree"].get("path"), str)}
    if wt_paths != set(roots):
        raise Refusal("Worktrunk and Git disagree about registered worktrees; inspect locally.")
    return {"registered_worktrees": len(roots), "path_template_verified": True,
            "probe_created": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="a checkout in an already migrated project")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="append a new project preference and verify")
    mode.add_argument("--check", action="store_true", help="verify effective paths and registered worktrees")
    args = parser.parse_args()
    try:
        if args.apply and "WORKTRUNK_WORKTREE_PATH" in os.environ:
            raise Refusal("Unset WORKTRUNK_WORKTREE_PATH before saving a durable project preference. "
                          "Use --check to inspect the effective override.")
        binary, primary, identifier, path, version = context(Path(args.repo).expanduser().resolve())
        summary = {"project_identifier": identifier, "config_path": str(path),
                   "worktree_path": TEMPLATE, "worktrunk_version": version,
                   "scope": "all local clones with this Worktrunk project identifier"}
        if args.check:
            summary.update(verify(binary, primary, identifier))
            summary["status"] = "verified"
        else:
            raw, identity, data = read_config(path)
            proposed, changed = candidate_config(raw, data, identifier)
            summary["change_needed"] = changed
            summary["status"] = "preview"
            if args.apply:
                summary["backup"] = save_config(path, raw, identity, proposed) if changed else None
                # If effective verification fails, preserve the edit/backup and report it.
                print(json.dumps(dict(summary, status="saved" if changed else "unchanged"), indent=2),
                      flush=True)
                summary.update(verify(binary, primary, identifier))
                summary["status"] = "verified"
        print(json.dumps(summary, indent=2))
        return 0
    except (Refusal, OSError, subprocess.TimeoutExpired) as exc:
        print(f"project-worktrees: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

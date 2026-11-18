#!/usr/bin/env python3
"""Plan, relocate, and verify conventional Git worktrees. Python 3.11+, stdlib only.

The plan is a complete content fingerprint, not a backup. All filesystem moves
are same-filesystem renames. No repository-supplied executable is intentionally
run. Worktrunk configuration is a separate operation.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import unicodedata
import uuid


SCHEMA = 1
LOCK_NAME = "project-worktrees-migration.lock"
GIT_ENV_VARS = (
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
)


class Refusal(Exception):
    """A condition the tool cannot safely automate."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: str | Path) -> Path:
    return Path(value).expanduser().absolute().resolve()


def repository_path(value: str) -> Path:
    path = Path(value).expanduser().absolute()
    if path.is_symlink():
        raise Refusal(f"Use the physical repository path, not a symlink: {path}")
    return path.resolve()


def explicit_path(value: str, label: str) -> Path:
    path = Path(value).expanduser().absolute()
    if path.is_symlink():
        raise Refusal(f"{label} cannot be a symlink: {path}")
    return path.resolve()


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def key(value: str) -> str:
    """Conservative collision key for default macOS filesystems."""
    return unicodedata.normalize("NFC", value).casefold()


def identity(path: Path) -> dict:
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or stat.S_ISLNK(s.st_mode):
        raise Refusal(f"Expected an ordinary directory: {path}")
    return {"dev": s.st_dev, "ino": s.st_ino}


def git(path: Path, *args: str, allow: tuple = (0,), input: bytes | None = None) -> bytes:
    env = os.environ.copy()
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    p = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
         "-C", str(path), *args], env=env, input=input, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if p.returncode not in allow:
        # Git errors can contain credential-bearing remote URLs/config values.
        # Commands here are local; print the operation but not arbitrary stderr.
        raise Refusal(f"git {' '.join(args[:2])} failed (exit {p.returncode}) in {path}; inspect that repository locally")
    return p.stdout


def git_text(path: Path, *args: str, allow: tuple = (0,)) -> str:
    return os.fsdecode(git(path, *args, allow=allow)).strip()


def check_environment() -> None:
    present = [name for name in GIT_ENV_VARS if name in os.environ]
    if present:
        raise Refusal("Unset repository-routing Git environment variables: " + ", ".join(present))
    if sys.version_info < (3, 11):
        raise Refusal("Python 3.11 or newer is required")
    p = subprocess.run(["git", "--version"], capture_output=True, check=False)
    match = re.search(rb"(\d+)\.(\d+)", p.stdout)
    if not match or tuple(map(int, match.groups())) < (2, 36):
        raise Refusal("Git 2.36 or newer is required for NUL-delimited worktree inventory")


def file_hash(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise Refusal(f"Cannot fingerprint a special file: {path}")
        h = hashlib.sha256()
        while data := os.read(fd, 1024 * 1024):
            h.update(data)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, f) != getattr(after, f) for f in fields):
            raise Refusal(f"File changed while fingerprinting; stop writers and retry: {path}")
        return h.hexdigest()
    finally:
        os.close(fd)


def optional_hash(path: Path) -> str | None:
    if not lexists(path):
        return None
    if path.is_symlink():
        raise Refusal(f"Git metadata is a symlink: {path}")
    return file_hash(path)


def fingerprint(root: Path) -> tuple[dict, list[str]]:
    files: dict = {}
    nested: list[str] = []

    def walk(directory: Path) -> None:
        before = directory.stat()
        for entry in sorted(os.scandir(directory), key=lambda e: os.fsencode(e.name)):
            path = Path(entry.path)
            rel = os.fsdecode(os.path.relpath(path, root))
            if directory == root and entry.name == ".git":
                continue
            if entry.name == ".git":
                nested.append(rel)
                # A nested repository must be handled separately. Do not read
                # its database, credentials, or potentially enormous objects.
                continue
            s = path.lstat()
            mode = stat.S_IMODE(s.st_mode)
            if stat.S_ISLNK(s.st_mode):
                files[rel] = {"type": "symlink", "mode": mode, "target": os.readlink(path)}
            elif stat.S_ISDIR(s.st_mode):
                files[rel] = {"type": "directory", "mode": mode}
                walk(path)
            elif stat.S_ISREG(s.st_mode):
                files[rel] = {"type": "file", "mode": mode, "size": s.st_size,
                              "sha256": file_hash(path)}
            else:
                raise Refusal(f"Special file needs a manual procedure: {path}")
        after = directory.stat()
        if before.st_mtime_ns != after.st_mtime_ns or before.st_ino != after.st_ino:
            raise Refusal(f"Directory changed while fingerprinting; stop writers and retry: {directory}")

    walk(root)
    return files, nested


def parse_worktrees(data: bytes) -> list[dict]:
    result: list[dict] = []
    record: dict = {}
    for token in data.split(b"\0"):
        if not token:
            if record:
                result.append(record)
                record = {}
            continue
        name, _, value = token.partition(b" ")
        record[os.fsdecode(name)] = os.fsdecode(value) if value else True
    if record:
        result.append(record)
    return result


def check_external_filters(root: Path) -> None:
    """Refuse executable filters used by tracked files without executing them.

    git status can run clean/process filters while comparing the working tree
    with the index. Disabling hooks and fsmonitor does not prevent this. Inspect
    effective attributes in both the working tree and index, and only consider
    drivers actually assigned to tracked/staged paths. An unused global Git LFS
    installation is therefore harmless. Never print filter names or commands.
    """
    tracked = git(root, "ls-files", "-z")
    if not tracked:
        return
    drivers: set[bytes] = set()
    for options in ((), ("--cached",)):
        attributes = git(root, "check-attr", "-z", *options, "--stdin", "filter", input=tracked)
        tokens = attributes.split(b"\0")
        if tokens[-1:] == [b""]:
            tokens.pop()
        if len(tokens) % 3:
            raise Refusal(f"Cannot safely inspect tracked-file filter attributes: {root}")
        # Attribute sentinel words are also legal driver names in assignments;
        # looking them up is conservative and does not execute a command.
        drivers.update(tokens[2::3])
    for driver in sorted(drivers):
        for operation in ("clean", "process"):
            value = git(root, "config", "--null", "--get-all",
                        "filter." + os.fsdecode(driver) + "." + operation, allow=(0, 1))
            if any(command for command in value.split(b"\0")):
                raise Refusal(
                    f"Tracked files use an executable Git clean/process filter; "
                    f"a manual migration procedure is required: {root}"
                )


def check_worktree_includes(root: Path) -> None:
    """Check each checkout's own configuration, not just the shared config."""
    enabled = git_text(root, "config", "--bool", "--get", "extensions.worktreeConfig", allow=(0, 1))
    if enabled != "true":
        return
    includes = git(root, "config", "--worktree", "--null", "--get-all", "include.path", allow=(0, 1))
    if any(not Path(os.fsdecode(value)).expanduser().is_absolute()
           for value in includes.split(b"\0") if value):
        raise Refusal(f"Relative worktree-scoped Git includes need a manual procedure: {root}")
    if git(root, "config", "--worktree", "--name-only", "--get-regexp", r"^includeif\.", allow=(0, 1)):
        raise Refusal(f"Conditional worktree-scoped Git includes need a manual procedure: {root}")


def snapshot(root: Path) -> dict:
    gd = canonical(git_text(root, "rev-parse", "--absolute-git-dir"))
    entries = git(root, "ls-files", "--stage", "-z")
    if any(token.startswith(b"160000 ") for token in entries.split(b"\0")):
        raise Refusal(f"Submodules/gitlinks require a manual migration: {root}")
    if (root / ".gitmodules").exists():
        raise Refusal(f"Repository declares submodules: {root}")
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-apply", "rebase-merge", "BISECT_LOG", "sequencer"):
        if lexists(gd / name):
            raise Refusal(f"Git operation in progress ({name}): {root}")
    for name in ("core.worktree", "core.sparseCheckout"):
        value = git_text(root, "config", "--get", name, allow=(0, 1))
        if value and (name == "core.worktree" or value.lower() not in ("false", "no", "0")):
            raise Refusal(f"Unsupported Git setting {name}: {root}")
    check_worktree_includes(root)
    check_external_filters(root)
    status = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching")
    files, nested = fingerprint(root)
    if nested:
        raise Refusal(f"Nested repositories need a separate migration: {root} ({len(nested)} found)")
    return {
        "files": files,
        "status_sha256": digest(status),
        "status_records": len([x for x in status.split(b"\0") if x]),
        "index_sha256": optional_hash(gd / "index"),
        "index_entries_sha256": digest(entries),
        "index_flags_sha256": digest(git(root, "ls-files", "-v", "-z")),
        "head_file_sha256": optional_hash(gd / "HEAD"),
        "worktree_config_sha256": optional_hash(gd / "config.worktree"),
    }


def audit(repo: Path, own_lock: bool = False) -> dict:
    check_environment()
    if not repo.is_dir():
        raise Refusal(f"Repository directory does not exist: {repo}")
    records = parse_worktrees(git(repo, "worktree", "list", "--porcelain", "-z"))
    if not records:
        raise Refusal("No registered Git worktrees found")
    common = Path(git_text(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    common = common.resolve()
    primary = canonical(str(records[0]["worktree"]))
    report = {"schema": SCHEMA, "kind": "project-worktrees-audit",
              "repo": {"primary": str(primary), "common": str(common)},
              "worktrees": [], "blockers": [], "warnings": [
                  "The script does not inspect running processes. Stop other agents, editors, Git operations and development servers before applying.",
                  "Moving paths can invalidate external editor sessions, virtual environments, absolute symlinks, scripts, caches and development services; these are not rewritten.",
              ]}
    blockers = report["blockers"]
    if records[0].get("bare"):
        blockers.append("Bare repositories are audit-only; automatic conversion is not supported")
        return report
    if common != primary / ".git" or not (primary / ".git").is_dir() or (primary / ".git").is_symlink():
        blockers.append("The primary checkout must contain an ordinary .git directory (no separated gitdir)")
        return report
    for record in records:
        path = Path(str(record["worktree"]))
        if path.is_symlink():
            blockers.append(f"Worktree root is a symlink: {path}")
        if not path.is_dir() or record.get("prunable"):
            blockers.append(f"Missing/prunable worktree needs repair first: {path}")
        if record.get("locked"):
            blockers.append(f"Locked worktree needs its owner's attention: {path}")
    paths = [canonical(str(r["worktree"])) for r in records]
    for i, path in enumerate(paths):
        if any(inside(path, other) or inside(other, path) for other in paths[:i]):
            blockers.append("Nested registered worktrees need a separate migration")
    if len({key(str(p)) for p in paths}) != len(paths):
        blockers.append("Worktree paths collide after Unicode normalization/case folding")
    for directory, dirs, names in os.walk(common, followlinks=False):
        # Lock files anywhere under shared/per-worktree metadata can identify
        # an active Git operation. Do not follow object alternates or symlinks.
        dirs[:] = [d for d in dirs if d not in ("objects", "hooks") and not Path(directory, d).is_symlink()]
        for name in names:
            if name.endswith(".lock") and not (own_lock and Path(directory, name) == common / LOCK_NAME):
                blockers.append(f"Git lock exists: {Path(directory, name)}")
    alternates = common / "objects" / "info" / "alternates"
    if alternates.exists() and alternates.stat().st_size:
        blockers.append("Object alternates/shared object stores need a manual procedure")
    if git(repo, "config", "--name-only", "--get-regexp", r"^includeif\.", allow=(0, 1)):
        blockers.append("Conditional Git includes may change with relocated paths; resolve them before automatic migration")
    local_includes = git(repo, "config", "--local", "--get-all", "include.path", allow=(0, 1))
    if any(not Path(os.fsdecode(x)).expanduser().is_absolute() for x in local_includes.splitlines()):
        blockers.append("Relative repository-local Git includes need a manual procedure")
    report["repo"].update({
        "identity": identity(primary),
        "common_identity": identity(common),
        "refs_sha256": digest(git(primary, "show-ref", "--head", allow=(0, 1))),
        "config_sha256": optional_hash(common / "config"),
        "packed_refs_sha256": optional_hash(common / "packed-refs"),
    })
    if blockers:
        return report
    for i, record in enumerate(records):
        path = paths[i]
        try:
            resolved_common = Path(git_text(path, "rev-parse", "--git-common-dir"))
            if not resolved_common.is_absolute():
                resolved_common = path / resolved_common
            if resolved_common.resolve() != common:
                raise Refusal(f"Worktree common directory is inconsistent: {path}")
            if not i and path != primary:
                raise Refusal("Cannot identify primary worktree")
            if i and ((path / ".git").is_symlink() or not (path / ".git").is_file()):
                raise Refusal(f"Linked checkout must have an ordinary .git file: {path}")
            state = snapshot(path)
            report["worktrees"].append({
                "source": str(path), "primary": i == 0,
                "branch": record.get("branch"), "head": record.get("HEAD"),
                "identity": identity(path), "snapshot": state,
            })
        except (Refusal, OSError) as exc:
            blockers.append(str(exc))
    return report


def slug(tree: dict) -> str:
    branch = tree["branch"]
    if branch:
        if not branch.startswith("refs/heads/"):
            raise Refusal("Unsupported branch ref")
        value = branch[len("refs/heads/"):]
    else:
        value = "detached-" + (tree["head"] or "unborn")[:12] + "-" + Path(tree["source"]).name
    value = value.replace("/", "-").replace("\\", "-")
    if value in ("", ".", "..") or "\0" in value or len(os.fsencode(value)) > 255:
        raise Refusal("Branch does not map to a safe directory name")
    return value


def nearest_existing(path: Path) -> Path:
    while not lexists(path):
        if path == path.parent:
            raise Refusal(f"Cannot find an existing ancestor for {path}")
        path = path.parent
    if not path.is_dir() or path.is_symlink():
        raise Refusal(f"Destination parent is not an ordinary directory: {path}")
    return path


def inspect_path(path: Path, allowed: set[Path], skip_below: Path | None = None) -> None:
    """Reject destinations that exist, alias another path, or cross symlinks."""
    if path in allowed:
        return
    if skip_below is not None and inside(path, skip_below):
        # The whole source directory will first be renamed out of this path;
        # current source content is not an existing destination occupant.
        return
    if lexists(path):
        raise Refusal(f"Destination already exists; it will not be overwritten: {path}")
    for component in (path, *path.parents):
        if skip_below is not None and inside(component, skip_below):
            continue
        if lexists(component) and component.is_symlink():
            raise Refusal(f"Destination contains a symlink: {component}")
        parent = component.parent
        if parent.is_dir():
            for name in os.listdir(parent):
                other = parent / name
                if key(name) == key(component.name) and name != component.name:
                    raise Refusal(f"Case/Unicode path collision: {component} and {other}")
        if component == component.parent:
            break


def add_mkdirs(ops: list[dict], path: Path, planned: set[Path]) -> None:
    missing = []
    current = path
    while not current.exists() and current not in planned:
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        ops.append({"action": "mkdir", "destination": str(directory)})
        planned.add(directory)


def make_plan(report: dict, container: Path, plan_id: str) -> dict:
    if report["blockers"]:
        raise Refusal("Cannot migrate:\n- " + "\n- ".join(report["blockers"]))
    primary = Path(report["repo"]["primary"])
    if inside(container, primary) and container != primary:
        raise Refusal("The container cannot be nested inside the current checkout; choose the checkout path itself or an external container")
    if any(inside(container, Path(t["source"])) for t in report["worktrees"][1:]):
        raise Refusal("The container cannot be inside a linked worktree")
    if lexists(container) and container != primary:
        if container.is_symlink() or not container.is_dir():
            raise Refusal("Container must be an ordinary directory")
        if lexists(container / ".git"):
            raise Refusal("The target container is already a Git checkout")
    anchor = nearest_existing(container if container != primary else container.parent)
    if anchor.stat().st_dev != primary.stat().st_dev:
        raise Refusal("Migration must stay on one filesystem; cross-device moves are not supported")
    if anchor != primary and not inside(anchor, primary):
        enclosing = git_text(anchor, "rev-parse", "--show-toplevel", allow=(0, 128))
        if enclosing:
            raise Refusal("The project container cannot be nested inside another Git checkout")
    trees = []
    destinations: set[str] = set()
    sources = {Path(t["source"]) for t in report["worktrees"]}
    for original in report["worktrees"]:
        tree = dict(original)
        destination = container / ("main" if tree["primary"] else "worktrees")
        if not tree["primary"]:
            destination /= slug(tree)
        tree["destination"] = str(destination)
        if key(str(destination)) in destinations:
            raise Refusal("Two worktrees map to the same directory after branch sanitization/case folding; rename a branch or handle them manually")
        destinations.add(key(str(destination)))
        source = Path(tree["source"])
        if source.stat().st_dev != anchor.stat().st_dev:
            raise Refusal(f"Worktree is on a different filesystem: {source}")
        if source != destination:
            inspect_path(destination, set(), primary if container == primary else None)
            if any(destination == s or inside(destination, s) for s in sources if s != primary):
                raise Refusal("A destination overlaps an existing linked worktree")
        trees.append(tree)
    worktrees_dir = container / "worktrees"
    if container != primary and lexists(worktrees_dir) and (not worktrees_dir.is_dir() or worktrees_dir.is_symlink()):
        raise Refusal("worktrees/ must be an ordinary directory")
    ops: list[dict] = []
    planned: set[Path] = set()
    main = container / "main"
    if container == primary:
        staging = primary.parent / ("." + primary.name + ".project-worktrees-" + plan_id[:12])
        inspect_path(staging, set())
        ops.append({"action": "rename", "source": str(primary), "destination": str(staging)})
        ops.append({"action": "mkdir", "destination": str(container)})
        planned.add(container)
        ops.append({"action": "rename", "source": str(staging), "destination": str(main)})
    elif main != primary:
        add_mkdirs(ops, container, planned)
        ops.append({"action": "rename", "source": str(primary), "destination": str(main)})
    if main != primary:
        ops.append({"action": "repair", "repository": str(main)})
    if container == primary:
        # Original content (including a directory named worktrees/) is now
        # inside main/; the new container has only the newly moved checkout.
        ops.append({"action": "mkdir", "destination": str(worktrees_dir)})
        planned.add(worktrees_dir)
    else:
        add_mkdirs(ops, worktrees_dir, planned)
    for tree in trees[1:]:
        if tree["source"] != tree["destination"]:
            ops.append({"action": "worktree-move", "repository": str(main),
                        "source": tree["source"], "destination": tree["destination"]})
    plan = {"schema": SCHEMA, "kind": "project-worktrees-plan", "id": plan_id,
            "repo": report["repo"], "container": str(container),
            "worktrees": trees, "operations": ops, "warnings": report["warnings"]}
    plan["digest"] = digest(json_bytes(plan))
    return plan


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path: Path, value: dict) -> None:
    if not path.parent.is_dir():
        raise Refusal(f"Create the plan/journal parent directory first: {path.parent}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = json_bytes(value)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    fsync_directory(path.parent)


def update_journal(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    write_new(temp, value)
    os.replace(temp, path)
    fsync_directory(path.parent)


def external_state_path(path: Path, plan: dict) -> None:
    roots = [Path(t["source"]) for t in plan["worktrees"]] + [Path(plan["container"])]
    if any(inside(path, root) for root in roots):
        raise Refusal("Keep plans and journals outside all source worktrees and the target container")
    for op in plan["operations"]:
        if op["action"] == "rename" and inside(path, Path(op["destination"])):
            raise Refusal("Plan/journal cannot be placed inside a staging directory")


def load_plan(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise Refusal("Plan must be an ordinary JSON file")
    plan = json.loads(path.read_text(encoding="utf-8"))
    validate_plan(plan)
    return plan


def validate_plan(plan: dict) -> None:
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA or plan.get("kind") != "project-worktrees-plan":
        raise Refusal("Unsupported plan format")
    expected = plan.get("digest")
    without_digest = {k: v for k, v in plan.items() if k != "digest"}
    if expected != digest(json_bytes(without_digest)):
        raise Refusal("Plan changed after creation; generate a fresh plan")
    if not isinstance(plan.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", plan["id"]):
        raise Refusal("Invalid plan identity")


def rename_exclusive(source: Path, destination: Path) -> None:
    """Atomic no-replace rename on the supported Linux and macOS hosts."""
    if lexists(destination):
        raise Refusal(f"Destination now exists: {destination}")
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = fn(os.fsencode(source), os.fsencode(destination), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        fn = libc.renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = fn(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
    else:
        raise Refusal("Atomic no-overwrite rename is supported only on macOS and Linux with renameat2")
    if result:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), str(source), str(destination))
    fsync_directory(source.parent)
    if source.parent != destination.parent:
        fsync_directory(destination.parent)


def verify_plan(plan: dict) -> dict:
    main = Path(plan["container"]) / "main"
    after = audit(main, own_lock=True)
    if after["blockers"]:
        raise Refusal("Post-migration audit failed:\n- " + "\n- ".join(after["blockers"]))
    expected_repo = dict(plan["repo"])
    expected_repo.update(primary=str(main), common=str(main / ".git"))
    if after["repo"] != expected_repo:
        raise Refusal("Shared Git metadata, refs, or primary directory identity changed")
    expected = {tree["destination"]: tree for tree in plan["worktrees"]}
    actual = {tree["source"]: tree for tree in after["worktrees"]}
    if set(expected) != set(actual):
        raise Refusal("Registered worktree set differs from the planned destinations")
    for path, before in expected.items():
        new = actual[path]
        for field in ("primary", "branch", "head", "identity", "snapshot"):
            if before[field] != new[field]:
                raise Refusal(f"Verification mismatch ({field}) at {path}; preserve both plan and journal for investigation")
    return {"result": "verified", "primary": str(main), "worktrees": sorted(actual),
            "files_verified": sum(len(t["snapshot"]["files"]) for t in actual.values())}


def apply_plan(path: Path, quiescent: bool) -> dict:
    if not quiescent:
        raise Refusal("--quiescent is required: stop other writers, Git operations, editors and services before applying")
    plan = load_plan(path)
    external_state_path(path, plan)
    journal_path = Path(str(path) + ".journal.json")
    external_state_path(journal_path, plan)
    if lexists(journal_path):
        raise Refusal(f"Journal already exists; inspect it before a new migration: {journal_path}")
    primary = Path(plan["repo"]["primary"])
    common = Path(plan["repo"]["common"])
    # Rebuild every mapping and operation from live Git data. The plan never
    # supplies a shell command, and edited/stale mappings cannot be executed.
    fresh = make_plan(audit(primary), Path(plan["container"]), plan["id"])
    if fresh != plan:
        raise Refusal("Repository state or destination changed since planning; generate a fresh plan")
    lock = common / LOCK_NAME
    lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    journal = {"schema": SCHEMA, "kind": "project-worktrees-journal", "state": "preparing",
               "plan": plan, "operations": [], "current_primary": str(primary),
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    successful = False
    try:
        os.write(lock_fd, json_bytes({"pid": os.getpid(), "plan": str(path), "journal": str(journal_path)}))
        os.fsync(lock_fd)
        write_new(journal_path, journal)
        # Status/hashing was read-only; now hold the advisory migration lock and
        # repeat before the first filesystem mutation to close planning races.
        fresh = make_plan(audit(primary, own_lock=True), Path(plan["container"]), plan["id"])
        if fresh != plan:
            raise Refusal("Repository changed while acquiring the migration lock; preserve journal and retry after inspection")
        os.chdir(journal_path.parent)
        live_sources = {t["source"]: t["identity"] for t in plan["worktrees"]}
        journal["state"] = "applying"
        update_journal(journal_path, journal)
        for op in plan["operations"]:
            record = {**op, "state": "intent"}
            journal["operations"].append(record)
            update_journal(journal_path, journal)
            action = op["action"]
            if action == "mkdir":
                destination = Path(op["destination"])
                destination.mkdir()  # no exist_ok, no recursive implicit writes
                fsync_directory(destination.parent)
            elif action in ("rename", "worktree-move"):
                source, destination = Path(op["source"]), Path(op["destination"])
                if str(source) not in live_sources or identity(source) != live_sources[str(source)]:
                    raise Refusal(f"Source directory changed before move: {source}")
                inspect_path(destination, set())
                if destination.parent.stat().st_dev != source.stat().st_dev:
                    raise Refusal("A destination crossed filesystems after planning")
                if action == "rename":
                    rename_exclusive(source, destination)
                    if str(source) == journal["current_primary"]:
                        journal["current_primary"] = str(destination)
                else:
                    git(Path(op["repository"]), "worktree", "move", str(source), str(destination))
                    fsync_directory(source.parent)
                    if source.parent != destination.parent:
                        fsync_directory(destination.parent)
                live_sources[str(destination)] = live_sources.pop(str(source))
            elif action == "repair":
                git(Path(op["repository"]), "worktree", "repair")
            else:
                raise Refusal("Unknown migration operation")
            record["state"] = "done"
            update_journal(journal_path, journal)
        result = verify_plan(plan)
        journal["state"] = "verified"
        journal["verification"] = result
        update_journal(journal_path, journal)
        successful = True
        return {**result, "changed": bool(plan["operations"]), "journal": str(journal_path)}
    except BaseException as exc:
        journal["state"] = "interrupted"
        journal["error"] = str(exc)
        journal["recovery"] = (
            "Stop writers. Inspect operation states and each source/destination; an intent may already have completed. "
            "Locate the original primary by its recorded device/inode and .git directory. "
            "Run git -C <current-primary> worktree repair after confirming its location. "
            "Do not delete paths or remove the migration lock until ownership and data preservation are verified. "
            "No automatic rollback or resume is performed."
        )
        if lexists(journal_path):
            update_journal(journal_path, journal)
        raise Refusal(f"Migration stopped without deleting data. Journal: {journal_path}. {exc}") from exc
    finally:
        os.close(lock_fd)
        if successful:
            lock = Path(journal["current_primary"]) / ".git" / LOCK_NAME
            lock.unlink()
            fsync_directory(lock.parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("audit", help="Read-only inventory and content fingerprint")
    p.add_argument("--repo", default=".")
    p.add_argument("--json", action="store_true", help="Print full JSON (default output is a concise inventory)")
    p = sub.add_parser("plan", help="Create a read-only migration plan")
    p.add_argument("--repo", default=".")
    p.add_argument("--container")
    p.add_argument("--output", "--plan", dest="output")
    p = sub.add_parser("apply", help="Apply one unchanged plan")
    p.add_argument("--plan", required=True)
    p.add_argument("--quiescent", action="store_true")
    p = sub.add_parser("verify", help="Verify migration against its preserved baseline")
    p.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            result = audit(repository_path(args.repo))
            if not args.json:
                result = {"primary": result["repo"]["primary"],
                          "worktrees": [{"path": t["source"], "branch": t["branch"],
                                         "files": len(t["snapshot"]["files"]),
                                         "status_records": t["snapshot"]["status_records"]}
                                        for t in result["worktrees"]],
                          "blockers": result["blockers"], "warnings": result["warnings"]}
            print(json_bytes(result).decode("utf-8"), end="")
            return 2 if result["blockers"] else 0
        if args.command == "plan":
            report = audit(repository_path(args.repo))
            primary = Path(report["repo"]["primary"])
            container = explicit_path(args.container, "Container") if args.container else (primary.parent if primary.name == "main" else primary)
            plan = make_plan(report, container, uuid.uuid4().hex)
            if args.output:
                output = explicit_path(args.output, "Plan output")
                external_state_path(output, plan)
                write_new(output, plan)
                result = {"result": "planned", "plan": str(output), "container": str(container),
                          "moves": [{"source": t["source"], "destination": t["destination"]}
                                    for t in plan["worktrees"]], "operations": len(plan["operations"]),
                          "warnings": plan["warnings"]}
            else:
                result = plan
        elif args.command == "apply":
            result = apply_plan(explicit_path(args.plan, "Plan"), args.quiescent)
        else:
            manifest = explicit_path(args.manifest, "Manifest")
            if manifest.is_symlink() or not manifest.is_file():
                raise Refusal("Manifest must be an ordinary JSON journal")
            journal = json.loads(manifest.read_text(encoding="utf-8"))
            if journal.get("schema") != SCHEMA or journal.get("kind") != "project-worktrees-journal":
                raise Refusal("Unsupported journal format")
            validate_plan(journal["plan"])
            # Verification is intentionally usable on an interrupted journal
            # once a human has repaired paths. It never clears the lock.
            result = verify_plan(journal["plan"])
        print(json_bytes(result).decode("utf-8"), end="")
        return 0
    except (Refusal, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"project-worktrees: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

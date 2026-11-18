#!/usr/bin/env python3
"""Real-Git migration regression tests; all repositories are disposable fixtures.

Run from anywhere with Python 3.11+ and Git on PATH. No package installation,
network access, existing repository mutation, or Worktrunk executable is needed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills/project-worktrees/scripts"
ENGINE = SCRIPTS / "project_worktrees.py"


def files_at(root: Path) -> dict[str, tuple]:
    """Capture actual fixture bytes/modes independently of the migration manifest."""
    result = {}
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        directory = Path(directory)
        if directory == root:
            subdirs[:] = [name for name in subdirs if name != ".git"]
            filenames = [name for name in filenames if name != ".git"]
        for name in list(subdirs):
            path = directory / name
            if path.is_symlink():
                result[str(path.relative_to(root))] = ("link", os.readlink(path))
                subdirs.remove(name)
            else:
                result[str(path.relative_to(root)) + "/"] = (
                    "directory", stat.S_IMODE(path.stat().st_mode)
                )
        for name in filenames:
            path = directory / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                result[str(path.relative_to(root))] = ("link", os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                result[str(path.relative_to(root))] = (
                    "file", stat.S_IMODE(info.st_mode), path.read_bytes()
                )
            else:
                result[str(path.relative_to(root))] = ("special", info.st_mode)
    return result


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="project-worktrees-eval-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "WT_", "WORKTRUNK_"))
        }
        self.env.update({
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_AUTHOR_DATE": "2026-01-02T03:04:05+00:00",
            "GIT_COMMITTER_DATE": "2026-01-02T03:04:05+00:00",
            "LC_ALL": "C",
        })
        self.git_root = self.base / "gits"
        self.git_root.mkdir()
        self.repo = self.git_root / "sample"
        self.plan_path = self.base / "migration-plan.json"
        self.journal_path = Path(str(self.plan_path) + ".journal.json")

    def command(self, argv, *, cwd=None, ok=True, env=None):
        result = subprocess.run(
            [str(value) for value in argv],
            cwd=cwd or self.base,
            env=env or self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=40,
        )
        if ok:
            self.assertEqual(
                result.returncode,
                0,
                f"{argv!r}\nstdout: {result.stdout.decode(errors='replace')}"
                f"\nstderr: {result.stderr.decode(errors='replace')}",
            )
        return result

    def git(self, repo, *args, ok=True):
        return self.command(["git", "-C", repo, *args], ok=ok)

    def run_engine(self, *args, ok=True, cwd=None, env=None):
        return self.command(
            [sys.executable, "-I", ENGINE, *args], ok=ok, cwd=cwd, env=env
        )

    def init_repo(self, path=None, branch="trunk") -> Path:
        path = Path(path or self.repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.command(["git", "init", "--quiet", "--initial-branch", branch, path])
        self.git(path, "config", "user.name", "Migration Fixture")
        self.git(path, "config", "user.email", "fixture@example.invalid")
        self.git(path, "config", "commit.gpgsign", "false")
        (path / "tracked.txt").write_text("committed\n", encoding="utf-8")
        (path / "binary.dat").write_bytes(bytes(range(256)))
        (path / ".gitignore").write_text(".env\ncache/\n", encoding="utf-8")
        (path / "executable.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (path / "executable.sh").chmod(0o755)
        self.git(path, "add", ".")
        self.git(path, "commit", "--quiet", "-m", "Fixture initial commit")
        return path

    def linked(self, branch="feature/auth", *, path=None, detached=False) -> Path:
        path = Path(path or self.git_root / "old-feature")
        args = ["worktree", "add", "--quiet"]
        if detached:
            args += ["--detach", path, "HEAD"]
        else:
            args += ["-b", branch, path, "HEAD"]
        self.git(self.repo, *args)
        return path

    def dirty(self, repo: Path, label="work") -> None:
        (repo / "tracked.txt").write_text(f"staged {label}\n", encoding="utf-8")
        self.git(repo, "add", "tracked.txt")
        (repo / "tracked.txt").write_text(f"unstaged {label}\n", encoding="utf-8")
        (repo / "untracked café.txt").write_bytes(b"untracked\x00\xff\n" + label.encode())
        (repo / ".env").write_bytes(b"LOCAL_FIXTURE_ONLY=never-publish\n")
        (repo / "cache").mkdir()
        (repo / "cache" / "ignored.bin").write_bytes(b"ignored\x00\xfe")
        (repo / "cache" / "empty").mkdir()
        (repo / "relative-link").symlink_to("tracked.txt")
        (repo / "dangling-link").symlink_to("does-not-exist")

    def capture(self, repo: Path) -> dict:
        return {
            "files": files_at(repo),
            "head": self.git(repo, "rev-parse", "HEAD").stdout,
            "branch": self.git(repo, "symbolic-ref", "-q", "HEAD", ok=False).stdout,
            "status": self.git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout,
            "index": self.git(repo, "ls-files", "--stage", "-z").stdout,
            "flags": self.git(repo, "ls-files", "-v", "-z").stdout,
            "refs": self.git(repo, "show-ref", "--head").stdout,
        }

    def plan(self, *, repo=None, container=None, output=None, ok=True):
        args = ["plan", "--repo", repo or self.repo, "--output", output or self.plan_path]
        if container is not None:
            args += ["--container", container]
        return self.run_engine(*args, ok=ok)

    def apply(self, *, ok=True):
        return self.run_engine("apply", "--plan", self.plan_path, "--quiescent", ok=ok)

    def verify(self, *, ok=True):
        return self.run_engine("verify", "--manifest", self.journal_path, ok=ok)

    def assert_refused(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout.decode(errors="replace"))

    def test_dirty_primary_and_linked_preserve_every_fixture_byte(self):
        self.init_repo()
        linked = self.linked()
        self.dirty(self.repo, "primary")
        self.dirty(linked, "linked")
        primary_before, linked_before = self.capture(self.repo), self.capture(linked)
        self.plan()
        self.assertEqual(self.capture(self.repo), primary_before, "planning mutated primary")
        self.assertEqual(self.capture(linked), linked_before, "planning mutated linked checkout")
        self.apply()
        main, feature = self.repo / "main", self.repo / "worktrees/feature-auth"
        self.assertEqual(self.capture(main), primary_before)
        self.assertEqual(self.capture(feature), linked_before)
        self.assertFalse(linked.exists())
        self.assertFalse((self.repo / ".git").exists())
        common = self.git(feature, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        self.assertEqual(Path(os.fsdecode(common)).resolve(), (main / ".git").resolve())
        self.verify()

    def test_non_default_primary_branch_is_not_switched(self):
        self.init_repo(branch="ongoing-work")
        self.git(self.repo, "branch", "main")
        before = self.capture(self.repo)
        self.plan()
        self.apply()
        self.assertEqual(self.capture(self.repo / "main"), before)
        self.verify()

    def test_detached_linked_head_and_local_files_survive(self):
        self.init_repo()
        linked = self.linked(detached=True)
        self.dirty(linked, "detached")
        before = self.capture(linked)
        self.plan()
        plan = json.loads(self.plan_path.read_text())
        destination = Path(next(item["destination"] for item in plan["worktrees"] if not item["primary"]))
        self.apply()
        self.assertEqual(self.capture(destination), before)
        self.assert_refused(self.git(destination, "symbolic-ref", "-q", "HEAD", ok=False))
        self.verify()

    def test_spaces_unicode_and_leading_dash_in_checkout_paths(self):
        self.repo = self.git_root / "-mon projet café"
        self.init_repo()
        linked = self.linked(branch="feature/café", path=self.git_root / "-old tree café")
        self.dirty(linked)
        before = self.capture(linked)
        self.plan()
        self.apply()
        self.assertEqual(self.capture(self.repo / "worktrees/feature-café"), before)
        self.verify()

    def test_a_distinct_container_can_be_selected(self):
        self.init_repo()
        linked = self.linked()
        before = self.capture(linked)
        container = self.git_root / "sample-home"
        self.plan(container=container)
        self.apply()
        self.assertFalse(self.repo.exists())
        self.assertEqual(self.capture(container / "worktrees/feature-auth"), before)
        self.verify()

    def test_audit_can_start_in_a_linked_checkout(self):
        self.init_repo()
        linked = self.linked()
        result = self.run_engine("audit", "--repo", linked, "--json")
        self.assertIsInstance(json.loads(result.stdout), dict)
        self.plan(repo=linked)
        self.apply()
        self.assertTrue((self.repo / "main/.git").is_dir())
        self.verify()

    def test_compliant_layout_is_idempotent(self):
        container = self.git_root / "sample"
        self.repo = container / "main"
        self.init_repo()
        (container / "worktrees").mkdir()
        linked = self.linked(path=container / "worktrees/feature-auth")
        primary_before, linked_before = self.capture(self.repo), self.capture(linked)
        self.plan()
        self.apply()
        self.assertEqual(self.capture(self.repo), primary_before)
        self.assertEqual(self.capture(linked), linked_before)
        self.verify()
        second = self.base / "second.json"
        self.plan(output=second)
        self.run_engine("apply", "--plan", second, "--quiescent")
        self.assertEqual(self.capture(self.repo), primary_before)
        self.assertEqual(self.capture(linked), linked_before)

    def test_primary_with_no_linked_worktrees(self):
        self.init_repo()
        before = self.capture(self.repo)
        self.plan()
        self.apply()
        self.assertEqual(self.capture(self.repo / "main"), before)
        self.assertTrue((self.repo / "worktrees").is_dir())
        self.verify()

    def test_wrapper_can_run_from_the_checkout_it_moves(self):
        self.init_repo()
        bundled = self.repo / ".agents/skills/project-worktrees/scripts"
        bundled.mkdir(parents=True)
        for name in ("project_worktrees.py", "audit.sh", "migrate.sh", "verify.sh"):
            shutil.copy2(SCRIPTS / name, bundled / name)
        before = self.capture(self.repo)
        self.command(["sh", bundled / "migrate.sh", "--repo", ".", "--plan", self.plan_path], cwd=self.repo)
        self.command(["sh", bundled / "migrate.sh", "--apply", self.plan_path, "--quiescent"], cwd=self.repo)
        self.assertEqual(self.capture(self.repo / "main"), before)
        new_scripts = self.repo / "main/.agents/skills/project-worktrees/scripts"
        self.command(["sh", new_scripts / "verify.sh", "--manifest", self.journal_path], cwd=self.repo / "main")

    def test_apply_requires_quiescence_attestation(self):
        self.init_repo()
        before = self.capture(self.repo)
        self.plan()
        self.assert_refused(self.run_engine("apply", "--plan", self.plan_path, ok=False))
        self.assertEqual(self.capture(self.repo), before)

    def test_an_ignored_file_changed_after_plan_blocks_migration(self):
        self.init_repo()
        (self.repo / ".env").write_text("initial fixture\n")
        self.plan()
        (self.repo / ".env").write_text("changed fixture\n")
        before = self.capture(self.repo)
        self.assert_refused(self.apply(ok=False))
        self.assertEqual(self.capture(self.repo), before)
        self.assertFalse((self.repo / "main").exists())

    def test_a_commit_created_after_plan_blocks_migration(self):
        self.init_repo()
        self.plan()
        self.git(self.repo, "commit", "--quiet", "--allow-empty", "-m", "After plan")
        before = self.capture(self.repo)
        self.assert_refused(self.apply(ok=False))
        self.assertEqual(self.capture(self.repo), before)

    def test_new_worktree_after_plan_blocks_migration(self):
        self.init_repo()
        self.plan()
        linked = self.linked()
        before = self.capture(linked)
        self.assert_refused(self.apply(ok=False))
        self.assertEqual(self.capture(linked), before)
        self.assertTrue((self.repo / ".git").is_dir())

    def test_new_destination_after_plan_is_not_overwritten(self):
        self.init_repo()
        container = self.git_root / "new-home"
        self.plan(container=container)
        (container / "main").mkdir(parents=True)
        marker = container / "main/do-not-touch"
        marker.write_text("existing destination\n")
        before = self.capture(self.repo)
        self.assert_refused(self.apply(ok=False))
        self.assertEqual(marker.read_text(), "existing destination\n")
        self.assertEqual(self.capture(self.repo), before)

    def test_sanitized_branch_collision_is_refused(self):
        self.init_repo()
        first = self.linked(branch="feature/auth")
        second = self.linked(branch="feature-auth", path=self.git_root / "other-feature")
        first_before, second_before = self.capture(first), self.capture(second)
        self.assert_refused(self.plan(ok=False))
        self.assertEqual(self.capture(first), first_before)
        self.assertEqual(self.capture(second), second_before)

    def test_casefold_branch_collision_is_refused_even_on_linux(self):
        self.init_repo()
        self.linked(branch="Feature/One")
        self.linked(branch="feature-one", path=self.git_root / "case-feature")
        self.assert_refused(self.plan(ok=False))
        self.assertTrue((self.repo / ".git").is_dir())

    def test_unicode_normalization_collision_is_refused(self):
        self.init_repo()
        self.linked(branch="feature/café")
        self.linked(branch="feature-cafe\u0301", path=self.git_root / "unicode-feature")
        self.assert_refused(self.plan(ok=False))
        self.assertTrue((self.repo / ".git").is_dir())

    def test_manifests_do_not_store_file_contents(self):
        self.init_repo()
        self.dirty(self.repo)
        self.plan()
        self.apply()
        for path in (self.plan_path, self.journal_path):
            self.assertNotIn("LOCAL_FIXTURE_ONLY=never-publish", path.read_text())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_edited_plan_cannot_redirect_a_worktree(self):
        self.init_repo()
        self.linked()
        self.plan()
        plan = json.loads(self.plan_path.read_text())
        target = self.base / "unrelated"
        target.mkdir()
        (target / "keep").write_text("untouched\n")
        plan["worktrees"][1]["destination"] = str(target)
        self.plan_path.write_text(json.dumps(plan))
        before = self.capture(self.repo)
        self.assert_refused(self.apply(ok=False))
        self.assertEqual(self.capture(self.repo), before)
        self.assertEqual((target / "keep").read_text(), "untouched\n")

    def test_existing_plan_file_is_not_overwritten(self):
        self.init_repo()
        self.plan_path.write_text("important existing file\n")
        self.assert_refused(self.plan(ok=False))
        self.assertEqual(self.plan_path.read_text(), "important existing file\n")

    def test_plan_inside_checkout_is_refused(self):
        self.init_repo()
        output = self.repo / "plan.json"
        self.assert_refused(self.plan(output=output, ok=False))
        self.assertFalse(output.exists())

    def test_locked_worktree_is_not_unlocked_or_moved(self):
        self.init_repo()
        linked = self.linked()
        self.git(self.repo, "worktree", "lock", "--reason", "fixture hold", linked)
        before = self.capture(linked)
        self.assert_refused(self.plan(ok=False))
        self.assertIn(b"locked fixture hold", self.git(self.repo, "worktree", "list", "--porcelain").stdout)
        self.assertEqual(self.capture(linked), before)

    def test_missing_worktree_is_not_silently_pruned(self):
        self.init_repo()
        linked = self.linked()
        parked = self.git_root / "temporarily-offline"
        linked.rename(parked)
        listed = self.git(self.repo, "worktree", "list", "--porcelain").stdout
        self.assert_refused(self.plan(ok=False))
        self.assertEqual(self.git(self.repo, "worktree", "list", "--porcelain").stdout, listed)
        self.assertTrue((parked / "tracked.txt").exists())

    def test_bare_repository_is_audited_but_not_converted(self):
        bare = self.git_root / "bare.git"
        self.command(["git", "init", "--quiet", "--bare", bare])
        result = self.run_engine("audit", "--repo", bare, "--json", ok=False)
        self.assert_refused(result)
        self.assertIn("bare", result.stdout.decode().lower() + result.stderr.decode().lower())
        self.assert_refused(self.plan(repo=bare, ok=False))
        self.assertTrue((bare / "HEAD").exists())

    def test_nested_repository_is_refused(self):
        self.init_repo()
        self.init_repo(self.repo / "nested")
        before = self.capture(self.repo)
        self.assert_refused(self.plan(ok=False))
        self.assertEqual(self.capture(self.repo), before)

    def test_nested_worktree_is_refused(self):
        self.init_repo()
        linked = self.linked(path=self.repo / ".worktrees/feature")
        before = self.capture(linked)
        self.assert_refused(self.plan(ok=False))
        self.assertEqual(self.capture(linked), before)

    def test_submodule_is_refused_without_touching_its_files(self):
        self.init_repo()
        source = self.init_repo(self.base / "module-source")
        self.git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "--quiet", source, "module")
        self.git(self.repo, "commit", "--quiet", "-m", "Submodule fixture")
        before = self.capture(self.repo)
        self.assert_refused(self.plan(ok=False))
        self.assertEqual(self.capture(self.repo), before)
        self.assertEqual((self.repo / "module/tracked.txt").read_text(), "committed\n")

    def test_index_lock_is_refused_and_preserved(self):
        self.init_repo()
        lock = self.repo / ".git/index.lock"
        lock.write_text("fixture-lock\n")
        self.assert_refused(self.plan(ok=False))
        self.assertEqual(lock.read_text(), "fixture-lock\n")

    def test_in_progress_rebase_is_refused(self):
        self.init_repo()
        state = self.repo / ".git/rebase-merge"
        state.mkdir()
        (state / "head-name").write_text("refs/heads/trunk\n")
        self.assert_refused(self.plan(ok=False))
        self.assertTrue(state.is_dir())

    def test_core_worktree_override_is_refused(self):
        self.init_repo()
        self.git(self.repo, "config", "core.worktree", str(self.repo))
        self.assert_refused(self.plan(ok=False))
        self.assertEqual(self.git(self.repo, "config", "core.worktree").stdout.strip(), os.fsencode(self.repo))

    def test_active_clean_filter_is_refused_without_execution(self):
        self.init_repo()
        (self.repo / ".gitattributes").write_text("tracked.txt filter=fixture-driver\n")
        self.git(self.repo, "add", ".gitattributes")
        self.git(self.repo, "commit", "--quiet", "-m", "Add filter attribute before executable configuration")
        marker = self.repo / ".env"
        marker.write_text("IGNORED_FILE_MUST_SURVIVE\n")
        command = "printf FILTER_EXECUTED > " + shlex.quote(str(marker)) + "; cat"
        self.git(self.repo, "config", "filter.fixture-driver.clean", command)
        (self.repo / "tracked.txt").write_text("dirty enough to require content comparison\n")
        before = files_at(self.repo)  # Calling Git status here could itself run the filter.
        result = self.plan(ok=False)
        self.assert_refused(result)
        self.assertIn(b"clean/process filter", result.stderr)
        self.assertEqual(files_at(self.repo), before)
        self.assertNotIn(command.encode(), result.stderr + result.stdout)
        self.assertFalse(self.plan_path.exists())

    def test_linked_worktree_process_filter_is_refused_without_execution(self):
        self.init_repo()
        linked = self.linked()
        self.git(self.repo, "config", "extensions.worktreeConfig", "true")
        (linked / ".gitattributes").write_text("tracked.txt filter=fixture-process\n")
        self.git(linked, "add", ".gitattributes")
        marker = linked / ".env"
        marker.write_text("IGNORED_LINKED_FILE_MUST_SURVIVE\n")
        command = "printf PROCESS_EXECUTED > " + shlex.quote(str(marker)) + "; exit 1"
        self.git(linked, "config", "--worktree", "filter.fixture-process.process", command)
        (linked / "tracked.txt").write_text("dirty linked tracked file\n")
        primary_before, linked_before = files_at(self.repo), files_at(linked)
        result = self.plan(ok=False)
        self.assert_refused(result)
        self.assertIn(b"clean/process filter", result.stderr)
        self.assertEqual(files_at(self.repo), primary_before)
        self.assertEqual(files_at(linked), linked_before)
        self.assertNotIn(command.encode(), result.stderr + result.stdout)
        self.assertFalse(self.plan_path.exists())

    def test_unused_global_lfs_driver_does_not_block_migration(self):
        self.init_repo()
        self.env["GIT_CONFIG_GLOBAL"] = str(self.home / "global-gitconfig")
        marker = self.base / "must-not-execute"
        command = "printf UNUSED_DRIVER_EXECUTED > " + shlex.quote(str(marker)) + "; cat"
        self.command(["git", "config", "--global", "filter.lfs.clean", command])
        self.command(["git", "config", "--global", "filter.lfs.process", command])
        self.command(["git", "config", "--global", "filter.lfs.required", "true"])
        before = self.capture(self.repo)
        self.plan()
        self.apply()
        self.verify()
        self.assertFalse(marker.exists())
        self.assertEqual(self.capture(self.repo / "main"), before)

    def test_relative_linked_worktree_include_is_refused_before_moving(self):
        self.init_repo()
        linked = self.linked()
        self.git(self.repo, "config", "extensions.worktreeConfig", "true")
        gitdir = Path(os.fsdecode(self.git(linked, "rev-parse", "--absolute-git-dir").stdout.strip()))
        extra = gitdir.parent / "shared-extra.cfg"
        extra.write_text("[core]\n\tautocrlf = false\n")
        self.git(linked, "config", "--worktree", "include.path", "../shared-extra.cfg")
        primary_before, linked_before = self.capture(self.repo), self.capture(linked)
        include_before = (gitdir / "config.worktree").read_bytes()
        result = self.plan(ok=False)
        self.assert_refused(result)
        self.assertIn(b"Relative worktree-scoped Git includes", result.stderr)
        self.assertEqual(self.capture(self.repo), primary_before)
        self.assertEqual(self.capture(linked), linked_before)
        self.assertEqual((gitdir / "config.worktree").read_bytes(), include_before)
        self.assertFalse(self.plan_path.exists())

    def test_symlink_checkout_root_is_refused(self):
        self.init_repo()
        alias = self.git_root / "alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        before = self.capture(self.repo)
        self.assert_refused(self.plan(repo=alias, ok=False))
        self.assertEqual(self.capture(self.repo), before)

    def test_symlink_container_root_is_refused(self):
        self.init_repo()
        target = self.git_root / "real-container"
        target.mkdir()
        alias = self.git_root / "container-alias"
        alias.symlink_to(target, target_is_directory=True)
        before = self.capture(self.repo)
        self.assert_refused(self.plan(container=alias, ok=False))
        self.assertEqual(self.capture(self.repo), before)
        self.assertEqual(list(target.iterdir()), [])

    def test_verify_detects_post_migration_file_changes(self):
        self.init_repo()
        self.plan()
        self.apply()
        self.verify()
        changed = self.repo / "main/tracked.txt"
        changed.write_text("changed after migration\n")
        self.assert_refused(self.verify(ok=False))
        self.assertEqual(changed.read_text(), "changed after migration\n")

    def test_partial_move_failure_keeps_data_and_journal(self):
        self.init_repo()
        linked = self.linked()
        self.dirty(self.repo)
        self.dirty(linked)
        primary_before, linked_before = self.capture(self.repo), self.capture(linked)
        self.plan()
        injection = """
import runpy, subprocess, sys
original_run = subprocess.run
def fail_one_move(argv, *args, **kwargs):
    tokens = [str(value) for value in argv] if isinstance(argv, (list, tuple)) else []
    if any(tokens[index:index+2] == ['worktree', 'move'] for index in range(len(tokens)-1)):
        raise subprocess.CalledProcessError(1, argv, output=b'', stderr=b'injected move failure')
    return original_run(argv, *args, **kwargs)
subprocess.run = fail_one_move
engine, plan = sys.argv[1:]
sys.argv = [engine, 'apply', '--plan', plan, '--quiescent']
runpy.run_path(engine, run_name='__main__')
"""
        result = self.command([sys.executable, "-I", "-c", injection, ENGINE, self.plan_path], ok=False)
        self.assert_refused(result)
        self.assertTrue(self.journal_path.is_file(), result.stderr.decode(errors="replace"))
        journal = json.loads(self.journal_path.read_text())
        self.assertIsInstance(journal, dict)
        self.assertEqual(journal["state"], "interrupted")
        self.assertTrue(journal["error"])
        self.assertEqual(journal["operations"][-1]["action"], "worktree-move")
        self.assertEqual(journal["operations"][-1]["state"], "intent")
        self.assertEqual(self.capture(self.repo / "main"), primary_before)
        self.assertEqual(self.capture(linked), linked_before)
        self.assert_refused(self.verify(ok=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)

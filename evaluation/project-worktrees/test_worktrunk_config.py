#!/usr/bin/env python3
"""Worktrunk configuration integration tests using disposable local repositories.

Set WORKTRUNK_TEST_BINARY to an existing Worktrunk >= 0.81 executable, or put
wt on PATH. Integration cases skip when it is unavailable; nothing is installed.
Each child process has isolated home, Git, and Worktrunk configuration. No real
remote is contacted, shell profile edited, or development hook executed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "skills/project-worktrees/scripts/configure_worktrunk.py"
TEMPLATE = "{{ repo_path }}/../worktrees/{{ branch | sanitize }}"
IDENTIFIER = "github.com/worktrunk-fixture/sample"
WT_BINARY = os.environ.get("WORKTRUNK_TEST_BINARY") or shutil.which("wt")


def fixture_environment(base: Path) -> dict[str, str]:
    """Isolate subprocess configuration without changing the runner environment."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "WT_", "WORKTRUNK_"))
    }
    fixture_home = base / "home"
    fixture_home.mkdir()
    env.update({
        "HOME": str(fixture_home),
        "XDG_CONFIG_HOME": str(fixture_home / ".config"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_AUTHOR_DATE": "2026-01-02T03:04:05+00:00",
        "GIT_COMMITTER_DATE": "2026-01-02T03:04:05+00:00",
        "WORKTRUNK_SYSTEM_CONFIG_PATH": str(base / "absent-system.toml"),
        "WORKTRUNK_CONFIG_PATH": str(fixture_home / ".config/worktrunk/config.toml"),
        "NO_COLOR": "1",
        "LC_ALL": "C",
    })
    return env


def json_documents(text: str) -> list[dict]:
    """The helper prints a saved receipt followed by a verification receipt."""
    decoder = json.JSONDecoder()
    documents = []
    text = text.lstrip()
    while text:
        value, offset = decoder.raw_decode(text)
        documents.append(value)
        text = text[offset:].lstrip()
    return documents


class MissingWorktrunkTests(unittest.TestCase):
    def test_missing_binary_refuses_without_creating_configuration(self):
        with tempfile.TemporaryDirectory(prefix="project-worktrunk-missing-") as raw:
            base = Path(raw).resolve()
            env = fixture_environment(base)
            env["PATH"] = str(base / "empty-bin")
            result = subprocess.run(
                [sys.executable, "-I", str(HELPER), "--repo", str(base), "--apply"],
                env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Worktrunk is missing", result.stderr)
            self.assertFalse(Path(env["WORKTRUNK_CONFIG_PATH"]).exists())


@unittest.skipUnless(WT_BINARY, "Set WORKTRUNK_TEST_BINARY or install wt to run integration tests")
class WorktrunkConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = Path(WT_BINARY).resolve()
        if not cls.binary.is_file() or not os.access(cls.binary, os.X_OK):
            raise unittest.SkipTest("WORKTRUNK_TEST_BINARY is not an executable file")
        version = subprocess.run(
            [str(cls.binary), "--version"], capture_output=True, text=True, timeout=20,
        )
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", version.stdout)
        if version.returncode or not match or tuple(map(int, match.groups())) < (0, 81, 0):
            raise unittest.SkipTest("Integration tests require Worktrunk >= 0.81.0")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="project-worktrunk-config-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.env = fixture_environment(self.base)
        self.config = Path(self.env["WORKTRUNK_CONFIG_PATH"])
        self.bin_dir = self.base / "bin"
        self.bin_dir.mkdir()
        (self.bin_dir / "wt").symlink_to(self.binary)
        self.env["PATH"] = str(self.bin_dir) + os.pathsep + self.env.get("PATH", "")
        self.container = self.base / "sample project"
        self.repo = self.container / "main"
        self.repo.mkdir(parents=True)
        (self.container / "worktrees").mkdir()
        self.git(self.repo, "init", "--quiet", "--initial-branch=main")
        self.git(self.repo, "config", "user.name", "Worktrunk Fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.invalid")
        self.git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "tracked.txt").write_text("fixture\n", encoding="utf-8")
        self.git(self.repo, "add", "tracked.txt")
        self.git(self.repo, "commit", "--quiet", "-m", "Fixture initial commit")
        self.git(self.repo, "remote", "add", "origin", f"https://{IDENTIFIER}.git")
        # A normal clone has these refs. Supply them locally so default-branch
        # discovery never needs Worktrunk's git ls-remote fallback.
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        # Any unexpected forge invocation fails locally rather than reaching a network.
        self.forge_marker = self.base / "forge-was-run"
        self.write_executable(
            self.bin_dir / "gh",
            "#!/bin/sh\n: > " + shlex.quote(str(self.forge_marker)) + "\nexit 73\n",
        )

    def command(self, args, *, env=None, ok=True):
        result = subprocess.run(
            [str(value) for value in args], cwd=self.base, env=env or self.env,
            capture_output=True, text=True, timeout=40,
        )
        if ok:
            self.assertEqual(
                result.returncode, 0,
                f"{args!r}\nstdout: {result.stdout}\nstderr: {result.stderr}",
            )
        return result

    def git(self, repo, *args):
        return self.command(["git", "-C", repo, *args])

    def helper(self, *args, repo=None, ok=True, env=None):
        return self.command(
            [sys.executable, "-I", HELPER, "--repo", repo or self.repo, *args],
            ok=ok, env=env,
        )

    def write_config(self, raw: str | bytes):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_bytes(raw.encode("utf-8") if isinstance(raw, str) else raw)

    def project_table(self, template=TEMPLATE):
        return f"[projects.{json.dumps(IDENTIFIER)}]\nworktree-path = {json.dumps(template)}\n"

    def write_executable(self, path, content):
        path.write_text(content, encoding="utf-8")
        path.chmod(0o700)

    def linked(self):
        path = self.container / "worktrees/feature-auth"
        self.git(self.repo, "worktree", "add", "--quiet", "-b", "feature/auth", path)
        return path

    def git_inventory(self):
        return (
            self.git(self.repo, "show-ref", "--head").stdout,
            self.git(self.repo, "worktree", "list", "--porcelain").stdout,
        )

    def assert_no_auxiliary_files(self):
        if self.config.parent.exists():
            self.assertEqual(sorted(p.name for p in self.config.parent.iterdir()), [self.config.name])

    def test_preview_without_config_makes_no_configuration_or_branch_changes(self):
        before = self.git_inventory()
        result = json_documents(self.helper().stdout)[-1]
        self.assertEqual(result["status"], "preview")
        self.assertTrue(result["change_needed"])
        self.assertEqual(result["project_identifier"], IDENTIFIER)
        self.assertFalse(self.config.exists())
        self.assertEqual(before, self.git_inventory())
        self.assertFalse(self.forge_marker.exists())

    def test_apply_repeat_and_check_from_linked_checkout(self):
        linked = self.linked()
        before = self.git_inventory()
        first = json_documents(self.helper("--apply").stdout)[-1]
        self.assertEqual(first["status"], "verified")
        self.assertEqual(first["registered_worktrees"], 2)
        self.assertTrue(first["path_template_verified"])
        self.assertFalse(first["probe_created"])
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        raw = self.config.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
        self.assertEqual(data["projects"][IDENTIFIER]["worktree-path"], TEMPLATE)
        repeat = json_documents(self.helper("--apply", repo=linked).stdout)
        self.assertEqual(repeat[0]["status"], "unchanged")
        self.assertFalse(repeat[-1]["change_needed"])
        self.assertEqual(self.config.read_bytes(), raw)
        checked = json_documents(self.helper("--check", repo=linked).stdout)[-1]
        self.assertEqual(checked["registered_worktrees"], 2)
        self.assertEqual(before, self.git_inventory())
        self.assert_no_auxiliary_files()
        self.assertFalse(self.forge_marker.exists())

    def test_preserves_unrelated_bytes_and_saves_private_exact_backup(self):
        original = ("# Preserve spacing and café comment.\r\n"
                    "[commit]\r\nstage = 'none'\r\n\r\n"
                    "[projects.\"github.com/another/repository\"]\r\n"
                    "worktree-path = 'elsewhere/{{ branch | sanitize }}'\r\n").encode("utf-8")
        self.write_config(original)
        self.config.chmod(0o640)
        result = json_documents(self.helper("--apply").stdout)[-1]
        self.assertTrue(self.config.read_bytes().startswith(original))
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o640)
        backup = Path(result["backup"])
        self.assertEqual(backup.parent, self.config.parent)
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(len(list(self.config.parent.glob("*.bak"))), 1)
        self.assertFalse(self.config.with_name(self.config.name + ".project-worktrees.lock").exists())

    def test_existing_conflicting_project_table_refuses_without_writes(self):
        original = self.project_table("../different/{{ branch | sanitize }}") + "merge.squash = false\n"
        self.write_config(original)
        result = self.helper("--apply", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scoped edit", result.stderr)
        self.assertEqual(self.config.read_text(), original)
        self.assert_no_auxiliary_files()

    def test_existing_matching_project_table_is_not_rewritten(self):
        original = "# Existing owner-maintained layout\n" + self.project_table() + "remove.delete-branch = false\n"
        self.write_config(original)
        result = json_documents(self.helper("--apply").stdout)
        self.assertEqual(result[0]["status"], "unchanged")
        self.assertEqual(result[-1]["status"], "verified")
        self.assertEqual(self.config.read_text(), original)
        self.assert_no_auxiliary_files()

    def test_inline_projects_table_refuses_append_without_writes(self):
        original = 'projects = { "github.com/another/repository" = { worktree-path = "../other" } }\n'
        self.write_config(original)
        result = self.helper("--apply", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be extended", result.stderr)
        self.assertEqual(self.config.read_text(), original)
        self.assert_no_auxiliary_files()

    def test_config_symlink_is_not_replaced_or_target_edited(self):
        target = self.base / "dotfile-managed-config.toml"
        target.write_text("# managed elsewhere\n", encoding="utf-8")
        self.config.parent.mkdir(parents=True)
        self.config.symlink_to(target)
        result = self.helper("--apply", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr)
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(target.read_text(), "# managed elsewhere\n")
        self.assert_no_auxiliary_files()

    def test_environment_path_override_fails_effective_check(self):
        self.helper("--apply")
        original = self.config.read_bytes()
        env = dict(self.env, WORKTRUNK_WORKTREE_PATH="../wrong-location/{{ branch | sanitize }}")
        result = self.helper("--check", env=env, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected path", result.stderr)
        self.assertEqual(self.config.read_bytes(), original)
        self.assert_no_auxiliary_files()

    def test_environment_path_override_refuses_apply_before_writing(self):
        env = dict(self.env, WORKTRUNK_WORKTREE_PATH="../wrong-location/{{ branch | sanitize }}")
        before = self.git_inventory()
        result = self.helper("--apply", env=env, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("WORKTRUNK_WORKTREE_PATH", result.stderr)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.config.parent.exists(), "refusal created a configuration directory")
        self.assertEqual(self.git_inventory(), before)

    def test_nested_bare_repository_refuses_apply_before_writing(self):
        bare_container = self.base / "bare layout"
        bare = bare_container / "main/.git"
        bare.parent.mkdir(parents=True)
        (bare_container / "worktrees").mkdir()
        self.command(["git", "init", "--quiet", "--bare", bare])
        git_config = (bare / "config").read_bytes()
        result = self.helper("--apply", repo=bare, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Bare repositories", result.stderr)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.config.parent.exists(), "refusal created a configuration directory")
        self.assertEqual((bare / "config").read_bytes(), git_config)

    def test_git_directory_override_is_refused(self):
        env = dict(self.env, GIT_DIR=str(self.repo / ".git"))
        result = self.helper("--apply", env=env, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unset GIT_DIR", result.stderr)
        self.assertFalse(self.config.exists())

    def test_noncanonical_registered_worktree_fails_check(self):
        self.helper("--apply")
        outside = self.base / "legacy-worktree"
        self.git(self.repo, "worktree", "add", "--quiet", "-b", "legacy", outside)
        before = self.git_inventory()
        result = self.helper("--check", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the canonical", result.stderr)
        self.assertEqual(self.git_inventory(), before)
        self.assertTrue(outside.is_dir())

    def test_global_full_mode_and_summary_do_not_execute_external_commands(self):
        linked = self.linked()
        self.git(linked, "commit", "--quiet", "--allow-empty", "-m", "Feature ahead")
        llm_marker = self.base / "llm-was-run"
        llm = self.bin_dir / "fixture-llm"
        self.write_executable(llm, "#!/bin/sh\n: > " + shlex.quote(str(llm_marker)) + "\nprintf 'fixture summary'\n")
        self.write_config(
            '[list]\nfull = true\nsummary = true\ncolumns = ["branch", "ci", "summary"]\n'
            '[commit.generation]\ncommand = "fixture-llm"\n'
            + self.project_table() + 'list.full = true\nlist.summary = true\n'
        )
        original = self.config.read_bytes()
        result = json_documents(self.helper("--check", repo=linked).stdout)[-1]
        self.assertEqual(result["status"], "verified")
        self.assertFalse(llm_marker.exists(), "verification invoked the configured LLM")
        self.assertFalse(self.forge_marker.exists(), "verification invoked the forge CLI")
        self.assertEqual(self.config.read_bytes(), original)

    def test_verification_creates_no_branch_or_worktree_and_runs_no_hooks(self):
        self.linked()
        marker = self.base / "hook-was-run"
        command = ": > " + shlex.quote(str(marker))
        project_config = self.repo / ".config/wt.toml"
        project_config.parent.mkdir()
        project_config.write_text(
            "pre-start = " + json.dumps(command) + "\npost-start = " + json.dumps(command) + "\n",
            encoding="utf-8",
        )
        git_hook = self.repo / ".git/hooks/post-checkout"
        self.write_executable(git_hook, "#!/bin/sh\n" + command + "\n")
        before = self.git_inventory()
        result = json_documents(self.helper("--apply").stdout)[-1]
        self.assertFalse(result["probe_created"])
        self.assertEqual(before, self.git_inventory())
        self.assertFalse(marker.exists())
        self.assertFalse(self.forge_marker.exists())

    def test_fsmonitor_is_suppressed_from_a_repository_local_skill(self):
        self.write_config(self.project_table())
        marker = self.base / "fsmonitor-was-run"
        monitor = self.bin_dir / "fixture-fsmonitor"
        self.write_executable(
            monitor,
            "#!/bin/sh\n: > " + shlex.quote(str(marker)) + "\nprintf 'fixture-token\\0'\n",
        )
        self.git(self.repo, "config", "core.fsmonitor", str(monitor))
        (self.repo / "tracked.txt").write_text("dirty working file\n", encoding="utf-8")
        # Prove this fixture actually invokes the configured executable without
        # the helper's suppression, then clear only the disposable marker.
        self.git(self.repo, "status", "--porcelain")
        self.assertTrue(marker.exists(), "fixture fsmonitor did not run in ordinary Git")
        marker.unlink()
        bundled = self.repo / ".agents/skills/project-worktrees/scripts"
        bundled.mkdir(parents=True)
        for name in ("configure_worktrunk.py", "project_worktrees.py"):
            shutil.copy2(HELPER.parent / name, bundled / name)
        result = self.command([
            sys.executable, "-I", bundled / "configure_worktrunk.py",
            "--repo", self.repo, "--check",
        ])
        self.assertEqual(json_documents(result.stdout)[-1]["status"], "verified")
        self.assertFalse(marker.exists(), "verification executed core.fsmonitor")
        self.assertEqual((self.repo / "tracked.txt").read_text(), "dirty working file\n")
        self.assertFalse(list(bundled.rglob("__pycache__")), "runpy left bytecode inside the skill")

    def install_mutating_clean_filter(self, checkout):
        """A filter would overwrite ignored data if inspection executed it."""
        marker = self.base / "clean-filter-was-run"
        ignored = checkout / ".env"
        ignored.write_bytes(b"FIXTURE_ONLY=keep-local-data\n")
        (self.repo / ".git/info/exclude").write_text(".env\n", encoding="utf-8")
        driver = self.bin_dir / "fixture-clean-filter"
        self.write_executable(
            driver,
            "#!/bin/sh\n: > " + shlex.quote(str(marker))
            + "\nprintf 'overwritten-by-filter\\n' > " + shlex.quote(str(ignored))
            + "\ncat\n",
        )
        self.git(self.repo, "config", "filter.fixture.clean", shlex.quote(str(driver)))
        (checkout / ".gitattributes").write_text("tracked.txt filter=fixture\n", encoding="utf-8")
        (checkout / "tracked.txt").write_text("dirty content requiring comparison\n", encoding="utf-8")
        return marker, ignored

    def test_clean_filter_refuses_apply_before_touching_data_or_config(self):
        original = b"# Preserve this user configuration exactly.\n"
        self.write_config(original)
        marker, ignored = self.install_mutating_clean_filter(self.repo)
        result = self.helper("--apply", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clean/process filter", result.stderr)
        self.assertFalse(marker.exists(), "preflight ran the clean filter")
        self.assertEqual(ignored.read_bytes(), b"FIXTURE_ONLY=keep-local-data\n")
        self.assertEqual((self.repo / "tracked.txt").read_text(), "dirty content requiring comparison\n")
        self.assertEqual(self.config.read_bytes(), original)
        self.assert_no_auxiliary_files()

    def test_clean_filter_in_linked_checkout_blocks_apply_from_primary(self):
        linked = self.linked()
        marker, ignored = self.install_mutating_clean_filter(linked)
        result = self.helper("--apply", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clean/process filter", result.stderr)
        self.assertIn(str(linked), result.stderr)
        self.assertFalse(marker.exists(), "preflight ran a linked checkout's clean filter")
        self.assertEqual(ignored.read_bytes(), b"FIXTURE_ONLY=keep-local-data\n")
        self.assertEqual((linked / "tracked.txt").read_text(), "dirty content requiring comparison\n")
        self.assertEqual((self.repo / "tracked.txt").read_text(), "fixture\n")
        self.assertFalse(self.config.exists())
        self.assertFalse(self.config.parent.exists())

    def test_inherited_git_config_is_preserved_while_fsmonitor_is_overridden(self):
        self.write_config(self.project_table())
        marker = self.base / "inherited-fsmonitor-was-run"
        monitor = self.bin_dir / "inherited-fsmonitor"
        self.write_executable(
            monitor,
            "#!/bin/sh\n: > " + shlex.quote(str(marker)) + "\nprintf 'fixture-token\\0'\n",
        )
        (self.repo / "tracked.txt").write_text("dirty for status\n", encoding="utf-8")
        real_git = shutil.which("git", path=self.env["PATH"])
        self.assertIsNotNone(real_git)
        audit = self.base / "inherited-config-observed"
        # Inspect effective configuration in a real Git subprocess, then forward
        # the original invocation. Losing the caller's restriction fails locally.
        self.write_executable(
            self.bin_dir / "git",
            "#!/bin/sh\npolicy=$(" + shlex.quote(real_git) + " config --get protocol.allow)\n"
            + '[ "$policy" = never ] || exit 74\n'
            + "printf 'preserved\\n' >> " + shlex.quote(str(audit)) + "\n"
            + "exec " + shlex.quote(real_git) + ' "$@"\n',
        )
        env = dict(self.env, GIT_CONFIG_COUNT="2",
                   GIT_CONFIG_KEY_0="protocol.allow", GIT_CONFIG_VALUE_0="never",
                   GIT_CONFIG_KEY_1="core.fsmonitor", GIT_CONFIG_VALUE_1=str(monitor))
        result = self.helper("--check", env=env)
        self.assertEqual(json_documents(result.stdout)[-1]["status"], "verified")
        self.assertTrue(audit.is_file())
        self.assertGreater(len(audit.read_text().splitlines()), 1)
        self.assertFalse(marker.exists(), "inherited core.fsmonitor took precedence over suppression")


if __name__ == "__main__":
    unittest.main(verbosity=2)

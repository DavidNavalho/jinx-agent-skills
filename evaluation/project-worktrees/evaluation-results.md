# Project Worktrees evaluation — 2026-11-18

## Local release checks

| Check | Executed result | Environment |
| --- | --- | --- |
| Real-Git migration suite | **39/39 passed** | Linux, Git 2.52.0, Python 3.12.14 |
| Real-Worktrunk configuration suite | **19/19 passed** | Same host; official Worktrunk 0.82.0 release, SHA-256 checked |
| Skill metadata validation | Passed | Skill creator's `quick_validate.py` |
| Script syntax, YAML, relative skill links | Passed | Python AST/YAML parsing, sh and zsh syntax checks |
| Natural-request agent trial | Migration and Worktrunk setup completed | Disposable project, primary `develop`, linked `feature/auth`, bootstrap disabled |
| Independent review regressions | Three concrete findings corrected and independently reproduced as resolved | Real Git/Worktrunk disposable fixtures |

The local checks above were run on Linux. The [focused CI workflow](../../.github/workflows/project-worktrees.yml)
runs both suites on `macos-latest` and `ubuntu-latest` with Python 3.11 and pinned Worktrunk
0.82.0. Refer to the pull request's checks or workflow run for executed CI-platform results;
the existence of the workflow alone is not evidence of a macOS pass.

Initial macOS CI exercised Git 2.56.1 and exposed a fixture race: automatic background
maintenance removed an ephemeral nested-repository lock between two preservation captures.
The fixture setup now disables automatic maintenance and auto-GC in its disposable repos;
the preservation assertions remain unchanged. The workflow tests pull requests and pushes
to `main`, avoiding duplicate branch-push and pull-request runs for the same candidate.
The production preflight also explicitly refuses object-maintenance locks and GC PID
markers instead of assuming a foreground Git command's exit means all writers stopped.

## What the tests establish

The migration suite exercises actual temporary Git repositories, rather than mocking all
Git behavior. It independently captures and compares working files, indexes, refs,
branches, and status around migration. Coverage includes:

- Primary and linked relocation, including converting a checkout's original path into
  its container and running the script from the checkout being moved.
- Staged and unstaged changes to the same file; ignored and untracked data; binary files;
  executable modes; empty directories; ordinary and dangling symlinks.
- Detached HEADs and a primary branch whose name differs from the `main/` directory.
- Repeated/compliant runs, explicit containers, spaces, Unicode, and leading-hyphen paths.
- Stale plans, edited plans, new worktrees/destinations, sanitization collisions, and
  case/Unicode collisions suitable for common macOS filesystems.
- Refusal of bare, submodule, nested, locked, missing, symlinked, or otherwise unsupported
  states; Git locks/in-progress operations and path-dependent configuration.
- Private manifests without plaintext file contents, historical verification detecting
  later edits, and preservation/journaling after an injected linked-move failure.
- Active clean/process filter refusal before Git status can execute them, while allowing
  unused global LFS definitions; relative worktree-scoped include refusal.

The Worktrunk suite uses the actual binary and isolated user/config directories. It covers
preview, application, idempotence, existing-config byte preservation and private backups,
conflicting TOML/settings, bare topology, environment overrides, effective expected-path
resolution from primary and linked contexts, Git/Worktrunk registration agreement,
no probe checkout creation, and suppression of CI/LLM collection and executable Git
filesystem monitors. Active filters are refused before changing configuration or files.
Existing command-scoped Git configuration entries remain effective.

Fixtures use local remote-tracking HEAD refs, so these tests do not need a real remote,
credentials, a paid model, shell-profile changes, or user development repositories.

## Agent trial and review

A fresh agent received the skill and a natural request to migrate a disposable project,
with authorization, stopped writers, and bootstrap disabled. It relocated the primary to
`payments/main` and its linked checkout to `payments/worktrees/feature-auth`, preserving
`develop`, `feature/auth`, staged/unstaged README changes, ignored environment files, and
untracked binary/notes/skill files. Both Git verification passes succeeded. Worktrunk's
effective path and registration checks also passed without a probe branch or hook.

Worktrunk added its ordinary `worktrunk.default-branch` cache setting to Git configuration.
The original config bytes remained an exact prefix. This validates the documented
separation between the byte-preserving Git migration receipt and later Worktrunk setup.

An independent code review then reproduced three issues: status executing an active clean
filter, Worktrunk status executing `core.fsmonitor`, and a relative `config.worktree`
include changing meaning after relocation. The implementation now rejects active filters
before status, disables filesystem-monitor hooks in child Git processes, and checks
worktree-scoped includes. Added regressions pass, and the reviewer independently reran
the original reproductions to confirm all three were resolved. The natural-request trial
preceded this final hardening; the final hardening is covered by those regression checks.

## Reproduce

```sh
python3 -I evaluation/project-worktrees/test_migration.py
WORKTRUNK_TEST_BINARY=/absolute/path/to/wt \
  python3 -I evaluation/project-worktrees/test_worktrunk_config.py
```

Without an available Worktrunk executable, its integration cases skip explicitly. Supply
the binary to reproduce the complete result. CI supplies one rather than relying on a
preinstalled executable.

## Limits

- The runner requires quiescence established externally. Its lock does not stop arbitrary
  agents, editors, or Git processes.
- A manifest is not a backup. Recovery from interruption is guided by preserved evidence;
  no automatic rollback/resume or recovery from disk failure is claimed.
- Full ignored-content hashing can be expensive for large dependency trees.
- The fixtures do not represent every filesystem, network mount, ACL, or extended-attribute
  configuration. Automatic moves are restricted to one filesystem.
- Unsupported layouts and active external Git filters deliberately require a separate
  procedure. Existing bare repositories are not converted.
- The agent trial is one example, not a model-wide reliability estimate or proof against
  an agent ignoring the skill's constraints.

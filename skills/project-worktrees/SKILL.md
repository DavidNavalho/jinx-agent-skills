---
name: project-worktrees
description: Audit, plan, migrate, and verify conventional Git repositories into a project container with main/ and worktrees/ using Worktrunk. Use when reorganizing scattered worktrees, adopting a consistent layout for concurrent coding agents, migrating a project without losing local changes, checking worktree health, or optionally adding bootstrap hooks. Target macOS with zsh and Homebrew; install per repository. Preserve branches, staged changes, untracked and ignored files; leave bare repositories and unusual Git layouts for a separate plan.
---

# Project Worktrees

Standardize one logical project per container. Keep the primary checkout conventional,
and give each concurrent writing agent its own linked worktree.

| Path | Purpose |
| --- | --- |
| `~/gits/<project>/` | Container only; never a Git working tree. |
| `~/gits/<project>/main/` | Primary checkout, containing the shared `.git/` database. |
| `~/gits/<project>/worktrees/<branch>/` | Linked checkouts; replace `/` and `\` in branch names with `-`. |
| `main/.agents/skills/project-worktrees/` | Repository-local installation when copied into the project. |

`main/` is a directory role, not an instruction to change branches. Keep the existing
branch, detached HEAD, index, local files, and history unchanged. Use an alternative
container when the user selected another base directory.

## Operating contract

- Resolve this skill's directory separately from the target repository. Never assume
  the current directory is the skill's repository.
- Use the bundled migration scripts. Do not substitute recloning, stashing, checkout/reset,
  `git clean`, forced removal, pruning, or `wt step relocate`.
- A request to **audit** authorizes inspection and a plan only. A request to **migrate**
  authorizes the agreed moves: show the concrete plan, then proceed once prerequisites
  are satisfied. Do not repeatedly request an already given approval.
- Leave bootstrap disabled unless requested. Do not install dependencies, copy secrets,
  edit shell startup files, start services, or approve hooks merely to move directories.
- Treat worktrees as file isolation, not a security sandbox. Shared refs and the Git
  database still require coordination; use `run-agents-in-sbx` separately when applicable.

Read [conventions.md](references/conventions.md) for layout, installation, naming, and daily
use; [migration.md](references/migration.md) before applying or recovering a move; and
[worktrunk.md](references/worktrunk.md) for configuration and optional bootstrap.

## 1. Inspect the environment and repository

Use Python 3.11+ and Git 2.43+ with Worktrunk 0.81+. The standalone migration runner
supports Git 2.36+; Worktrunk itself needs the newer Git.
On macOS, when installation and shell setup are requested:

```sh
brew install git python worktrunk
wt config shell install zsh
```

Use existing sufficient installations. A new terminal loads integration; `type wt` should
show a shell function. `wt config show` supplies diagnostics; there is no `shell status`
command. zsh is the interactive shell; bundled wrappers use portable `/bin/sh`.

Locate the exact target and audit from its primary or any linked checkout:

```sh
sh <skill>/scripts/audit.sh --repo /absolute/path/to/checkout
```

Use the concise inventory for discussion; `--json` includes full file fingerprints and
should be written to a private external file when machine processing is needed.
Review registered worktrees, branches, local state, and reported blockers. Inspect known
agents, terminals, editors, servers, and containers associated with these paths. A Git
audit does not prove no process is writing. The preflight refuses active external Git
clean/process filters before status inspection and disables filesystem-monitor hooks.
Pause other writers and processes whose paths
or mounts will move. Ask a targeted question only when quiescence or the target cannot
be established. The migration controller can remain active: its Python runner loads its
code and changes to a stable directory before moving a checkout containing the skill.

## 2. Produce a concrete plan

Choose a new, private plan filename outside **all** source checkouts and the destination
container, for example under `~/.local/state/project-worktrees/`. Never commit plans or
journals. They contain local paths and hashes, not file contents.

```sh
sh <skill>/scripts/migrate.sh \
  --repo /absolute/path/to/checkout \
  --container /absolute/path/to/project-container \
  --plan /absolute/path/outside/project/migration.json
```

Omit `--container` to use the primary checkout's current path as the future container;
if the primary directory is named `main`, the default is its parent. Override this for a
repository actually named `main` that is not already in a container layout. Without
`--plan`, the command prints a plan without applying moves.

Explain the move mapping, staging path if used, blockers, and path-dependent tools needing
attention. Full fingerprinting includes ignored files and can take time with large
dependency/build directories; do not silently exclude them to make verification pass.
An already standardized project remains unchanged. Resolve collisions or blockers rather
than inventing destructive workarounds.

## 3. Apply and verify

After the target is authorized and other writers are paused:

```sh
sh <skill>/scripts/migrate.sh \
  --apply /absolute/path/outside/project/migration.json --quiescent
```

`--quiescent` attests that relevant processes are paused; it is not process discovery.
The runner revalidates the frozen plan, takes an exclusive migration lock, writes a durable
journal, relocates the primary, repairs links, moves linked checkouts with Git, and compares
the result to original fingerprints. It does not run project hooks.

Use the returned journal for an independent verification pass when needed:

```sh
sh <skill-at-its-current-location>/scripts/verify.sh \
  --manifest /absolute/path/outside/project/migration.json.journal.json
```

If the skill moved, resolve its new path before another command. On failure preserve all
directories and the journal; follow [migration.md](references/migration.md). Do not report
a partial operation as successful or roll it back automatically over live data.

## 4. Configure Worktrunk for this project

After Git verification, preview and apply the project preference:

```sh
python3 -I <skill>/scripts/configure_worktrunk.py --repo /absolute/path/to/project/main
python3 -I <skill>/scripts/configure_worktrunk.py --repo /absolute/path/to/project/main --apply
```

The helper appends only when safe, backs up an existing user config, and verifies effective
paths without creating a probe worktree. A conflicting existing project entry requires a
scoped edit preserving other settings; rerun `--check` afterward.

The preference belongs in Worktrunk's **user** config under the identifier returned by
`wt config show --format=json`; `.config/wt.toml` is for shared hooks. Verification disables
CI collection and LLM summaries. Worktrunk may update its own Git metadata cache.

Mention that the project identifier normally follows the remote, so the preference also
affects other local clones of that same project. Check those clones if known. Do not
change the global path default unless the user asks for all repositories.

## 5. Optional bootstrap and handoff

Only when requested, inspect the project's setup and propose a bootstrap command and
`.config/wt.toml` hook. Start with the disabled examples in `assets/`. Keep secrets,
dependency installation, ports, services, and hook approval explicit; preserve existing hooks.

Report old/new paths and retained branches, the journal location, Git preservation and
Worktrunk configuration as separate outcomes, any failures with recovery evidence, and
editor/virtualenv/container/absolute-path references needing updates. Give a minimal next
command such as `cd <container>/main`, `wt list`, or `code -n <worktree>`.

Stop after the requested scope. Do not merge branches, remove worktrees, or enable
bootstrap just because the layout is now standardized.

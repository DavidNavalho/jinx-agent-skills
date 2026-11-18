# Layout, installation, and daily use

Contents: [Structure](#canonical-structure) · [Installation](#repository-local-installation) ·
[Naming](#naming) · [Daily use](#daily-use) · [Sources](#sources)

## Canonical structure

| Directory | Role |
| --- | --- |
| `~/gits/payments/` | Plain container. Open a checkout inside it in an editor. |
| `~/gits/payments/main/` | Primary clone; shared `.git/` lives here. |
| `~/gits/payments/worktrees/feature-auth/` | Branch `feature/auth`. |
| `~/gits/payments/worktrees/fix-api/` | Branch `fix-api`. |

Prefer visible `worktrees/` for macOS Finder and editor file pickers. Keep linked checkouts
outside `main/` to avoid cross-checkout watchers, indexing, test discovery, and agent scans.
Do not rename branches just because checkouts move: primary branch `develop` can live in
directory `main/`.

Give each writing agent its own checkout. Worktrees share refs, object storage, remotes,
and normally Git configuration. Coordinate merges and shared-repository operations. Use
separate mutable dependencies, build output, and runtime resources when the project needs them.

## Repository-local installation

Distribution lives in `jinx-agent-skills/skills/project-worktrees/`. Copy the complete
folder into the target's `.agents/skills/project-worktrees/` for Codex. Honor existing
`AGENTS.md`; do not install globally when the user selected repository scope.

Substitute actual paths:

```sh
pw_source=/absolute/path/to/jinx-agent-skills/skills/project-worktrees
pw_target=/absolute/path/to/target-repository
mkdir -p "$pw_target/.agents/skills"
if test -e "$pw_target/.agents/skills/project-worktrees" || test -L "$pw_target/.agents/skills/project-worktrees"; then
  printf '%s\n' 'An installation exists; review the differences before updating.'
else
  cp -R "$pw_source" "$pw_target/.agents/skills/project-worktrees"
fi
```

For another agent, use its supported repository-local skill path or point it directly at
`SKILL.md`; do not claim all agents discover `.agents/skills/`. Copying keeps the target
self-contained and avoids symlinks breaking when either repository moves. The skill may
be committed by the project owner for future branches; existing branches do not acquire
the new skill automatically. Use the central copy where needed.

Example requests:

> Use $project-worktrees to audit this repository and show its migration plan.

> Use $project-worktrees to migrate this repository to main/ and worktrees/. Keep bootstrap disabled.

> Use $project-worktrees to verify this migrated project and its Worktrunk configuration.

## Naming

Match Worktrunk exactly: replace `/` and `\` with `-`. Do not lowercase, truncate, or invent
another slug algorithm. Reject `feature/auth` versus `feature-auth`, case-only, and Unicode
normalization collisions conservatively even when testing on a case-sensitive filesystem.

Detached checkouts retain detached HEAD and receive a deterministic
`detached-<12-character-commit>-<original-directory>` name; the plan records the exact mapping.
When existing names cannot fit the standard, explain the collision and obtain a choice
about renaming a branch, retaining an exception, or choosing another layout. Never guess.

## Daily use

With shell integration and the project preference configured:

```sh
cd ~/gits/payments/main
wt list
wt switch --create feature/auth
# Checkout: ~/gits/payments/worktrees/feature-auth
```

Use `--base <branch-or-ref>` when a particular base is intended; inspect local CLI help
for installed-version behavior. Migration never invokes merge or branch deletion.

Open an existing checkout in VS Code:

```sh
code -n ~/gits/payments/worktrees/feature-auth
# Or preserve the terminal's directory while Worktrunk opens it:
wt switch feature/auth --no-cd -x code -- -n .
```

On macOS, VS Code's Command Palette provides **Shell Command: Install 'code' command in
PATH**. A separate window per checkout keeps terminals and agents clear; multi-root
workspaces remain optional. Zed can open the same paths; switching editors is not required.

Before cleanup, check integration, local changes, and active ownership. Native
`git worktree remove <path>` refuses ordinary dirty checkouts; do not add `--force`.
Branch deletion is a separate action (`git branch -d <branch>` when suitable). Worktrunk
merge/removal can perform additional operations; review its options before use.

## Sources

- [Git worktree](https://git-scm.com/docs/git-worktree)
- [Worktrunk configuration](https://worktrunk.dev/config/)
- [Worktrunk switch](https://worktrunk.dev/switch/)
- [VS Code CLI](https://code.visualstudio.com/docs/configure/command-line)
- [VS Code macOS setup](https://code.visualstudio.com/docs/setup/mac)

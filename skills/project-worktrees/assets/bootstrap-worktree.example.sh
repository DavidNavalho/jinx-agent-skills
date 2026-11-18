#!/bin/sh
# Optional starting point for scripts/bootstrap-worktree. No side effects by default.
# Replace the message with the project's reviewed, repeatable setup command.
# Keep secrets out of tracked files/logs; don't start background services implicitly.
set -eu
printf '%s\n' 'Bootstrap is not configured. Choose the project setup command first.'
exit 1

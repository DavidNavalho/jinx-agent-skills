#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec python3 -I "$script_dir/project_worktrees.py" audit "$@"

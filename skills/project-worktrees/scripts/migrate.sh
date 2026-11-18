#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ "${1-}" = "--apply" ]; then
    shift
    if [ "$#" -eq 0 ]; then
        echo 'migrate.sh: --apply requires a plan file' >&2
        exit 2
    fi
    plan=$1
    shift
    exec python3 -I "$script_dir/project_worktrees.py" apply --plan "$plan" "$@"
fi
exec python3 -I "$script_dir/project_worktrees.py" plan "$@"

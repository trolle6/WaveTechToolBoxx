#!/bin/bash
# Run before main.py in Docker / TrueNAS custom apps.
# Ensures the container checks out the intended branch (not stale master HEAD).
#
# Env:
#   GIT_UPDATE=true|false     — fetch + hard reset (default: true if .git exists)
#   GIT_BRANCH                — branch to deploy (default: cursor/ss-command-simplify-c0c2)
#   GIT_REMOTE=origin
#   PIP_INSTALL_ON_START=true — pip install -r requirements.txt before bot start

set -euo pipefail

cd /app 2>/dev/null || cd "$(dirname "$0")/.." || true

GIT_BRANCH="${GIT_BRANCH:-cursor/ss-command-simplify-c0c2}"
GIT_REMOTE="${GIT_REMOTE:-origin}"
GIT_UPDATE="${GIT_UPDATE:-true}"
PIP_INSTALL_ON_START="${PIP_INSTALL_ON_START:-true}"

export_git_identity() {
    if ! command -v git >/dev/null 2>&1 || ! git rev-parse --git-dir >/dev/null 2>&1; then
        echo "Git: not a repository (using baked image files only)"
        return
    fi
    export GIT_COMMIT
    GIT_COMMIT="$(git rev-parse HEAD)"
    export GIT_COMMIT_SHORT
    GIT_COMMIT_SHORT="$(git rev-parse --short HEAD)"
    export GIT_BRANCH_ACTUAL
    GIT_BRANCH_ACTUAL="$(git rev-parse --abbrev-ref HEAD)"
    echo "Git: branch=${GIT_BRANCH_ACTUAL} commit=${GIT_COMMIT_SHORT} full=${GIT_COMMIT}"
}

if [ "${GIT_UPDATE}" = "true" ] && [ -d .git ]; then
    echo "Updating ${GIT_REMOTE}/${GIT_BRANCH}..."
    git fetch "${GIT_REMOTE}" "${GIT_BRANCH}" --prune
    if git show-ref --verify --quiet "refs/remotes/${GIT_REMOTE}/${GIT_BRANCH}"; then
        git checkout -B "${GIT_BRANCH}" "${GIT_REMOTE}/${GIT_BRANCH}"
        git reset --hard "${GIT_REMOTE}/${GIT_BRANCH}"
    else
        echo "ERROR: ${GIT_REMOTE}/${GIT_BRANCH} not found after fetch" >&2
        exit 1
    fi
    export_git_identity
elif [ -d .git ]; then
    export_git_identity
fi

if [ -f requirements.txt ] && [ "${PIP_INSTALL_ON_START}" = "true" ]; then
    python3 -m pip install -q -r requirements.txt
fi

exec "$@"

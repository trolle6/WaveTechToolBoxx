#!/bin/sh
# TrueNAS / volume-mounted /app startup (see docker-compose.truenas.example.yml).
# Syncs GIT_BRANCH from origin, installs Python deps, starts the bot.
set -eu

cd /app

GIT_REMOTE="${GIT_REMOTE:-origin}"
GIT_BRANCH="${GIT_BRANCH:-cursor/ss-command-simplify-c0c2}"
PIP_INSTALL_ON_START="${PIP_INSTALL_ON_START:-true}"

if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git not installed (run apt install git once)" >&2
    exit 1
fi

git config --global --add safe.directory /app 2>/dev/null || true

echo "Syncing ${GIT_REMOTE}/${GIT_BRANCH}..."
git fetch "${GIT_REMOTE}" "${GIT_BRANCH}" --prune
git checkout -B "${GIT_BRANCH}" "${GIT_REMOTE}/${GIT_BRANCH}"
git reset --hard "${GIT_REMOTE}/${GIT_BRANCH}"

COMMIT_SHORT="$(git rev-parse --short HEAD)"
COMMIT_FULL="$(git rev-parse HEAD)"
export GIT_COMMIT="${COMMIT_FULL}"
export GIT_COMMIT_SHORT="${COMMIT_SHORT}"
export GIT_BRANCH_ACTUAL="${GIT_BRANCH}"

echo "Deployed: branch=${GIT_BRANCH} commit=${COMMIT_SHORT} (${COMMIT_FULL})"

if [ "${PIP_INSTALL_ON_START}" = "true" ] && [ -f /app/requirements.txt ]; then
    python3 -m pip install --no-cache-dir -r /app/requirements.txt
fi

exec python3 /app/main.py

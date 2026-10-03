#!/bin/bash
# Pterodactyl startup. Works as the panel Startup command `bash startup.sh` or `source startup.sh`.
# With AUTO_UPDATE=1 the code is forced to match GitHub on every start. Edits made on the
# server are stashed (recover: `git stash list` / `git stash show -p`), so change code on GitHub.
# Never `exit` here: under `source` that would kill the container shell.
_root="${CONTAINER_DIR:-/home/container}"
cd "$_root" || true

if [[ -d .git ]] && [[ "${AUTO_UPDATE}" == "1" ]]; then
    git config --global --get-all safe.directory 2>/dev/null | grep -qx "$_root" || git config --global --add safe.directory "$_root" 2>/dev/null
    _branch="${BRANCH:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null)}"
    [[ -z "$_branch" || "$_branch" == "HEAD" ]] && _branch=master
    _before=$(git rev-parse --short HEAD 2>/dev/null)
    if git fetch -q origin "$_branch"; then
        # Secret Santa writes into tracked archive files; they are kept as-is, not treated as edits.
        _keep=$(mktemp -d) && cp -a cogs/archive "$_keep/" 2>/dev/null
        if ! git diff --quiet HEAD -- . ':(exclude)cogs/archive' 2>/dev/null; then
            git stash push -q -m "server edits before update $(date '+%F %T')" -- . ':(exclude)cogs/archive' \
                && echo "[update] Local code edits stashed (git stash list): $(git stash list -1 --format=%gs)"
        fi
        git checkout -q -f -B "$_branch" "origin/$_branch"
        [[ -d "$_keep/archive" ]] && cp -a "$_keep/archive/." cogs/archive/
        rm -rf "$_keep"
        _after=$(git rev-parse --short HEAD)
        if [[ "$_before" == "$_after" ]]; then
            echo "[update] Already on latest $_branch ($_after)"
        else
            echo "[update] Updated $_branch: $_before -> $_after"
        fi
    else
        echo "[update] WARNING: git fetch failed; starting old code ($_before)"
    fi
    unset _root _branch _before _after _keep
fi

if [[ -n "${PY_PACKAGES}" ]]; then pip install -q -U --prefix .local ${PY_PACKAGES}; fi
if [[ -n "${REQUIREMENTS_FILE}" && -f "${REQUIREMENTS_FILE}" ]]; then
    pip install -q -U --prefix .local -r "${REQUIREMENTS_FILE}" --disable-pip-version-check
fi
"${PYTHON_BIN:-/usr/local/bin/python}" "${PY_FILE:-main.py}"

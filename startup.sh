#!/bin/bash
# Pterodactyl startup. Works as the panel Startup command `bash startup.sh` or `source startup.sh`.
# AUTO_UPDATE=1: safe update. Only fast-forwards to GitHub; never overwrites or deletes anything
#   on the server. If server edits clash with the update, it is skipped and the files are listed.
# GIT_HARD_RESET_NUKE=1 (rare cases only): server code is forced to match GitHub. Tracked code
#   edits are stashed first (`git stash list`); config.env, bot data and cogs/archive are kept.
# Never `exit` here: under `source` that would kill the container shell.
_root="${CONTAINER_DIR:-/home/container}"
cd "$_root" || true

if [[ -d .git ]] && [[ "${AUTO_UPDATE}" == "1" ]]; then
    git config --global --get-all safe.directory 2>/dev/null | grep -qx "$_root" || git config --global --add safe.directory "$_root" 2>/dev/null
    _branch="${BRANCH:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null)}"
    [[ -z "$_branch" || "$_branch" == "HEAD" ]] && _branch=master
    _before=$(git rev-parse --short HEAD 2>/dev/null)
    _nuke=0
    case "${GIT_HARD_RESET_NUKE,,}" in 1|true|yes|on) _nuke=1 ;; esac
    if ! git fetch -q origin "$_branch"; then
        echo "[update] WARNING: git fetch failed; starting current code ($_before)"
    elif [[ $_nuke == 1 ]]; then
        echo "[update] GIT_HARD_RESET_NUKE is ON: forcing code to origin/$_branch (turn it OFF after this boot)"
        _keep=$(mktemp -d) && cp -a cogs/archive "$_keep/" 2>/dev/null
        if ! git diff --quiet HEAD -- . ':(exclude)cogs/archive' 2>/dev/null; then
            git stash push -q -m "server edits before nuke $(date '+%F %T')" -- . ':(exclude)cogs/archive' \
                && echo "[update] Server code edits backed up: $(git stash list -1 --format=%gs)"
        fi
        git checkout -q -f -B "$_branch" "origin/$_branch"
        [[ -d "$_keep/archive" ]] && cp -a "$_keep/archive/." cogs/archive/
        rm -rf "$_keep"
    elif _out=$(git merge --ff-only -q "origin/$_branch" 2>&1); then
        :
    else
        echo "[update] WARNING: update to origin/$_branch SKIPPED; nothing on the server was changed."
        echo "$_out" | grep -E '^\s+\S' | sed 's/^\s*/[update]   blocked by: /'
        echo "[update] Fix: undo/move those server edits, or enable GIT_HARD_RESET_NUKE for one boot."
    fi
    _after=$(git rev-parse --short HEAD)
    if [[ "$_before" == "$_after" ]]; then
        echo "[update] Running $_branch at $_after"
    else
        echo "[update] Updated $_branch: $_before -> $_after"
    fi
    unset _root _branch _before _after _keep _nuke _out
fi

if [[ -n "${PY_PACKAGES}" ]]; then pip install -q -U --prefix .local ${PY_PACKAGES}; fi
if [[ -n "${REQUIREMENTS_FILE}" && -f "${REQUIREMENTS_FILE}" ]]; then
    pip install -q -U --prefix .local -r "${REQUIREMENTS_FILE}" --disable-pip-version-check
fi
"${PYTHON_BIN:-/usr/local/bin/python}" "${PY_FILE:-main.py}"

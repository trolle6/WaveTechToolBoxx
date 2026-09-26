#!/bin/bash
# WaveTechToolBoxx — Pterodactyl / console entrypoint.
# ALWAYS force-resets tracked files to origin/master (never soft git pull).
# Safe to: bash startup.sh   OR   source startup.sh
#
# Keeps: config.env, .local/, cogs/archive/, SS state, distributed_files
set -euo pipefail
cd /home/container 2>/dev/null || cd "$(dirname "$0")"

if [[ ! -d .git ]]; then
  echo "FATAL: no .git — Reinstall server with GIT_ADDRESS=https://github.com/trolle6/WaveTechToolBoxx"
  exit 1
fi

echo "FORCE RESET → origin/master (discard local tracked changes)"
git fetch origin master --prune || {
  echo "FATAL: git fetch failed"
  exit 1
}

# Fixes: "Please commit your changes or stash them before you merge"
# (dirty deploy.py / main.py etc. must not block deploy)
git reset --hard HEAD || true
git checkout -f -B master origin/master
git reset --hard origin/master

git clean -fd \
  -e config.env \
  -e .local \
  -e cogs/archive \
  -e cogs/secret_santa_state.json \
  -e cogs/distributed_files \
  -e cogs/distributed_files_metadata.json \
  -e __pycache__ \
  -e startup.sh || true

export GIT_BRANCH_ACTUAL=master
echo "Deployed: branch=master commit=$(git rev-parse --short HEAD)"

PY_FILE="${PY_FILE:-main.py}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-requirements.txt}"

if [[ -n "${PY_PACKAGES:-}" ]]; then
  pip install -U --prefix .local ${PY_PACKAGES}
fi
if [[ -f "${REQUIREMENTS_FILE}" ]]; then
  pip install -U --prefix .local -r "${REQUIREMENTS_FILE}"
fi

export PATH="/home/container/.local/bin:${PATH:-}"
for d in /home/container/.local/lib/python*/site-packages; do
  if [[ -d "$d" ]]; then
    export PYTHONPATH="${d}${PYTHONPATH:+:$PYTHONPATH}"
  fi
done

echo "PYTHONPATH=${PYTHONPATH:-}"
echo "Starting ${PY_FILE} (python=$(command -v python3 >/dev/null && python3 -V || /usr/local/bin/python -V 2>&1))"

# Prefer egg python; fall back to python3
if [[ -x /usr/local/bin/python ]]; then
  PY=/usr/local/bin/python
else
  PY=python3
fi

# If sourced, do not exec (would kill the parent shell); if executed, exec.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  "${PY}" "${PY_FILE}"
else
  exec "${PY}" "${PY_FILE}"
fi

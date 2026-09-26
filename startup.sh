#!/bin/bash
# WaveTechToolBoxx — force-reset to origin/master, then run the bot.
# NEVER soft git pull. Panel should rm -f startup.sh before first fetch if an
# old untracked soft-pull startup.sh is blocking merge.
set -euo pipefail
cd /home/container 2>/dev/null || cd "$(dirname "$0")"

if [[ ! -d .git ]]; then
  echo "FATAL: no .git — Reinstall with GIT_ADDRESS=https://github.com/trolle6/WaveTechToolBoxx"
  exit 1
fi

echo "FORCE RESET → origin/master (discard local tracked changes)"
git fetch origin master --prune || {
  echo "FATAL: git fetch failed"
  exit 1
}

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
  -e __pycache__ || true

export GIT_BRANCH_ACTUAL=master
echo "Deployed: branch=master commit=$(git rev-parse --short HEAD)"

# Guard: refuse to run if we somehow still look like the ancient tree
if [[ "$(git rev-parse --short HEAD)" == "7a72b8baf578" ]] || [[ "$(git rev-parse --short HEAD)" == "7a72b8b" ]]; then
  echo "FATAL: still on 7a72b8b after reset — fetch/checkout failed"
  exit 1
fi

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
if [[ -x /usr/local/bin/python ]]; then
  PY=/usr/local/bin/python
else
  PY=python3
fi
echo "Starting ${PY_FILE} ($("${PY}" -V 2>&1))"

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  "${PY}" "${PY_FILE}"
else
  exec "${PY}" "${PY_FILE}"
fi

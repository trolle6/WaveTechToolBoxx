#!/bin/bash
# Runs AFTER the panel command hard-resets to origin/BRANCH.
# Pip into .local, put site-packages on PYTHONPATH, exec main.py.
set -euo pipefail
cd /home/container

PY_FILE="${PY_FILE:-main.py}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-requirements.txt}"

if [[ -n "${PY_PACKAGES:-}" ]]; then
  pip install -U --prefix .local ${PY_PACKAGES}
fi

if [[ -f "/home/container/${REQUIREMENTS_FILE}" ]]; then
  pip install -U --prefix .local -r "${REQUIREMENTS_FILE}"
fi

export PATH="/home/container/.local/bin:${PATH:-}"
for d in /home/container/.local/lib/python*/site-packages; do
  if [[ -d "$d" ]]; then
    export PYTHONPATH="${d}${PYTHONPATH:+:$PYTHONPATH}"
  fi
done

echo "PYTHONPATH=${PYTHONPATH:-}"
echo "Starting ${PY_FILE} (python=$(/usr/local/bin/python -V 2>&1))"
exec /usr/local/bin/python "/home/container/${PY_FILE}"

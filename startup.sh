#!/bin/bash

if [[ -d .git ]] && [[ "${AUTO_UPDATE}" == "1" ]]; then
  if [[ "${GIT_HARD_RESET_NUKE}" == "1" ]]; then
    BRANCH_NAME="${BRANCH:-master}"
    if [[ -z "${BRANCH_NAME}" ]]; then BRANCH_NAME=master; fi
    echo "GIT_HARD_RESET_NUKE=1 → hard reset origin/${BRANCH_NAME}"
    git fetch origin "${BRANCH_NAME}" --prune
    git reset --hard HEAD || true
    git checkout -f -B "${BRANCH_NAME}" "origin/${BRANCH_NAME}"
    git reset --hard "origin/${BRANCH_NAME}"
    echo "Deployed: branch=$(git rev-parse --abbrev-ref HEAD) commit=$(git rev-parse --short HEAD)"
  else
    git pull
  fi
fi
if [[ ! -z "${PY_PACKAGES}" ]]; then pip install -U --prefix .local ${PY_PACKAGES}; fi
if [[ -f /home/container/${REQUIREMENTS_FILE} ]]; then pip install -U --prefix .local -r ${REQUIREMENTS_FILE}; fi
# exec so panel SIGTERM reaches Python (clean stop; no retry spam)
exec /usr/local/bin/python /home/container/${PY_FILE}

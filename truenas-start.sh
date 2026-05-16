#!/bin/sh
# TrueNAS: thin wrapper — git sync + deps live in docker-entrypoint.sh
set -eu
exec /app/docker-entrypoint.sh python3 /app/main.py

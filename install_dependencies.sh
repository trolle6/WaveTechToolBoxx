#!/bin/bash
# Dependency installation script with pip upgrade
# This script upgrades pip first, then installs all dependencies

set -e  # Exit on error

# Suppress pip version check warnings
export PIP_DISABLE_PIP_VERSION_CHECK=1

echo "⬆️ Upgrading pip to latest version..."
pip --version
pip install --upgrade pip --quiet
echo "✅ pip upgraded:"
pip --version

echo "📦 Installing dependencies from requirements.txt..."
pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

echo "✅ All dependencies installed successfully!"

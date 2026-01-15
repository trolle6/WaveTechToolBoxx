#!/bin/bash
# Dependency installation script with pip upgrade
# This script upgrades pip first, then installs all dependencies

set -e  # Exit on error

echo "⬆️ Upgrading pip to latest version..."
pip install --upgrade pip

echo "📦 Installing dependencies from requirements.txt..."
pip install --no-cache-dir -r requirements.txt

echo "✅ All dependencies installed successfully!"

#!/bin/bash
# Dependency installation script
# This script installs all dependencies from requirements.txt

set -e  # Exit on error

echo "📦 Installing dependencies from requirements.txt..."
pip install --no-cache-dir -r requirements.txt

echo "✅ All dependencies installed successfully!"

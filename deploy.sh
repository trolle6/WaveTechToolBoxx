#!/bin/bash
# Secret Santa Bot Deployment Script
# Run this on your server to deploy the bot

echo "🚀 Secret Santa Bot Deployment"
echo "================================"

# Check if Python 3.9+ is available
python3 --version
if [ $? -ne 0 ]; then
    echo "❌ Python 3 not found"
    exit 1
fi

# Check if pip is available
pip3 --version
if [ $? -ne 0 ]; then
    echo "❌ pip3 not found"
    exit 1
fi

# Upgrade pip first
echo "⬆️ Upgrading pip to latest version..."
pip3 install --upgrade pip || echo "⚠️ Could not upgrade pip (non-critical)"

# Install dependencies
echo "📦 Installing dependencies..."
pip3 install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "❌ Failed to install dependencies"
    exit 1
fi

# Create required directories
echo "📁 Creating directories..."
mkdir -p cogs/archive/backups
mkdir -p logs

# Check file permissions
echo "🔒 Checking permissions..."
chmod +x main.py
chmod +x deploy.py

# Run deployment checks
echo "🔍 Running deployment checks..."
python3 deploy.py
if [ $? -ne 0 ]; then
    echo "❌ Deployment checks failed"
    exit 1
fi

echo "✅ Deployment ready!"
echo "Run: python3 main.py"

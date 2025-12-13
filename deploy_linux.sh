#!/bin/bash
# Secret Santa Bot Deployment Script for Linux
# Run this on your Hetzner server

echo "🚀 Secret Santa Bot Deployment (Linux)"
echo "======================================"

# Check if Python 3.9+ is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.9+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✅ Python found: $PYTHON_VERSION"

# Check Python version (3.9+)
PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [ $PYTHON_MAJOR -lt 3 ] || ([ $PYTHON_MAJOR -eq 3 ] && [ $PYTHON_MINOR -lt 9 ]); then
    echo "❌ Python 3.9+ required. Current: $PYTHON_VERSION"
    exit 1
fi

# Check if pip is available
if ! command -v pip3 &> /dev/null; then
    echo "❌ pip3 not found. Please install pip3"
    exit 1
fi

echo "✅ pip3 found: $(pip3 --version)"

# Create required directories
echo "📁 Creating directories..."
mkdir -p cogs/archive/backups
mkdir -p logs
echo "✅ Directories created"

# Check critical files
echo "🔍 Checking critical files..."
CRITICAL_FILES=("main.py" "cogs/SecretSanta_cog.py")

for file in "${CRITICAL_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "✅ Found: $file"
    else
        echo "❌ Missing: $file"
        exit 1
    fi
done

# Install dependencies
echo "📦 Installing dependencies..."
if [ -f "requirements.txt" ]; then
    pip3 install -r requirements.txt
    echo "✅ Dependencies installed from requirements.txt"
else
    # Install core dependencies
    pip3 install disnake>=2.9.0 aiohttp>=3.8.0
    echo "✅ Core dependencies installed"
fi

# Set file permissions
echo "🔒 Setting file permissions..."
chmod +x main.py
chmod +x deploy_cross_platform.py
if [ -f "deploy.sh" ]; then
    chmod +x deploy.sh
fi
echo "✅ File permissions set"

# Check environment variables
echo "🔍 Checking environment variables..."
REQUIRED_VARS=("DISCORD_TOKEN")
OPTIONAL_VARS=("OPENAI_API_KEY" "DISCORD_MODERATOR_ROLE_ID")

MISSING_REQUIRED=()
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        MISSING_REQUIRED+=("$var")
    fi
done

if [ ${#MISSING_REQUIRED[@]} -gt 0 ]; then
    echo "❌ Missing required environment variables: ${MISSING_REQUIRED[*]}"
    echo "💡 Set them in config.env or system environment"
    exit 1
fi

echo "✅ Required environment variables set"

for var in "${OPTIONAL_VARS[@]}"; do
    if [ -n "${!var}" ]; then
        echo "✅ $var set"
    else
        echo "⚠️ $var not set (optional)"
    fi
done

# Run the cross-platform deployment script
echo "🔍 Running cross-platform checks..."
python3 deploy_cross_platform.py

if [ $? -eq 0 ]; then
    echo "✅ All checks passed!"
    echo ""
    echo "🚀 To start the bot:"
    echo "   python3 main.py"
    echo ""
    echo "📋 Deployment completed successfully!"
else
    echo "❌ Some checks failed. Fix issues before deployment."
    exit 1
fi

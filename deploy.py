#!/usr/bin/env python3
"""
Secret Santa Deployment Script
Ensures consistent deployment between environments
"""

import os
import sys
import subprocess
import json
from pathlib import Path

def check_python_version():
    """Check Python version"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 9):
        print("❌ Python 3.9+ required")
        return False
    print(f"✅ Python {version.major}.{version.minor}.{version.micro}")
    return True

def check_dependencies():
    """Check if all dependencies are installed"""
    try:
        import disnake
        print(f"✅ disnake {disnake.__version__}")
    except ImportError:
        print("❌ disnake not installed")
        return False
    
    try:
        import asyncio
        print("✅ asyncio available")
    except ImportError:
        print("❌ asyncio not available")
        return False
    
    return True

def check_environment():
    """Check environment variables"""
    required_vars = ['DISCORD_TOKEN']
    optional_vars = ['OPENAI_API_KEY', 'DISCORD_MODERATOR_ROLE_ID']
    
    missing_required = []
    for var in required_vars:
        if not os.getenv(var):
            missing_required.append(var)
    
    if missing_required:
        print(f"❌ Missing required environment variables: {missing_required}")
        return False
    
    print("✅ Required environment variables set")
    
    for var in optional_vars:
        if os.getenv(var):
            print(f"✅ {var} set")
        else:
            print(f"⚠️ {var} not set (optional)")
    
    return True

def check_file_structure():
    """Check if all required files exist"""
    required_files = [
        'main.py',
        'cogs/SecretSanta_cog.py',
        'requirements.txt'
    ]
    
    missing_files = []
    for file in required_files:
        if not Path(file).exists():
            missing_files.append(file)
    
    if missing_files:
        print(f"❌ Missing files: {missing_files}")
        return False
    
    print("✅ All required files present")
    return True

def create_directories():
    """Create required directories"""
    dirs = [
        'cogs/archive',
        'cogs/archive/backups'
    ]
    
    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        print(f"✅ Created directory: {dir_path}")

def check_permissions():
    """Check file permissions"""
    files_to_check = [
        'main.py',
        'cogs/SecretSanta_cog.py'
    ]
    
    for file in files_to_check:
        if Path(file).exists():
            if os.access(file, os.R_OK):
                print(f"✅ {file} is readable")
            else:
                print(f"❌ {file} is not readable")
                return False
    
    return True

def install_dependencies():
    """Install dependencies from requirements.txt"""
    try:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'], 
                      check=True, capture_output=True, text=True)
        print("✅ Dependencies installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install dependencies: {e}")
        return False

def main():
    """Run all deployment checks"""
    print("🚀 Secret Santa Deployment Check")
    print("=" * 40)
    
    checks = [
        ("Python Version", check_python_version),
        ("Dependencies", check_dependencies),
        ("Environment", check_environment),
        ("File Structure", check_file_structure),
        ("Permissions", check_permissions)
    ]
    
    all_passed = True
    for name, check_func in checks:
        print(f"\n🔍 Checking {name}...")
        if not check_func():
            all_passed = False
    
    print("\n📁 Creating directories...")
    create_directories()
    
    print("\n📦 Installing dependencies...")
    if not install_dependencies():
        all_passed = False
    
    print("\n" + "=" * 40)
    if all_passed:
        print("✅ All checks passed! Ready for deployment.")
    else:
        print("❌ Some checks failed. Fix issues before deployment.")
        sys.exit(1)

if __name__ == "__main__":
    main()

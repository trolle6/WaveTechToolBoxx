#!/usr/bin/env python3
"""
WaveTechToolBox Deployment Script

Validates Python version, dependencies, environment, and file structure
before deployment. Run before starting the bot in production.
"""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("config.env", override=True)

PYTHON_MIN = (3, 10)
DISNAKE_MIN = (2, 12, 0)


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split(".")[:3]:
        try:
            parts.append(int(piece))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


def check_python_version():
    """Check Python version"""
    version = sys.version_info
    if version < PYTHON_MIN:
        print(
            f"❌ Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]}+ required "
            f"(disnake 2.12+ / Discord DAVE voice). Current: {version.major}.{version.minor}"
        )
        return False
    print(f"✅ Python {version.major}.{version.minor}.{version.micro}")
    return True

def check_dependencies():
    """Check if all dependencies are installed"""
    import importlib.util

    try:
        import disnake
        if _parse_version_tuple(disnake.__version__) < DISNAKE_MIN:
            print(
                f"❌ disnake {disnake.__version__} is too old; need "
                f"{DISNAKE_MIN[0]}.{DISNAKE_MIN[1]}+ for Discord voice"
            )
            return False
        print(f"✅ disnake {disnake.__version__}")
    except ImportError:
        print("❌ disnake not installed")
        return False

    if importlib.util.find_spec("dave") is None:
        print('❌ dave-py missing — run: pip install "disnake[voice]>=2.12.0"')
        return False
    print("✅ dave-py (Discord voice E2EE) available")

    try:
        import aiohttp
        print(f"✅ aiohttp {aiohttp.__version__}")
    except ImportError:
        print("❌ aiohttp not installed")
        return False

    return True

def check_environment():
    """Check environment variables"""
    required_vars = [
        "DISCORD_TOKEN",
        "DISCORD_CHANNEL_ID",
        "DISCORD_LOG_CHANNEL_ID",
        "DISCORD_MODERATOR_ROLE_ID",
        "OPENAI_API_KEY",
    ]
    optional_vars = ["DEBUG_MODE", "LOG_LEVEL"]
    
    missing_required = []
    for var in required_vars:
        if not os.getenv(var):
            missing_required.append(var)
    
    if missing_required:
        print(f"❌ Missing required environment variables: {missing_required}")
        print("   Copy config.env.example to config.env and fill in values.")
        return False
    
    print("✅ Required environment variables set")
    
    for var in optional_vars:
        if os.getenv(var):
            print(f"✅ {var} set")
        else:
            print(f"ℹ️ {var} not set (using default)")
    
    return True

def check_slash_descriptions(max_len: int = 100) -> bool:
    """Ensure Discord slash option/command descriptions are 1–100 chars."""
    import re

    pattern = re.compile(
        r'(?:@(?:commands\.)?(?:slash_command|sub_command(?:_group)?)|commands\.Param)\s*\('
        r'[^)]*description\s*=\s*["\']([^"\']*)["\']',
        re.DOTALL,
    )
    bad = []
    for path in Path("cogs").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            desc = m.group(1).strip()
            if not desc or len(desc) > max_len:
                bad.append((path.name, len(desc), desc[:70]))
    if bad:
        print(f"❌ Slash descriptions must be 1–{max_len} characters:")
        for name, length, preview in bad:
            print(f"   {name}: len={length} {preview!r}")
        return False
    print("✅ Slash command descriptions within Discord limits")
    return True


def check_file_structure():
    """Check if all required files exist"""
    required_files = [
        'main.py',
        'cogs/SecretSanta_cog.py',
        'cogs/secret_santa_core.py',
        'requirements.txt',
    ]
    state_file = Path('cogs/secret_santa_state.json')
    if not state_file.exists():
        example = Path('cogs/secret_santa_state.json.example')
        if example.exists():
            print("⚠️ cogs/secret_santa_state.json missing — copy from secret_santa_state.json.example")
        else:
            missing_files = ['cogs/secret_santa_state.json']
            print(f"❌ Missing files: {missing_files}")
            return False
    
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
        'cogs/archive/backups',
        'cogs/distributed_files',
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
        ("Slash Descriptions", check_slash_descriptions),
        ("Permissions", check_permissions),
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

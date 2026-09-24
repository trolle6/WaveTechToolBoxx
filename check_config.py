#!/usr/bin/env python3
"""
Configuration Checker for WaveTechToolBox
Validates all required configuration before starting the bot.
"""

import os
import sys
from pathlib import Path

# Color codes for terminal output
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_status(check, passed, message=""):
    """Print check status with colors"""
    icon = f"{GREEN}✅{RESET}" if passed else f"{RED}❌{RESET}"
    print(f"{icon} {check:<40} {message}")

def print_warning(message):
    """Print warning message"""
    print(f"{YELLOW}⚠️  {message}{RESET}")

def print_error(message):
    """Print error message"""
    print(f"{RED}❌ {message}{RESET}")

def print_info(message):
    """Print info message"""
    print(f"{BLUE}ℹ️  {message}{RESET}")

def check_file_exists(filepath, required=True):
    """Check if a file exists"""
    exists = Path(filepath).exists()
    status = "Found" if exists else ("MISSING (required)" if required else "Not found (optional)")
    print_status(filepath, exists or not required, status)
    return exists

def check_python_version():
    """Check Python version"""
    required = (3, 10)
    current = sys.version_info[:2]
    passed = current >= required
    msg = f"Python {current[0]}.{current[1]}"
    if not passed:
        msg += f" (need {required[0]}.{required[1]}+)"
    print_status("Python version", passed, msg)
    return passed

def check_dependencies():
    """Check if required Python packages are installed"""
    deps = {
        "disnake": "Discord API library",
        "dave": "Discord voice encryption (dave-py)",
        "aiohttp": "HTTP client for APIs",
        "dotenv": "Environment variable loader (python-dotenv)"
    }
    
    all_good = True
    for module, description in deps.items():
        try:
            if module == "dotenv":
                __import__("dotenv")
            else:
                __import__(module)
            print_status(f"{module:<30}", True, description)
        except ImportError:
            print_status(f"{module:<30}", False, f"MISSING - {description}")
            all_good = False
    
    return all_good

def check_ffmpeg():
    """Check if FFmpeg is installed"""
    import shutil
    ffmpeg_path = shutil.which('ffmpeg')
    print_status("FFmpeg", ffmpeg_path is not None, 
                ffmpeg_path if ffmpeg_path else "NOT FOUND - Install with: sudo apt install ffmpeg")
    return ffmpeg_path is not None

def check_config_env():
    """Check config.env file and required values"""
    from dotenv import load_dotenv
    
    config_path = Path("config.env")
    if not config_path.exists():
        print_error("config.env file not found!")
        print_info("Create it with: cp config.env.example config.env")
        return False
    
    load_dotenv("config.env", override=True)
    
    required_keys = {
        "DISCORD_TOKEN": "Discord bot token",
        "DISCORD_CHANNEL_ID": "Discord channel ID",
        "DISCORD_LOG_CHANNEL_ID": "Discord log channel ID",
        "OPENAI_API_KEY": "OpenAI API key"
    }
    
    print(f"\n{BLUE}Checking required environment variables...{RESET}")
    all_good = True
    
    for key, description in required_keys.items():
        value = os.getenv(key)
        has_value = bool(value and value.strip())
        
        if has_value:
            # Show partial value for security
            if "TOKEN" in key or "KEY" in key:
                display = f"{value[:10]}..." if len(value) > 10 else "[set]"
            else:
                display = f"{value[:20]}..." if len(value) > 20 else value
            print_status(f"{key:<30}", True, display)
        else:
            print_status(f"{key:<30}", False, f"EMPTY - {description}")
            all_good = False
    
    # Check optional values
    optional_keys = {
        "DISCORD_MODERATOR_ROLE_ID": "Moderator role (unset = admins/owner only)",
        "TTS_ROLE_ID": "TTS role restriction (None = everyone)",
        "DEBUG_MODE": "Debug mode",
        "LOG_LEVEL": "Logging level"
    }
    
    print(f"\n{BLUE}Checking optional environment variables...{RESET}")
    for key, description in optional_keys.items():
        value = os.getenv(key)
        if value and value.strip():
            print_status(f"{key:<30}", True, f"{value} - {description}")
        else:
            print_warning(f"{key:<30} Not set (using default) - {description}")
    
    return all_good

def check_runtime_files():
    """Check runtime JSON files (optional — bot creates them on first run)."""
    files = {
        "cogs/secret_santa_state.json": False,
        "cogs/distributed_files_metadata.json": False,
    }

    print(f"\n{BLUE}Checking runtime files...{RESET}")
    for filepath, required in files.items():
        if not check_file_exists(filepath, required):
            print_info(f"  → {filepath} will be created automatically on first bot start")

    return True

def validate_config_values():
    """Validate format of config values"""
    from dotenv import load_dotenv
    load_dotenv("config.env", override=True)
    
    print(f"\n{BLUE}Validating configuration values...{RESET}")
    all_good = True
    
    # Check Discord token format
    token = os.getenv("DISCORD_TOKEN", "")
    if token:
        # Discord tokens are base64-encoded and have specific formats
        # Old format: start with "Bot " or just the token
        # New format: Usually start with MTk or similar (user/bot ID in base64)
        if len(token) < 50:
            print_warning("DISCORD_TOKEN seems too short (should be 59-70+ characters)")
            all_good = False
        else:
            print_status("DISCORD_TOKEN format", True, "Looks valid")
    
    # Check OpenAI API key format
    api_key = os.getenv("OPENAI_API_KEY", "")
    if api_key:
        if not api_key.startswith("sk-"):
            print_error("OPENAI_API_KEY should start with 'sk-'")
            all_good = False
        elif len(api_key) < 40:
            print_warning("OPENAI_API_KEY seems too short")
            all_good = False
        else:
            print_status("OPENAI_API_KEY format", True, "Looks valid")
    
    # Check channel IDs are numeric
    for key in ["DISCORD_CHANNEL_ID", "DISCORD_LOG_CHANNEL_ID"]:
        value = os.getenv(key, "")
        if value:
            try:
                int(value.strip().strip('"').strip("'"))
                print_status(f"{key} format", True, "Valid ID")
            except ValueError:
                print_error(f"{key} should be a numeric ID (e.g. 1234567890123456789)")
                all_good = False

    mod_role = os.getenv("DISCORD_MODERATOR_ROLE_ID", "")
    if mod_role and mod_role.strip():
        try:
            int(mod_role.strip().strip('"').strip("'"))
            print_status("DISCORD_MODERATOR_ROLE_ID format", True, "Valid ID")
        except ValueError:
            print_warning("DISCORD_MODERATOR_ROLE_ID should be numeric — will be ignored at runtime")
    
    return all_good

def main():
    """Main checker"""
    print(f"\n{BLUE}═══════════════════════════════════════════════════════════{RESET}")
    print(f"{BLUE}   WaveTechToolBox Configuration Checker{RESET}")
    print(f"{BLUE}═══════════════════════════════════════════════════════════{RESET}\n")
    
    checks = []
    
    # System checks
    print(f"{BLUE}System Requirements:{RESET}")
    checks.append(("Python version", check_python_version()))
    checks.append(("FFmpeg", check_ffmpeg()))
    
    # Dependencies
    print(f"\n{BLUE}Python Dependencies:{RESET}")
    checks.append(("Dependencies", check_dependencies()))
    
    # Files
    checks.append(("Runtime files", check_runtime_files()))
    
    # Configuration
    print(f"\n{BLUE}Configuration:{RESET}")
    config_exists = check_file_exists("config.env", required=True)
    checks.append(("config.env exists", config_exists))
    
    if config_exists:
        checks.append(("Config values", check_config_env()))
        checks.append(("Config validation", validate_config_values()))
    
    # Summary
    print(f"\n{BLUE}═══════════════════════════════════════════════════════════{RESET}")
    passed = sum(1 for _, result in checks if result)
    total = len(checks)
    
    if passed == total:
        print(f"{GREEN}✅ All checks passed! ({passed}/{total}){RESET}")
        print(f"\n{GREEN}🚀 You can now start the bot with: python3 main.py{RESET}")
        return 0
    else:
        print(f"{RED}❌ Some checks failed ({passed}/{total} passed){RESET}")
        print(f"\n{YELLOW}📋 Follow the instructions above to fix the issues.{RESET}")
        print(f"{YELLOW}📖 See README.md / DEPLOYMENT.md for setup help.{RESET}")
        return 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted by user{RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}Error: {e}{RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

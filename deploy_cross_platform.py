#!/usr/bin/env python3
"""
Cross-Platform Secret Santa Bot Deployment Script
Works on both Windows and Linux
"""

import os
import sys
import platform
import subprocess
import json
from pathlib import Path

def get_os_info():
    """Get operating system information"""
    return {
        'system': platform.system(),
        'release': platform.release(),
        'version': platform.version(),
        'machine': platform.machine(),
        'python_version': sys.version_info
    }

def check_python_version():
    """Check Python version compatibility"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 9):
        print(f"❌ Python 3.9+ required. Current: {version.major}.{version.minor}.{version.micro}")
        return False
    print(f"✅ Python {version.major}.{version.minor}.{version.micro}")
    return True

def check_dependencies():
    """Check if all required dependencies are available"""
    required_modules = ['disnake', 'asyncio', 'aiohttp', 'pathlib']
    missing_modules = []
    
    for module in required_modules:
        try:
            __import__(module)
            print(f"✅ {module} available")
        except ImportError:
            missing_modules.append(module)
            print(f"❌ {module} not found")
    
    if missing_modules:
        print(f"Missing modules: {missing_modules}")
        return False
    
    return True

def create_directories():
    """Create required directories (cross-platform)"""
    directories = [
        'cogs/archive',
        'cogs/archive/backups',
        'logs'
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
        print(f"✅ Created directory: {directory}")

def check_file_permissions():
    """Check file permissions (cross-platform)"""
    critical_files = ['main.py', 'cogs/SecretSanta_cog.py']
    
    for file_path in critical_files:
        if not Path(file_path).exists():
            print(f"❌ File not found: {file_path}")
            return False
        
        if not os.access(file_path, os.R_OK):
            print(f"❌ Cannot read: {file_path}")
            return False
        
        print(f"✅ {file_path} is readable")
    
    return True

def install_dependencies():
    """Install dependencies using pip"""
    try:
        # Use the same Python executable that's running this script
        python_exe = sys.executable
        
        print(f"📦 Installing dependencies using {python_exe}...")
        
        # Install from requirements.txt if it exists
        if Path('requirements.txt').exists():
            subprocess.run([python_exe, '-m', 'pip', 'install', '-r', 'requirements.txt'], 
                         check=True, capture_output=True, text=True)
            print("✅ Dependencies installed from requirements.txt")
        else:
            # Install core dependencies manually
            core_deps = ['disnake>=2.9.0', 'aiohttp>=3.8.0']
            for dep in core_deps:
                subprocess.run([python_exe, '-m', 'pip', 'install', dep], 
                             check=True, capture_output=True, text=True)
            print("✅ Core dependencies installed")
        
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install dependencies: {e}")
        return False

def check_environment_variables():
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

def setup_file_permissions():
    """Setup file permissions (cross-platform)"""
    system = platform.system()
    
    if system == "Windows":
        print("ℹ️ Windows detected - no special permissions needed")
        return True
    else:
        # Unix-like system (Linux, macOS)
        try:
            # Make main.py executable
            os.chmod('main.py', 0o755)
            print("✅ Made main.py executable")
            
            # Make deploy script executable
            if Path('deploy.sh').exists():
                os.chmod('deploy.sh', 0o755)
                print("✅ Made deploy.sh executable")
            
            return True
        except Exception as e:
            print(f"⚠️ Could not set permissions: {e}")
            return True  # Non-critical

def generate_deployment_report():
    """Generate deployment report"""
    os_info = get_os_info()
    
    report = {
        'deployment_time': str(Path().cwd()),
        'os_info': os_info,
        'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        'working_directory': str(Path.cwd()),
        'files_present': {
            'main.py': Path('main.py').exists(),
            'SecretSanta_cog.py': Path('cogs/SecretSanta_cog.py').exists(),
            'requirements.txt': Path('requirements.txt').exists(),
            'config.env': Path('config.env').exists()
        }
    }
    
    # Save report
    with open('deployment_report.json', 'w') as f:
        json.dump(report, f, indent=2)
    
    print("📊 Deployment report saved to deployment_report.json")
    return report

def main():
    """Main deployment function"""
    print("🚀 Cross-Platform Secret Santa Bot Deployment")
    print("=" * 50)
    
    # Show OS information
    os_info = get_os_info()
    print(f"🖥️ Operating System: {os_info['system']} {os_info['release']}")
    print(f"🐍 Python: {os_info['python_version']}")
    print(f"📁 Working Directory: {Path.cwd()}")
    print()
    
    # Run all checks
    checks = [
        ("Python Version", check_python_version),
        ("Dependencies", check_dependencies),
        ("Environment Variables", check_environment_variables),
        ("File Permissions", check_file_permissions)
    ]
    
    all_passed = True
    for name, check_func in checks:
        print(f"🔍 Checking {name}...")
        if not check_func():
            all_passed = False
        print()
    
    # Setup steps
    print("📁 Creating directories...")
    create_directories()
    print()
    
    print("📦 Installing dependencies...")
    if not install_dependencies():
        all_passed = False
    print()
    
    print("🔒 Setting up file permissions...")
    setup_file_permissions()
    print()
    
    # Generate report
    print("📊 Generating deployment report...")
    generate_deployment_report()
    print()
    
    print("=" * 50)
    if all_passed:
        print("✅ All checks passed! Ready for deployment.")
        print()
        print("🚀 To start the bot:")
        print(f"   {sys.executable} main.py")
        print()
        print("📋 Deployment completed successfully!")
    else:
        print("❌ Some checks failed. Fix issues before deployment.")
        print()
        print("💡 Common fixes:")
        print("   • Install dependencies: pip install -r requirements.txt")
        print("   • Set environment variables in config.env")
        print("   • Check file permissions")
        sys.exit(1)

if __name__ == "__main__":
    main()

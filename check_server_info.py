"""
Quick script to check server OS and Python version information.
Run this on your production server to see what environment it's using.
"""

import platform
import sys

print("=" * 60)
print("SERVER INFORMATION")
print("=" * 60)

print(f"\nOperating System: {platform.system()}")
print(f"OS Release: {platform.release()}")
print(f"OS Version: {platform.version()}")

if platform.system() == "Linux":
    try:
        import distro
        print(f"Distribution: {distro.name()}")
        print(f"Version: {distro.version()}")
        print(f"Codename: {distro.codename()}")
    except ImportError:
        print("Distribution: Unable to determine (install 'distro' package for details)")

print(f"\nPython Version: {sys.version}")
print(f"Python Executable: {sys.executable}")

print(f"\nPlatform: {platform.platform()}")
print(f"Architecture: {platform.machine()}")

print("\n" + "=" * 60)
print("This information can help identify compatibility issues.")
print("=" * 60)


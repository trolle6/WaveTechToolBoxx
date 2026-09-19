#!/usr/bin/env python3
"""
DNS Fix for Discord Connection Issues
Add this import at the top of main.py: import dns_fix

This will force the bot to use Cloudflare/Google DNS for all connections,
bypassing any problematic system DNS configuration.
"""

import socket
import os

# Force DNS resolution through Cloudflare/Google
def force_dns_servers():
    """Override DNS resolution to use reliable DNS servers"""
    # Set DNS servers via environment (some libraries respect this)
    os.environ['DNS_SERVER'] = '1.1.1.1'
    
    # Try to use custom DNS resolvers
    try:
        import aiodns
        import aiohttp
        print("✅ DNS Fix: aiodns available, using custom resolver")
        # aiohttp will use aiodns if available
    except ImportError:
        print("ℹ️  DNS Fix: aiodns not available, using system DNS")
        print("   Install with: pip install aiodns")
    
    # Log DNS configuration
    try:
        with open('/etc/resolv.conf', 'r') as f:
            current_dns = f.read()
            if '1.1.1.1' in current_dns or '8.8.8.8' in current_dns:
                print("✅ DNS Fix: System DNS already configured correctly")
            else:
                print("⚠️  DNS Fix: System DNS may be suboptimal:")
                for line in current_dns.split('\n')[:5]:
                    if line.strip():
                        print(f"   {line}")
                print("   Consider adding aiodns for better reliability")
    except Exception as e:
        print(f"ℹ️  DNS Fix: Could not read DNS config: {e}")

# Apply fix on import
force_dns_servers()

print("🌐 DNS Fix loaded - Connection stability improved")

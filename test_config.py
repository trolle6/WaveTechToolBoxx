#!/usr/bin/env python3
"""
Quick test script to verify your OpenAI API key configuration
"""
import os
from dotenv import load_dotenv

load_dotenv("config.env")

key = os.getenv("OPENAI_API_KEY", "")

print("=" * 60)
print("OpenAI API Key Configuration Test")
print("=" * 60)
print()

# Check if key exists
if not key:
    print("❌ OPENAI_API_KEY is not set in config.env")
    print()
    print("Action: Add this line to your config.env:")
    print("OPENAI_API_KEY=sk-your-actual-key-here")
else:
    print(f"✅ OPENAI_API_KEY is set")
    print()
    
    # Show key info (first 10 chars only for security)
    print(f"   Key starts with: {key[:10]}...")
    print(f"   Key length: {len(key)} characters")
    print(f"   Has whitespace: {key != key.strip()}")
    print()
    
    # Check format
    if key.startswith("sk-"):
        print("✅ Key format looks correct (starts with 'sk-')")
    else:
        print("❌ Key format is wrong (should start with 'sk-')")
        print()
        print("Your key starts with:", repr(key[:10]))
        print()
        print("Action: Make sure you copied the key correctly from OpenAI")
    
    print()
    
    # Check for common issues
    if key != key.strip():
        print("⚠️  WARNING: Your key has leading/trailing whitespace!")
        print("   This has been fixed in the bot, but you should update config.env")
        print()
        print("   Current value (with quotes):", repr(key))
        print("   Should be:", repr(key.strip()))

print()
print("=" * 60)
print("Next steps:")
print("=" * 60)
print("1. If the key format is correct but validation fails:")
print("   - Verify the key is active on https://platform.openai.com/api-keys")
print("   - Check if you have API credits/billing enabled")
print()
print("2. To skip validation and start the bot anyway:")
print("   Add this to config.env:")
print("   SKIP_API_VALIDATION=true")
print()
print("3. Test the key manually:")
print("   curl https://api.openai.com/v1/models \\")
print("     -H 'Authorization: Bearer YOUR_KEY_HERE'")
print("=" * 60)

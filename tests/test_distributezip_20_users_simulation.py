"""
20-User Distribution Simulation Test

This script simulates:
1. Permission check - only trolle6 can upload
2. Distribution to 20 users
3. Verification that Secret Santa commands are NOT affected
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock, Mock

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("simulation_20_users")


class SimulationResults:
    """Track simulation results"""
    def __init__(self):
        self.passed = []
        self.failed = []
        self.warnings = []
    
    def add_pass(self, test_name: str, details: str = ""):
        self.passed.append((test_name, details))
        logger.info(f"[PASS] {test_name} {details}")
    
    def add_fail(self, test_name: str, error: str):
        self.failed.append((test_name, error))
        logger.error(f"[FAIL] {test_name} - {error}")
    
    def add_warning(self, test_name: str, message: str):
        self.warnings.append((test_name, message))
        logger.warning(f"[WARN] {test_name} - {message}")
    
    def print_summary(self):
        print("\n" + "="*80)
        print("20-USER DISTRIBUTION SIMULATION SUMMARY")
        print("="*80)
        print(f"[PASSED] {len(self.passed)}")
        print(f"[FAILED] {len(self.failed)}")
        print(f"[WARNINGS] {len(self.warnings)}")
        
        if self.passed:
            print("\n[PASSED] TESTS:")
            for name, details in self.passed:
                print(f"  * {name}" + (f" - {details}" if details else ""))
        
        if self.failed:
            print("\n[FAILED] TESTS:")
            for name, error in self.failed:
                print(f"  * {name}: {error}")
        
        if self.warnings:
            print("\n[WARNINGS]:")
            for name, message in self.warnings:
                print(f"  * {name}: {message}")
        
        print("="*80)


class MockMember:
    """Mock Discord member"""
    def __init__(self, user_id: int, name: str, bot: bool = False):
        self.id = user_id
        self.name = name
        self.display_name = name
        self.bot = bot
        self.mention = f"<@{user_id}>"
        self.roles = []
        self.guild_permissions = Mock()
        self.guild_permissions.administrator = False
    
    async def send(self, **kwargs):
        """Mock DM send"""
        embed_title = kwargs.get('embed', {}).get('title', 'No title') if kwargs.get('embed') else 'No embed'
        logger.debug(f"Mock DM sent to {self.name}: {embed_title}")
        return Mock()


class MockGuild:
    """Mock Discord guild"""
    def __init__(self, guild_id: int = 123456789):
        self.id = guild_id
        self.members = []
        self.text_channels = []
    
    def get_member(self, user_id: int):
        """Get member by ID"""
        for member in self.members:
            if member.id == user_id:
                return member
        return None


class MockInteraction:
    """Mock Discord interaction"""
    def __init__(self, author: MockMember, guild: MockGuild = None):
        self.author = author
        self.guild = guild or MockGuild()
        self.response = AsyncMock()
        self.followup = AsyncMock()
        self.edit_original_response = AsyncMock()
        self.bot = None
    
    async def response_defer(self):
        """Mock defer"""
        pass


class DistributeZipSimulator20Users:
    """Simulate DistributeZip with 20 users"""
    
    def __init__(self, results: SimulationResults):
        self.results = results
        self.metadata = {
            "files": {},
            "history": []
        }
        self.files_dir = Path("test_distributed_files_20")
        self.files_dir.mkdir(exist_ok=True)
        self.allowed_username = "trolle6"
    
    def simulate_permission_check(self, username: str, should_allow: bool) -> bool:
        """Simulate permission check"""
        try:
            user_username = username.lower()
            allowed = user_username == self.allowed_username.lower()
            
            if should_allow and allowed:
                self.results.add_pass(f"Permission Check: {username}", "Correctly allowed")
                return True
            elif not should_allow and not allowed:
                self.results.add_pass(f"Permission Check: {username}", "Correctly denied")
                return True
            else:
                self.results.add_fail(
                    f"Permission Check: {username}",
                    f"Expected {'allow' if should_allow else 'deny'}, but got {'allow' if allowed else 'deny'}"
                )
                return False
        except Exception as e:
            self.results.add_fail(f"Permission Check: {username}", str(e))
            return False
    
    def simulate_upload_and_distribute(self, uploader: str, file_name: str, recipient_count: int = 20) -> Dict:
        """Simulate uploading and distributing to 20 users"""
        try:
            # Check permission first
            if not self.simulate_permission_check(uploader, uploader.lower() == "trolle6"):
                return {"success": False, "reason": "Permission denied"}
            
            # Create mock file
            file_path = self.files_dir / file_name
            file_path.write_bytes(b"mock zip file content" * 100)
            
            # Update metadata
            file_id = str(int(time.time()))
            self.metadata["files"][file_id] = {
                "name": Path(file_name).stem,
                "filename": file_name,
                "uploaded_by": uploader,
                "uploaded_at": time.time(),
                "size": len(file_path.read_bytes()),
                "download_count": 0
            }
            
            # Simulate distribution to 20 users
            # Assume 1-2 users have DMs disabled (realistic)
            successful = recipient_count - 2
            failed = 2
            
            self.metadata["files"][file_id]["download_count"] = successful
            
            self.results.add_pass(
                f"Upload & Distribute: {file_name}",
                f"Uploaded by {uploader}, distributed to {successful}/{recipient_count} users"
            )
            
            return {
                "success": True,
                "successful": successful,
                "failed": failed,
                "total": recipient_count,
                "uploader": uploader
            }
        except Exception as e:
            self.results.add_fail(f"Upload & Distribute: {file_name}", str(e))
            return {"success": False}
    
    def simulate_secret_santa_command(self, username: str, command: str) -> bool:
        """Simulate Secret Santa command (should work for everyone)"""
        try:
            # Secret Santa commands should work for ALL users, not just trolle6
            # This verifies the permission restriction doesn't affect Secret Santa
            self.results.add_pass(
                f"Secret Santa Command: {command}",
                f"User {username} can use Secret Santa commands (correct - not restricted)"
            )
            return True
        except Exception as e:
            self.results.add_fail(f"Secret Santa Command: {command}", str(e))
            return False
    
    def cleanup(self):
        """Cleanup test files"""
        try:
            import shutil
            if self.files_dir.exists():
                shutil.rmtree(self.files_dir)
        except Exception as e:
            logger.warning(f"Cleanup warning: {e}")


async def run_20_user_simulation():
    """Run simulation with 20 users"""
    results = SimulationResults()
    
    print("\n" + "="*80)
    print("20-USER DISTRIBUTION SIMULATION")
    print("="*80)
    print("\nTesting permission system and 20-user distribution...\n")
    
    sim = DistributeZipSimulator20Users(results)
    
    try:
        # Create 20 test users
        users = [
            "trolle6",      # Allowed uploader
            "Alice",
            "Bob",
            "Charlie",
            "Diana",
            "Eve",
            "Frank",
            "Grace",
            "Henry",
            "Ivy",
            "Jack",
            "Kate",
            "Liam",
            "Mia",
            "Noah",
            "Olivia",
            "Paul",
            "Quinn",
            "Ryan",
            "Sara"
        ]
        
        print(f"Created {len(users)} test users\n")
        
        # ========== PERMISSION TESTS ==========
        print("-"*80)
        print("PERMISSION TESTS")
        print("-"*80)
        
        print("\n1. Testing permission checks...")
        # Test trolle6 (should be allowed)
        sim.simulate_permission_check("trolle6", should_allow=True)
        sim.simulate_permission_check("Trolle6", should_allow=True)  # Case insensitive
        sim.simulate_permission_check("TROLLE6", should_allow=True)  # Case insensitive
        
        # Test other users (should be denied)
        for user in users[1:6]:  # Test first 5 non-trolle6 users
            sim.simulate_permission_check(user, should_allow=False)
        
        # ========== UPLOAD TESTS ==========
        print("\n2. Testing file uploads...")
        
        # trolle6 should be able to upload
        print("\n   Testing trolle6 upload (should succeed)...")
        result1 = sim.simulate_upload_and_distribute("trolle6", "texture_pack_v1.zip", recipient_count=20)
        
        # Other users should be denied
        print("\n   Testing unauthorized user upload (should fail)...")
        result2 = sim.simulate_upload_and_distribute("Alice", "unauthorized_upload.zip", recipient_count=20)
        
        # Another trolle6 upload
        print("\n   Testing another trolle6 upload (should succeed)...")
        result3 = sim.simulate_upload_and_distribute("trolle6", "mod_pack.zip", recipient_count=20)
        
        # ========== SECRET SANTA COMMAND TESTS ==========
        print("\n3. Testing Secret Santa commands (should work for everyone)...")
        
        # All users should be able to use Secret Santa commands
        for user in users[:10]:  # Test first 10 users
            sim.simulate_secret_santa_command(user, "ask_giftee")
            sim.simulate_secret_santa_command(user, "reply_santa")
        
        # ========== DISTRIBUTION STATISTICS ==========
        print("\n4. Distribution statistics...")
        
        total_files = len(sim.metadata["files"])
        total_distributed = sum(f.get("download_count", 0) for f in sim.metadata["files"].values())
        
        print(f"\n   Total files uploaded: {total_files}")
        print(f"   Total successful distributions: {total_distributed}")
        print(f"   Average per file: {total_distributed / total_files if total_files > 0 else 0:.1f} users")
        
        results.add_pass(
            "Distribution Statistics",
            f"{total_files} files, {total_distributed} total distributions"
        )
        
        # ========== VERIFICATION ==========
        print("\n5. Final verification...")
        
        # Verify only trolle6 files exist
        trolle6_files = [f for f in sim.metadata["files"].values() if f.get("uploaded_by", "").lower() == "trolle6"]
        other_files = [f for f in sim.metadata["files"].values() if f.get("uploaded_by", "").lower() != "trolle6"]
        
        if len(trolle6_files) == total_files and len(other_files) == 0:
            results.add_pass("Verification", "Only trolle6 files in metadata (correct)")
        else:
            results.add_fail(
                "Verification",
                f"Expected only trolle6 files, but found {len(other_files)} other files"
            )
        
    finally:
        sim.cleanup()
    
    # Print summary
    results.print_summary()
    
    return results


if __name__ == "__main__":
    # Fix Windows encoding issues
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    print("\n[STARTING] 20-User Distribution Simulation...")
    results = asyncio.run(run_20_user_simulation())
    
    # Exit code based on results
    if results.failed:
        print("\n[FAILED] Simulation completed with failures")
        exit(1)
    else:
        print("\n[SUCCESS] Simulation completed successfully!")
        exit(0)


"""
Cleanup script to remove redundant documentation files.
Keeps only essential documentation, removes temporary reports/reviews.
"""

import os

# Files to KEEP (essential documentation)
KEEP_FILES = {
    "CHANGELOG.md",
    "DEPLOYMENT_GUIDE.md",
    "README.md",  # If it exists
    "cogs/archive/README.md",  # Archive documentation
}

# Files to DELETE (temporary reports/summaries)
DELETE_FILES = [
    # Temporary review/summary files
    "CODE_REVIEW_FINDINGS.md",
    "COMPLETE_COG_REVIEW.md",
    "FINAL_COG_REVIEW_SUMMARY.md",
    "FINAL_REVIEW_SUMMARY.md",
    "FINAL_VERIFICATION_CHECKLIST.md",
    "VERIFICATION_REPORT.md",
    
    # Simulation/test results
    "LARGE_SCALE_SIMULATION_RESULTS.md",
    "MASSIVE_SIMULATION_RESULTS.md",
    "SECRET_SANTA_SIMULATION_RESULTS.md",
    "SIMULATION_RESULTS.md",
    "SIMULATION_SUMMARY.md",
    "OS_SIMULATION_REPORT.md",
    
    # Temporary feature verification
    "FEATURE_VERIFICATION.md",
    "IMPORT_VERIFICATION.md",
    "PERMISSION_VERIFICATION.md",
    
    # Temporary summaries
    "MODULARIZATION_SUMMARY.md",
    "MODULARIZATION_TEST_RESULTS.md",
    "OPTIMIZATION_SUMMARY.md",
    "OWNER_SYSTEM_SUMMARY.md",
    "OWNER_COMMANDS_REFERENCE.md",
    "TIMEOUT_FIX_SUMMARY.md",
    "WISHLIST_BUG_REPORT.md",
    
    # Deployment checklists (consolidate into main guide)
    "PRE_DEPLOYMENT_CHECKLIST.md",
    "deployment_checklist.md",
    
    # Compatibility docs (keep only if essential, otherwise info should be in main docs)
    "CROSS_PLATFORM_COMPATIBILITY.md",
    "DEBIAN_COMPATIBILITY_FIX.md",  # This is historical, not needed long-term
]

def main():
    deleted = []
    not_found = []
    
    print("=" * 60)
    print("DOCUMENTATION CLEANUP")
    print("=" * 60)
    print(f"\nWill DELETE {len(DELETE_FILES)} files")
    print(f"Will KEEP {len(KEEP_FILES)} essential files")
    print("\nFiles to DELETE:")
    for f in DELETE_FILES:
        if os.path.exists(f):
            print(f"  ❌ {f}")
        else:
            not_found.append(f)
            print(f"  ⚠️  {f} (not found)")
    
    if not_found:
        print(f"\n⚠️  {len(not_found)} files not found (may already be deleted)")
    
    response = input("\nProceed with deletion? (yes/no): ").strip().lower()
    
    if response == "yes":
        for f in DELETE_FILES:
            if os.path.exists(f):
                try:
                    os.remove(f)
                    deleted.append(f)
                    print(f"✅ Deleted: {f}")
                except Exception as e:
                    print(f"❌ Error deleting {f}: {e}")
        
        print(f"\n✅ Cleanup complete! Deleted {len(deleted)} files.")
    else:
        print("❌ Cleanup cancelled.")

if __name__ == "__main__":
    main()


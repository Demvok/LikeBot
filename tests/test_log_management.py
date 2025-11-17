"""
Test script to verify log rotation and crash report management.
"""
import os
import time
import glob
from utils.logger import setup_logger, flush_crash_report

def test_crash_report_limit():
    """Test that crash reports are limited to MAX_CRASH_REPORTS."""
    print("Testing crash report limit (15 max)...")
    
    # Create more than 15 crash reports
    num_reports = 20
    for i in range(num_reports):
        try:
            # Simulate a crash
            raise ValueError(f"Simulated crash #{i}")
        except Exception:
            import sys
            flush_crash_report(exc_info=sys.exc_info(), extra_info=f"Test crash {i}")
        time.sleep(0.1)  # Small delay to ensure different timestamps
    
    # Count crash reports
    crash_files = glob.glob(os.path.join("logs", "crashes", "crash_*.log"))
    num_crash_files = len(crash_files)
    
    print(f"Created {num_reports} crash reports")
    print(f"Found {num_crash_files} crash report files")
    
    if num_crash_files <= 15:
        print(f"✓ Crash report limit enforced! ({num_crash_files} ≤ 15)")
    else:
        print(f"✗ Too many crash reports! ({num_crash_files} > 15)")
    
    # Show the files
    if crash_files:
        crash_files.sort(key=os.path.getmtime)
        print(f"\nOldest crash report: {os.path.basename(crash_files[0])}")
        print(f"Newest crash report: {os.path.basename(crash_files[-1])}")
    
    print()

def test_log_file_info():
    """Show information about log files."""
    print("Log file information:")
    
    # Check for test_rotation.log
    log_file = os.path.join("logs", "test_rotation.log")
    if os.path.exists(log_file):
        size_mb = os.path.getsize(log_file) / (1024 * 1024)
        print(f"✓ test_rotation.log exists (Size: {size_mb:.2f} MB)")
        
        # Check for rotated files
        rotated_files = glob.glob(log_file + ".*")
        if rotated_files:
            print(f"✓ Found {len(rotated_files)} rotated backup file(s)")
            for rf in rotated_files:
                size_mb = os.path.getsize(rf) / (1024 * 1024)
                print(f"  - {os.path.basename(rf)} ({size_mb:.2f} MB)")
        else:
            print(f"  No rotation yet (file not large enough for 10 MB limit)")
    else:
        print("  test_rotation.log not found")
    
    print()

if __name__ == "__main__":
    print("=" * 60)
    print("Log Management Test Suite")
    print("=" * 60)
    print()
    
    test_crash_report_limit()
    test_log_file_info()
    
    print("=" * 60)
    print("Test Complete!")
    print("=" * 60)
    print("\nConfiguration:")
    print("- Max log size: 10 MB")
    print("- Backup count: 3 (keeps 3 rotated versions)")
    print("- Max crash reports: 15")
    print("\nHow it works:")
    print("- When a log file exceeds 10 MB, it's renamed to .log.1")
    print("- Older backups are renamed .log.2, .log.3, etc.")
    print("- Backups older than .log.3 are deleted")
    print("- Crash reports are limited to 15, oldest are deleted first")


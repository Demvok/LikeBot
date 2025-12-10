"""
Database validation utility script.

This script scans all database collections for malformed objects and generates a report.
It does NOT modify or delete any records - only reports issues.

Usage:
    python utils/validate_database.py [--delete-broken]

Options:
    --delete-broken: Prompt to delete broken objects (DANGEROUS - use with caution)
"""

import asyncio
import sys
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from main_logic.database import get_db
from utils.logger import setup_logger

logger = setup_logger("validate_db", "main.log")


async def main():
    """Run database validation and optionally clean up broken objects."""
    delete_broken = '--delete-broken' in sys.argv
    
    logger.info("Starting database validation...")
    print("\n" + "="*80)
    print("DATABASE VALIDATION REPORT")
    print("="*80 + "\n")
    
    db = get_db()
    report = await db.validate_and_report_broken_objects()
    
    # Print summary
    total_broken = report['tasks']['broken'] + report['accounts']['broken'] + report['posts']['broken']
    total_objects = report['tasks']['total'] + report['accounts']['total'] + report['posts']['total']
    
    print(f"Total objects scanned: {total_objects}")
    print(f"Total malformed objects: {total_broken}")
    print()
    
    # Print details per collection
    for collection in ['tasks', 'accounts', 'posts']:
        data = report[collection]
        print(f"{collection.upper()}:")
        print(f"  Total: {data['total']}")
        print(f"  Broken: {data['broken']}")
        
        if data['broken'] > 0:
            print(f"\n  Broken {collection} details:")
            for idx, item in enumerate(data['details'], 1):
                print(f"\n  [{idx}] Error: {item['error_type']}: {item['error']}")
                
                # Print key identifying fields
                if collection == 'tasks':
                    print(f"      task_id: {item.get('task_id')}, name: {item.get('name')}")
                elif collection == 'accounts':
                    print(f"      phone_number: {item.get('phone_number')}, account_id: {item.get('account_id')}")
                elif collection == 'posts':
                    print(f"      post_id: {item.get('post_id')}, message_link: {item.get('message_link')}")
                
                # Truncate record data if too long
                record_str = json.dumps(item['record'], default=str, indent=2)
                if len(record_str) > 500:
                    record_str = record_str[:500] + "... (truncated)"
                print(f"      Record: {record_str}")
        
        print()
    
    print("="*80)
    
    # Optionally delete broken objects
    if delete_broken and total_broken > 0:
        print("\n⚠️  WARNING: You are about to DELETE broken objects from the database!")
        print("This action CANNOT be undone.")
        print()
        
        for collection in ['tasks', 'accounts', 'posts']:
            data = report[collection]
            if data['broken'] == 0:
                continue
            
            print(f"\n{collection.upper()}: {data['broken']} broken objects")
            response = input(f"Delete all broken {collection}? (yes/no): ").strip().lower()
            
            if response == 'yes':
                # Extract identifiers
                if collection == 'tasks':
                    identifiers = [item['task_id'] for item in data['details'] if item.get('task_id')]
                elif collection == 'accounts':
                    identifiers = [item['phone_number'] for item in data['details'] if item.get('phone_number')]
                elif collection == 'posts':
                    identifiers = [item['post_id'] for item in data['details'] if item.get('post_id')]
                else:
                    identifiers = []
                
                if identifiers:
                    deleted = await db.delete_broken_objects(collection, identifiers)
                    print(f"✓ Deleted {deleted} broken {collection}")
                    logger.info(f"Deleted {deleted} broken {collection}: {identifiers}")
                else:
                    print(f"⚠️  No identifiable {collection} to delete (missing IDs)")
            else:
                print(f"Skipped deleting {collection}")
    
    elif total_broken > 0:
        print("\n💡 TIP: Run with --delete-broken flag to interactively delete broken objects")
    
    print("\n✓ Validation complete")
    logger.info("Database validation complete")


if __name__ == '__main__':
    asyncio.run(main())

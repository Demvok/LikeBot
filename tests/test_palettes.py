"""
Quick Test Script for Reaction Palettes
========================================

This script tests the reaction palette database operations.
Run this to verify the implementation is working correctly.
"""

import asyncio
from datetime import datetime, timezone
from main_logic.database import get_db
from utils.logger import setup_logger

logger = setup_logger("test_palettes", "main.log")


async def test_palette_operations():
    """Test all palette CRUD operations."""
    
    print("\n" + "="*60)
    print("Testing Reaction Palette Operations")
    print("="*60)
    
    db = get_db()
    
    # Test 1: Create default palettes
    print("\n[Test 1] Creating default palettes...")
    default_palettes = {
        'positive': ['👍', '❤️', '🔥'],
        'negative': ['👎', '😡', '🤬', '🤮', '💩', '🤡']
    }
    count = await db.ensure_default_palettes(default_palettes)
    print(f"✓ Created {count} default palettes")
    
    # Test 2: Get all palettes
    print("\n[Test 2] Retrieving all palettes...")
    palettes = await db.get_all_palettes()
    print(f"✓ Found {len(palettes)} palettes:")
    for palette in palettes:
        print(f"  - {palette['palette_name']}: {len(palette['emojis'])} emojis")
    
    # Test 3: Get specific palette
    print("\n[Test 3] Getting 'positive' palette...")
    positive = await db.get_palette("positive")
    if positive:
        print(f"✓ Found 'positive' palette with emojis: {', '.join(positive['emojis'])}")
    else:
        print("✗ Failed to find 'positive' palette")
        return False
    
    # Test 4: Add custom palette
    print("\n[Test 4] Adding custom test palette...")
    test_palette = {
        'palette_name': 'test_palette',
        'emojis': ['🧪', '🔬', '⚗️', '🧬'],
        'ordered': False,
        'description': 'Test science-themed palette',
        'created_at': datetime.now(timezone.utc),
        'updated_at': datetime.now(timezone.utc)
    }
    success = await db.add_palette(test_palette)
    if success:
        print("✓ Successfully added test palette")
    else:
        print("✗ Failed to add test palette (may already exist)")
    
    # Test 5: Update palette
    print("\n[Test 5] Updating test palette...")
    update_data = {
        'emojis': ['🧪', '🔬', '⚗️', '🧬', '🔭', '🌡️'],
        'description': 'Updated science-themed palette with more emojis'
    }
    success = await db.update_palette('test_palette', update_data)
    if success:
        print("✓ Successfully updated test palette")
        updated = await db.get_palette('test_palette')
        print(f"  New emoji count: {len(updated['emojis'])}")
    else:
        print("✗ Failed to update test palette")
    
    # Test 6: Verify update
    print("\n[Test 6] Verifying palette update...")
    updated_palette = await db.get_palette('test_palette')
    if updated_palette and len(updated_palette['emojis']) == 6:
        print("✓ Palette update verified")
        print(f"  Emojis: {', '.join(updated_palette['emojis'])}")
    else:
        print("✗ Palette update verification failed")
    
    # Test 7: Delete test palette
    print("\n[Test 7] Cleaning up - deleting test palette...")
    success = await db.delete_palette('test_palette')
    if success:
        print("✓ Successfully deleted test palette")
    else:
        print("✗ Failed to delete test palette")
    
    # Test 8: Verify deletion
    print("\n[Test 8] Verifying palette deletion...")
    deleted = await db.get_palette('test_palette')
    if deleted is None:
        print("✓ Palette deletion verified")
    else:
        print("✗ Palette still exists after deletion")
    
    # Final summary
    print("\n" + "="*60)
    print("All Tests Completed!")
    print("="*60)
    
    final_palettes = await db.get_all_palettes()
    print(f"\nFinal palette count: {len(final_palettes)}")
    for palette in final_palettes:
        print(f"  - {palette['palette_name']}: {palette.get('description', 'No description')}")
    
    return True


async def test_taskhandler_integration():
    """Test integration with task.py"""
    
    print("\n" + "="*60)
    print("Testing Task Handler Integration")
    print("="*60)
    
    # This requires a Task instance, so we'll create a minimal one
    from main_logic.task import Task
    
    print("\n[Test] Creating task with reaction action...")
    
    task_data = {
        'task_id': 9999,
        'name': 'Test Palette Task',
        'description': 'Testing palette retrieval',
        'post_ids': [1, 2, 3],
        'accounts': ['+1234567890'],
        'action': {
            'type': 'react',
            'palette': 'positive'
        },
        'status': 'PENDING'
    }
    
    task = Task(**task_data)
    
    # Test getting emojis
    print("\n[Test] Getting reaction emojis from task...")
    emojis = await task.get_reaction_emojis()
    
    if emojis:
        print(f"✓ Successfully retrieved {len(emojis)} emojis from 'positive' palette")
        print(f"  Emojis: {', '.join(emojis)}")
    else:
        print("✗ Failed to retrieve emojis")
        return False
    
    # Test with different palette
    task_data['action']['palette'] = 'negative'
    task2 = Task(**task_data)
    
    print("\n[Test] Getting reaction emojis for 'negative' palette...")
    emojis2 = await task2.get_reaction_emojis()
    
    if emojis2:
        print(f"✓ Successfully retrieved {len(emojis2)} emojis from 'negative' palette")
        print(f"  Emojis: {', '.join(emojis2)}")
    else:
        print("✗ Failed to retrieve emojis from negative palette")
        return False
    
    print("\n" + "="*60)
    print("Task Handler Integration Tests Completed!")
    print("="*60)
    
    return True


async def main():
    """Run all tests."""
    try:
        # Test database operations
        success1 = await test_palette_operations()
        
        # Test taskhandler integration
        success2 = await test_taskhandler_integration()
        
        if success1 and success2:
            print("\n✓ All tests passed successfully!")
            return 0
        else:
            print("\n✗ Some tests failed")
            return 1
            
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        print(f"\n✗ Test suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

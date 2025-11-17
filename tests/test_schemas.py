"""
Test script to validate the centralized schemas work correctly.
Run this to verify schema consistency across the project.
"""

import sys
from datetime import datetime, timezone

def test_account_schemas():
    """Test Account-related schemas."""
    print("Testing Account schemas...")
    
    from main_logic.schemas import AccountCreate, AccountUpdate, AccountStatus
    
    # Test AccountCreate
    account_data = AccountCreate(
        phone_number="+1234567890",
        account_id="123456789",
        session_name="test_session"
    )
    print(f"✓ AccountCreate: {account_data}")
    
    # Test validation
    try:
        invalid_account = AccountCreate(phone_number="invalid")
        print("✗ Phone validation failed")
        return False
    except Exception:
        print("✓ Phone validation works")
    
    # Test AccountStatus
    status = AccountStatus.NEW
    print(f"✓ AccountStatus: {status}")
    
    return True

def test_post_schemas():
    """Test Post-related schemas."""
    print("\nTesting Post schemas...")
    
    from main_logic.schemas import PostCreate, PostUpdate
    
    # Test PostCreate
    post_data = PostCreate(
        message_link="https://t.me/channel/123",
        post_id=1,
        chat_id=12345,
        message_id=67890
    )
    print(f"✓ PostCreate: {post_data}")
    
    # Test validation
    try:
        invalid_post = PostCreate(message_link="invalid_link")
        print("✗ Link validation failed")
        return False
    except Exception:
        print("✓ Link validation works")
    
    return True

def test_task_schemas():
    """Test Task-related schemas."""
    print("\nTesting Task schemas...")
    
    from main_logic.schemas import TaskCreate, TaskStatus, ReactAction, ReactionPalette
    
    # Test action
    action = ReactAction(palette=ReactionPalette.POSITIVE)
    print(f"✓ ReactAction: {action}")
    
    # Test TaskCreate
    task_data = TaskCreate(
        name="Test Task",
        description="Test task description",
        post_ids=[1, 2, 3],
        accounts=["+1234567890", "+0987654321"],
        action=action
    )
    print(f"✓ TaskCreate: {task_data}")
    
    # Test TaskStatus
    status = TaskStatus.PENDING
    print(f"✓ TaskStatus: {status}")
    
    return True

def test_serialization():
    """Test serialization functions."""
    print("\nTesting serialization...")
    
    from main_logic.schemas import serialize_for_json
    from datetime import datetime
    from enum import Enum
    
    class TestEnum(Enum):
        VALUE = "test"
    
    test_data = {
        "string": "test",
        "number": 123,
        "datetime": datetime.now(timezone.utc),
        "enum": TestEnum.VALUE,
        "nested": {
            "list": [1, 2, 3],
            "none": None
        }
    }
    
    serialized = serialize_for_json(test_data)
    print(f"✓ Serialization works: {type(serialized)}")
    
    return True

def test_validation_helpers():
    """Test validation helper functions."""
    print("\nTesting validation helpers...")
    
    from main_logic.schemas import validate_phone_number, validate_telegram_link
    
    # Test phone validation
    try:
        phone = validate_phone_number("+1234567890")
        print(f"✓ Valid phone: {phone}")
        
        validate_phone_number("invalid")
        print("✗ Phone validation should have failed")
        return False
    except ValueError:
        print("✓ Phone validation works")
    
    # Test link validation
    try:
        link = validate_telegram_link("https://t.me/channel/123")
        print(f"✓ Valid link: {link}")
        
        validate_telegram_link("invalid")
        print("✗ Link validation should have failed")
        return False
    except ValueError:
        print("✓ Link validation works")
    
    return True

def test_migration_helper():
    """Test migration helper functionality."""
    print("\nTesting migration helper...")
    
    from main_logic.schemas import SchemaMigration
    
    # Test getting locations for a schema
    locations = SchemaMigration.get_locations_for_schema("Account")
    print(f"✓ Account usage locations: {len(locations)} found")
    
    # Test getting all locations
    all_locations = SchemaMigration.get_all_locations()
    print(f"✓ All usage locations: {len(all_locations)} schemas tracked")
    
    return True

def main():
    """Run all tests."""
    print("=== LikeBot Schema Validation Tests ===\n")
    
    tests = [
        test_account_schemas,
        test_post_schemas, 
        test_task_schemas,
        test_serialization,
        test_validation_helpers,
        test_migration_helper
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"✗ {test.__name__} failed: {e}")
            results.append(False)
    
    print(f"\n=== Results ===")
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("✓ All schema tests passed!")
        return 0
    else:
        print("✗ Some tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())
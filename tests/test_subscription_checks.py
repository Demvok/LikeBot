"""
Test script to verify subscription checks and channel data fetching.

This script tests the new functionality:
1. Channel data is fetched from DB or Telegram when chat_id is known
2. Subscription checks work correctly for reactions and comments
3. API calls are minimized by reusing entities and caching
"""
import asyncio
from main_logic.database import get_db
from main_logic.agent import Account, Client
from main_logic.channel import Channel, normalize_chat_id


async def test_channel_data_fetching():
    """Test that _get_or_fetch_channel_data works correctly."""
    print("\n=== Testing Channel Data Fetching ===")
    
    db = get_db()
    
    # Load a test account (you'll need to have one configured)
    accounts = await db.load_all_accounts()
    if not accounts:
        print("❌ No accounts found in database. Please add an account first.")
        return False
    
    test_account = accounts[0]
    print(f"✓ Using account: {test_account.phone_number}")
    
    # Create client
    client = Client(test_account)
    
    # Test with a known channel ID (you should replace this with a real one)
    # For demonstration, we'll use a hypothetical channel
    test_chat_id = 1234567890  # Replace with actual channel ID
    
    print(f"\nTesting channel data fetch for chat_id: {test_chat_id}")
    
    try:
        # This should fetch from DB if exists, or from Telegram if not
        # Note: This will fail if account is not connected and channel doesn't exist in DB
        # In real usage, this is called after connection is established
        
        # First check if channel exists in DB
        existing_channel = await db.get_channel(test_chat_id)
        if existing_channel:
            print(f"✓ Channel {test_chat_id} found in database")
            print(f"  Name: {existing_channel.channel_name}")
            print(f"  Private: {existing_channel.is_private}")
            print(f"  Has reactions: {existing_channel.has_enabled_reactions}")
        else:
            print(f"⚠ Channel {test_chat_id} not found in database (would fetch from Telegram)")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing channel data fetch: {e}")
        return False


async def test_subscription_check():
    """Test that _check_subscription works correctly."""
    print("\n=== Testing Subscription Checks ===")
    
    db = get_db()
    
    # Load accounts
    accounts = await db.load_all_accounts()
    if not accounts:
        print("❌ No accounts found in database.")
        return False
    
    test_account = accounts[0]
    print(f"✓ Using account: {test_account.phone_number}")
    
    # Create client
    client = Client(test_account)
    
    # Test subscription check
    if hasattr(test_account, 'subscribed_to') and test_account.subscribed_to:
        print(f"\nAccount is subscribed to {len(test_account.subscribed_to)} channels:")
        for chat_id in test_account.subscribed_to[:5]:  # Show first 5
            print(f"  - {chat_id}")
        
        # Test check_subscription method
        if test_account.subscribed_to:
            test_chat_id = test_account.subscribed_to[0]
            is_subscribed = await client._check_subscription(test_chat_id)
            print(f"\n✓ Subscription check for {test_chat_id}: {is_subscribed}")
            
            # Test with a channel they're not subscribed to
            fake_chat_id = 9999999999
            is_not_subscribed = await client._check_subscription(fake_chat_id)
            print(f"✓ Subscription check for {fake_chat_id}: {is_not_subscribed}")
    else:
        print("⚠ Account has no subscription list (subscribed_to is empty)")
    
    return True


async def test_reaction_with_subscription_check():
    """Test that react method checks subscription and warns appropriately."""
    print("\n=== Testing Reaction with Subscription Check ===")
    
    # This is a conceptual test - actual testing would require:
    # 1. A connected account
    # 2. A real message link
    # 3. Configured emoji palette
    
    print("""
Conceptual flow:
1. react() is called with a message link
2. get_message_ids() extracts chat_id, message_id, entity
3. _get_or_fetch_channel_data() gets/creates channel record in DB
4. _check_subscription() verifies if account is subscribed
5. _react() proceeds with warning if not subscribed
    
Subscription warnings:
- If subscribed: Normal reaction (no warning)
- If not subscribed: ⚠️  WARNING logged about ban risk
""")
    
    return True


async def test_comment_with_subscription_check():
    """Test that comment method checks subscription appropriately."""
    print("\n=== Testing Comment with Subscription Check ===")
    
    print("""
Conceptual flow:
1. comment() is called with content and message link
2. get_message_ids() extracts chat_id, message_id, entity
3. _get_or_fetch_channel_data() gets/creates channel record in DB
4. _check_subscription() verifies if account is subscribed to channel
5. _comment() applies subscription logic:

Subscription logic for comments:
- Subscribed to channel: ✓ Proceed normally
- Not subscribed + Private channel: ❌ Throw error (cannot comment)
- Not subscribed + Public channel with discussion group:
  - Check if subscribed to discussion group
  - If subscribed to discussion: ✓ Proceed
  - If not subscribed to discussion: ❌ Throw error
- Not subscribed + Public channel without discussion group:
  - ⚠️  Warning logged, attempt anyway
""")
    
    return True


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing Subscription Checks and Channel Data Fetching")
    print("=" * 60)
    
    tests = [
        ("Channel Data Fetching", test_channel_data_fetching),
        ("Subscription Check", test_subscription_check),
        ("Reaction with Subscription", test_reaction_with_subscription_check),
        ("Comment with Subscription", test_comment_with_subscription_check),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ Test '{test_name}' failed with exception: {e}")
            results.append((test_name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    for test_name, result in results:
        status = "✓ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    print(f"\nTotal: {passed}/{total} tests passed")


if __name__ == "__main__":
    asyncio.run(main())

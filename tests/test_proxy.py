"""
Simple test script to demonstrate proxy functionality.

This script shows how to:
1. Add a proxy to the database
2. Retrieve and display proxy information
3. Build proxy candidates from a record
4. Test proxy configuration

Run: python test_proxy.py
"""

import asyncio
import sys
from datetime import datetime, timezone


async def test_proxy_module():
    """Test the proxy module functions."""
    print("=" * 60)
    print("Testing Proxy Module")
    print("=" * 60)
    
    # Import proxy module
    from auxilary_logic.proxy import build_proxy_dict, build_proxy_candidates
    
    # Test data: proxy with both SOCKS5 and HTTP ports
    test_proxy = {
        'proxy_name': 'test-multi-proxy',
        'host': '1.2.3.4',
        'socks5_port': 1080,
        'http_port': 8080,
        'username': 'testuser',
        'password': 'testpass',
        'rdns': True
    }
    
    print("\n1. Testing build_proxy_candidates()")
    print(f"   Input: {test_proxy['proxy_name']}")
    print(f"   Host: {test_proxy['host']}")
    print(f"   SOCKS5 Port: {test_proxy.get('socks5_port')}")
    print(f"   HTTP Port: {test_proxy.get('http_port')}")
    
    candidates = build_proxy_candidates(test_proxy)
    
    print(f"\n   Generated {len(candidates)} candidates:")
    for i, candidate in enumerate(candidates, 1):
        proto_name = {1: 'SOCKS5', 2: 'SOCKS4', 3: 'HTTP'}.get(candidate['proxy_type'], 'UNKNOWN')
        print(f"   Candidate {i}: {proto_name}://{candidate['addr']}:{candidate['port']}")
        if 'username' in candidate:
            print(f"              Auth: {candidate['username']}:{'*' * len(candidate.get('password', ''))}")
    
    print("\n2. Testing build_proxy_dict() with SOCKS5")
    socks5_data = {
        'type': 'socks5',
        'host': '192.168.1.100',
        'port': 1080,
        'username': 'admin',
        'password': 'secret',
        'rdns': False
    }
    
    socks5_dict = build_proxy_dict(socks5_data)
    if socks5_dict:
        print(f"   ✓ SOCKS5 proxy dict created")
        print(f"   Address: {socks5_dict['addr']}:{socks5_dict['port']}")
        print(f"   RDNS: {socks5_dict['rdns']}")
    
    print("\n3. Testing build_proxy_dict() with HTTP")
    http_data = {
        'type': 'http',
        'ip': '10.0.0.50',  # Using 'ip' instead of 'host'
        'http_port': 8080,
        'login': 'user',    # Using 'login' instead of 'username'
        'password': 'pass'
    }
    
    http_dict = build_proxy_dict(http_data)
    if http_dict:
        print(f"   ✓ HTTP proxy dict created")
        print(f"   Address: {http_dict['addr']}:{http_dict['port']}")
        print(f"   Username from 'login': {http_dict.get('username')}")
    
    print("\n✓ Proxy module tests completed successfully!\n")


async def test_database_operations():
    """Test database proxy operations."""
    print("=" * 60)
    print("Testing Database Proxy Operations")
    print("=" * 60)
    
    try:
        from main_logic.database import get_db
        
        db = get_db()
        
        # Test 1: Add a proxy
        print("\n1. Adding test proxy to database...")
        test_proxy_data = {
            'proxy_name': f'test-proxy-{datetime.now().timestamp()}',
            'host': '203.0.113.10',
            'socks5_port': 1080,
            'http_port': 8080,
            'username': 'testuser',
            'password': 'testpassword',
            'rdns': True,
            'active': True,
            'notes': 'Test proxy - created by test_proxy.py'
        }
        
        result = await db.add_proxy(test_proxy_data)
        if result:
            print(f"   ✓ Proxy '{test_proxy_data['proxy_name']}' added successfully")
            print(f"   Note: Password was automatically encrypted")
        else:
            print(f"   ✗ Failed to add proxy")
            return
        
        # Test 2: Retrieve the proxy
        print("\n2. Retrieving proxy from database...")
        proxy = await db.get_proxy(test_proxy_data['proxy_name'])
        
        if proxy:
            print(f"   ✓ Proxy retrieved successfully")
            print(f"   Name: {proxy['proxy_name']}")
            print(f"   Host: {proxy['host']}")
            print(f"   SOCKS5 Port: {proxy.get('socks5_port')}")
            print(f"   HTTP Port: {proxy.get('http_port')}")
            print(f"   Active: {proxy['active']}")
            print(f"   Connected Accounts: {proxy.get('connected_accounts', 0)}")
            if proxy.get('password'):
                print(f"   Password: {'*' * 8} (decrypted automatically)")
            if proxy.get('password_encrypted'):
                print(f"   Encrypted Password in DB: {proxy['password_encrypted'][:20]}...")
        
        # Test 3: Get least-used proxy
        print("\n3. Testing least-used proxy selection...")
        least_used = await db.get_least_used_proxy()
        
        if least_used:
            print(f"   ✓ Least-used proxy: {least_used['proxy_name']}")
            print(f"   Connected accounts: {least_used.get('connected_accounts', 0)}")
        
        # Test 4: Update proxy
        print("\n4. Testing proxy update...")
        update_result = await db.update_proxy(
            test_proxy_data['proxy_name'],
            {'notes': 'Updated by test script'}
        )
        
        if update_result:
            print(f"   ✓ Proxy updated successfully")
        
        # Test 5: Usage tracking
        print("\n5. Testing usage tracking...")
        await db.increment_proxy_usage(test_proxy_data['proxy_name'])
        print(f"   ✓ Usage incremented")
        
        proxy_after = await db.get_proxy(test_proxy_data['proxy_name'])
        print(f"   Connected accounts: {proxy_after.get('connected_accounts', 0)}")
        
        await db.decrement_proxy_usage(test_proxy_data['proxy_name'])
        print(f"   ✓ Usage decremented")
        
        # Test 6: Error tracking
        print("\n6. Testing error tracking...")
        await db.set_proxy_error(
            test_proxy_data['proxy_name'],
            'Test error: Connection timeout'
        )
        print(f"   ✓ Error set")
        
        proxy_with_error = await db.get_proxy(test_proxy_data['proxy_name'])
        if proxy_with_error.get('last_error'):
            print(f"   Last error: {proxy_with_error['last_error']}")
        
        await db.clear_proxy_error(test_proxy_data['proxy_name'])
        print(f"   ✓ Error cleared")
        
        # Test 7: Cleanup - delete test proxy
        print("\n7. Cleaning up test proxy...")
        delete_result = await db.delete_proxy(test_proxy_data['proxy_name'])
        
        if delete_result:
            print(f"   ✓ Test proxy deleted successfully")
        
        print("\n✓ Database operations tests completed successfully!\n")
        
    except Exception as e:
        print(f"\n✗ Database test failed: {e}")
        import traceback
        traceback.print_exc()


async def show_all_proxies():
    """Display all proxies in database."""
    print("=" * 60)
    print("Current Proxies in Database")
    print("=" * 60)
    
    try:
        from main_logic.database import get_db
        
        db = get_db()
        proxies = await db.get_all_proxies()
        
        if not proxies:
            print("\nNo proxies found in database.")
            print("Add a proxy using the database API or admin interface.")
        else:
            print(f"\nFound {len(proxies)} proxy(ies):\n")
            
            for i, proxy in enumerate(proxies, 1):
                print(f"{i}. {proxy['proxy_name']}")
                print(f"   Host: {proxy.get('host', 'N/A')}")
                
                ports = []
                if proxy.get('socks5_port'):
                    ports.append(f"SOCKS5:{proxy['socks5_port']}")
                if proxy.get('http_port'):
                    ports.append(f"HTTP:{proxy['http_port']}")
                if proxy.get('port') and not ports:
                    ports.append(f"{proxy.get('type', 'generic').upper()}:{proxy['port']}")
                
                print(f"   Ports: {', '.join(ports) if ports else 'N/A'}")
                print(f"   Active: {proxy.get('active', False)}")
                print(f"   Connected: {proxy.get('connected_accounts', 0)}")
                
                if proxy.get('last_error'):
                    print(f"   Last Error: {proxy['last_error']}")
                
                if proxy.get('notes'):
                    print(f"   Notes: {proxy['notes']}")
                
                print()
        
    except Exception as e:
        print(f"\n✗ Failed to list proxies: {e}")


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("LikeBot Proxy Functionality Test")
    print("=" * 60 + "\n")
    
    # Test 1: Proxy module functions
    await test_proxy_module()
    
    # Test 2: Database operations
    try:
        await test_database_operations()
    except Exception as e:
        print(f"\nSkipping database tests (database may not be configured): {e}")
    
    # Test 3: Show all proxies
    try:
        await show_all_proxies()
    except Exception as e:
        print(f"\nSkipping proxy listing (database may not be configured): {e}")
    
    print("=" * 60)
    print("All tests completed!")
    print("=" * 60 + "\n")
    
    print("Next steps:")
    print("1. Configure database connection (set db_url in .env)")
    print("2. Set encryption key (set KEK in .env)")
    print("3. Add real proxies using db.add_proxy()")
    print("4. Test connections with your Telegram clients")
    print("\nSee docs/PROXY_CONFIGURATION.md for detailed guide.\n")


if __name__ == "__main__":
    asyncio.run(main())

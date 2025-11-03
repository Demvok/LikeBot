"""
LikeBot Initial Setup Script

This script helps set up JWT authentication for LikeBot API.
It generates necessary secrets and creates the first admin user.
"""

import asyncio
import os
import atexit
import time
from dotenv import load_dotenv, set_key
from encryption import generate_jwt_secret_key, hash_password
from datetime import datetime, timezone

async def setup_jwt_secret():
    """Generate and save JWT secret key to .env file."""
    print("\n=== JWT Secret Key Setup ===")
    
    # Check if JWT_SECRET_KEY already exists
    load_dotenv()
    existing_key = os.getenv("JWT_SECRET_KEY")
    
    if existing_key:
        print("✓ JWT_SECRET_KEY already exists in .env file")
        overwrite = input("Do you want to generate a new one? (y/N): ").strip().lower()
        if overwrite != 'y':
            return
    
    # Generate new secret key
    new_key = generate_jwt_secret_key()
    
    # Save to .env file
    env_path = ".env"
    if not os.path.exists(env_path):
        print("✗ .env file not found. Creating new one...")
        with open(env_path, 'w') as f:
            f.write(f"JWT_SECRET_KEY={new_key}\n")
        print(f"✓ Created .env file with JWT_SECRET_KEY")
    else:
        set_key(env_path, "JWT_SECRET_KEY", new_key)
        print(f"✓ JWT_SECRET_KEY updated in .env file")
    
    print(f"\nGenerated JWT Secret Key: {new_key}")
    print("⚠ Keep this secret secure and never commit it to version control!")


async def create_admin_user():
    """Create the first admin user."""
    print("\n=== Create Admin User ===")
    
    # Import here to ensure .env is loaded
    from database import get_db
    
    # Get admin credentials
    username = input("Enter admin username (3-50 chars, alphanumeric): ").strip()
    if len(username) < 3 or len(username) > 50:
        print("✗ Username must be 3-50 characters")
        return
    
    # Check if username already exists
    db = get_db()
    existing_user = await db.get_user(username)
    if existing_user:
        print(f"✗ User '{username}' already exists")
        update = input("Do you want to update this user to admin? (y/N): ").strip().lower()
        if update == 'y':
            await db.update_user(username, {
                "role": "admin",
                "is_verified": True,
                "updated_at": datetime.now(timezone.utc)
            })
            print(f"✓ User '{username}' updated to admin with verified status")
        return
    
    # Get password
    import getpass
    password = getpass.getpass("Enter admin password (min 6 chars): ")
    if len(password) < 6:
        print("✗ Password must be at least 6 characters")
        return
    
    # Check if password is too long for bcrypt (72 bytes limit)
    password_bytes = password.encode('utf-8')
    if len(password_bytes) > 72:
        print(f"⚠ Warning: Password is {len(password_bytes)} bytes, which exceeds bcrypt's 72-byte limit.")
        print("  Password will be truncated to 72 bytes for hashing.")
        truncate = input("Continue with truncated password? (y/N): ").strip().lower()
        if truncate != 'y':
            print("✗ Password setup cancelled")
            return
    
    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("✗ Passwords do not match")
        return
    
    # Hash password
    password_hash = hash_password(password)
    
    # Create admin user
    user_data = {
        "username": username.lower(),
        "password_hash": password_hash,
        "role": "admin",
        "is_verified": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc)
    }
    
    success = await db.create_user(user_data)
    
    if success:
        print(f"\n✓ Admin user '{username}' created successfully!")
        print(f"  - Username: {username}")
        print(f"  - Role: admin")
        print(f"  - Verified: True")
        print(f"\nYou can now login at POST /api/v1/auth/login")
    else:
        print(f"✗ Failed to create admin user")


async def verify_setup():
    """Verify that the setup is complete."""
    print("\n=== Verifying Setup ===")
    
    load_dotenv()
    
    # Check JWT secret
    jwt_secret = os.getenv("JWT_SECRET_KEY")
    if jwt_secret:
        print("✓ JWT_SECRET_KEY is set")
    else:
        print("✗ JWT_SECRET_KEY is not set")
        return False
    
    # Check encryption key
    kek = os.getenv("KEK")
    if kek:
        print("✓ KEK (encryption key) is set")
    else:
        print("✗ KEK (encryption key) is not set")
        print("  Run: python -c \"from encryption import generate_master_key_base64; print('KEK=' + generate_master_key_base64())\"")
    
    # Check database connection
    db_url = os.getenv("db_url")
    if db_url:
        print("✓ db_url is set")
    else:
        print("✗ db_url is not set")
        return False
    
    # Check if admin user exists
    from database import get_db
    db = get_db()
    
    # Try to find any admin user
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(db_url)
        db_name = os.getenv("db_name", "LikeBot")
        users_coll = client[db_name]["users"]
        
        admin_users = await users_coll.count_documents({"role": "admin", "is_verified": True})
        if admin_users > 0:
            print(f"✓ Found {admin_users} verified admin user(s)")
        else:
            print("✗ No verified admin users found")
            print("  Please create an admin user using option 2")
            return False
    except Exception as e:
        print(f"✗ Could not connect to database: {e}")
        return False
    
    print("\n✓ Setup verification complete!")
    return True


async def main():
    """Main setup menu."""
    print("=" * 50)
    print("LikeBot Authentication Setup")
    print("=" * 50)
    
    while True:
        print("\nSelect an option:")
        print("1. Generate/Update JWT Secret Key")
        print("2. Create Admin User")
        print("3. Verify Setup")
        print("4. Complete Setup (All steps)")
        print("5. Exit")
        
        choice = input("\nEnter choice (1-5): ").strip()
        
        if choice == "1":
            await setup_jwt_secret()
        elif choice == "2":
            await create_admin_user()
        elif choice == "3":
            await verify_setup()
        elif choice == "4":
            print("\n=== Running Complete Setup ===")
            await setup_jwt_secret()
            await create_admin_user()
            await verify_setup()
            print("\n✓ Complete setup finished!")
            break
        elif choice == "5":
            print("\nExiting setup...")
            break
        else:
            print("Invalid choice. Please select 1-5.")


def cleanup():
    """Cleanup resources before exit."""
    try:
        from logger import cleanup_logging
        cleanup_logging()
        # Give the logging queue listener time to stop gracefully
        time.sleep(0.1)
    except Exception:
        pass


if __name__ == "__main__":
    # Register cleanup handler
    atexit.register(cleanup)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nSetup interrupted by user.")
    except Exception as e:
        print(f"\n✗ Error during setup: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Explicitly call cleanup to ensure it runs
        cleanup()

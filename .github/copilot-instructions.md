# LikeBot AI Agent Instructions

## Project Overview
LikeBot is a Telegram automation bot built with FastAPI and Telethon. It manages multiple Telegram accounts to perform reactions and comments on posts through scheduled tasks. The system uses MongoDB for persistence, with encrypted credentials and a WebSocket-based real-time log viewer.

## Architecture

### Core Components
- **main.py**: FastAPI application with REST/WebSocket endpoints for CRUD operations and task execution
- **main_logic/**: Domain models (Agent, Task, Post, Channel, Database)
- **auxilary_logic/**: Support services (authentication, encryption, login flow, proxies, reporting, error handling)
- **utils/**: Cross-cutting concerns (logging, environment setup, WebSocket log viewer)

### Data Flow
1. **Tasks** reference **Posts** (by post_id) and **Accounts** (by phone_number)
2. Tasks execute via worker pattern: each Account gets a Client that processes Posts concurrently
3. **Reporter** batches events to MongoDB (`runs` and `events` collections) for async task reporting
4. **Database** (MongoStorage singleton via `get_db()`) handles all persistence with Motor async client

## Critical Patterns

### Async-First Design
- All database operations use Motor (async MongoDB driver)
- Use `@ensure_async` decorator (database.py) to wrap sync helper functions for async execution
- Never block the event loop; run blocking ops with `asyncio.to_thread()`

### Error Handling
- **@crash_handler** decorator (utils/logger.py) wraps all API endpoints for centralized exception logging
- **map_telethon_exception()** (auxilary_logic/telethon_error_handler.py) converts Telethon errors to account status updates
- Prefer specific AccountStatus states (AUTH_KEY_INVALID, BANNED, RESTRICTED) over generic ERROR

### Security & Encryption
- **KEK** (Key Encryption Key) in env encrypts all secrets via HKDF+AES-GCM (auxilary_logic/encryption.py)
- Use purpose-specific constants: `PURPOSE_STRING_SESSION`, `PURPOSE_PASSWORD`, `PURPOSE_PROXY_PASSWORD`
- JWT authentication with bcrypt password hashing (max 72 bytes; truncate longer passwords)
- **Never** store plaintext passwords or sessions; always use `encrypt_secret()` before DB storage

### Database Conventions
- **Singleton pattern**: `get_db()` returns initialized MongoStorage class instance
- **Lazy initialization**: `_ensure_ready()` creates indexes on first access
- **ID normalization**: Chat IDs stored as signed int64; use `normalize_chat_id()` from main_logic/channel.py
- **Counter pattern**: Atomic ID generation via `counters` collection (see `_get_next_id()`)
- **Serialization**: Remove MongoDB `_id` before returning; use `serialize_for_json()` from schemas.py for complex types

### Rate Limiting & Humanization
- **Global TelegramAPIRateLimiter** (auxilary_logic/humaniser.py) enforces method-specific delays:
  - get_entity: 500ms, send_reaction: 500ms, get_messages: 300ms
- **Entity caching**: Client.get_entity_cached() uses LRU cache (100 entries, 5min TTL) to reduce API calls by 80-90%
- **Reading time estimation**: Use `estimate_reading_time()` for humanization_level=1+ (config.yaml)
- **Worker stagger**: Random delays (2-10s) prevent simultaneous account activity spikes

### Login Flow Architecture
- **Multi-step async process**: `/accounts/create/start` → background task → `/accounts/create/verify` → `/accounts/create/status` polling
- **LoginProcess** stores Futures for code/2FA in `pending_logins` dict (global state in auxilary_logic/login.py)
- **Session persistence**: Encrypted StringSession saved to DB after successful auth
- **Cleanup**: 10-minute expiration with automatic cleanup of expired sessions

### Task Execution Model
- **Status transitions**: PENDING → RUNNING → (PAUSED/FINISHED/CRASHED)
- **Worker pattern**: `client_worker()` per account processes all posts with pause event support
- **Failure policy**: Single worker failure doesn't crash task; all workers failing → CRASHED status
- **Reporter integration**: `run_id` tracks execution; events batched (100/batch or 0.5s timeout) to DB

## Key Development Workflows

### Running Tests
```powershell
# Run all tests
pytest tests/

# Run specific test with verbose output
pytest tests/test_database_counters.py -v

# Run with coverage
pytest --cov=main_logic --cov=auxilary_logic tests/
```

### Starting Development Server
```powershell
# Activate venv
.\.venv\Scripts\Activate.ps1

# Run with auto-reload (reads backend_ip/backend_port from .env)
python main.py

# Or with uvicorn directly
uvicorn main:app --host 127.0.0.1 --port 8080 --reload
```

### Environment Setup
```powershell
# Generate KEK, JWT_SECRET_KEY, create admin user
python utils/setup_env.py
```

### Database Operations
```python
# Always get singleton instance
db = get_db()
await db._ensure_ready()  # Call once per application lifecycle

# CRUD pattern (consistent across all collections)
account = await db.get_account(phone_number)
await db.add_account(account_dict)
await db.update_account(phone_number, {"status": "ACTIVE"})
await db.delete_account(phone_number)
```

### Adding New Endpoints
1. Add Pydantic schemas to `main_logic/schemas.py` (centralized data models)
2. Decorate with `@crash_handler` for error handling
3. Use dependency injection: `current_user: dict = Depends(get_current_user)`
4. Admin-only: `Depends(get_current_admin_user)`
5. Return serializable data: use `serialize_for_json()` for numpy/pandas types

### Encryption Workflow
```python
from auxilary_logic.encryption import encrypt_secret, decrypt_secret, PURPOSE_PASSWORD

# Encrypt before DB storage
encrypted = encrypt_secret(plaintext_password, PURPOSE_PASSWORD)
await db.update_account(phone, {"password_encrypted": encrypted})

# Decrypt for use
plaintext = decrypt_secret(encrypted, PURPOSE_PASSWORD)
```

## File Organization Principles
- **Centralized schemas**: All Pydantic models and Enums live in `main_logic/schemas.py`
- **Domain separation**: `main_logic/` = core business logic; `auxilary_logic/` = cross-cutting support
- **Config-driven**: All delays, paths, DB settings in `config.yaml` (loaded via `load_config()`)
- **Logging structure**: Per-module loggers via `setup_logger(name, "main.log")`; account logs go to `logs/accounts/{phone}.log`

## Common Pitfalls
- ❌ Don't call `client.get_entity()` directly → Use `client.get_entity_cached()`
- ❌ Don't store status as string in memory → Use Enum (AccountStatus, TaskStatus) then convert to string for DB
- ❌ Don't parse message URLs manually → Use `Post.parse_link()` or `Client.get_message_ids()`
- ❌ Don't forget to normalize chat IDs → Always use `normalize_chat_id()` for signed int64 consistency
- ❌ Don't block event loop → Wrap CPU-bound/blocking calls with `asyncio.to_thread()`

## Testing Patterns
- Use mocks for external dependencies (Telethon client, database)
- Async test structure: `async def test_name(monkeypatch)` with `asyncio.run()`
- Mock database via `monkeypatch.setattr("main_logic.database.get_db", lambda: MockDB())`
- Test failure cases explicitly (e.g., `test_task_worker_failure_policy.py` validates crash handling)

## Documentation Standards
- API changes → Update `docs/API_Documentation.md`
- Schema changes → Update **all 7 locations** listed in `main_logic/schemas.py` docstring
- New features → Add to `README.md` changelog with version bump
- Complex flows → Dedicated docs in `docs/` (e.g., LOGIN_PROCESS_DOCUMENTATION.md)

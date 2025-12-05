# Database Validation and Broken Object Handling

## Overview
LikeBot now includes robust handling for malformed database objects to prevent application crashes when encountering corrupted or incomplete records.

## Features Implemented

### 1. Defensive Object Loading
All database loading methods now include error handling to skip malformed objects:

- **`load_all_tasks()`**: Skips tasks with missing required fields (e.g., `post_ids=None`)
- **`load_all_accounts()`**: Skips accounts with initialization errors
- **`load_all_posts()`**: Skips posts with missing required fields

### 2. Improved Task Initialization
The `Task` class now handles `None` values defensively:
```python
# Old behavior - crashes with TypeError
self.post_ids = sorted(post_ids)  # Fails if post_ids is None

# New behavior - uses empty list as fallback
self.post_ids = sorted(post_ids) if post_ids is not None else []
```

### 3. Database Validation Utility

#### Method: `validate_and_report_broken_objects()`
Scans all collections (tasks, accounts, posts) for malformed objects and returns a detailed report.

**Returns:**
```python
{
    'tasks': {
        'total': 42,
        'broken': 1,
        'details': [
            {
                'task_id': 123,
                'name': 'Broken Task',
                'error': "'NoneType' object is not iterable",
                'error_type': 'TypeError',
                'record': {...}  # Full record data
            }
        ]
    },
    'accounts': {...},
    'posts': {...}
}
```

**Usage:**
```python
from main_logic.database import get_db

db = get_db()
report = await db.validate_and_report_broken_objects()

# Check for issues
if report['tasks']['broken'] > 0:
    print(f"Found {report['tasks']['broken']} broken tasks")
```

#### Method: `delete_broken_objects(collection, identifiers)`
Permanently deletes broken objects from a collection.

**⚠️ WARNING**: This action cannot be undone! Use with caution.

**Usage:**
```python
# Delete specific broken tasks
await db.delete_broken_objects('tasks', [123, 456, 789])

# Delete broken accounts
await db.delete_broken_objects('accounts', ['+1234567890', '+9876543210'])
```

### 4. Validation Script

#### `utils/validate_database.py`
Interactive CLI tool to scan and optionally clean up broken objects.

**Basic Usage:**
```powershell
# Scan only (no modifications)
python utils/validate_database.py
```

**Output Example:**
```
================================================================================
DATABASE VALIDATION REPORT
================================================================================

Total objects scanned: 150
Total malformed objects: 2

TASKS:
  Total: 42
  Broken: 1

  Broken tasks details:

  [1] Error: TypeError: 'NoneType' object is not iterable
      task_id: 123, name: Test Task
      Record: {
        "task_id": 123,
        "name": "Test Task",
        "post_ids": null,  # ← PROBLEM
        "accounts": ["user1"],
        ...
      }

ACCOUNTS:
  Total: 50
  Broken: 0

POSTS:
  Total: 58
  Broken: 1

  Broken posts details:

  [1] Error: TypeError: __init__() missing required argument: 'message_link'
      post_id: 456, message_link: None
      Record: {...}

================================================================================
```

**Interactive Deletion:**
```powershell
# Prompt for deletion of broken objects
python utils/validate_database.py --delete-broken
```

**Deletion Workflow:**
```
TASKS: 1 broken objects
Delete all broken tasks? (yes/no): yes
✓ Deleted 1 broken tasks

POSTS: 1 broken objects
Delete all broken posts? (yes/no): no
Skipped deleting posts
```

## Logging Behavior

### When Loading Objects
If a malformed object is encountered during normal operations:

```
[ERROR] Skipping malformed task record (task_id=123, name=Test Task): 'NoneType' object is not iterable. Record data: {...}
[WARNING] Loaded 41 tasks from MongoDB, skipped 1 malformed records.
```

### API Response
The API will continue to work and return valid objects:
```json
{
  "tasks": [
    // Only valid tasks, broken ones automatically skipped
  ]
}
```

## Common Broken Object Patterns

### Tasks with `None` values:
```python
# Missing required fields
{
    "task_id": 123,
    "name": "Broken Task",
    "post_ids": null,      # ← Should be array
    "accounts": null,      # ← Should be array
    "action": null         # ← Should be object
}
```

### Posts without message_link:
```python
{
    "post_id": 456,
    "message_link": null,  # ← Required field
    "chat_id": 123
}
```

### Accounts with invalid data:
```python
{
    "phone_number": null,  # ← Required field
    "account_id": 789
}
```

## Fixing Broken Objects

### Option 1: Manual Fix in MongoDB
Connect to MongoDB and update the record:
```javascript
db.tasks.updateOne(
  { task_id: 123 },
  { $set: { post_ids: [], accounts: [] } }
)
```

### Option 2: Delete and Recreate
1. Run validation script with `--delete-broken`
2. Recreate the object through the API

### Option 3: Database Migration
Create a migration script to fix all broken objects:
```python
from main_logic.database import get_db

async def fix_broken_tasks():
    db = get_db()
    
    # Fix tasks with null post_ids
    await db._tasks.update_many(
        {"post_ids": None},
        {"$set": {"post_ids": []}}
    )
    
    # Fix tasks with null accounts
    await db._tasks.update_many(
        {"accounts": None},
        {"$set": {"accounts": []}}
    )
```

## Prevention

### Best Practices
1. **Always use API endpoints** to create objects (they validate required fields)
2. **Never manually edit MongoDB** without validation
3. **Run validation script** after database migrations or manual changes
4. **Check logs regularly** for skipped object warnings

### API Validation
All creation endpoints validate required fields:
```python
# POST /tasks - validates post_ids and accounts are arrays
# POST /accounts - validates phone_number is present
# POST /posts - validates message_link is present
```

## Monitoring

### Log Files to Monitor
- `logs/main.log` - Contains warnings about skipped objects
- `logs/crashes/` - Should NOT contain crashes from malformed objects anymore

### Metrics
Check periodically:
```python
report = await db.validate_and_report_broken_objects()

# Alert if broken objects found
if report['tasks']['broken'] > 0:
    send_alert(f"Found {report['tasks']['broken']} broken tasks!")
```

## Troubleshooting

### "Task failed to load: 'NoneType' object is not iterable"
**Before fix:** Application crashes when loading tasks.
**After fix:** Task is logged and skipped, application continues.

### "No tasks displayed in frontend"
If all tasks are broken, the list will be empty. Run validation script to identify issues.

### "Deleted broken objects but they reappear"
Check if application code is recreating broken objects. Review creation logic and add validation.

## Future Enhancements

Potential improvements:
1. **Auto-repair**: Automatically fix common issues (e.g., `null` → `[]`)
2. **Admin API endpoint**: Web UI for validation and cleanup
3. **Scheduled validation**: Cron job to check database health
4. **Metrics dashboard**: Track broken object counts over time

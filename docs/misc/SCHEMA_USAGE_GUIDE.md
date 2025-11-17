# Schema Usage Guide

This document provides a comprehensive guide on how the centralized schemas in `schemas.py` are used throughout the LikeBot project and how to propagate changes.

## Overview

The `schemas.py` file centralizes all data structures used in the project using Pydantic models for validation, type hints, and serialization. This ensures consistency across all components.

## Schema Definitions

### Core Schemas
- **AccountStatus**: Enum for account states (ACTIVE, LOGGED_IN, NEW, BANNED, ERROR)
- **TaskStatus**: Enum for task execution states (PENDING, RUNNING, PAUSED, FINISHED, CRASHED)
- **Account Schemas**: AccountBase, AccountCreate, AccountUpdate, AccountResponse, AccountDict
- **Post Schemas**: PostBase, PostCreate, PostUpdate, PostResponse, PostDict
- **Task Schemas**: TaskBase, TaskCreate, TaskUpdate, TaskResponse, TaskDict
- **Action Schemas**: ReactAction, CommentAction, UndoReactionAction, UndoCommentAction
- **Reporter Schemas**: RunCreate, RunResponse, EventCreate, EventResponse
- **API Schemas**: SuccessResponse, ErrorResponse, BulkOperationResult, DatabaseStats, ValidationResult

## Usage Locations and Update Requirements

### 1. Account Schemas

#### Files to update when modifying Account schemas:
- `agent.py`:
  - `Account.__init__()` - Constructor parameters
  - `Account.to_dict()` - Serialization method
  - `Account.from_keys()` - Factory method
  - `Account.AccountStatus` - Now references schemas.AccountStatus

- `database.py`:
  - `*Storage.add_account()` - Account creation
  - `*Storage.get_account()` - Account retrieval
  - `*Storage.update_account()` - Account updates
  - `*Storage.load_all_accounts()` - Bulk loading

- `main.py`:
  - API endpoint type hints and validation
  - Now imports AccountCreate, AccountUpdate from schemas

- `API_Documentation.md`:
  - Account model documentation
  - Request/response examples

#### Update checklist for Account schemas:
1. Modify schemas in `schemas.py`
2. Update `Account.__init__()` to match new fields
3. Update `Account.to_dict()` for serialization
4. Update database storage methods
5. Test API endpoints
6. Update documentation

### 2. Post Schemas

#### Files to update when modifying Post schemas:
- `taskhandler.py`:
  - `Post.__init__()` - Constructor parameters
  - `Post.to_dict()` - Serialization method
  - `Post.from_keys()` - Factory method
  - `Post.validate()` - Validation logic

- `database.py`:
  - `*Storage.add_post()` - Post creation
  - `*Storage.get_post()` - Post retrieval
  - `*Storage.update_post()` - Post updates
  - `*Storage.load_all_posts()` - Bulk loading

- `main.py`:
  - API endpoint type hints and validation
  - Now imports PostCreate, PostUpdate from schemas

- `API_Documentation.md`:
  - Post model documentation
  - Request/response examples

#### Update checklist for Post schemas:
1. Modify schemas in `schemas.py`
2. Update `Post.__init__()` to match new fields
3. Update `Post.to_dict()` for serialization
4. Update validation logic if needed
5. Update database storage methods
6. Test API endpoints
7. Update documentation

### 3. Task Schemas

#### Files to update when modifying Task schemas:
- `taskhandler.py`:
  - `Task.__init__()` - Constructor parameters
  - `Task.to_dict()` - Serialization method
  - `Task.TaskStatus` - Now references schemas.TaskStatus
  - Action-related methods (get_action_type, get_reaction_palette_name, etc.)

- `database.py`:
  - `*Storage.add_task()` - Task creation
  - `*Storage.get_task()` - Task retrieval
  - `*Storage.update_task()` - Task updates
  - `*Storage.load_all_tasks()` - Bulk loading

- `main.py`:
  - API endpoint type hints and validation
  - Now imports TaskCreate, TaskUpdate from schemas

- `API_Documentation.md`:
  - Task model documentation
  - Action types documentation
  - Request/response examples

#### Update checklist for Task schemas:
1. Modify schemas in `schemas.py`
2. Update `Task.__init__()` to match new fields
3. Update `Task.to_dict()` for serialization
4. Update action-related methods
5. Update database storage methods
6. Test API endpoints and task execution
7. Update documentation

### 4. Action Schemas

#### Files to update when modifying Action schemas:
- `taskhandler.py`:
  - `Task.get_action()` - Action retrieval
  - `Task.get_action_type()` - Action type detection
  - `Task.get_reaction_palette_name()` - Palette extraction
  - `Task.get_reaction_emojis()` - Emoji resolution

- `agent.py`:
  - Client action methods (_react, _comment, etc.)
  - Action execution logic

- `config.yaml`:
  - Reaction palettes configuration

- `API_Documentation.md`:
  - Action types section
  - Available emoji palettes

#### Update checklist for Action schemas:
1. Modify action schemas in `schemas.py`
2. Update action processing methods in Task class
3. Update action execution in Client class
4. Update configuration if new palettes added
5. Test action execution
6. Update documentation

### 5. Reporter Schemas

#### Files to update when modifying Reporter schemas:
- `reporter.py`:
  - `Reporter.new_run()` - Run creation
  - `Reporter.event()` - Event creation
  - `RunEventManager` methods
  - Now imports EventLevel from schemas

- `taskhandler.py`:
  - Task execution reporting
  - Event generation during task runs

#### Update checklist for Reporter schemas:
1. Modify schemas in `schemas.py`
2. Update Reporter methods
3. Update task execution reporting
4. Test event collection and reporting
5. Update any reporting dashboards

## How to Propagate Schema Changes

### 1. Identify Impact
Use the `SchemaMigration` class in `schemas.py`:

```python
from schemas import SchemaMigration

# Get locations that need updates for a specific schema
locations = SchemaMigration.get_locations_for_schema("Account")
print("Update these locations:", locations)

# Print complete migration guide
SchemaMigration.print_migration_guide()
```

### 2. Update Process
1. **Modify schema** in `schemas.py`
2. **Run the migration helper** to see affected locations
3. **Update each location** systematically:
   - Start with core classes (Account, Post, Task)
   - Then update database layer
   - Then update API layer
   - Finally update documentation
4. **Test thoroughly** at each step
5. **Update documentation** last

### 3. Testing Strategy
1. **Unit tests** for individual classes
2. **Integration tests** for database operations
3. **API tests** for endpoint validation
4. **End-to-end tests** for complete workflows

### 4. Backward Compatibility
When making breaking changes:
1. Consider adding migration scripts for existing data
2. Provide deprecation warnings for old field names
3. Support both old and new formats during transition
4. Document migration steps for users

## Helper Functions

### Validation
- `validate_phone_number()` - Ensures phone number format
- `validate_telegram_link()` - Ensures Telegram link format

### Serialization
- `serialize_for_json()` - Handles complex object serialization
- Pydantic model `Config` classes - Automatic enum/datetime serialization

## Best Practices

1. **Always use the centralized schemas** instead of creating local models
2. **Import schemas explicitly** rather than using wildcard imports
3. **Update all usage locations** when modifying schemas
4. **Test thoroughly** after schema changes
5. **Document changes** in API documentation
6. **Consider backward compatibility** for breaking changes
7. **Use the migration helper** to identify impact

## Common Patterns

### Adding a New Field
1. Add field to base schema in `schemas.py`
2. Add field to corresponding class `__init__()` method
3. Add field to `to_dict()` serialization method
4. Update database storage methods
5. Update API documentation
6. Add validation if needed

### Changing Field Types
1. Update type in schema
2. Add validation if needed
3. Update all usage locations
4. Consider migration for existing data
5. Test thoroughly

### Adding New Action Types
1. Create new action schema inheriting from base
2. Add to TaskAction union type
3. Update action processing methods
4. Update client execution methods
5. Update documentation

## Monitoring Schema Usage

To find all schema usage in the codebase:

```bash
# Find Account usage
grep -r "Account" --include="*.py" .

# Find Post usage  
grep -r "Post" --include="*.py" .

# Find Task usage
grep -r "Task" --include="*.py" .

# Find Action usage
grep -r "Action" --include="*.py" .
```

This centralized approach ensures data consistency and makes the codebase more maintainable. Always refer to this guide when making schema changes.
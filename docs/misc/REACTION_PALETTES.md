# Reaction Palettes Documentation

## Overview

Reaction palettes are now stored in MongoDB instead of the YAML configuration file. This provides better flexibility, versioning, and management of emoji reaction sets used by the bot.

## Schema

Each reaction palette has the following structure:

```python
{
    "palette_name": str,        # Unique identifier (lowercase, alphanumeric + underscores/hyphens)
    "emojis": List[str],        # List of emoji reactions (at least 1 required)
    "ordered": bool,            # If True, emojis are used in sequence; if False, chosen randomly
    "description": str,         # Optional description of the palette
    "created_at": datetime,     # Timestamp when palette was created
    "updated_at": datetime      # Timestamp when palette was last updated
}
```

## Migration from config.yaml

### Automatic Migration

Run the migration script to automatically populate MongoDB with palettes from `config.yaml`:

```bash
# Interactive mode
python migrate_palettes.py

# Auto mode (non-interactive)
python migrate_palettes.py --auto
```

### Manual Migration

You can also ensure default palettes exist programmatically:

```python
from database import get_db

db = get_db()
await db.ensure_default_palettes()
```

This will read palettes from `config.yaml` and create them in MongoDB if they don't already exist.

## Database Operations

### Get a Palette

```python
from database import get_db

db = get_db()
palette = await db.get_palette("positive")

if palette:
    emojis = palette['emojis']
    ordered = palette['ordered']
```

### Get All Palettes

```python
from database import get_db

db = get_db()
palettes = await db.get_all_palettes()

for palette in palettes:
    print(f"{palette['palette_name']}: {palette['emojis']}")
```

### Add a Custom Palette

```python
from database import get_db
from datetime import datetime, timezone

db = get_db()

palette_data = {
    'palette_name': 'my_custom_palette',
    'emojis': ['😊', '😁', '🎉', '🌟'],
    'ordered': False,
    'description': 'My custom positive reactions',
    'created_at': datetime.now(timezone.utc),
    'updated_at': datetime.now(timezone.utc)
}

success = await db.add_palette(palette_data)
```

### Update a Palette

```python
from database import get_db

db = get_db()

update_data = {
    'emojis': ['👍', '❤️', '🔥', '✨'],
    'description': 'Updated positive reactions'
}

success = await db.update_palette('positive', update_data)
```

### Delete a Palette

```python
from database import get_db

db = get_db()
success = await db.delete_palette('my_custom_palette')
```

## Using Palettes in Tasks

When creating a task with a reaction action, specify the palette name:

```python
from schemas import ReactAction, ReactionPalette

action = ReactAction(
    type="react",
    palette=ReactionPalette.POSITIVE  # or "positive" as string
)

task = TaskCreate(
    name="My Task",
    post_ids=[123, 456],
    accounts=["+1234567890"],
    action=action
)
```

## How It Works

### Task Execution Flow

1. **Task Creation**: Specify which palette to use in the task's action configuration
2. **Worker Initialization**: When a worker starts, it fetches the palette from MongoDB
3. **Emoji Selection**: 
   - If `ordered=True`: Emojis are used sequentially from the list
   - If `ordered=False`: An emoji is randomly chosen from the list
4. **Fallback**: If database fetch fails, the system falls back to `config.yaml`

### Code Flow

```python
# In taskhandler.py
async def get_reaction_emojis(self):
    palette = self.get_reaction_palette_name()  # e.g., "positive"
    
    # Try database first
    db = get_db()
    palette_data = await db.get_palette(palette)
    if palette_data:
        return palette_data.get('emojis', [])
    
    # Fallback to config.yaml
    return config.get('reactions_palettes', {}).get(palette, [])

# In client_worker
client.active_emoji_palette = await self.get_reaction_emojis()
```

## Ordered vs Random Selection

### Random Selection (`ordered=False`)

Default behavior. Emojis are chosen randomly from the palette for each reaction:

```python
{
    "palette_name": "positive",
    "emojis": ["👍", "❤️", "🔥"],
    "ordered": False
}
# Result: Random emoji each time (👍, 🔥, ❤️, 👍, ...)
```

### Sequential Selection (`ordered=True`)

Emojis are used in the order they appear in the list:

```python
{
    "palette_name": "numbered",
    "emojis": ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"],
    "ordered": True
}
# Result: Sequential emojis (1️⃣, 2️⃣, 3️⃣, 4️⃣, 5️⃣, repeats...)
```

**Note**: Sequential selection is not yet fully implemented. Currently, all palettes use random selection via `random.choice()` in `agent.py`.

## Default Palettes

The system comes with two default palettes from `config.yaml`:

### Positive Palette
```yaml
positive:
  - "👍"
  - "❤️"
  - "🔥"
```

### Negative Palette
```yaml
negative:
  - "👎"
  - "😡"
  - "🤬"
  - "🤮"
  - "💩"
  - "🤡"
```

## Backwards Compatibility

The system maintains backwards compatibility with `config.yaml`:

1. **If MongoDB is unavailable**: Falls back to reading from `config.yaml`
2. **If palette not found in DB**: Falls back to reading from `config.yaml`
3. **Client initialization**: Uses default "positive" palette from config during client creation

This ensures the bot continues to function even if the database is temporarily unavailable.

## Best Practices

1. **Always migrate after deployment**: Run `migrate_palettes.py --auto` after deploying to ensure default palettes exist
2. **Use descriptive names**: Choose clear, descriptive palette names (e.g., `enthusiastic`, `supportive`, `critical`)
3. **Test palettes**: Verify emojis render correctly on your platform before adding them
4. **Document custom palettes**: Add meaningful descriptions to custom palettes
5. **Avoid duplicates**: Check if a palette exists before creating a new one
6. **Use validation**: The schema automatically validates emoji lists (non-empty, no blank strings)

## API Endpoints

If using the web API, you can manage palettes via HTTP endpoints (to be implemented in `main.py`):

```http
# Get all palettes
GET /palettes/

# Get specific palette
GET /palettes/{palette_name}

# Create palette
POST /palettes/
{
    "palette_name": "custom",
    "emojis": ["😊", "😁"],
    "ordered": false,
    "description": "Custom palette"
}

# Update palette
PUT /palettes/{palette_name}
{
    "emojis": ["😊", "😁", "🎉"],
    "description": "Updated custom palette"
}

# Delete palette
DELETE /palettes/{palette_name}
```

## Troubleshooting

### Palette not loading

1. Check MongoDB connection: Ensure database is accessible
2. Verify palette exists: Use `migrate_palettes.py` to list all palettes
3. Check logs: Look for errors in the task execution logs
4. Fallback working: Ensure `config.yaml` has the palette definition

### Migration issues

1. **Duplicates**: Migration script skips existing palettes (won't overwrite)
2. **Connection errors**: Check MongoDB connection string in `.env`
3. **Permission errors**: Ensure database user has write permissions

### Empty emoji lists

Palettes must have at least one emoji. The schema validation will reject empty lists.

## Future Enhancements

Potential improvements for the palette system:

1. **Emoji sequences**: Support for ordered emoji selection (implement counter in Client)
2. **Weighted selection**: Different probabilities for each emoji
3. **Context-aware palettes**: Select palettes based on content analysis
4. **Palette categories**: Group palettes by sentiment, topic, or use case
5. **Usage analytics**: Track which emojis are most effective
6. **Import/Export**: Bulk import palettes from JSON/CSV files

## Related Files

- `schemas.py`: Pydantic models for palette validation
- `database.py`: Database CRUD operations for palettes
- `taskhandler.py`: Palette selection logic during task execution
- `agent.py`: Client initialization with default palette
- `migrate_palettes.py`: Migration and management tool
- `config.yaml`: Legacy palette storage (fallback)

## Examples

### Creating a Sequential Voting Palette

```python
from database import get_db
from datetime import datetime, timezone

db = get_db()

voting_palette = {
    'palette_name': 'voting',
    'emojis': ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'],
    'ordered': True,
    'description': 'Numbered voting reactions (1-10)',
    'created_at': datetime.now(timezone.utc),
    'updated_at': datetime.now(timezone.utc)
}

await db.add_palette(voting_palette)
```

### Creating a Themed Palette

```python
from database import get_db
from datetime import datetime, timezone

db = get_db()

holiday_palette = {
    'palette_name': 'winter_holiday',
    'emojis': ['🎄', '❄️', '⛄', '🎁', '🎅', '🤶', '⛷️', '🏂'],
    'ordered': False,
    'description': 'Winter holiday themed reactions',
    'created_at': datetime.now(timezone.utc),
    'updated_at': datetime.now(timezone.utc)
}

await db.add_palette(holiday_palette)
```

### Bulk Creating Palettes

```python
from database import get_db
from datetime import datetime, timezone

db = get_db()

palettes = [
    {
        'palette_name': 'celebration',
        'emojis': ['🎉', '🎊', '🥳', '🎈', '🎆', '🎇'],
        'ordered': False,
        'description': 'Celebration reactions'
    },
    {
        'palette_name': 'thinking',
        'emojis': ['🤔', '💭', '🧐', '🤨'],
        'ordered': False,
        'description': 'Thoughtful reactions'
    },
    {
        'palette_name': 'love',
        'emojis': ['❤️', '💕', '💖', '💗', '💓', '💝'],
        'ordered': False,
        'description': 'Love and affection reactions'
    }
]

for palette_data in palettes:
    palette_data['created_at'] = datetime.now(timezone.utc)
    palette_data['updated_at'] = datetime.now(timezone.utc)
    await db.add_palette(palette_data)
```

---

**Last Updated**: November 3, 2025  
**Version**: 1.0

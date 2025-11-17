# Log Management Implementation Summary

## Overview
The logging system now includes automatic log rotation and crash report management to prevent excessive disk space usage.

## Features Implemented

### 1. Automatic Log File Rotation
- **Technology**: Uses `RotatingFileHandler` from Python's logging module
- **How it works**: 
  - When a log file exceeds the configured size (default: 10 MB), it's automatically rotated
  - The current log file is renamed to `.log.1`
  - Older backups are renamed incrementally (`.log.2`, `.log.3`, etc.)
  - Backups exceeding the configured count (default: 3) are automatically deleted
- **Benefits**: 
  - Prevents individual log files from growing indefinitely
  - Maintains a history of recent logs while managing disk space
  - Works automatically without manual intervention

### 2. Crash Report Limit
- **Functionality**: Limits the total number of crash reports stored
- **How it works**:
  - Before writing a new crash report, checks the total count
  - If the count meets or exceeds the limit (default: 15), deletes the oldest reports first
  - Maintains the most recent crash reports for debugging
- **Benefits**:
  - Prevents crash folder from accumulating indefinitely
  - Always keeps the most recent crash information
  - Automatically manages disk space

### 3. Per-Logger File Routing
- **Functionality**: Each logger writes to its designated file
- **Implementation**: Custom `RoutingHandler` routes log records based on logger name
- **Benefits**:
  - Account logs go to `logs/accounts/account_{phone}.log`
  - System logs go to `logs/main.log`
  - Each file is independently rotated based on its own size

## Configuration

All settings are in `config.yaml`:

```yaml
logging:
  level: "DEBUG"  # Log level
  console_log: true  # Enable console output
  save_to: "./logs/"  # Base log directory
  max_log_size_mb: 10  # Maximum size per log file before rotation
  backup_count: 3  # Number of rotated backups to keep
  max_crash_reports: 15  # Maximum number of crash reports to retain
```

### Configuration Options Explained

| Setting | Default | Description |
|---------|---------|-------------|
| `max_log_size_mb` | 10 | Maximum size in MB before a log file is rotated |
| `backup_count` | 3 | Number of rotated log files to keep (e.g., .log.1, .log.2, .log.3) |
| `max_crash_reports` | 15 | Maximum number of crash report files to retain |

## File Structure

```
logs/
├── main.log              # Current main log
├── main.log.1            # Rotated backup (most recent)
├── main.log.2            # Older backup
├── main.log.3            # Oldest backup
├── accounts/
│   ├── account_1234567890.log
│   ├── account_1234567890.log.1
│   └── ...
└── crashes/
    ├── crash_12345_20251104_120000.log
    ├── crash_12346_20251104_120100.log
    └── ... (up to 15 files, oldest deleted automatically)
```

## How It Works

### Log Rotation Process

1. Logger writes a record to a file
2. `RotatingFileHandler` checks if file size exceeds `max_log_size_mb`
3. If exceeded:
   - Current file renamed to `.log.1`
   - Existing `.log.1` renamed to `.log.2`
   - Existing `.log.2` renamed to `.log.3`
   - Existing `.log.3` (if beyond `backup_count`) is deleted
4. New messages written to fresh log file

### Crash Report Management

1. When `flush_crash_report()` or `write_crash_report()` is called
2. `_cleanup_old_crash_reports()` runs first
3. Counts all `crash_*.log` files in crashes folder
4. If count >= `max_crash_reports`:
   - Sorts files by modification time (oldest first)
   - Calculates how many to delete
   - Deletes the oldest files
5. Writes new crash report

## Testing

Run the test script to verify functionality:

```bash
python test_log_management.py
```

This will:
- Create multiple crash reports and verify the limit is enforced
- Show current log file sizes
- Demonstrate that rotation will occur at 10 MB

## Benefits

1. **Automatic Cleanup**: No manual intervention needed
2. **Predictable Disk Usage**: Log files won't grow beyond configured limits
3. **Recent History Preserved**: Keeps recent logs and crash reports for debugging
4. **Per-Account Isolation**: Each account's logs are independently managed
5. **Thread-Safe**: Works correctly in multi-threaded environments
6. **Multiprocessing Compatible**: Works with the existing queue-based logging system

## Performance Impact

- **Minimal**: Rotation only occurs when size threshold is reached
- **Crash Report Cleanup**: Small overhead when writing crash reports (O(n) where n is number of crash files)
- **No Impact on Normal Logging**: Regular log writes are not affected

## Maintenance

The system is self-maintaining. No manual cleanup required. However, you can:

- Adjust `max_log_size_mb` to increase/decrease rotation frequency
- Modify `backup_count` to keep more/fewer rotated files
- Change `max_crash_reports` to retain more/fewer crash reports
- Manually delete old rotated files if needed (they won't be recreated)

## Troubleshooting

### Logs not rotating?
- Check that log files actually reach the size limit (default 10 MB)
- Verify `max_log_size_mb` setting in config.yaml
- Ensure write permissions on log directory

### Too many crash reports?
- Check `max_crash_reports` setting
- Verify crash reports are being written correctly
- Look for issues in `_cleanup_old_crash_reports()` function

### Disk space still high?
- Reduce `max_log_size_mb` value
- Reduce `backup_count` value
- Reduce `max_crash_reports` value
- Check for other non-managed log files

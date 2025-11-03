import logging, time, sys, os, yaml, multiprocessing, threading, traceback, inspect, copy
from asyncio import CancelledError
from functools import wraps
from logging.handlers import QueueHandler, QueueListener
from collections import defaultdict, deque

# Load configuration from YAML
def load_config():
    with open('config.yaml', 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)

config = load_config()
LOGGING_LEVEL = config['logging']['level']
CONSOLE_LOG = config['logging']['console_log']
SAVE_TO = config['logging']['save_to']

if LOGGING_LEVEL == "DEBUG":
    LEVEL = logging.DEBUG
elif LOGGING_LEVEL == "INFO":
    LEVEL = logging.INFO
elif LOGGING_LEVEL == "WARNING":
    LEVEL = logging.WARNING
elif LOGGING_LEVEL == "ERROR":
    LEVEL = logging.ERROR
elif LOGGING_LEVEL == "CRITICAL":
    LEVEL = logging.CRITICAL
else:
    raise ValueError(f"Unknown logging level: {LOGGING_LEVEL}")

if not os.path.exists(SAVE_TO):
    os.makedirs(SAVE_TO)

accounts_folder = os.path.join(SAVE_TO, "accounts")
if not os.path.exists(accounts_folder):
    os.makedirs(accounts_folder)

crashes_folder = os.path.join(SAVE_TO, "crashes")
if not os.path.exists(crashes_folder):
    os.makedirs(crashes_folder)

# Per-process log buffer (PID -> deque)
_log_buffers = defaultdict(lambda: deque(maxlen=200))  # Adjust buffer size as needed
_log_buffers_lock = threading.Lock()

class CustomFormatter(logging.Formatter):
    def format(self, record):
        # Set default value for execution_time if not provided
        if not hasattr(record, 'execution_time'):
            record.execution_time = 'N/A'
        # Fixate levelname length to 8 symbols
        record.levelname = str(record.levelname).ljust(8)[:8]
        
        try:
            return super().format(record)
        except (TypeError, ValueError) as e:
            # Handle format errors gracefully
            try:
                # Try to get the basic message without formatting
                if hasattr(record, 'msg') and hasattr(record, 'args'):
                    if record.args:
                        # Convert all args to strings to avoid format errors
                        safe_args = tuple(str(arg) for arg in record.args)
                        record.args = safe_args
                        return super().format(record)
                    else:
                        # No args, just format the message as-is
                        return super().format(record)
                else:
                    # Fallback: create a basic formatted message
                    return f"{record.asctime if hasattr(record, 'asctime') else 'N/A'} - {record.name} - {record.levelname} - FORMAT_ERROR: {str(record.msg)}"
            except:
                # Ultimate fallback
                return f"LOGGING_FORMAT_ERROR: {str(record.msg)} (original error: {str(e)})"

class SafeConsoleFormatter(CustomFormatter):
    """
    Formatter that forcefully casts string data to safe UTF representation
    before formatting for console output, without mutating the original record.
    """
    def format(self, record):
        # Work on a shallow copy so original record stays unchanged
        rec = copy.copy(record)
        try:
            # Ensure message is a str; decode bytes to utf-8 with replacement
            if isinstance(rec.msg, (bytes, bytearray)):
                rec.msg = rec.msg.decode('utf-8', errors='replace')
            else:
                rec.msg = str(rec.msg)
        except Exception:
            pass

        try:
            # Normalize args to strings, preserving mapping or sequence shape
            if rec.args:
                if isinstance(rec.args, dict):
                    safe_args = {}
                    for k, v in rec.args.items():
                        if isinstance(v, (bytes, bytearray)):
                            safe_args[k] = v.decode('utf-8', errors='replace')
                        else:
                            safe_args[k] = str(v)
                    rec.args = safe_args
                else:
                    safe_args = []
                    for v in rec.args:
                        if isinstance(v, (bytes, bytearray)):
                            safe_args.append(v.decode('utf-8', errors='replace'))
                        else:
                            safe_args.append(str(v))
                    rec.args = tuple(safe_args)
        except Exception:
            pass

        # Also ensure record.getMessage() won't fail due to non-str parts
        try:
            # Precompute message so custom format uses safe string
            rec.message = rec.getMessage()
        except Exception:
            try:
                rec.message = str(rec.msg)
            except Exception:
                rec.message = "<unrepresentable message>"

        return super().format(rec)

class BufferingHandler(logging.Handler):
    """
    Handler that stores log records in a per-process buffer for crash reporting.
    """
    def emit(self, record):
        pid = getattr(record, 'process', os.getpid())
        with _log_buffers_lock:
            _log_buffers[pid].append(self.format(record))

def write_crash_report(pid=None, exc_info=None, extra_info=None):
    """
    Write the buffered logs for the given PID to a crash report file.
    """
    if pid is None:
        pid = os.getpid()
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    crash_file = os.path.join(crashes_folder, f'crash_{pid}_{timestamp}.log')
    with _log_buffers_lock:
        buffer = list(_log_buffers.get(pid, []))
    with open(crash_file, 'w', encoding='utf-8') as f:
        f.write(f"Crash Report for PID {pid} at {timestamp}\n\n")
        if extra_info:
            f.write(f"Extra Info: {extra_info}\n\n")
        if exc_info:
            f.write("Exception Traceback:\n")
            traceback.print_exception(*exc_info, file=f)
            f.write("\n")
        f.write("Recent Log Buffer:\n")
        for line in buffer:
            f.write(line + '\n')

# Set up the logging queue and listener (main process only)
_log_queue = multiprocessing.Queue(-1)

# Handlers for the listener (file, console, buffer)
_listener_handlers = []

def _make_handlers(log_file):
    handlers = []
    file_path = os.path.join(SAVE_TO, log_file)
    formatter = CustomFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    formatter.converter = lambda *args: time.localtime(*args)
    formatter.default_time_format = '%Y-%m-%d %H:%M:%S'
    formatter.default_msec_format = ''
    # Ensure file handler writes in utf-8
    file_handler = logging.FileHandler(file_path, encoding='utf-8')
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)
    if CONSOLE_LOG:
        # Use SafeConsoleFormatter for console output to avoid non-UTF problems
        console_formatter = SafeConsoleFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_formatter.converter = formatter.converter
        console_formatter.default_time_format = formatter.default_time_format
        console_formatter.default_msec_format = formatter.default_msec_format
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        handlers.append(console_handler)
    buffer_handler = BufferingHandler()
    buffer_handler.setFormatter(formatter)
    handlers.append(buffer_handler)
    return handlers

_main_listener = None
_main_listener_lock = threading.Lock()

def _ensure_listener(log_file):
    global _main_listener, _listener_handlers
    with _main_listener_lock:
        if _main_listener is None:
            _listener_handlers = _make_handlers(log_file)
            _main_listener = QueueListener(_log_queue, *_listener_handlers, respect_handler_level=True)
            _main_listener.start()

def cleanup_logging():
    """Clean up logging resources"""
    global _main_listener, _listener_handlers, _log_queue
    with _main_listener_lock:
        if _main_listener is not None:
            try:
                _main_listener.stop()
            except Exception:
                pass
            finally:
                _main_listener = None
        
        # Close all handlers
        for handler in _listener_handlers:
            try:
                handler.close()
            except Exception:
                pass
        _listener_handlers.clear()
        
        # Close the queue (cancel join thread if any)
        try:
            _log_queue.close()
            _log_queue.join_thread()
        except Exception:
            pass


def get_log_directory() -> str:
    """Expose the configured log directory for external consumers."""
    return SAVE_TO

def setup_logger(name: str, log_file: str) -> logging.Logger:
    """
    Sets up a logger that puts records into a multiprocessing queue.
    :param name: Name of the logger.
    :param log_file: Path to the log file for this logger.
    :return: Configured Logger object.
    """
    if len(name) < 5:
        name = name.ljust(4, ' ')
    else:
        name = name.ljust(12, ' ')
    # FORCE RESET: Remove any existing logger with this name
    if name in logging.Logger.manager.loggerDict:
        del logging.Logger.manager.loggerDict[name]

    logger = logging.getLogger(name)
    logger.propagate = False
    logger.setLevel(LEVEL)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Add QueueHandler to send logs to the main process
    logger.addHandler(QueueHandler(_log_queue))

    # Ensure the listener is running (main process only)
    _ensure_listener(log_file)

    return logger

# Optionally, provide a function to flush crash report on demand
def flush_crash_report(exc_info=None, extra_info=None):
    write_crash_report(pid=os.getpid(), exc_info=exc_info, extra_info=extra_info)

def crash_handler(func):
    """Decorator to wrap functions with crash report handling."""
    if inspect.iscoroutinefunction(func):  # async function
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception:
                import sys
                flush_crash_report(exc_info=sys.exc_info())
                raise
    else:  # synchronous function
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception:
                import sys
                flush_crash_report(exc_info=sys.exc_info())
                raise
    return wrapper

def handle_task_exception(task):
    try:
        task.result()
    except CancelledError:
        pass
    except Exception:
        import sys
        flush_crash_report(exc_info=sys.exc_info())
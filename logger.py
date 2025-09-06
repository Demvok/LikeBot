import logging, time, sys, os
import yaml

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

class CustomFormatter(logging.Formatter):
    def format(self, record):
            # Set default value for execution_time if not provided
            if not hasattr(record, 'execution_time'):
                record.execution_time = 'N/A'
            # Fixate levelname length to 7 symbols
            record.levelname = str(record.levelname).ljust(7)[:7]
            return super().format(record)

def setup_logger(name: str, log_file: str) -> logging.Logger:
    """
    Sets up a logger with individual file logging and console log.
    
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
    
    # Create a fresh logger instance
    logger = logging.getLogger(name)
    logger.propagate = False
    logger.setLevel(LEVEL)
    
    # Remove any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # File handler for individual file logging
    file_path = os.path.join(SAVE_TO, log_file)
    file_handler = logging.FileHandler(file_path)
    formatter = CustomFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    formatter.converter = lambda *args: time.localtime(*args)
    formatter.default_time_format = '%Y-%m-%d %H:%M:%S'
    formatter.default_msec_format = ''
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Add console handler
    if CONSOLE_LOG:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


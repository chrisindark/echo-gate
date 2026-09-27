import logging
import uuid
from contextvars import ContextVar
import copy

# Context variable for storing the correlation ID
correlation_id_ctx_var: ContextVar[str] = ContextVar("correlation_id", default="-")

class ColoredFormatter(logging.Formatter):
    """
    A custom formatter that adds colors to log levels and includes the correlation ID.
    """
    # ANSI escape sequences for colors
    COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Green
        logging.WARNING: "\033[33m",   # Yellow
        logging.ERROR: "\033[31m",     # Red
        logging.CRITICAL: "\033[1;31m" # Bold Red
    }
    RESET = "\033[0m"
    DIM = "\033[2m"

    def format(self, record):
        record_copy = copy.copy(record)
        
        # Add correlation ID to the record
        record_copy.correlation_id = correlation_id_ctx_var.get()

        # Apply color based on the log level
        level_color = self.COLORS.get(record_copy.levelno, self.RESET)
        
        # Colorize the level name
        record_copy.levelname = f"{level_color}{record_copy.levelname}{self.RESET}"
        
        # Format the rest
        msg = super().format(record_copy)
        return msg

def setup_logger():
    """
    Configures the root logger with the colored formatter and correlation ID.
    """
    logger = logging.getLogger()
    
    # Remove existing handlers if any
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        
    handler = logging.StreamHandler()
    
    # Define the log format
    fmt = "%(asctime)s - %(name)s - %(levelname)s - [%(correlation_id)s] - %(message)s"
    formatter = ColoredFormatter(fmt)
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    
    # Update Uvicorn loggers if they exist to use our formatter
    for uvicorn_logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers = [handler]
        # Prevent uvicorn from duplicating logs to the root logger
        uvicorn_logger.propagate = False

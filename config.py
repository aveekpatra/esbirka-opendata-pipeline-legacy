"""
E-Sbírka Pipeline Configuration
Shared settings and logging setup for all scripts.
"""

import os
import logging
from pathlib import Path
from datetime import datetime

# ============================================================================
# PATHS - Modify these for your environment
# ============================================================================

# For Google Colab with Drive mounted
BASE_DIR = Path('/content/drive/MyDrive/esbirka')

# Subdirectories
DATA_DIR = BASE_DIR / 'data'
CACHE_DIR = BASE_DIR / 'cache'
OUTPUT_DIR = BASE_DIR / 'output'
LOGS_DIR = BASE_DIR / 'logs'
CHECKPOINT_DIR = BASE_DIR / 'checkpoints'

# Output subdirs
ACTS_DIR = OUTPUT_DIR / 'acts'
SYNC_DIR = OUTPUT_DIR / '_sync'

# ============================================================================
# OPENDATA SETTINGS
# ============================================================================

OPENDATA_BASE_URL = "https://opendata.eselpoint.cz/DDATAJ1/"

# Files needed for Phase 1
REQUIRED_FILES = [
    "001PravniAktZneni",
    "002PravniAkt", 
    "003PravniAktZneniFragment",
    "004PravniAktFragment",
    "014CiselnikTypZneni",
]

# ============================================================================
# PROCESSING SETTINGS
# ============================================================================

# Batch sizes (tune for memory - Colab free tier has ~12GB RAM)
INDEX_CHECKPOINT_INTERVAL = 100000  # Save checkpoint every N records
FRAGMENT_BATCH_SAVE = 500  # Save acts every N unique docs in buffer
MIN_TEXT_LENGTH = 10  # Skip fragments shorter than this

# Download settings
DOWNLOAD_CHUNK_SIZE = 32768  # 32KB chunks for faster download
DOWNLOAD_TIMEOUT = 300  # 5 minute timeout for large files

# ============================================================================
# LOGGING SETUP
# ============================================================================

_logger_configured = False

def setup_directories() -> None:
    """Create all required directories."""
    for d in [DATA_DIR, CACHE_DIR, ACTS_DIR, SYNC_DIR, LOGS_DIR, CHECKPOINT_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    print(f"✅ Directories created at: {BASE_DIR}")


def setup_logging(name: str = "esbirka") -> logging.Logger:
    """
    Setup dual logging: full log + errors only.
    
    Args:
        name: Logger name (used in header)
        
    Returns:
        Configured root logger
    """
    global _logger_configured
    
    # Ensure logs dir exists
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Only configure once
    if _logger_configured:
        return logging.getLogger()
    
    # Clear existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Formatter with source info for debugging
    fmt = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Full log (DEBUG+) - captures everything
    full_handler = logging.FileHandler(
        LOGS_DIR / 'pipeline_full.log', 
        encoding='utf-8',
        mode='a'  # Append mode
    )
    full_handler.setLevel(logging.DEBUG)
    full_handler.setFormatter(fmt)
    
    # Error log (WARNING+) - only problems
    error_handler = logging.FileHandler(
        LOGS_DIR / 'pipeline_errors.log', 
        encoding='utf-8',
        mode='a'
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(fmt)
    
    # Console (INFO+) - user-visible
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)
    
    # Configure root logger
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(full_handler)
    root_logger.addHandler(error_handler)
    root_logger.addHandler(console_handler)
    
    _logger_configured = True
    
    # Log startup
    root_logger.info("=" * 60)
    root_logger.info(f"E-SBÍRKA PIPELINE - {name.upper()}")
    root_logger.info(f"Timestamp: {datetime.now().isoformat()}")
    root_logger.info(f"Base directory: {BASE_DIR}")
    root_logger.info("=" * 60)
    
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a named logger (child of root)."""
    return logging.getLogger(name)


if __name__ == "__main__":
    setup_directories()
    logger = setup_logging("test")
    logger.info("Configuration loaded successfully")
    logger.debug("This is a debug message (visible in full log only)")
    logger.warning("This is a warning (visible in error log too)")

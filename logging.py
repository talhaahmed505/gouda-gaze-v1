"""
Centralized logging configuration for Gouda Gaze.
Logs to files WITHOUT automatic rotation/deletion.
"""

import logging
from pathlib import Path


def setup_loggers():
    """Initialize all application loggers."""
    
    # Create logs directory
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    
    # Standard formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # ========== MAIN APP LOGGER ==========
    app_logger = logging.getLogger('gouda_gaze')
    app_logger.setLevel(logging.DEBUG)
    
    # Main app log (all levels) - NO ROTATION
    app_file_handler = logging.FileHandler(
        log_dir / 'app.log',
        encoding='utf-8'
    )
    app_file_handler.setLevel(logging.DEBUG)
    app_file_handler.setFormatter(formatter)
    app_logger.addHandler(app_file_handler)
    
    # Error log (errors only) - NO ROTATION
    error_handler = logging.FileHandler(
        log_dir / 'app-error.log',
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    app_logger.addHandler(error_handler)
    
    # Console output
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    app_logger.addHandler(console_handler)
    
    # ========== HTTP REQUEST LOGGER ==========
    http_logger = logging.getLogger('http')
    http_logger.setLevel(logging.INFO)
    
    http_handler = logging.FileHandler(
        log_dir / 'http.log',
        encoding='utf-8'
    )
    http_handler.setLevel(logging.INFO)
    http_formatter = logging.Formatter(
        '%(asctime)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    http_handler.setFormatter(http_formatter)
    http_logger.addHandler(http_handler)
    
    # ========== PTZ OPERATIONS LOGGER ==========
    ptz_logger = logging.getLogger('ptz')
    ptz_logger.setLevel(logging.INFO)
    
    ptz_handler = logging.FileHandler(
        log_dir / 'ptz.log',
        encoding='utf-8'
    )
    ptz_handler.setLevel(logging.INFO)
    ptz_handler.setFormatter(formatter)
    ptz_logger.addHandler(ptz_handler)
    
    # ========== PRIVACY LOGGER ==========
    privacy_logger = logging.getLogger('privacy')
    privacy_logger.setLevel(logging.INFO)
    
    privacy_handler = logging.FileHandler(
        log_dir / 'privacy.log',
        encoding='utf-8'
    )
    privacy_handler.setLevel(logging.INFO)
    privacy_handler.setFormatter(formatter)
    privacy_logger.addHandler(privacy_handler)
    
    return app_logger, http_logger, ptz_logger, privacy_logger


def get_loggers():
    """Get or create all loggers."""
    app_logger = logging.getLogger('gouda_gaze')
    http_logger = logging.getLogger('http')
    ptz_logger = logging.getLogger('ptz')
    privacy_logger = logging.getLogger('privacy')
    
    # Initialize if empty
    if not app_logger.handlers:
        setup_loggers()
    
    return app_logger, http_logger, ptz_logger, privacy_logger
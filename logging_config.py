#!/usr/bin/env python3
"""
Logging Configuration Module
Configures logging levels per module from config file
WITH DAILY LOG FILE ROTATION + CONSOLE MODE CONTROL + MULTI-FILE SUPPORT
"""
import logging
import sys
import os
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, List


def setup_logging(config: Dict = None):
    """
    Setup logging with per-module level control and daily log rotation

    Config structure:
    {
        "logging": {
            "root_level": "INFO",
            "console": true,
            "console_modules": ["twap_state_tracker"],
            "files": [
                {
                    "path": "logs/hype_twap.log",
                    "modules": ["twap.hyperliquid", "twap_state_tracker"]
                },
                {
                    "path": "logs/other_twap.log",
                    "modules": ["twap.binance", "twap.okx", "twap.bybit"]
                },
                {
                    "path": "logs/main.log",
                    "modules": ["main", "api_client"]
                }
            ],
            "modules": {
                "twap.hyperliquid": "INFO",
                "twap.binance": "INFO",
                "twap.okx": "INFO",
                "main": "INFO"
            }
        }
    }
    """
    if config is None:
        config = {}

    logging_config = config.get('logging', {})

    # Default settings
    root_level = logging_config.get('root_level', 'INFO')
    console_enabled = logging_config.get('console', True)
    console_modules = logging_config.get('console_modules', None)
    log_files = logging_config.get('files', [])
    module_levels = logging_config.get('modules', {})

    # Convert string levels to logging constants
    level_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }

    # Setup root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level_map.get(root_level.upper(), logging.INFO))

    # Clear existing handlers
    root_logger.handlers = []

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Add file handlers for each configured file
    for file_config in log_files:
        log_file = file_config.get('path')
        file_modules = file_config.get('modules', [])

        if not log_file:
            continue

        # Create logs directory if needed
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # Create file handler with daily rotation
        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when='midnight',
            interval=1,
            backupCount=7,
            encoding='utf-8'
        )
        file_handler.suffix = '%Y-%m-%d'
        file_handler.setFormatter(formatter)

        # Add filter to only log specific modules to this file
        if file_modules:
            file_handler.addFilter(ModuleFilter(file_modules))

        root_logger.addHandler(file_handler)

        print(f"Log file: {log_file}")
        print(f"   Modules: {file_modules if file_modules else 'ALL'}")

    # Add console handler with filtering
    if console_enabled:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)

        if console_modules is not None:
            console_handler.addFilter(ModuleFilter(console_modules))
            print(f"Console filter: {console_modules if console_modules else 'NONE'}")

        root_logger.addHandler(console_handler)

    # Configure module-specific log levels
    for module_name, level_str in module_levels.items():
        module_logger = logging.getLogger(module_name)
        level = level_map.get(level_str.upper(), logging.INFO)
        module_logger.setLevel(level)

        print(f"Logger [{module_name}] set to {level_str}")

    print(f"\nLogging configured (root level: {root_level})")
    print(f"   Console: {console_enabled}")
    print()


class ModuleFilter(logging.Filter):
    """Filter to only allow specific modules"""

    def __init__(self, allowed_modules: List[str]):
        super().__init__()
        self.allowed_modules = set(allowed_modules) if allowed_modules else set()

    def filter(self, record):
        """Return True if this record should be logged"""
        # If no modules specified (empty list), block everything
        if not self.allowed_modules:
            return False

        # Check if the logger name matches any allowed module
        # Supports both exact match and prefix match (for submodules)
        for allowed in self.allowed_modules:
            if record.name == allowed or record.name.startswith(f"{allowed}."):
                return True

        return False


def get_module_logger(module_name: str) -> logging.Logger:
    """Get a logger for a specific module"""
    return logging.getLogger(module_name)
#!/usr/bin/env python3
"""
Logging Configuration Module
Configures logging levels per module from config file
"""
import logging
import sys
from typing import Dict


def setup_logging(config: Dict = None):
    """
    Setup logging with per-module level control

    Config structure:
    {
        "logging": {
            "root_level": "INFO",
            "file": "twap_tracker.log",
            "console": true,
            "modules": {
                "api_client.hypurrscan_client": "DEBUG",
                "twap_state_tracker": "INFO",
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
    log_file = logging_config.get('file', 'twap_tracker.log')
    console_enabled = logging_config.get('console', True)
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

    # Add file handler
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Add console handler
    if console_enabled:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # Configure module-specific log levels
    for module_name, level_str in module_levels.items():
        module_logger = logging.getLogger(module_name)
        level = level_map.get(level_str.upper(), logging.INFO)
        module_logger.setLevel(level)

        print(f"📝 Logger [{module_name}] set to {level_str}")

    print(f"✅ Logging configured (root level: {root_level})")
    print(f"   File: {log_file}")
    print(f"   Console: {console_enabled}")
    print()


def get_module_logger(module_name: str) -> logging.Logger:
    """Get a logger for a specific module"""
    return logging.getLogger(module_name)
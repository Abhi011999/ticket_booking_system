"""
Structured logging configuration for the ticket booking system.

This module configures structlog for JSON-formatted logging with correlation IDs.
"""

import logging
import structlog


def configure_logging():
    """Configure structured logging with JSON output and correlation IDs."""
    logging.basicConfig(format="%(message)s", stream=None, level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger():
    """Get configured structlog logger instance."""
    return structlog.get_logger()


# Configure logging when module is imported
configure_logging()

# Export configured logger
logger = get_logger()

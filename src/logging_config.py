from __future__ import annotations

import sys

from loguru import logger

from src.config import settings


def configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )


def get_logger(name: str | None = None):
    if name:
        return logger.bind(module=name)
    return logger
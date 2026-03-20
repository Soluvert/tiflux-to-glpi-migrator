from __future__ import annotations

import os
import sys
from logging import INFO

from loguru import logger


def init_logging(*, logs_dir: str, verbose: bool = False) -> None:
    os.makedirs(logs_dir, exist_ok=True)

    # Remove sinks if already configured (permite reexec em testes).
    logger.remove()

    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, colorize=True, backtrace=False, diagnose=False)
    logger.add(
        os.path.join(logs_dir, "migrator.log"),
        level=level,
        rotation="10 MB",
        retention="14 days",
        enqueue=True,
        backtrace=False,
        diagnose=False,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
    )


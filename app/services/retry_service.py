from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    initial_wait_seconds: float = 0.5
    max_wait_seconds: float = 10.0


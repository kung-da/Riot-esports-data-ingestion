"""Retry helpers with exponential backoff and jitter."""

from __future__ import annotations

import random


def compute_backoff(attempt: int, base_delay: float = 0.5, max_delay: float = 30.0) -> float:
    """Return 1, 2, 4, 8 style backoff with small jitter."""

    exponential = min(max_delay, base_delay * (2 ** max(attempt - 1, 0)))
    return exponential + random.uniform(0.1, 0.5)

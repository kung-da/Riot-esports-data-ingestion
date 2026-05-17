"""Polite async rate limiting for safe long-running Riot API crawlers."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUSES = {429, 503, 504}


@dataclass
class RegionLimiterState:
    """Mutable limiter state for one Riot platform or routing region."""

    semaphore: asyncio.Semaphore
    request_times: deque[float] = field(default_factory=deque)
    method_request_times: dict[str, deque[float]] = field(default_factory=dict)
    last_request_at: float = 0.0
    throttle_factor: float = 1.0
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0


class RiotRateLimiter:
    """Global, per-region, and per-method limiter with jitter and circuit breaker."""

    def __init__(
        self,
        *,
        max_concurrency: int = 6,
        requests_per_minute: int = 40,
        method_requests_per_minute: int = 20,
        request_sleep_min_seconds: float = 1.2,
        request_sleep_max_seconds: float = 1.8,
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_cooldown_seconds: float = 300.0,
    ) -> None:
        self.max_concurrency = max(1, min(max_concurrency, 5))
        self.requests_per_minute = max(1, min(requests_per_minute, 22))
        self.method_requests_per_minute = max(1, min(method_requests_per_minute, 15))
        self.request_sleep_min_seconds = max(request_sleep_min_seconds, 1.8)
        self.request_sleep_max_seconds = max(request_sleep_max_seconds, self.request_sleep_min_seconds)
        self.circuit_breaker_failure_threshold = max(circuit_breaker_failure_threshold, 1)
        self.circuit_breaker_cooldown_seconds = max(circuit_breaker_cooldown_seconds, 60.0)

        self._global_semaphore = asyncio.Semaphore(self.max_concurrency)
        self._global_request_times: deque[float] = deque()
        self._states: dict[str, RegionLimiterState] = {}
        self._state_lock = asyncio.Lock()
        self._traffic_lock = asyncio.Lock()

    @asynccontextmanager
    async def limit(self, region: str, method_key: str) -> AsyncIterator[None]:
        """Wait for a safe slot before allowing one HTTP request."""

        state = await self._state(region)
        await self._global_semaphore.acquire()
        await state.semaphore.acquire()
        try:
            await self._wait_for_turn(region, method_key, state)
            yield
        finally:
            state.semaphore.release()
            self._global_semaphore.release()

    async def acquire(self, region: str, method_key: str) -> None:
        """Compatibility helper used by tests."""

        async with self.limit(region, method_key):
            return

    async def cooldown(self, region: str, method_key: str, seconds: float) -> None:
        """Open the region circuit for a cooldown window."""

        state = await self._state(region)
        cooldown_seconds = max(seconds, 1.0)
        async with self._traffic_lock:
            state.circuit_open_until = max(state.circuit_open_until, time.monotonic() + cooldown_seconds)
        LOGGER.warning(
            "Rate-limit cooldown active region=%s method=%s seconds=%.1f",
            region,
            method_key,
            cooldown_seconds,
        )

    async def record_response(self, region: str, method_key: str, status: int) -> None:
        """Update adaptive throttling state from one response status."""

        state = await self._state(region)
        async with self._traffic_lock:
            if status in RETRYABLE_STATUSES:
                state.consecutive_failures += 1
                if status == 429:
                    state.throttle_factor = max(0.25, state.throttle_factor * 0.65)
                    LOGGER.warning(
                        "Received 429 region=%s method=%s; reducing throughput to %.0f%%.",
                        region,
                        method_key,
                        state.throttle_factor * 100,
                    )
                if state.consecutive_failures >= self.circuit_breaker_failure_threshold:
                    state.circuit_open_until = max(
                        state.circuit_open_until,
                        time.monotonic() + self.circuit_breaker_cooldown_seconds,
                    )
                    LOGGER.warning(
                        "Circuit breaker opened region=%s method=%s failures=%s cooldown_seconds=%.0f",
                        region,
                        method_key,
                        state.consecutive_failures,
                        self.circuit_breaker_cooldown_seconds,
                    )
                return

            if 200 <= status < 300:
                state.consecutive_failures = 0
                if state.throttle_factor < 1.0:
                    state.throttle_factor = min(1.0, state.throttle_factor + 0.02)

    async def soft_throttle(self, region: str, method_key: str, usage_ratio: float, cooldown_seconds: float = 5.0) -> None:
        """Reduce speed before a hard 429 when Riot headers show high usage."""

        state = await self._state(region)
        async with self._traffic_lock:
            state.throttle_factor = max(0.4, min(state.throttle_factor, 1.0 - min(usage_ratio - 0.80, 0.35)))
            state.circuit_open_until = max(state.circuit_open_until, time.monotonic() + cooldown_seconds)
        LOGGER.warning(
            "Preemptive throttle region=%s method=%s usage=%.0f%% throttle=%.0f%% cooldown=%.1fs",
            region,
            method_key,
            usage_ratio * 100,
            state.throttle_factor * 100,
            cooldown_seconds,
        )

    async def _state(self, region: str) -> RegionLimiterState:
        normalized = region.lower()
        async with self._state_lock:
            if normalized not in self._states:
                self._states[normalized] = RegionLimiterState(semaphore=asyncio.Semaphore(self.max_concurrency))
            return self._states[normalized]

    async def _wait_for_turn(self, region: str, method_key: str, state: RegionLimiterState) -> None:
        while True:
            sleep_for = 0.0
            async with self._traffic_lock:
                now = time.monotonic()
                method_times = state.method_request_times.setdefault(method_key, deque())
                self._discard_old(self._global_request_times, now)
                self._discard_old(state.request_times, now)
                self._discard_old(method_times, now)

                if now < state.circuit_open_until:
                    sleep_for = state.circuit_open_until - now
                else:
                    effective_region_limit = max(1, int(self.requests_per_minute * state.throttle_factor))
                    effective_method_limit = max(1, int(self.method_requests_per_minute * state.throttle_factor))

                    if len(self._global_request_times) >= self.requests_per_minute:
                        sleep_for = max(sleep_for, self._global_request_times[0] + 60.0 - now)
                    if len(state.request_times) >= effective_region_limit:
                        sleep_for = max(sleep_for, state.request_times[0] + 60.0 - now)
                    if len(method_times) >= effective_method_limit:
                        sleep_for = max(sleep_for, method_times[0] + 60.0 - now)

                    required_gap = random.uniform(self.request_sleep_min_seconds, self.request_sleep_max_seconds)
                    sleep_for = max(sleep_for, required_gap - (now - state.last_request_at))

                if sleep_for <= 0:
                    state.last_request_at = now
                    self._global_request_times.append(now)
                    state.request_times.append(now)
                    method_times.append(now)
                    LOGGER.debug(
                        "Limiter granted region=%s method=%s throttle=%.0f%%",
                        region,
                        method_key,
                        state.throttle_factor * 100,
                    )
                    return

            await asyncio.sleep(max(sleep_for, 0.05))

    @staticmethod
    def _discard_old(values: deque[float], now: float) -> None:
        while values and now - values[0] >= 60.0:
            values.popleft()

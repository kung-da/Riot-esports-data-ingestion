import asyncio

from crawler.utils.rate_limiter import RiotRateLimiter


def test_rate_limiter_acquire_completes() -> None:
    async def run() -> None:
        limiter = RiotRateLimiter(
            max_concurrency=6,
            requests_per_minute=40,
            method_requests_per_minute=20,
            request_sleep_min_seconds=1.2,
            request_sleep_max_seconds=1.8,
        )
        await limiter.acquire("vn2", "match.detail")

    asyncio.run(run())


def test_rate_limiter_caps_unsafe_values() -> None:
    limiter = RiotRateLimiter(
        max_concurrency=100,
        requests_per_minute=500,
        method_requests_per_minute=100,
        request_sleep_min_seconds=0.1,
        request_sleep_max_seconds=0.2,
    )

    assert limiter.max_concurrency == 8
    assert limiter.requests_per_minute == 50
    assert limiter.method_requests_per_minute == 30
    assert limiter.request_sleep_min_seconds == 1.2
    assert limiter.request_sleep_max_seconds == 1.2

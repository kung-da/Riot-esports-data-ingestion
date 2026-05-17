"""Async Riot API client with retries, rate limiting, and typed errors."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

import aiohttp

from crawler.config.settings import Settings
from crawler.utils.rate_limiter import RiotRateLimiter
from crawler.utils.retry import compute_backoff


LOGGER = logging.getLogger(__name__)

PLATFORM_HOST_TEMPLATE = "https://{region}.api.riotgames.com"
REGIONAL_HOST_TEMPLATE = "https://{region}.api.riotgames.com"


class RiotAPIError(RuntimeError):
    """Raised when Riot API returns a non-retryable error."""

    def __init__(self, status: int, message: str, payload: Any | None = None) -> None:
        super().__init__(f"Riot API error {status}: {message}")
        self.status = status
        self.payload = payload


class RiotRateLimitError(RiotAPIError):
    """Raised when Riot API rate limits a request after retries are exhausted."""


class RiotClient:
    """Small production-minded async client for Riot Games APIs.

    The client deliberately returns raw JSON dictionaries so the ingestion layer
    can persist complete source responses before applying typed validation and
    analytics transformations.
    """

    def __init__(
        self,
        settings: Settings,
        rate_limiter: RiotRateLimiter | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.settings = settings
        if not self.settings.riot_api_key.get_secret_value():
            raise ValueError("RIOT_API_KEY is required for Riot API crawling. Add it to .env or the environment.")
        self.rate_limiter = rate_limiter or RiotRateLimiter(
            max_concurrency=settings.max_concurrency,
            requests_per_minute=settings.requests_per_minute,
            method_requests_per_minute=settings.method_requests_per_minute,
            request_sleep_min_seconds=settings.request_sleep_min_seconds,
            request_sleep_max_seconds=settings.request_sleep_max_seconds,
            circuit_breaker_failure_threshold=settings.circuit_breaker_failure_threshold,
            circuit_breaker_cooldown_seconds=settings.circuit_breaker_cooldown_seconds,
        )
        self._external_session = session
        self._session: aiohttp.ClientSession | None = session

    async def __aenter__(self) -> "RiotClient":
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout_seconds)
            connector = aiohttp.TCPConnector(limit=self.settings.max_concurrency)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session is not None and self._external_session is None:
            await self._session.close()
        self._session = self._external_session

    @property
    def session(self) -> aiohttp.ClientSession:
        """Return the active aiohttp session."""

        if self._session is None:
            raise RuntimeError("RiotClient must be used as an async context manager.")
        return self._session

    async def request_json(
        self,
        region: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        method: str = "GET",
        regional: bool = False,
        method_key: str | None = None,
    ) -> Any:
        """Execute a Riot API request and return parsed JSON."""

        normalized_region = region.lower()
        host_template = REGIONAL_HOST_TEMPLATE if regional else PLATFORM_HOST_TEMPLATE
        host = host_template.format(region=normalized_region)
        clean_path = path if path.startswith("/") else f"/{path}"
        query = f"?{urlencode(params, doseq=True)}" if params else ""
        url = f"{host}{clean_path}{query}"
        limiter_key = method_key or clean_path.split("?")[0]
        headers = {
            "X-Riot-Token": self.settings.riot_api_key.get_secret_value(),
            "User-Agent": self.settings.user_agent,
            "Accept": "application/json",
        }

        last_error: BaseException | None = None
        for attempt in range(1, self.settings.retry_attempts + 1):
            try:
                LOGGER.info(
                    "Riot request attempt=%s/%s region=%s method=%s path=%s",
                    attempt,
                    self.settings.retry_attempts,
                    normalized_region,
                    limiter_key,
                    clean_path,
                )
                async with self.rate_limiter.limit(normalized_region, limiter_key):
                    async with self.session.request(method, url, headers=headers) as response:
                        payload = await self._read_payload(response)
                        await self.rate_limiter.record_response(normalized_region, limiter_key, response.status)
                        LOGGER.info(
                            "Riot response status=%s region=%s method=%s path=%s",
                            response.status,
                            normalized_region,
                            limiter_key,
                            clean_path,
                        )

                        if response.status == 429:
                            retry_after = self._retry_after(response.headers)
                            cooldown = max(retry_after, self._retry_delay(attempt) * 3)
                            await self.rate_limiter.cooldown(normalized_region, limiter_key, cooldown)
                            last_error = RiotRateLimitError(response.status, "rate limited", payload)
                            await self._sleep_before_retry(attempt, cooldown)
                            continue

                        if response.status in {503, 504}:
                            last_error = RiotAPIError(response.status, "temporary Riot service error", payload)
                            await self._sleep_before_retry(attempt)
                            continue

                        if response.status >= 400:
                            message = self._extract_message(payload)
                            raise RiotAPIError(response.status, message, payload)

                        return payload
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                LOGGER.error(
                    "Non-HTTP Riot request failure for region=%s method=%s path=%s: %s",
                    normalized_region,
                    limiter_key,
                    clean_path,
                    exc,
                )
                raise RiotAPIError(0, f"network/client request failed: {exc}") from exc

        if isinstance(last_error, RiotRateLimitError):
            raise last_error
        if isinstance(last_error, RiotAPIError):
            raise last_error
        raise RiotAPIError(0, f"request failed after retries: {last_error}")

    async def get_summoner_by_puuid(self, platform_region: str, puuid: str) -> dict[str, Any]:
        return await self.request_json(
            platform_region,
            f"/lol/summoner/v4/summoners/by-puuid/{puuid}",
            method_key="summoner.by_puuid",
        )

    async def get_summoner_by_id(self, platform_region: str, encrypted_summoner_id: str) -> dict[str, Any]:
        return await self.request_json(
            platform_region,
            f"/lol/summoner/v4/summoners/{encrypted_summoner_id}",
            method_key="summoner.by_id",
        )

    async def get_ranked_entries_by_summoner_id(
        self,
        platform_region: str,
        encrypted_summoner_id: str,
    ) -> list[dict[str, Any]]:
        return await self.request_json(
            platform_region,
            f"/lol/league/v4/entries/by-summoner/{encrypted_summoner_id}",
            method_key="league.by_summoner",
        )

    async def get_high_tier_league(
        self,
        platform_region: str,
        queue: str = "RANKED_SOLO_5x5",
        tier: str = "CHALLENGER",
    ) -> dict[str, Any]:
        tier_path = tier.lower()
        if tier_path not in {"challenger", "grandmaster", "master"}:
            raise ValueError("High-tier leaderboard tier must be CHALLENGER, GRANDMASTER, or MASTER.")
        return await self.request_json(
            platform_region,
            f"/lol/league/v4/{tier_path}leagues/by-queue/{queue}",
            method_key=f"league.{tier_path}",
        )

    async def get_league_entries(
        self,
        platform_region: str,
        queue: str,
        tier: str,
        division: str,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        return await self.request_json(
            platform_region,
            f"/lol/league/v4/entries/{queue}/{tier}/{division}",
            params={"page": page},
            method_key="league.entries",
        )

    async def get_match_ids_by_puuid(
        self,
        routing_region: str,
        puuid: str,
        *,
        start: int = 0,
        count: int = 20,
        queue: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        match_type: str | None = None,
    ) -> list[str]:
        params: dict[str, Any] = {"start": start, "count": count}
        if queue is not None:
            params["queue"] = queue
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        if match_type is not None:
            params["type"] = match_type
        return await self.request_json(
            routing_region,
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            params=params,
            regional=True,
            method_key="match.ids",
        )

    async def get_match(self, routing_region: str, match_id: str) -> dict[str, Any]:
        return await self.request_json(
            routing_region,
            f"/lol/match/v5/matches/{match_id}",
            regional=True,
            method_key="match.detail",
        )

    async def get_timeline(self, routing_region: str, match_id: str) -> dict[str, Any]:
        return await self.request_json(
            routing_region,
            f"/lol/match/v5/matches/{match_id}/timeline",
            regional=True,
            method_key="match.timeline",
        )

    async def _sleep_before_retry(self, attempt: int, retry_after: float | None = None) -> None:
        if attempt >= self.settings.retry_attempts:
            return
        delay = retry_after if retry_after is not None else self._retry_delay(attempt)
        LOGGER.warning("Retrying Riot request after %.2f seconds.", delay)
        await asyncio.sleep(delay)

    def _retry_delay(self, attempt: int) -> float:
        return compute_backoff(
            attempt=attempt,
            base_delay=self.settings.retry_base_delay_seconds,
            max_delay=self.settings.retry_max_delay_seconds,
        )

    @staticmethod
    async def _read_payload(response: aiohttp.ClientResponse) -> Any:
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return await response.json(content_type=None)
        text = await response.text()
        return {"text": text} if text else {}

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> float:
        value = headers.get("Retry-After")
        if value is None:
            return 1.0
        try:
            return max(float(value), 0.1)
        except ValueError:
            return 1.0

    @staticmethod
    def _extract_message(payload: Any) -> str:
        if isinstance(payload, dict):
            status = payload.get("status")
            if isinstance(status, dict) and status.get("message"):
                return str(status["message"])
            if payload.get("message"):
                return str(payload["message"])
            if payload.get("text"):
                return str(payload["text"])
        return "request failed"

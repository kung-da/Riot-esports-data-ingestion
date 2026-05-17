"""Application settings loaded from `.env` via pydantic-settings."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LOGGER = logging.getLogger(__name__)

PLATFORM_TO_ROUTING_REGION: dict[str, str] = {
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "na1": "americas",
    "oc1": "sea",
    "ph2": "sea",
    "sg2": "sea",
    "th2": "sea",
    "tw2": "sea",
    "vn2": "sea",
    "eun1": "europe",
    "euw1": "europe",
    "ru": "europe",
    "tr1": "europe",
    "jp1": "asia",
    "kr": "asia",
}


class Settings(BaseSettings):
    """Runtime configuration with safe defaults for Riot development keys."""

    riot_api_key: SecretStr = Field(default=SecretStr(""), alias="RIOT_API_KEY")
    default_platform_region: str = Field(default="vn2", alias="DEFAULT_PLATFORM_REGION")
    default_routing_region: str | None = Field(default=None, alias="DEFAULT_ROUTING_REGION")
    regions: str = Field(default="vn2", alias="REGIONS")

    request_timeout_seconds: float = Field(default=30.0, alias="REQUEST_TIMEOUT_SECONDS")
    max_concurrency: int = Field(default=6, alias="MAX_CONCURRENCY")
    requests_per_minute: int = Field(default=40, alias="REQUESTS_PER_MINUTE")
    method_requests_per_minute: int = Field(default=20, alias="METHOD_REQUESTS_PER_MINUTE")
    request_sleep_min_seconds: float = Field(default=1.2, alias="REQUEST_SLEEP_MIN_SECONDS")
    request_sleep_max_seconds: float = Field(default=1.8, alias="REQUEST_SLEEP_MAX_SECONDS")

    retry_attempts: int = Field(default=5, alias="RETRY_ATTEMPTS")
    retry_base_delay_seconds: float = Field(default=1.0, alias="RETRY_BASE_DELAY_SECONDS")
    retry_max_delay_seconds: float = Field(default=60.0, alias="RETRY_MAX_DELAY_SECONDS")
    circuit_breaker_failure_threshold: int = Field(default=5, alias="CIRCUIT_BREAKER_FAILURE_THRESHOLD")
    circuit_breaker_cooldown_seconds: float = Field(default=300.0, alias="CIRCUIT_BREAKER_COOLDOWN_SECONDS")
    timeline_extra_delay_seconds: float = Field(default=2.0, alias="TIMELINE_EXTRA_DELAY_SECONDS")

    user_agent: str = Field(
        default="RiotDataCrawler/1.0 (Personal Educational Project)",
        alias="USER_AGENT",
    )
    default_match_count: int = Field(default=10, alias="DEFAULT_MATCH_COUNT")
    output_dir: Path = Field(default=Path("output"), alias="OUTPUT_DIR")
    checkpoint_dir: Path = Field(default=Path("checkpoints"), alias="CHECKPOINT_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("default_platform_region")
    @classmethod
    def normalize_platform_region(cls, value: str) -> str:
        region = value.lower().strip()
        if region not in PLATFORM_TO_ROUTING_REGION:
            supported = ", ".join(sorted(PLATFORM_TO_ROUTING_REGION))
            raise ValueError(f"Unsupported platform region '{value}'. Supported regions: {supported}")
        return region

    @field_validator("default_routing_region")
    @classmethod
    def normalize_optional_routing_region(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        region = value.lower().strip()
        if region not in {"americas", "asia", "europe", "sea"}:
            raise ValueError("Routing region must be one of americas, asia, europe, or sea.")
        return region

    @field_validator("regions")
    @classmethod
    def normalize_regions(cls, value: str) -> str:
        regions = [item.strip().lower() for item in value.split(",") if item.strip()]
        return ",".join(regions or ["vn2"])

    @field_validator("max_concurrency")
    @classmethod
    def cap_max_concurrency(cls, value: int) -> int:
        if value > 8:
            LOGGER.warning("MAX_CONCURRENCY=%s is too high for safe Riot crawling; capping to 8.", value)
            return 8
        return max(value, 1)

    @field_validator("requests_per_minute")
    @classmethod
    def cap_requests_per_minute(cls, value: int) -> int:
        if value > 50:
            LOGGER.warning("REQUESTS_PER_MINUTE=%s is risky for a development key; capping to 50.", value)
            return 50
        return max(value, 1)

    @field_validator("method_requests_per_minute")
    @classmethod
    def cap_method_requests_per_minute(cls, value: int) -> int:
        if value > 30:
            LOGGER.warning("METHOD_REQUESTS_PER_MINUTE=%s is risky; capping to 30.", value)
            return 30
        return max(value, 1)

    @model_validator(mode="after")
    def validate_safety_bounds(self) -> "Settings":
        if self.request_sleep_min_seconds < 1.2:
            LOGGER.warning("REQUEST_SLEEP_MIN_SECONDS=%s is too low; raising to 1.2.", self.request_sleep_min_seconds)
            self.request_sleep_min_seconds = 1.2
        if self.request_sleep_max_seconds < self.request_sleep_min_seconds:
            LOGGER.warning("REQUEST_SLEEP_MAX_SECONDS is below min; raising it to match REQUEST_SLEEP_MIN_SECONDS.")
            self.request_sleep_max_seconds = self.request_sleep_min_seconds
        if self.request_sleep_max_seconds > 5.0:
            LOGGER.warning("REQUEST_SLEEP_MAX_SECONDS=%s is high but safe; keeping it.", self.request_sleep_max_seconds)
        if self.retry_attempts > 5:
            LOGGER.warning("RETRY_ATTEMPTS=%s is high; capping to 5.", self.retry_attempts)
            self.retry_attempts = 5
        if self.retry_base_delay_seconds < 1.0:
            LOGGER.warning("RETRY_BASE_DELAY_SECONDS=%s is too low; raising to 1.0.", self.retry_base_delay_seconds)
            self.retry_base_delay_seconds = 1.0
        if self.timeline_extra_delay_seconds < 2.0:
            LOGGER.warning("TIMELINE_EXTRA_DELAY_SECONDS=%s is low; raising to 2.0.", self.timeline_extra_delay_seconds)
            self.timeline_extra_delay_seconds = 2.0
        return self

    @property
    def region_list(self) -> list[str]:
        """Configured platform regions as a normalized list."""

        return [item.strip().lower() for item in self.regions.split(",") if item.strip()]

    def routing_region_for_platform(self, platform_region: str | None = None) -> str:
        """Return Riot Match-V5 routing region for a platform shard."""

        if self.default_routing_region and platform_region is None:
            return self.default_routing_region
        platform = (platform_region or self.default_platform_region).lower()
        try:
            return PLATFORM_TO_ROUTING_REGION[platform]
        except KeyError as exc:
            raise ValueError(f"Unsupported platform region '{platform}'.") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process."""

    return Settings()

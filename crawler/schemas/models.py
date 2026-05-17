"""Pydantic models for validating Riot API payloads without dropping fields."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RiotBaseModel(BaseModel):
    """Base model that preserves unknown Riot fields for raw-data fidelity."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SummonerDTO(RiotBaseModel):
    id: str
    account_id: str | None = Field(default=None, alias="accountId")
    puuid: str
    name: str | None = None
    profile_icon_id: int | None = Field(default=None, alias="profileIconId")
    revision_date: int | None = Field(default=None, alias="revisionDate")
    summoner_level: int | None = Field(default=None, alias="summonerLevel")


class MiniSeriesDTO(RiotBaseModel):
    losses: int | None = None
    progress: str | None = None
    target: int | None = None
    wins: int | None = None


class LeagueEntryDTO(RiotBaseModel):
    league_id: str | None = Field(default=None, alias="leagueId")
    summoner_id: str = Field(alias="summonerId")
    puuid: str | None = None
    queue_type: str = Field(alias="queueType")
    tier: str
    rank: str
    league_points: int = Field(alias="leaguePoints")
    wins: int
    losses: int
    veteran: bool
    inactive: bool
    fresh_blood: bool = Field(alias="freshBlood")
    hot_streak: bool = Field(alias="hotStreak")
    mini_series: MiniSeriesDTO | None = Field(default=None, alias="miniSeries")


class MetadataDTO(RiotBaseModel):
    data_version: str | None = Field(default=None, alias="dataVersion")
    match_id: str = Field(alias="matchId")
    participants: list[str] = Field(default_factory=list)


class ParticipantDTO(RiotBaseModel):
    participant_id: int = Field(alias="participantId")
    puuid: str
    summoner_name: str | None = Field(default=None, alias="summonerName")
    riot_id_game_name: str | None = Field(default=None, alias="riotIdGameName")
    riot_id_tagline: str | None = Field(default=None, alias="riotIdTagline")
    champion_id: int = Field(alias="championId")
    champion_name: str = Field(alias="championName")
    kills: int
    deaths: int
    assists: int
    total_damage_dealt_to_champions: int = Field(alias="totalDamageDealtToChampions")
    gold_earned: int = Field(alias="goldEarned")
    vision_score: int = Field(alias="visionScore")
    win: bool
    team_id: int = Field(alias="teamId")
    team_position: str | None = Field(default=None, alias="teamPosition")
    individual_position: str | None = Field(default=None, alias="individualPosition")


class ObjectiveDTO(RiotBaseModel):
    first: bool | None = None
    kills: int | None = None


class ObjectivesDTO(RiotBaseModel):
    baron: ObjectiveDTO | None = None
    champion: ObjectiveDTO | None = None
    dragon: ObjectiveDTO | None = None
    inhibitor: ObjectiveDTO | None = None
    rift_herald: ObjectiveDTO | None = Field(default=None, alias="riftHerald")
    tower: ObjectiveDTO | None = None


class TeamDTO(RiotBaseModel):
    team_id: int = Field(alias="teamId")
    win: bool
    objectives: ObjectivesDTO | None = None


class MatchInfoDTO(RiotBaseModel):
    game_creation: int = Field(alias="gameCreation")
    game_duration: int = Field(alias="gameDuration")
    game_end_timestamp: int | None = Field(default=None, alias="gameEndTimestamp")
    game_id: int | None = Field(default=None, alias="gameId")
    game_mode: str | None = Field(default=None, alias="gameMode")
    game_name: str | None = Field(default=None, alias="gameName")
    game_start_timestamp: int | None = Field(default=None, alias="gameStartTimestamp")
    game_type: str | None = Field(default=None, alias="gameType")
    game_version: str | None = Field(default=None, alias="gameVersion")
    map_id: int = Field(alias="mapId")
    platform_id: str | None = Field(default=None, alias="platformId")
    queue_id: int = Field(alias="queueId")
    tournament_code: str | None = Field(default=None, alias="tournamentCode")
    participants: list[ParticipantDTO] = Field(default_factory=list)
    teams: list[TeamDTO] = Field(default_factory=list)


class MatchDTO(RiotBaseModel):
    metadata: MetadataDTO
    info: MatchInfoDTO


class TimelineMetadataDTO(RiotBaseModel):
    data_version: str | None = Field(default=None, alias="dataVersion")
    match_id: str = Field(alias="matchId")
    participants: list[str] = Field(default_factory=list)


class TimelineInfoDTO(RiotBaseModel):
    frame_interval: int | None = Field(default=None, alias="frameInterval")
    frames: list[dict[str, Any]] = Field(default_factory=list)


class TimelineDTO(RiotBaseModel):
    metadata: TimelineMetadataDTO
    info: TimelineInfoDTO

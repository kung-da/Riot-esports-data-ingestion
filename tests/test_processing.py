from crawler.schemas.models import MatchDTO, SummonerDTO, TimelineDTO
from crawler.services.match_service import MatchService
from crawler.services.timeline_service import TimelineService


def test_match_processing_outputs_match_and_player_rows() -> None:
    payload = {
        "metadata": {"dataVersion": "2", "matchId": "VN2_1", "participants": ["p1"]},
        "info": {
            "gameCreation": 1710000000000,
            "gameDuration": 1800,
            "gameEndTimestamp": 1710001800000,
            "gameStartTimestamp": 1710000000000,
            "gameVersion": "14.1.1",
            "mapId": 11,
            "platformId": "VN2",
            "queueId": 420,
            "gameMode": "CLASSIC",
            "gameType": "MATCHED_GAME",
            "teams": [{"teamId": 100, "win": True}, {"teamId": 200, "win": False}],
            "participants": [
                {
                    "participantId": 1,
                    "puuid": "p1",
                    "summonerName": "Player One",
                    "riotIdGameName": "Player",
                    "riotIdTagline": "VN2",
                    "championId": 103,
                    "championName": "Ahri",
                    "kills": 10,
                    "deaths": 2,
                    "assists": 8,
                    "totalDamageDealtToChampions": 25000,
                    "goldEarned": 13000,
                    "visionScore": 22,
                    "win": True,
                    "teamId": 100,
                    "teamPosition": "MIDDLE",
                    "individualPosition": "MIDDLE",
                }
            ],
        },
    }

    match = MatchDTO.model_validate(payload)

    assert MatchService.to_match_record(match)["winning_team"] == 100
    player = MatchService.to_player_records(match)[0]
    assert player["match_id"] == "VN2_1"
    assert player["kda"] == 9.0
    assert player["position"] == "MIDDLE"


def test_timeline_processing_expands_kill_death_assist_rows() -> None:
    payload = {
        "metadata": {"dataVersion": "2", "matchId": "VN2_1", "participants": ["p1", "p2", "p3"]},
        "info": {
            "frameInterval": 60000,
            "frames": [
                {
                    "timestamp": 120000,
                    "events": [
                        {
                            "type": "CHAMPION_KILL",
                            "timestamp": 120000,
                            "killerId": 1,
                            "victimId": 2,
                            "assistingParticipantIds": [3],
                            "position": {"x": 1000, "y": 2000},
                        }
                    ],
                }
            ],
        },
    }

    timeline = TimelineDTO.model_validate(payload)
    records = TimelineService.to_event_records(timeline)

    assert [record["event_category"] for record in records] == ["kill", "death", "assist"]
    assert records[0]["position_x"] == 1000
    assert records[2]["participant_id"] == 3


def test_summoner_payload_without_encrypted_id_is_valid() -> None:
    payload = {
        "puuid": "sample-puuid",
        "profileIconId": 1,
        "revisionDate": 1710000000000,
        "summonerLevel": 71,
    }

    summoner = SummonerDTO.model_validate(payload)

    assert summoner.id is None
    assert summoner.puuid == "sample-puuid"

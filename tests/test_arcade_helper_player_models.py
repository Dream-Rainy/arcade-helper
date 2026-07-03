from __future__ import annotations

from arcade_helper.players import (
    ChunithmBests,
    ChunithmCollection,
    ChunithmScore,
    ChunithmTrend,
    MaimaiBests,
    MaimaiCollection,
    MaimaiScore,
    MaimaiTrend,
)


def test_maimai_score_accepts_lxns_aliases() -> None:
    score = MaimaiScore.model_validate(
        {
            "song_id": 123,
            "title": "Test Song",
            "level_index": 4,
            "dxScore": 1999,
            "dxStar": 5,
            "dxRating": 320.5,
            "playedAt": "2026-01-01T10:00:00Z",
            "lastPlayedTime": "2026-01-02T10:00:00Z",
            "achievements": 100.1,
            "type": "dx",
        }
    )

    assert score.id == 123
    assert score.song_name == "Test Song"
    assert score.level_index == 4
    assert score.dx_score == 1999
    assert score.dx_star == 5
    assert score.dx_rating == 320.5
    assert score.play_time == "2026-01-01T10:00:00Z"
    assert score.last_played_time == "2026-01-02T10:00:00Z"


def test_chunithm_score_flattens_oauth_song_payload() -> None:
    score = ChunithmScore.model_validate(
        {
            "song": {"id": 55, "title": "Nested Song"},
            "levelIndex": 3,
            "score": 1009000,
            "playedAt": "2026-01-01T10:00:00Z",
        }
    )

    assert score.id == 55
    assert score.song_name == "Nested Song"
    assert score.level_index == 3
    assert score.score == 1009000
    assert score.play_time == "2026-01-01T10:00:00Z"


def test_maimai_composite_player_models_parse_nested_payloads() -> None:
    bests = MaimaiBests.model_validate(
        {
            "standard_total": 10000,
            "dx_total": 12000,
            "standard": [{"id": 1, "song_name": "Std", "achievements": 99.5}],
            "dx": [{"song_id": 2, "title": "DX", "type": "dx", "achievements": 100.5}],
        }
    )
    trend = MaimaiTrend.model_validate({"trend": [{"total": 22000, "standard": 10000, "dx": 12000, "date": "2026-01-01"}]})
    collection = MaimaiCollection.model_validate(
        {
            "id": 7,
            "name": "Frame",
            "required": [
                {
                    "rate": "sss",
                    "fc": "ap",
                    "songs": [{"id": 1, "title": "Std", "type": "standard", "completed_difficulties": [3]}],
                }
            ],
        }
    )

    assert bests.standard_total == 10000
    assert bests.dx[0].id == 2
    assert bests.dx[0].song_name == "DX"
    assert trend.trend[0].total == 22000
    assert collection.required is not None
    assert collection.required[0].songs is not None
    assert collection.required[0].songs[0].completed_difficulties == [3]


def test_chunithm_composite_player_models_parse_nested_payloads() -> None:
    bests = ChunithmBests.model_validate(
        {
            "rating": 15.25,
            "bests_rating": 15.1,
            "selections_rating": 15.0,
            "bests": [{"song": {"id": 55, "title": "Best"}, "levelIndex": 3, "score": 1009000}],
        }
    )
    trend = ChunithmTrend.model_validate(
        {
            "trend": [
                {
                    "rating": 15.25,
                    "bests_rating": 15.1,
                    "selections_rating": 15.0,
                    "new_bests_rating": 15.2,
                    "date": "2026-01-01",
                }
            ]
        }
    )
    collection = ChunithmCollection.model_validate(
        {
            "id": 9,
            "name": "Title",
            "level": 20,
            "required": [
                {
                    "rank": "sss",
                    "full_combo": "alljustice",
                    "songs": [{"id": 55, "title": "Best", "completed": True}],
                }
            ],
        }
    )

    assert bests.rating == 15.25
    assert bests.bests[0].id == 55
    assert bests.bests[0].song_name == "Best"
    assert trend.trend[0].new_bests_rating == 15.2
    assert collection.required is not None
    assert collection.required[0].songs is not None
    assert collection.required[0].songs[0].completed is True

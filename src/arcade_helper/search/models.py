"""Song search result models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from arcade_helper.core.song import SongData


class MatchType(Enum):
    """Song search match strategy."""

    EXACT_ID = "exact_id"
    EXACT_TITLE = "exact_title"
    EXACT_ALIAS = "exact_alias"
    FUZZY_TITLE = "fuzzy_title"
    FUZZY_ALIAS = "fuzzy_alias"
    PINYIN_INITIALS = "pinyin_initials"
    PINYIN_FULL = "pinyin_full"
    SIMPLIFIED = "simplified"


@dataclass(frozen=True)
class SongQueryResult:
    """One song search match."""

    song_id: int
    title: str
    match_type: MatchType
    match_score: float
    matched_text: str
    song_data: SongData


__all__ = ["MatchType", "SongQueryResult"]

"""Provider-independent song search."""

from .models import MatchType, SongQueryResult
from .song_query import (
    SongSearchNotConfiguredError,
    SongSearchRepository,
    SongSearchService,
    get_song_aliases,
    get_song_data,
    get_song_data_from_id,
    get_song_with_difficulty,
    invalidate_alias_cache,
    query_song_by_alias_exact,
    query_song_by_id,
    query_song_by_title_exact,
    query_song_fuzzy,
    search_song,
)

__all__ = [
    "MatchType",
    "SongQueryResult",
    "SongSearchNotConfiguredError",
    "SongSearchRepository",
    "SongSearchService",
    "get_song_aliases",
    "get_song_data",
    "get_song_data_from_id",
    "get_song_with_difficulty",
    "invalidate_alias_cache",
    "query_song_by_alias_exact",
    "query_song_by_id",
    "query_song_by_title_exact",
    "query_song_fuzzy",
    "search_song",
]

"""Public alias values and conservative text normalization."""

import re
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict
from arcade_helper.core.errors import ArcadeHelperError
from arcade_helper.core.song_identity import normalize_width


class AliasError(ArcadeHelperError):
    pass


class AliasPermissionError(AliasError):
    pass


class AliasConflictError(AliasError):
    pass


def alias_key(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("Alias must contain 1–256 characters")
    text = normalize_width(text)
    if not text or len(text) > 256:
        raise ValueError("Normalized alias must contain 1–256 characters")
    return " ".join(text.casefold().split()) or text.casefold()


def identity_text(text: str) -> str:
    text = normalize_width(text).casefold()
    return " ".join(text.split()) or text


class AliasObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_key: str
    text: str
    record_key: str = ""
    kind: Literal["title", "alias"] = "alias"
    scope: Literal["work", "game_song"] = "work"


@dataclass(frozen=True)
class AliasCandidate:
    game_song_id: int
    work_id: str
    game_code: str
    title: str
    matched_text: str
    match_type: str
    score: float
    source_games: tuple[str, ...] = ()
    evidence_ids: tuple[int, ...] = ()
    preferred_game: str | None = None
    shared: bool = False
    scope: str = "game_song"
    chart_variant: tuple[str, ...] | None = None


@dataclass(frozen=True)
class AliasResolution:
    resolution: str
    inferred_game: str | None
    inference_reason: str
    candidates: tuple[AliasCandidate, ...]
    unavailable_games: tuple[str, ...] = ()


def merge_alias_resolutions(results, *, game_code=None):
    candidates = {}
    for result in results:
        for candidate in result.candidates:
            key = (candidate.game_code, candidate.game_song_id)
            rank = (candidate.match_type.startswith("exact_"), candidate.score)
            old = candidates.get(key)
            if old is None or rank > (old.match_type.startswith("exact_"), old.score):
                candidates[key] = candidate
    exact = any(c.match_type.startswith("exact_") for c in candidates.values())
    rows = [c for c in candidates.values() if not exact or c.match_type.startswith("exact_")]
    preferences = {}
    for work in {c.work_id for c in rows}:
        choices = {c.preferred_game for c in rows if c.work_id == work}
        preferences[work] = next(iter(choices)) if len(choices) == 1 else None
    rows = [replace(c, preferred_game=preferences[c.work_id]) for c in rows]
    rows.sort(key=lambda c: (c.game_code != c.preferred_game, -c.score, c.game_code, c.game_song_id))
    inferred = next(iter(preferences.values())) if exact and len(preferences) == 1 else None
    return AliasResolution("explicit" if game_code else "inferred" if inferred else "ambiguous" if rows else "unknown",
        game_code or inferred, "explicit_game" if game_code else "alias_origin_or_only_game" if inferred else "candidates_require_choice",
        tuple(rows), tuple(sorted({game for result in results for game in result.unavailable_games})))


def has_music_variant(title: str, payload: dict) -> bool:
    return bool(payload.get("music_variant") or re.search(
        r"\b(remix|mix|edit|arrange|arrangement|short|long|version|ver\.)\b", title, re.I,
    ))

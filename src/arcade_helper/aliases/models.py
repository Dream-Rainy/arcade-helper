"""Public alias values and conservative text normalization."""

import re
from dataclasses import dataclass
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


def has_music_variant(title: str, payload: dict) -> bool:
    return bool(payload.get("music_variant") or re.search(
        r"\b(remix|mix|edit|arrange|arrangement|short|long|version|ver\.)\b", title, re.I,
    ))

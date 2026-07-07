"""Local JSONL embedding index helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from typing import Any

from .constants import EMBEDDING_DEFAULT_THRESHOLD
from .normalization import _normalize_for_matching, _normalize_game_code


@dataclass(frozen=True)
class _EmbeddingIndex:
    query_vectors_by_game: dict[str, dict[str, tuple[float, ...]]]
    song_vectors_by_game: dict[str, dict[int, tuple[float, ...]]]


_embedding_index_cache_by_path: dict[str, tuple[tuple[int, int] | None, _EmbeddingIndex]] = {}


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _embedding_path() -> Path | None:
    configured = os.getenv("SONG_SEARCH_EMBEDDING_PATH", "").strip()
    return Path(configured) if configured else None


def _embedding_threshold() -> float:
    try:
        return float(os.getenv("SONG_SEARCH_EMBEDDING_THRESHOLD", EMBEDDING_DEFAULT_THRESHOLD))
    except ValueError:
        return EMBEDDING_DEFAULT_THRESHOLD


def _file_token(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _vector_from_row(row: dict[str, Any]) -> tuple[float, ...] | None:
    raw_vector = row.get("embedding") or row.get("vector")
    if not isinstance(raw_vector, list) or not raw_vector:
        return None
    try:
        vector = tuple(float(item) for item in raw_vector)
    except (TypeError, ValueError):
        return None
    return vector if any(value != 0.0 for value in vector) else None


def _load_embedding_index(path: Path) -> _EmbeddingIndex:
    path_key = str(path.resolve())
    token = _file_token(path)
    cached = _embedding_index_cache_by_path.get(path_key)
    if cached is not None and cached[0] == token:
        return cached[1]

    query_vectors_by_game: dict[str, dict[str, tuple[float, ...]]] = {}
    song_vectors_by_game: dict[str, dict[int, tuple[float, ...]]] = {}
    if token is not None:
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            vector = _vector_from_row(row)
            if vector is None:
                continue
            game = _normalize_game_code(str(row.get("game") or "maimai"))
            query = str(row.get("query") or "").strip()
            if query:
                query_vectors_by_game.setdefault(game, {})[_normalize_for_matching(query)] = vector
            if row.get("song_id") is not None:
                try:
                    song_id = int(row["song_id"])
                except (TypeError, ValueError):
                    continue
                song_vectors_by_game.setdefault(game, {})[song_id] = vector

    index = _EmbeddingIndex(query_vectors_by_game, song_vectors_by_game)
    _embedding_index_cache_by_path[path_key] = (token, index)
    return index


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)

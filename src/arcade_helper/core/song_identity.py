"""Conservative song identity and leading chart-marker parsing."""

from __future__ import annotations

import re
import unicodedata

_MARKER = re.compile(r"\[([^\[\]\r\n]+)\]")


def normalize_width(text: str) -> str:
    """Use the same Unicode width normalization for storage and matching."""
    return unicodedata.normalize("NFKC", text)


def parse_chart_title(title: str, *, literal: bool = False) -> tuple[str, tuple[str, ...]]:
    """Return the base title and ordered markers; never change the input title.

    ``literal`` handles a source-confirmed title whose brackets are music text.
    Invalid/incomplete prefixes are kept entirely literal.
    """
    title = normalize_width(title)
    original = title.strip() or title
    remaining = original
    markers: list[str] = []
    if literal:
        return original, ()
    while remaining.startswith("["):
        match = _MARKER.match(remaining)
        if match is None or not match[1].strip():
            return original, ()
        markers.append(unicodedata.normalize("NFC", match[1].strip()))
        remaining = remaining[match.end():].lstrip()
    return (remaining, tuple(markers)) if remaining else (original, ())


def song_identity_material(
    title: str, artist: str | None, chart_variant: tuple[str, ...] | None,
    music_variant: str | None = None,
) -> dict:
    """Source-local continuity material, not proof of cross-game equivalence."""
    def clean(value: str | None) -> str | None:
        return unicodedata.normalize("NFC", value.strip()) if value is not None else None

    return {
        "title": normalize_width(title).strip() or normalize_width(title),
        "artist": clean(artist) or None,
        "chart_variant": [clean(mark) for mark in chart_variant] if chart_variant is not None else None,
        "music_variant": clean(music_variant),
    }

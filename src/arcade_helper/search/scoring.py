"""Scoring helpers for song search."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import math

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from .constants import (
    BM25_B,
    BM25_K1,
    CJK_DICE_WEIGHT,
    CJK_PARTIAL_WEIGHT,
    CJK_RATIO_WEIGHT,
    COVERAGE_HARD_CUTOFF,
    COVERAGE_SOFT_CUTOFF,
    SHORT_QUERY_LENGTH,
)
from .index import _SearchDocument
from .normalization import (
    _ascii_tokens,
    _has_cjk,
    _is_cjk_char,
    _is_meaningful_ascii_token,
    _meaningful_ascii_tokens,
    _normalize_for_matching,
    _search_terms,
)

BM25_MIN_QUERY_COVERAGE = 0.6


def is_short_song_query(query: str | int) -> bool:
    """Whether a normalized query needs text-only matching (1–4 characters)."""
    return 0 < len(_normalize_for_matching(str(query))) <= 4


def _short_text_score(query: str, candidate: str) -> float:
    normalized = _normalize_for_matching(candidate)
    if query == normalized:
        return 100.0
    if len(query) <= 2:
        return 0.0
    targets = _meaningful_ascii_tokens(candidate) if query.isascii() and query.isalnum() else [normalized]
    for index, target in enumerate(targets):
        if query == target:
            return 97.0 if index == 0 else 93.0
    if len(query) >= 3 and any(
        abs(len(query) - len(target)) <= 1 and Levenshtein.distance(query, target, score_cutoff=1) <= 1
        for target in targets
    ):
        return 95.0
    return 0.0


def _literal_query_covered(query: str, candidate: str) -> bool:
    targets = _ascii_tokens(candidate)
    return all(any(
        term == target
        or (len(term) > 4 and target.startswith(term))
        or (len(term) >= 3 and Levenshtein.distance(term, target, score_cutoff=1) <= 1)
        for target in targets
    ) for term in _meaningful_ascii_tokens(query))


def _bm25_query_terms(query: str) -> list[str]:
    return _search_terms(query)


def _has_multiple_search_terms(query: str) -> bool:
    return len(set(_bm25_query_terms(query))) >= 2


def _bm25_query_coverage(query_counts: Mapping[str, int], document: _SearchDocument) -> float:
    # Keep unseen terms in the denominator so a rare word cannot validate an unrelated sentence.
    if any(term.isascii() and document.bm25_counts.get(term, 0) <= 0 for term in query_counts):
        return 0.0
    total = sum(query_counts.values())
    matched = sum(count for term, count in query_counts.items() if document.bm25_counts.get(term, 0) > 0)
    if not total or matched / total < BM25_MIN_QUERY_COVERAGE:
        return 0.0
    artist_terms = set(_search_terms(document.artist))
    best = 0.0
    for text in (document.title, *document.aliases):
        terms = artist_terms.union(_search_terms(text))
        if any(term.isascii() and term not in terms for term in query_counts):
            continue
        coverage = sum(count for term, count in query_counts.items() if term in terms) / total
        best = max(best, coverage)
        if best == 1.0:
            break
    return best


def _bm25_score(
    query_counts: Mapping[str, int],
    document: _SearchDocument,
    *,
    total_docs: int,
    doc_freq: Counter[str],
    avg_len: float,
) -> float:
    if not query_counts or document.bm25_len <= 0 or total_docs <= 0:
        return 0.0

    score = 0.0
    for term, query_freq in query_counts.items():
        freq = document.bm25_counts.get(term, 0)
        if freq <= 0:
            continue
        df = doc_freq.get(term, 0)
        idf = math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5))
        norm = freq + BM25_K1 * (1.0 - BM25_B + BM25_B * (document.bm25_len / max(avg_len, 1.0)))
        score += query_freq * idf * ((freq * (BM25_K1 + 1.0)) / norm)
    return score


def _is_short_ascii_query(query_norm: str) -> bool:
    return (
        query_norm.isascii()
        and not _has_cjk(query_norm)
        and 3 <= len(query_norm) <= 4
        and _is_meaningful_ascii_token(query_norm)
    )


def _is_compact_cross_token_match(query_norm: str, candidate: str) -> bool:
    if not _is_short_ascii_query(query_norm):
        return False
    tokens = _ascii_tokens(candidate)
    if query_norm in tokens:
        return False
    return query_norm in _normalize_for_matching(candidate)


def _cap_weak_compact_match_score(score: float, query_norm: str, candidate: str) -> float:
    if _is_compact_cross_token_match(query_norm, candidate):
        return min(score, 70.0)
    return score


def _get_fuzzy_scorer(query: str):
    if _has_cjk(query):
        if len(query) <= SHORT_QUERY_LENGTH:
            return fuzz.ratio
        return fuzz.partial_ratio
    return fuzz.WRatio


def _get_symmetric_coverage(query: str, matched_text: str) -> float:
    query_len = len(query)
    matched_len = len(matched_text)
    if query_len == 0 or matched_len == 0:
        return 0.0
    longer = max(query_len, matched_len)
    shorter = min(query_len, matched_len)
    return shorter / longer


def _apply_coverage_guard(score: float, query: str, matched_text: str) -> float:
    query_len = len(query)
    matched_len = len(matched_text)

    if matched_len == 0:
        return 0.0

    if query_len == 1:
        return score if query.lower() == matched_text.lower() else 0.0

    coverage = _get_symmetric_coverage(query, matched_text)

    if coverage < COVERAGE_HARD_CUTOFF:
        return 0.0

    if coverage < COVERAGE_SOFT_CUTOFF:
        return score * (coverage / COVERAGE_SOFT_CUTOFF)

    return score


def _cjk_bigram_dice(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if len(a) == 1 and len(b) == 1:
        return 100.0 if a == b else 0.0
    if len(a) < 2 or len(b) < 2:
        return 50.0 if a in b or b in a else 0.0

    a_bigrams = [a[i:i + 2] for i in range(len(a) - 1)]
    b_bigrams = [b[i:i + 2] for i in range(len(b) - 1)]
    inter = Counter(a_bigrams) & Counter(b_bigrams)
    inter_count = sum(inter.values())
    if inter_count == 0:
        return 0.0
    return (2.0 * inter_count / (len(a_bigrams) + len(b_bigrams))) * 100.0


def _compute_composite_score(query: str, candidate: str, raw_score: float) -> tuple[float, float, float, float]:
    if _has_cjk(query) or _has_cjk(candidate):
        ratio = fuzz.ratio(query, candidate)
        partial = fuzz.partial_ratio(query, candidate)
        dice = _cjk_bigram_dice(query, candidate)
        composite = (
            CJK_RATIO_WEIGHT * ratio
            + CJK_PARTIAL_WEIGHT * partial
            + CJK_DICE_WEIGHT * dice
        )
        return composite, ratio, partial, dice
    return raw_score, raw_score, raw_score, 0.0


def _alias_ambiguity_penalty(alias_norm: str, freq: int) -> float:
    alias_len = len(alias_norm)
    if alias_len <= 1:
        return 0.0
    penalty = 1.0
    if alias_len == 2:
        penalty *= 0.75
    if freq >= 3:
        penalty *= 1.0 / (1.0 + 0.25 * (freq - 1))
    return penalty


def _get_effective_threshold(query_norm: str, base_threshold: float) -> float:
    query_len = len(query_norm)
    if query_len <= 1:
        return max(base_threshold, 95.0)
    if query_len == 2:
        return max(base_threshold, 92.0)
    if query_len <= 4:
        return max(base_threshold, 86.0)
    return base_threshold

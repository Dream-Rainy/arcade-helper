"""Search tuning constants."""

FUZZY_THRESHOLD = 80
MAX_RESULTS = 10

TITLE_SCORE_BOOST = 1.15
ALIAS_SCORE_PENALTY = 0.85

SHORT_QUERY_LENGTH = 2
SHORT_QUERY_THRESHOLD_BOOST = 10

COVERAGE_HARD_CUTOFF = 0.15
COVERAGE_SOFT_CUTOFF = 0.40

CJK_RATIO_WEIGHT = 0.45
CJK_PARTIAL_WEIGHT = 0.20
CJK_DICE_WEIGHT = 0.35
ALIAS_CACHE_TTL_SECONDS = 300.0
BM25_TOP_CANDIDATES = 50
BM25_K1 = 1.5
BM25_B = 0.75
EMBEDDING_DEFAULT_THRESHOLD = 80.0

ASCII_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

"""Text normalisation shared by matcher and poller."""

from __future__ import annotations

import unicodedata


def normalize(text: str) -> str:
    """Return a comparison-friendly form of *text*.

    NFKC folds full-width characters onto their ASCII equivalents;
    lowercasing removes case sensitivity.  Traditional/Simplified
    conversion is deliberately not performed.
    """
    return unicodedata.normalize("NFKC", text).lower()

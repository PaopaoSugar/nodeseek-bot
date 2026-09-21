"""Keyword matching."""

from __future__ import annotations

from nodeseek_bot.normalize import normalize

MAX_KEYWORD_CHARS = 64


class RuleLimitExceeded(Exception):
    """Raised when a user already holds the maximum number of rules."""


def expand_rules(keyword_raw: str) -> str | None:
    """Normalise user input into a matchable rule.

    Returns ``None`` when the input is blank or implausibly long.
    Terms are whitespace separated and combined with AND.
    """
    stripped = keyword_raw.strip()
    if not stripped or len(stripped) > MAX_KEYWORD_CHARS:
        return None
    terms = normalize(stripped).split()
    if not terms:
        return None
    return " ".join(terms)


def matches(title_norm: str, keyword_norm: str) -> bool:
    """True when every term of *keyword_norm* occurs in *title_norm*."""
    terms = keyword_norm.split()
    if not terms:
        return False
    return all(term in title_norm for term in terms)

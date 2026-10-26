"""Text normalisation shared by the crawler, the matcher and the audit.

The same function is applied to keywords and to page text, so "Co-ord Sets",
"coord set" and "CO-ORDS" all become "coord set", and "Under ₹20,000" and
"under 20K" both become "under 20000".

Two forms are produced:
  strict: every word kept (used for phrases given in the `match` column)
  loose : filler words and generic modifiers removed, so the keyword
          "co-ord sets for women" matches a page that says "Co-ord Set"
"""
from __future__ import annotations

import re
import unicodedata

# Filler words and generic modifiers. These are dropped from BOTH the keyword and
# the page text in "loose" mode, so they never decide a match.
LOOSE_DROP = {
    "a", "an", "the", "for", "in", "of", "to", "and", "or", "with", "on", "at", "by", "from",
    "your", "you", "how", "what", "is", "are", "do", "does", "can", "i", "my", "me", "we", "our",
    "best", "top", "new", "buy", "shop", "online", "india", "indian", "woman", "women", "womens",
    "lady", "girl", "2024", "2025", "2026",
}


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith("sses"):
        return w[:-2]
    if w.endswith("ss"):
        return w
    if len(w) > 3 and w.endswith("s"):
        return w[:-1]
    return w


def base_tokens(text: str) -> list[str]:
    """Lower-case, de-accent, unify co-ord / numbers, split, stem."""
    s = _strip_accents(text or "").lower()
    s = s.replace("₹", " ").replace("&", " and ")
    s = re.sub(r"\b(?:rs|inr)\b\.?\s*(?=\d)", " ", s)      # "Rs. 5,000" / "INR 5000" -> "5000"
    s = re.sub(r"\bco[\s\-]?ords?\b", "coord", s)
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    s = re.sub(r"\b(\d+)\s?k\b", lambda m: str(int(m.group(1)) * 1000), s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return [_stem(w) for w in s.split() if w]


def strict(text: str) -> str:
    return " ".join(base_tokens(text))


def loose(text: str) -> str:
    toks = [t for t in base_tokens(text) if t not in LOOSE_DROP]
    return " ".join(toks)


def contains(haystack: str, needle: str) -> bool:
    """Whole-token phrase containment on already-normalised strings."""
    if not needle:
        return False
    return f" {needle} " in f" {haystack} "


def count(haystack: str, needle: str) -> int:
    if not needle:
        return 0
    return f" {haystack} ".count(f" {needle} ")

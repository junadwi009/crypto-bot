"""
news/sanitizer.py
Hapus potensi prompt injection dari konten berita sebelum masuk ke Claude.
"""

from __future__ import annotations
import re
import logging

log = logging.getLogger("sanitizer")

# Frasa berbahaya yang bisa dipakai untuk prompt injection
_INJECTION_PATTERNS = [
    r"ignore\s+(previous|all|above)\s+instructions?",
    r"disregard\s+(previous|all|above)",
    r"forget\s+(previous|all|above|your)\s+instructions?",
    r"new\s+instructions?:",
    r"system\s+prompt",
    r"you\s+are\s+now",
    r"act\s+as\s+(if|a|an)",
    r"jailbreak",
    r"DAN\s+mode",
    r"override\s+(your\s+)?(instructions?|rules?|guidelines?)",
    r"<\s*system\s*>",
    r"\[INST\]",
    r"<\s*\|im_start\|\s*>",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

MAX_HEADLINE_LEN = 200
MAX_SUMMARY_LEN  = 500


def sanitize_headline(text: str) -> tuple[str, bool]:
    """
    Sanitasi headline berita.
    Return (cleaned_text, injection_detected).
    """
    if not text:
        return "", False

    text = text.strip()

    # Cek injection
    for pattern in _COMPILED:
        if pattern.search(text):
            log.warning("Injection detected in headline: %s", text[:80])
            return "[HEADLINE FILTERED]", True

    # Potong kalau terlalu panjang
    if len(text) > MAX_HEADLINE_LEN:
        text = text[:MAX_HEADLINE_LEN] + "..."

    # Hapus karakter kontrol
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text, False


def sanitize_summary(text: str) -> tuple[str, bool]:
    """Sanitasi ringkasan berita."""
    if not text:
        return "", False

    text = text.strip()

    for pattern in _COMPILED:
        if pattern.search(text):
            log.warning("Injection detected in summary")
            return "[SUMMARY FILTERED]", True

    if len(text) > MAX_SUMMARY_LEN:
        text = text[:MAX_SUMMARY_LEN] + "..."

    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text, False


def sanitize_news_item(item: dict) -> dict:
    """
    Sanitasi satu item berita (headline + summary).
    Return item yang sudah bersih + flag injection_detected.
    """
    headline, inj1 = sanitize_headline(item.get("headline", ""))
    summary,  inj2 = sanitize_summary(item.get("summary", ""))

    return {
        **item,
        "headline":          headline,
        "summary":           summary,
        "injection_detected": inj1 or inj2,
    }

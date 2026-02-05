from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord


# ═══════════════════════════════════════════════
# LANGUAGE DETECTION
# ═══════════════════════════════════════════════

INDONESIAN_MARKERS = {
    "aku", "kamu", "saya", "gw", "gue", "lu", "lo",
    "gak", "dong", "sih", "nih", "banget", "udah",
    "gimana", "apa", "ini", "itu", "bisa", "mau",
    "kan", "deh", "lah", "ya", "ngga", "nggak",
    "anjir", "wkwk", "awkwk", "cuk",
}


def detect_language(text: str) -> str:
    if not text or len(text) < 3:
        return "en"

    sample = text[:150]

    if re.search(r"[\u3040-\u309F\u30A0-\u30FF]", sample):
        return "ja"
    if re.search(r"[\u4E00-\u9FFF]", sample):
        return "zh"
    if re.search(r"[\uAC00-\uD7AF]", sample):
        return "ko"

    words = set(sample.lower().split())
    id_matches = words & INDONESIAN_MARKERS
    if len(id_matches) >= 2:
        return "id"

    return "en"


# ═══════════════════════════════════════════════
# MENTION CLEANING
# ═══════════════════════════════════════════════

_MENTION_RE = re.compile(r"<@!?\d+>")


def clean_bot_mentions(content: str, bot_id: int) -> str:
    pattern = re.compile(rf"<@!?{bot_id}>")
    return pattern.sub("", content).strip()


def clean_all_mentions(content: str) -> str:
    return _MENTION_RE.sub("", content).strip()


# ═══════════════════════════════════════════════
# ATTACHMENT DETECTION
# ═══════════════════════════════════════════════

IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
IMAGE_URL_RE = re.compile(r"https?://\S+\.(?:png|jpg|jpeg|gif|webp)(?:\?\S*)?", re.IGNORECASE)


def has_image_attachment(message: discord.Message) -> bool:
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.split(";")[0] in IMAGE_CONTENT_TYPES:
            return True
    return False


def has_sticker(message: discord.Message) -> bool:
    return len(message.stickers) > 0


def extract_image_url(message: discord.Message) -> str | None:
    for attachment in message.attachments:
        ct = (attachment.content_type or "").split(";")[0]
        if ct in IMAGE_CONTENT_TYPES:
            return attachment.url

    for sticker in message.stickers:
        return sticker.url

    match = IMAGE_URL_RE.search(message.content)
    if match:
        return match.group(0)

    return None


def has_any_image(message: discord.Message) -> bool:
    return has_image_attachment(message) or has_sticker(message) or bool(IMAGE_URL_RE.search(message.content))


# ═══════════════════════════════════════════════
# CONTENT SANITIZATION
# ═══════════════════════════════════════════════

_EXCESSIVE_NEWLINES_RE = re.compile(r"\n{3,}")
_EXCESSIVE_SPACES_RE = re.compile(r" {3,}")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")


def sanitize(content: str, max_length: int = 2000) -> str:
    result = _ZERO_WIDTH_RE.sub("", content)
    result = _EXCESSIVE_NEWLINES_RE.sub("\n\n", result)
    result = _EXCESSIVE_SPACES_RE.sub(" ", result)
    return result[:max_length].strip()


# ═══════════════════════════════════════════════
# TEXT UTILITIES
# ═══════════════════════════════════════════════

def truncate(text: str, max_length: int = 200, suffix: str = "...") -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def is_empty_or_whitespace(text: str) -> bool:
    return not text or not text.strip()
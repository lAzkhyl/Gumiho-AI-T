from __future__ import annotations

import random
import re


# ═══════════════════════════════════════════════
# TYPO ENGINE
# ═══════════════════════════════════════════════

COMMON_TYPOS: dict[str, list[str]] = {
    "the": ["teh", "hte"],
    "that": ["taht", "tht"],
    "this": ["tihs", "ths"],
    "with": ["wiht", "wth"],
    "have": ["ahve", "hve"],
    "just": ["jsut", "jst"],
    "what": ["waht", "wht"],
    "about": ["abuot", "abut"],
    "really": ["relaly", "rly"],
    "because": ["becuase", "bc"],
    "people": ["poeple", "ppl"],
    "would": ["woudl", "wuld"],
    "should": ["shoudl", "shuld"],
    "think": ["thnk", "thnik"],
    "know": ["knwo", "kno"],
    "right": ["rihgt", "riht"],
}

TYPO_RATES = {"light": 0.02, "medium": 0.05, "heavy": 0.08}


def inject_typos(text: str, intensity: str = "heavy") -> str:
    rate = TYPO_RATES.get(intensity, 0.05)
    words = text.split()
    result = []

    for word in words:
        lower = word.lower()
        if lower in COMMON_TYPOS and random.random() < rate:
            replacement = random.choice(COMMON_TYPOS[lower])
            if word[0].isupper():
                replacement = replacement.capitalize()
            result.append(replacement)
        elif len(word) > 4 and random.random() < rate * 0.5:
            result.append(_swap_adjacent(word))
        else:
            result.append(word)

    return " ".join(result)


def _swap_adjacent(word: str) -> str:
    if len(word) < 3:
        return word
    idx = random.randint(1, len(word) - 2)
    chars = list(word)
    chars[idx], chars[idx + 1] = chars[idx + 1], chars[idx]
    return "".join(chars)


# ═══════════════════════════════════════════════
# FILLER WORDS
# ═══════════════════════════════════════════════

FILLERS_START = ["oh", "hmm", "eh", "ah", "well", "ngl", "honestly"]
FILLERS_MID = ["like", "tho", "kinda", "lowkey"]
FILLER_RATES = {"light": 0.03, "medium": 0.08, "heavy": 0.12}


def inject_fillers(text: str, intensity: str = "heavy") -> str:
    rate = FILLER_RATES.get(intensity, 0.08)

    if random.random() < rate and not text.startswith(tuple(FILLERS_START)):
        filler = random.choice(FILLERS_START)
        text = f"{filler} {text}"

    words = text.split()
    if len(words) > 6 and random.random() < rate:
        mid = len(words) // 2
        filler = random.choice(FILLERS_MID)
        words.insert(mid, filler)
        text = " ".join(words)

    return text


# ═══════════════════════════════════════════════
# CASE NORMALIZATION
# ═══════════════════════════════════════════════

def normalize_case(text: str) -> str:
    if text.isupper() and len(text) < 30:
        return text

    result = text.lower()

    if random.random() < 0.15:
        result = result.capitalize()

    return result


# ═══════════════════════════════════════════════
# PUNCTUATION CLEANUP
# ═══════════════════════════════════════════════

def clean_punctuation(text: str) -> str:
    text = re.sub(r"\.{4,}", "...", text)
    text = re.sub(r"!{3,}", "!!", text)
    text = re.sub(r"\?{3,}", "??", text)

    if text.endswith(".") and len(text) < 40 and random.random() < 0.6:
        text = text[:-1]

    return text


# ═══════════════════════════════════════════════
# MESSAGE SPLITTING (for long responses)
# ═══════════════════════════════════════════════

def split_message(text: str, max_per_chunk: int = 80) -> list[str]:
    if len(text) <= max_per_chunk:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)

    if len(sentences) <= 1:
        words = text.split()
        mid = len(words) // 2
        chunk1 = " ".join(words[:mid])
        chunk2 = " ".join(words[mid:])
        return [chunk1, chunk2]

    chunks = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) > max_per_chunk and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip() if current else sentence

    if current:
        chunks.append(current.strip())

    return chunks[:3]


# ═══════════════════════════════════════════════
# FULL PIPELINE
# ═══════════════════════════════════════════════

def humanize(text: str, intensity: str = "heavy") -> str:
    if not text or len(text) < 2:
        return text

    result = text.strip()
    result = normalize_case(result)
    result = inject_typos(result, intensity)
    result = inject_fillers(result, intensity)
    result = clean_punctuation(result)

    return result.strip()


def humanize_and_split(text: str, intensity: str = "heavy") -> list[str]:
    humanized = humanize(text, intensity)
    return split_message(humanized)
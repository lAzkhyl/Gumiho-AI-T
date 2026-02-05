from __future__ import annotations

import re

# ═══════════════════════════════════════════════
# CLAN TAG PATTERNS
# ═══════════════════════════════════════════════

CLAN_PREFIXES = [
    "2M_", "TM_", "2m_", "tm_",
    "GD_", "gd_", "DD_", "dd_",
    "KR_", "kr_", "FX_", "fx_",
    "RX_", "rx_", "PH_", "ph_",
]

CLAN_TAG_RE = re.compile(
    r"^(?:\[[^\]]{1,6}\]|【[^】]{1,6}】|\([^)]{1,6}\)|<[^>]{1,6}>)\s*"
)

SEPARATOR_RE = re.compile(r"[_\-.|•·]")
SPECIAL_CHARS_RE = re.compile(r"[^\w\s]", re.UNICODE)
MULTI_SPACE_RE = re.compile(r"\s{2,}")


# ═══════════════════════════════════════════════
# MAIN EXTRACTION
# ═══════════════════════════════════════════════

def extract_nickname(display_name: str) -> str:
    if not display_name or not display_name.strip():
        return "user"

    name = display_name.strip()

    # ─── Remove bracket-style clan tags: [2M], 【TM】, (GD), <RX> ───
    name = CLAN_TAG_RE.sub("", name).strip()

    # ─── Remove prefix-style clan tags: 2M_, TM_, etc ───
    for prefix in CLAN_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    # ─── Split by separators, take first meaningful part ───
    parts = SEPARATOR_RE.split(name)
    parts = [p.strip() for p in parts if p.strip()]

    if parts:
        name = parts[0]

    # ─── Remove special characters (keep Unicode letters) ───
    cleaned = SPECIAL_CHARS_RE.sub("", name).strip()
    cleaned = MULTI_SPACE_RE.sub(" ", cleaned)

    if not cleaned:
        cleaned = re.sub(r"[^\w]", "", name, flags=re.UNICODE).strip()

    if not cleaned:
        return "user"

    # ─── Truncate to reasonable length ───
    if len(cleaned) > 12:
        space_idx = cleaned.find(" ")
        if 2 < space_idx <= 10:
            cleaned = cleaned[:space_idx]
        else:
            cleaned = cleaned[:8]

    return cleaned.lower()


# ═══════════════════════════════════════════════
# MENTION FORMATTER
# ═══════════════════════════════════════════════

def format_user_label(display_name: str, user_id: str) -> str:
    nick = extract_nickname(display_name)
    return f"{nick} (<@{user_id}>)"


def build_user_list(users: dict[str, str]) -> str:
    if not users:
        return ""
    lines = []
    for uid, display in users.items():
        nick = extract_nickname(display)
        lines.append(f"{nick} = <@{uid}>")
    return "\n".join(lines)
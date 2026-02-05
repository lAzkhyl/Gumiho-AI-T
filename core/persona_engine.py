from __future__ import annotations

import re
from datetime import datetime, timezone

import asyncpg
import structlog
from redis.asyncio import Redis

from database import persona_store

logger = structlog.get_logger("twomoon.persona")


# ═══════════════════════════════════════════════
# BASE IDENTITY — 2M_Gumiho
# ═══════════════════════════════════════════════

BASE_PROMPT = """You are Gumiho, a member of the Two Moon clan Discord server.
Your tag is 2M_Gumiho. You are NOT a bot, NOT an assistant.
You've been part of this server for a while and know everyone.

ABSOLUTE RULES — BREAK THESE = FAIL:
- BANNED PHRASES: "How may I assist", "What's on your mind", "I appreciate", "Let me know if", "Feel free", "I'm here to help", "However", "I must clarify", "Would you like me to", "Greetings", "It seems like", "I understand your", "That's a great question", "Is there anything else", "How can I help"
- NO questions at the end UNLESS genuinely confused (max 1 question per 10 responses)
- NO constant agreement. If you disagree, say so.
- NO long responses for short messages. "hi" = "yo" NOT a paragraph.
- NO explaining what you're doing. Just do it.
- NO using emojis unless the user does first.

HOW YOU TALK:
- Lowercase mostly
- Short. 1-10 words for casual chat, 15-30 for real topics
- Skip punctuation except ... or ?
- Skip subjects: "nice" not "that's nice"
- Slang ok: lol, bruh, damn, ngl, fr, gg, wkwk
- If roasted, roast back harder
- Natural filler words sometimes: "oh", "hmm", "wait", "yo"
- Can be blunt, sarcastic, or deadpan
- Match the energy of the conversation

WHEN NOT TO REPLY (set should_respond=false):
- "ok", "okay", "k", "kk" with nothing else
- emoji-only messages
- "lol", "lmao", "haha", "wkwk" with nothing else
- messages clearly not directed at you

WHEN TO REPLY (set should_respond=true):
- Direct question to you
- When mentioned or replied to
- Interesting topic you have opinions on
- Something factually wrong you want to correct
- Good roast opportunity

You MUST respond in this JSON structure:
{"should_respond": true/false, "response_text": "your reply", "mood": "neutral/positive/negative/playful/aggressive"}

CORRECT EXAMPLES:
User: "hi" -> {"should_respond": true, "response_text": "yo", "mood": "neutral"}
User: "how are you" -> {"should_respond": true, "response_text": "chillin, you?", "mood": "neutral"}
User: "explain quantum physics" -> {"should_respond": true, "response_text": "basically particles can be in 2 states at once. weird stuff", "mood": "neutral"}
User: "ok" -> {"should_respond": false, "response_text": "", "mood": "neutral"}
User: "you're dumb" -> {"should_respond": true, "response_text": "right back at ya", "mood": "playful"}
User: "1+1?" -> {"should_respond": true, "response_text": "2", "mood": "neutral"}"""


# ═══════════════════════════════════════════════
# PERSONA MODIFIERS
# ═══════════════════════════════════════════════

PERSONA_MODS = {
    "twomoon": "\nPersonality: Calm, composed, balanced between serious and chill. Default Two Moon energy.",
    "homie": "\nPersonality: Super relaxed, jokes around, supportive friend vibes. Uses more slang.",
    "mentor": "\nPersonality: Wise but not preachy, gives insights not lectures. Can be philosophical.",
    "chaos": "\nPersonality: Chaotic energy, roasts often, unpredictable, savage humor. No filter.",
    "professional": "\nPersonality: More formal but still not robotic. Structured responses, less slang.",
    "matchuser": "",
}

PRESET_EMOJI = {
    "twomoon": "🌙",
    "homie": "😎",
    "mentor": "🧙",
    "chaos": "🔥",
    "professional": "💼",
    "matchuser": "🪞",
}

PRESET_DESC = {
    "twomoon": "Calm, balanced, Two Moon presence",
    "homie": "Chill, playful, your buddy",
    "mentor": "Wise, thoughtful",
    "chaos": "Savage, unhinged",
    "professional": "Formal, serious",
    "matchuser": "Mirrors your style",
}


# ═══════════════════════════════════════════════
# TIME CONTEXT
# ═══════════════════════════════════════════════

def _time_context() -> str:
    hour = datetime.now(timezone.utc).hour
    if 0 <= hour < 5:
        return '\n[Its late night/early morning. You can comment on it if natural like "still up?" or "go sleep"]'
    if hour >= 22:
        return "\n[Its nighttime]"
    return ""


# ═══════════════════════════════════════════════
# STYLE ANALYZER (for matchuser preset)
# ═══════════════════════════════════════════════

CULTURE_MARKERS = {
    "gen_z": ["fr fr", "no cap", "lowkey", "highkey", "bussin", "slay", "bet", "sus", "mid", "ratio", "ong", "ngl"],
    "gamer": ["gg", "ez", "noob", "nerf", "buff", "op", "meta", "carry", "clutch", "throw", "diff", "feed"],
    "weeb": ["nani", "kawaii", "sugoi", "baka", "senpai", "desu", "uwu", "owo"],
    "indo_slang": ["gw", "gue", "lu", "lo", "anjir", "bangsat", "cuk", "wkwk", "awkwk", "dong", "sih", "deh", "nih", "mager"],
}


def analyze_style(messages: list[str]) -> dict:
    if not messages:
        return {"formality": "casual", "energy": "medium", "verbosity": "brief", "humor": "subtle", "culture": None}

    combined = " ".join(messages)
    lower = combined.lower()
    avg_len = len(combined) / max(len(messages), 1)

    formality = "neutral"
    if re.search(r"please|thank you|could you|would you|kindly", combined, re.IGNORECASE):
        formality = "formal"
    elif re.search(r"gw|lu|anjir|lol|bruh|yo|bro|dude", lower):
        formality = "casual"

    energy = "medium"
    if re.search(r"!{2,}|[A-Z]{4,}", combined):
        energy = "high"
    elif avg_len < 15:
        energy = "low"

    verbosity = "normal"
    if avg_len > 80:
        verbosity = "verbose"
    elif avg_len < 25:
        verbosity = "brief"

    humor = "subtle"
    if re.search(r"wkwk|haha|lol|lmao|😂|💀|xd", lower):
        humor = "playful"

    culture = None
    for name, markers in CULTURE_MARKERS.items():
        matches = sum(1 for m in markers if m in lower)
        if matches >= 2:
            culture = name
            break

    return {
        "formality": formality,
        "energy": energy,
        "verbosity": verbosity,
        "humor": humor,
        "culture": culture,
    }


def style_to_prompt(style: dict) -> str:
    parts = ["MIRROR USER STYLE:"]

    if style["formality"] == "casual":
        parts.append("- Use casual/slang language")
    elif style["formality"] == "formal":
        parts.append("- Be slightly more polite")

    if style["energy"] == "high":
        parts.append("- High energy, expressive")
    elif style["energy"] == "low":
        parts.append("- Chill, minimal responses")

    if style["verbosity"] == "brief":
        parts.append("- Keep responses very short")
    elif style["verbosity"] == "verbose":
        parts.append("- Can be more detailed")

    culture_map = {
        "indo_slang": "- Use Indo slang (gw, lu, anjir, etc)",
        "gen_z": "- Use gen z slang (fr, no cap, bet)",
        "gamer": "- Use gaming terms (gg, ez, clutch)",
        "weeb": "- Mix in weeb expressions if natural",
    }
    if style["culture"] and style["culture"] in culture_map:
        parts.append(culture_map[style["culture"]])

    if style["humor"] == "playful":
        parts.append("- Can joke around freely")

    return "\n".join(parts)


# ═══════════════════════════════════════════════
# PROMPT BUILDER
# ═══════════════════════════════════════════════

async def build_system_prompt(
    pool: asyncpg.Pool,
    redis: Redis,
    user_id: str,
    server_id: str,
    user_messages: list[str] | None = None,
    language: str = "en",
    mention_map: str = "",
    talking_to: str = "",
) -> str:
    persona = await persona_store.get_effective_persona(pool, redis, user_id, server_id)

    prompt = BASE_PROMPT
    prompt += _time_context()
    prompt += PERSONA_MODS.get(persona["preset"], PERSONA_MODS["twomoon"])

    if persona["preset"] == "matchuser" and user_messages:
        style = analyze_style(user_messages)
        prompt += "\n\n" + style_to_prompt(style)

    if mention_map:
        prompt += f"\n\n[USER LIST — use <@ID> to mention]\n{mention_map}"

    if talking_to:
        prompt += f"\nTalking to: {talking_to}"

    if language == "id":
        prompt += "\n[Language: respond in Indonesian]"
    elif language == "ja":
        prompt += "\n[Language: respond in Japanese]"

    return prompt


# ═══════════════════════════════════════════════
# HELPERS (for slash commands)
# ═══════════════════════════════════════════════

def get_emoji(preset: str) -> str:
    return PRESET_EMOJI.get(preset, "🌙")


def get_description(preset: str) -> str:
    return PRESET_DESC.get(preset, "")
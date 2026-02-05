from __future__ import annotations

import random
from enum import Enum
from typing import TYPE_CHECKING

import structlog
from semantic_router import Route, RouteLayer
from semantic_router.encoders import FastEmbedEncoder

if TYPE_CHECKING:
    from config import RouterConfig

logger = structlog.get_logger("twomoon.router")


# ═══════════════════════════════════════════════
# ROUTE TYPES
# ═══════════════════════════════════════════════

class RouteType(str, Enum):
    IGNORE = "ignore"
    CHITCHAT = "chitchat"
    LLM_REQUIRED = "llm_required"
    VISION = "vision"


# ═══════════════════════════════════════════════
# TRAINING UTTERANCES
# ═══════════════════════════════════════════════

IGNORE_UTTERANCES = [
    "ok", "okay", "k", "kk", "okok", "oke",
    "hmm", "hm", "hmmm",
    "lol", "lmao", "lmfao", "wkwk", "wkwkwk", "awkwk",
    "haha", "hahaha", "hihi",
    "yes", "no", "yep", "nope", "nah", "ye", "yeh",
    "nice", "cool", "bet", "aight", "alright",
    "damn", "dope", "sick", "based",
    "fr", "ong", "facts",
    "💀", "😂", "🔥", "👍", "👎", "😭", "❤️",
    ".", "..", "...",
    "gg", "ez", "wp",
    "iya", "ya", "gitu", "oh", "ooh",
    "woi", "oi", "eh",
]

CHITCHAT_UTTERANCES = [
    "hi", "hello", "hey", "yo", "sup",
    "halo", "hai", "hei",
    "good morning", "gm", "morning",
    "good night", "gn", "night",
    "good afternoon", "good evening",
    "pagi", "selamat pagi", "met pagi",
    "malam", "selamat malam",
    "how are you", "how are you doing",
    "whats up", "what's up", "wassup",
    "apa kabar", "gimana kabarnya",
    "lagi ngapain", "ngapain",
    "thanks", "thank you", "thx", "ty",
    "makasih", "terima kasih", "mksh",
    "bye", "goodbye", "see you", "see ya",
    "dadah", "sampai jumpa",
]

LLM_REQUIRED_UTTERANCES = [
    "what do you think about", "apa pendapatmu tentang",
    "explain this", "jelasin dong", "jelaskan",
    "why is", "kenapa", "mengapa",
    "how does", "gimana caranya", "bagaimana",
    "tell me about", "ceritain dong",
    "can you help me", "bisa bantu gak",
    "what should I do", "gw harus gimana",
    "do you know", "tau gak",
    "I have a question", "gw mau tanya",
    "what happened", "apa yang terjadi",
    "have you ever", "pernah gak",
    "I think that", "menurut gw",
    "let's talk about", "ngomongin soal",
    "I need advice", "butuh saran",
    "what is your opinion", "pendapat lo gimana",
    "can you explain", "bisa jelasin",
    "I disagree", "gw gak setuju",
    "that's interesting because", "menarik karena",
    "have you heard about", "udah denger belum",
    "I'm confused about", "gw bingung soal",
    "what's the difference between", "apa bedanya",
    "roast me", "coba roast gw",
    "tell me a joke", "cerita lucu dong",
    "recommend me something", "rekomendasiin dong",
]


# ═══════════════════════════════════════════════
# CHITCHAT RESPONSE TEMPLATES
# ═══════════════════════════════════════════════

CHITCHAT_RESPONSES = {
    "greeting": [
        "yo", "hey", "sup", "halo", "yo wassup",
        "eh ada", "oi", "yoo",
    ],
    "greeting_morning": [
        "pagi", "gm", "morning", "pagi juga",
        "yo pagi", "met pagi",
    ],
    "greeting_night": [
        "night", "gn", "malam", "nite",
        "tidur sana", "met malam",
    ],
    "how_are_you": [
        "chillin, you?", "biasa aja", "fine",
        "santai, lu?", "good good", "ya gitu deh",
    ],
    "thanks": [
        "np", "no prob", "santai", "yoi",
        "sama sama", "anytime",
    ],
    "bye": [
        "see ya", "bye", "dadah", "later",
        "sampai nanti", "peace",
    ],
    "whats_up": [
        "nothing much", "biasa aja", "chillin",
        "gitu gitu aja", "santai", "lagi gabut",
    ],
}

GREETING_KEYWORDS = {"hi", "hello", "hey", "yo", "sup", "halo", "hai", "hei"}
MORNING_KEYWORDS = {"morning", "gm", "pagi"}
NIGHT_KEYWORDS = {"night", "gn", "malam"}
HOW_ARE_YOU_KEYWORDS = {"how are you", "apa kabar", "gimana kabarnya"}
THANKS_KEYWORDS = {"thanks", "thank", "thx", "ty", "makasih", "terima kasih", "mksh"}
BYE_KEYWORDS = {"bye", "goodbye", "dadah", "see ya", "see you", "sampai"}
WHATS_UP_KEYWORDS = {"whats up", "what's up", "wassup", "ngapain", "lagi ngapain"}


# ═══════════════════════════════════════════════
# LOCAL ROUTER CLASS
# ═══════════════════════════════════════════════

class LocalRouter:
    def __init__(self, config: RouterConfig) -> None:
        self._config = config
        self._route_layer: RouteLayer | None = None
        self._encoder: FastEmbedEncoder | None = None
        self._ready = False

    async def initialize(self) -> None:
        try:
            self._encoder = FastEmbedEncoder(model_name=self._config.embedding_model)

            routes = [
                Route(name=RouteType.IGNORE, utterances=IGNORE_UTTERANCES),
                Route(name=RouteType.CHITCHAT, utterances=CHITCHAT_UTTERANCES),
                Route(name=RouteType.LLM_REQUIRED, utterances=LLM_REQUIRED_UTTERANCES),
            ]

            self._route_layer = RouteLayer(encoder=self._encoder, routes=routes)
            self._ready = True
            logger.info("gatekeeper_ready", model=self._config.embedding_model)
        except Exception as error:
            logger.error("gatekeeper_init_failed", error=str(error))
            self._ready = False

    @property
    def encoder(self) -> FastEmbedEncoder | None:
        return self._encoder

    def classify(self, content: str, has_image: bool = False) -> RouteResult:
        # CRITICAL: Vision bypass — image presence overrides text classification
        if has_image:
            return RouteResult(
                route=RouteType.VISION,
                confidence=1.0,
                chitchat_response=None,
            )

        if not self._ready or self._route_layer is None:
            return RouteResult(
                route=RouteType.LLM_REQUIRED,
                confidence=0.0,
                chitchat_response=None,
            )

        cleaned = content.strip().lower()

        if not cleaned or len(cleaned) > 500:
            fallback = RouteType.IGNORE if not cleaned else RouteType.LLM_REQUIRED
            return RouteResult(route=fallback, confidence=1.0, chitchat_response=None)

        try:
            result = self._route_layer(cleaned)
        except Exception as error:
            logger.error("gatekeeper_classify_failed", error=str(error))
            return RouteResult(
                route=RouteType.LLM_REQUIRED,
                confidence=0.0,
                chitchat_response=None,
            )

        if result.name is None:
            return RouteResult(
                route=RouteType.LLM_REQUIRED,
                confidence=0.0,
                chitchat_response=None,
            )

        route_type = RouteType(result.name)

        chitchat_response = None
        if route_type == RouteType.CHITCHAT:
            chitchat_response = _pick_chitchat_response(cleaned)

        return RouteResult(
            route=route_type,
            confidence=result.similarity_score or 0.0,
            chitchat_response=chitchat_response,
        )

    def get_embedding(self, text: str) -> list[float] | None:
        if not self._ready or self._encoder is None:
            return None
        try:
            embeddings = self._encoder(docs=[text])
            if embeddings and len(embeddings) > 0:
                return embeddings[0].tolist()
        except Exception as error:
            logger.error("embedding_failed", error=str(error))
        return None


# ═══════════════════════════════════════════════
# ROUTE RESULT
# ═══════════════════════════════════════════════

class RouteResult:
    __slots__ = ("route", "confidence", "chitchat_response")

    def __init__(
        self,
        route: RouteType,
        confidence: float,
        chitchat_response: str | None,
    ) -> None:
        self.route = route
        self.confidence = confidence
        self.chitchat_response = chitchat_response

    @property
    def should_call_llm(self) -> bool:
        return self.route in (RouteType.LLM_REQUIRED, RouteType.VISION)

    @property
    def is_vision(self) -> bool:
        return self.route == RouteType.VISION

    def __repr__(self) -> str:
        return f"RouteResult(route={self.route}, confidence={self.confidence:.2f})"


# ═══════════════════════════════════════════════
# CHITCHAT RESPONSE PICKER
# ═══════════════════════════════════════════════

def _pick_chitchat_response(content: str) -> str:
    words = set(content.split())

    if words & MORNING_KEYWORDS:
        return random.choice(CHITCHAT_RESPONSES["greeting_morning"])
    if words & NIGHT_KEYWORDS:
        return random.choice(CHITCHAT_RESPONSES["greeting_night"])
    if words & THANKS_KEYWORDS:
        return random.choice(CHITCHAT_RESPONSES["thanks"])
    if words & BYE_KEYWORDS:
        return random.choice(CHITCHAT_RESPONSES["bye"])
    if words & WHATS_UP_KEYWORDS or _phrase_match(content, WHATS_UP_KEYWORDS):
        return random.choice(CHITCHAT_RESPONSES["whats_up"])
    if _phrase_match(content, HOW_ARE_YOU_KEYWORDS):
        return random.choice(CHITCHAT_RESPONSES["how_are_you"])
    if words & GREETING_KEYWORDS:
        return random.choice(CHITCHAT_RESPONSES["greeting"])

    return random.choice(CHITCHAT_RESPONSES["greeting"])


def _phrase_match(content: str, phrases: set[str]) -> bool:
    return any(phrase in content for phrase in phrases)
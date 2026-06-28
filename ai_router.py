"""
PolyGlotty AI Router
====================
Provider routing for ALEX chat + dictionary word breakdown.

Goal: offload the Anthropic balance by sending FREE users to Google's free
Gemini 1.5 Flash tier, while PAID (Platform / grandfathered / premium) users
keep using Anthropic Claude.

    is_premium == True   →  Anthropic Claude (handled in server.handle_chat)
    is_premium == False  →  Google Gemini 1.5 Flash (this module)

This codebase has no AI SDKs — Anthropic itself is called over raw httpx — so we
talk to Gemini the same way (the official `@google/generative-ai` package the
brief mentions is Node-only; the Python analogue would be `google-generativeai`,
but raw REST keeps the dependency surface identical to the rest of the app).

Rate-limit guard: Google's free tier returns HTTP 429 / RESOURCE_EXHAUSTED when
the daily quota is spent. We never let that crash the request — we raise a typed
`GeminiRateLimit` so the caller can show a friendly "come back tomorrow / go
Premium" card instead.
"""
from __future__ import annotations

import os
import logging

import httpx

logger = logging.getLogger(__name__)

# ── Config (all env-driven; nothing hardcoded) ────────────────────────────────
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
# Master switch. Defaults ON when a key is present so the free tier is offloaded
# automatically; set GEMINI_FREE_ENABLED=0 to fall back to the old credit path.
GEMINI_FREE_ENABLED = os.getenv("GEMINI_FREE_ENABLED", "1") != "0"
# TEMP TEST OVERRIDE. When ON, EVERY user (including Premium / Platform) is routed
# to Gemini and Anthropic is bypassed entirely — used to verify the Gemini path on
# prod. Default OFF. Flip GEMINI_FORCE_ALL=1 in Railway to enable; delete the var
# to instantly restore Claude for paying users (no code change / redeploy needed).
GEMINI_FORCE_ALL = os.getenv("GEMINI_FORCE_ALL", "0") == "1"
GEMINI_MAX_TOKENS = int(os.getenv("GEMINI_MAX_TOKENS", "600") or "600")
GEMINI_TIMEOUT    = float(os.getenv("GEMINI_TIMEOUT", "45") or "45")
GEMINI_BASE_URL   = os.getenv(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
)

if GEMINI_FREE_ENABLED and not GEMINI_API_KEY:
    logger.warning("⚠️ GEMINI_FREE_ENABLED but GEMINI_API_KEY is empty — free users will fall back to the credit path.")
elif GEMINI_API_KEY:
    logger.info(f"✅ GEMINI_API_KEY loaded (model={GEMINI_MODEL}, starts with {GEMINI_API_KEY[:6]}...)")

if GEMINI_FORCE_ALL:
    logger.warning("🟡 GEMINI_FORCE_ALL=1 — ALL users (incl. Premium) routed to Gemini; Anthropic Claude is BYPASSED. Temporary test mode.")


# ── Typed errors ──────────────────────────────────────────────────────────────
class GeminiError(Exception):
    """Any non-recoverable Gemini failure (bad request, 5xx, network)."""


class GeminiRateLimit(GeminiError):
    """Google free-tier daily quota hit (HTTP 429 / RESOURCE_EXHAUSTED)."""


# ── Routing decision ──────────────────────────────────────────────────────────
def gemini_available() -> bool:
    return bool(GEMINI_FREE_ENABLED and GEMINI_API_KEY)


def should_use_gemini(is_paid: bool) -> bool:
    """Route FREE (non-paying) users to Gemini; paid users to Anthropic.

    GEMINI_FORCE_ALL overrides the paid check and sends everyone to Gemini
    (temporary test switch to disable Claude on prod)."""
    if not gemini_available():
        return False
    if GEMINI_FORCE_ALL:
        return True
    return not is_paid


# ── History adapter ───────────────────────────────────────────────────────────
def _to_gemini_contents(messages: list[dict]) -> list[dict]:
    """Convert the internal [{role:'user'|'assistant', content:str}] history into
    Gemini's contents format ([{role:'user'|'model', parts:[{text}]}])."""
    out: list[dict] = []
    for m in messages or []:
        role = "model" if m.get("role") == "assistant" else "user"
        text = str(m.get("content") or "")
        if not text:
            continue
        out.append({"role": role, "parts": [{"text": text}]})
    return out


# ── Core call ─────────────────────────────────────────────────────────────────
async def gemini_generate(system: str, messages: list[dict],
                          max_tokens: int | None = None,
                          timeout: float | None = None) -> dict:
    """Call Gemini 1.5 Flash and return {"text": str, "usage": {...}}.

    Raises GeminiRateLimit on 429 / RESOURCE_EXHAUSTED, GeminiError otherwise.
    Never returns a partial/empty success silently — an empty candidate raises
    GeminiError so the caller refunds nothing and shows a clean error.
    """
    if not gemini_available():
        raise GeminiError("Gemini not configured")

    url = f"{GEMINI_BASE_URL}/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": _to_gemini_contents(messages),
        "generationConfig": {
            "maxOutputTokens": int(max_tokens or GEMINI_MAX_TOKENS),
            "temperature": 0.7,
        },
    }
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}

    try:
        async with httpx.AsyncClient(timeout=timeout or GEMINI_TIMEOUT) as client:
            r = await client.post(url, json=payload,
                                  headers={"content-type": "application/json"})
    except httpx.HTTPError as e:
        raise GeminiError(f"network: {e}") from e

    # ── Rate-limit guard ──────────────────────────────────────────────────────
    if r.status_code == 429:
        raise GeminiRateLimit("Gemini daily free quota exhausted (HTTP 429)")

    try:
        data = r.json()
    except Exception as e:
        raise GeminiError(f"bad json (status {r.status_code})") from e

    if r.status_code != 200 or "error" in data:
        err = (data.get("error") or {}) if isinstance(data, dict) else {}
        status = str(err.get("status") or "")
        msg = str(err.get("message") or f"HTTP {r.status_code}")
        # Some quota errors arrive as 400/403 with RESOURCE_EXHAUSTED status.
        if status == "RESOURCE_EXHAUSTED" or err.get("code") == 429:
            raise GeminiRateLimit(msg)
        raise GeminiError(msg[:200])

    # ── Extract text ──────────────────────────────────────────────────────────
    text = ""
    try:
        cands = data.get("candidates") or []
        if cands:
            # A safety block also lands here with no parts → treat as error.
            parts = (cands[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts).strip()
    except Exception as e:
        raise GeminiError(f"parse: {e}") from e

    if not text:
        raise GeminiError("empty completion")

    um = data.get("usageMetadata") or {}
    usage = {
        "input_tokens": int(um.get("promptTokenCount") or 0),
        "output_tokens": int(um.get("candidatesTokenCount") or 0),
        "total_tokens": int(um.get("totalTokenCount") or 0),
    }
    return {"text": text, "usage": usage}


# ══════════════════════════════════════════════════════════════════════════════
#  DeepSeek provider (OpenAI-compatible REST) — free-tier ALEX
# ══════════════════════════════════════════════════════════════════════════════
#  Provider-agnostic by design: base URL, key, model and token budget are all
#  env-driven, so the SAME code talks to DeepSeek-direct (api.deepseek.com) OR an
#  OpenAI-compatible gateway (e.g. NVIDIA NIM integrate.api.nvidia.com) just by
#  flipping env vars — no redeploy. We call it over raw httpx like every other
#  provider here (no openai SDK dependency). When configured, free users are
#  routed here in preference to Gemini; premium users keep using Claude.
DEEPSEEK_API_KEY      = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL     = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
# NOTE: "deepseek-v4-flash" is NOT a confirmed public model name. DeepSeek-direct
# exposes "deepseek-chat" (V3) and "deepseek-reasoner" (R1). Keep the model in env
# so a wrong/renamed model never requires a code change. Default = deepseek-chat.
DEEPSEEK_MODEL        = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_FREE_ENABLED = os.getenv("DEEPSEEK_FREE_ENABLED", "1") != "0"
DEEPSEEK_MAX_TOKENS   = int(os.getenv("DEEPSEEK_MAX_TOKENS", "600") or "600")
DEEPSEEK_TIMEOUT      = float(os.getenv("DEEPSEEK_TIMEOUT", "45") or "45")

if DEEPSEEK_API_KEY:
    logger.info(f"✅ DEEPSEEK_API_KEY loaded (model={DEEPSEEK_MODEL}, base={DEEPSEEK_BASE_URL}, starts with {DEEPSEEK_API_KEY[:6]}...)")


class DeepSeekError(GeminiError):
    """Any non-recoverable DeepSeek failure (bad request, 5xx, network)."""


class DeepSeekRateLimit(GeminiRateLimit):
    """DeepSeek quota / rate-limit hit (HTTP 429)."""


def deepseek_available() -> bool:
    return bool(DEEPSEEK_FREE_ENABLED and DEEPSEEK_API_KEY)


def should_use_deepseek(is_paid: bool) -> bool:
    """Route FREE (non-paying) users to DeepSeek when it is configured. Premium
    users are NEVER sent here (they stay on Claude)."""
    if not deepseek_available():
        return False
    return not is_paid


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """Convert the internal [{role:'user'|'assistant', content:str}] history into
    the OpenAI chat format, prepending the system prompt as a system message."""
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages or []:
        role = "assistant" if m.get("role") == "assistant" else "user"
        text = str(m.get("content") or "")
        if not text:
            continue
        out.append({"role": role, "content": text})
    return out


async def deepseek_generate(system: str, messages: list[dict],
                            max_tokens: int | None = None,
                            timeout: float | None = None) -> dict:
    """Call DeepSeek (OpenAI-compatible /chat/completions) and return
    {"text": str, "usage": {...}}. Free tier → no reasoning params (fast, cheap).
    Raises DeepSeekRateLimit on 429, DeepSeekError otherwise. Never returns an
    empty success silently."""
    if not deepseek_available():
        raise DeepSeekError("DeepSeek not configured")

    url = f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": _to_openai_messages(system, messages),
        "max_tokens": int(max_tokens or DEEPSEEK_MAX_TOKENS),
        "temperature": 0.7,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout or DEEPSEEK_TIMEOUT) as client:
            r = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise DeepSeekError(f"network: {e}") from e

    if r.status_code == 429:
        raise DeepSeekRateLimit("DeepSeek quota / rate limit (HTTP 429)")
    if r.status_code >= 400:
        raise DeepSeekError(f"HTTP {r.status_code}: {r.text[:200]}")

    try:
        data = r.json()
    except Exception as e:
        raise DeepSeekError(f"bad json (status {r.status_code})") from e

    try:
        choices = data.get("choices") or []
        msg = (choices[0].get("message") or {}) if choices else {}
        text = str(msg.get("content") or "").strip()
    except Exception as e:
        raise DeepSeekError(f"parse: {e}") from e

    if not text:
        raise DeepSeekError("empty completion")

    u = data.get("usage") or {}
    usage = {
        "input_tokens": int(u.get("prompt_tokens") or 0),
        "output_tokens": int(u.get("completion_tokens") or 0),
        "total_tokens": int(u.get("total_tokens") or 0),
    }
    return {"text": text, "usage": usage}


# ── Friendly rate-limit message (i18n) ────────────────────────────────────────
# Rendered as an HTML limit-card inside the chat reply (same classes the other
# limit messages use), so it shows up styled rather than as raw text.
_LIMIT_TXT = {
    "ru": "На сегодня бесплатные лимиты ИИ-ассистента исчерпаны. Возвращайся завтра или переходи на Premium для безлимитного доступа к ALEX без очередей!",
    "en": "Today's free AI-assistant limits are used up. Come back tomorrow, or go Premium for unlimited ALEX access with no queues!",
    "es": "Los límites gratuitos del asistente de IA se agotaron por hoy. Vuelve mañana o pásate a Premium para acceso ilimitado a ALEX sin colas.",
    "pt": "Os limites gratuitos do assistente de IA acabaram por hoje. Volte amanhã ou assine o Premium para acesso ilimitado ao ALEX sem filas!",
    "de": "Die kostenlosen KI-Assistenten-Limits sind für heute aufgebraucht. Komm morgen wieder oder hol dir Premium für unbegrenzten ALEX-Zugang ohne Warteschlangen!",
    "fr": "Les limites gratuites de l’assistant IA sont épuisées pour aujourd’hui. Reviens demain ou passe à Premium pour un accès illimité à ALEX, sans file d’attente !",
    "uk": "Безкоштовні ліміти ШІ-асистента на сьогодні вичерпано. Повертайся завтра або переходь на Premium для безлімітного доступу до ALEX без черг!",
    "tr": "Yapay zekâ asistanının bugünkü ücretsiz limitleri doldu. Yarın tekrar gel veya sırasız sınırsız ALEX erişimi için Premium’a geç!",
    "zh": "今日的免费 AI 助手额度已用完。明天再来，或升级 Premium 享受无限制、无排队的 ALEX！",
    "ar": "انتهت حدود مساعد الذكاء الاصطناعي المجانية لليوم. عُد غدًا أو اشترك في Premium للوصول غير المحدود إلى ALEX بلا انتظار!",
}
_LIMIT_KICKER = {
    "ru": "Лимит ИИ", "en": "AI limit", "es": "Límite de IA", "pt": "Limite de IA",
    "de": "KI-Limit", "fr": "Limite IA", "uk": "Ліміт ШІ", "tr": "YZ limiti",
    "zh": "AI 上限", "ar": "حد الذكاء الاصطناعي",
}
_LIMIT_CTA = {
    "ru": "Перейти на Premium", "en": "Go Premium", "es": "Pasar a Premium",
    "pt": "Assinar Premium", "de": "Premium holen", "fr": "Passer à Premium",
    "uk": "Перейти на Premium", "tr": "Premium’a geç", "zh": "升级 Premium",
    "ar": "الانتقال إلى Premium",
}


def gemini_free_limit_message(lang: str = "ru") -> str:
    """HTML limit-card shown when the free Gemini quota is exhausted."""
    txt = _LIMIT_TXT.get(lang, _LIMIT_TXT["en"])
    kicker = _LIMIT_KICKER.get(lang, _LIMIT_KICKER["en"])
    cta = _LIMIT_CTA.get(lang, _LIMIT_CTA["en"])
    return (
        '<div class="limit-card">'
        f'<div class="limit-kicker">{kicker}</div>'
        f'<div class="limit-title">ALEX</div>'
        f'<div class="limit-text">{txt}</div>'
        f'<button class="chip" onclick="openPremium()">{cta}</button>'
        '</div>'
    )


# ── Per-user daily-cap message (i18n) ─────────────────────────────────────────
# Shown when a FREE user spends their AI_FREE_DAILY messages for the UTC day —
# distinct from the upstream provider-quota card above (which is global).
_DAILY_TXT = {
    "ru": "Вы исчерпали свои 4 бесплатных запроса к ALEX на сегодня. Возвращайтесь завтра или переходите на Premium для безлимитного общения!",
    "en": "Your daily free ALEX AI limit is used up. Come back tomorrow, or go Premium!",
    "es": "Has agotado tu límite diario gratuito de ALEX. ¡Vuelve mañana o pásate a Premium!",
    "pt": "Seu limite diário gratuito do ALEX acabou. Volte amanhã ou assine o Premium!",
    "de": "Dein kostenloses ALEX-Tageslimit ist aufgebraucht. Komm morgen wieder oder hol dir Premium!",
    "fr": "Ta limite quotidienne gratuite d’ALEX est atteinte. Reviens demain ou passe à Premium !",
    "uk": "Денний безкоштовний ліміт ALEX вичерпано. Повертайся завтра або переходь на Premium!",
    "tr": "Günlük ücretsiz ALEX limitin doldu. Yarın tekrar gel veya Premium’a geç!",
    "zh": "今日免费 ALEX 额度已用完。明天再来，或升级 Premium！",
    "ar": "انتهى حدّك اليومي المجاني من ALEX. عُد غدًا أو اشترك في Premium!",
}


def ai_daily_limit_message(lang: str = "ru") -> str:
    """HTML limit-card shown when a free user hits the per-user daily AI cap."""
    txt = _DAILY_TXT.get(lang, _DAILY_TXT["en"])
    kicker = _LIMIT_KICKER.get(lang, _LIMIT_KICKER["en"])
    cta = _LIMIT_CTA.get(lang, _LIMIT_CTA["en"])
    return (
        '<div class="limit-card">'
        f'<div class="limit-kicker">{kicker}</div>'
        f'<div class="limit-title">ALEX</div>'
        f'<div class="limit-text">{txt}</div>'
        f'<button class="chip" onclick="openPremium()">{cta}</button>'
        '</div>'
    )

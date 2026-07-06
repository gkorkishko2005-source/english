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
GEMINI_MAX_TOKENS = int(os.getenv("GEMINI_MAX_TOKENS", "1000") or "1000")
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


# ── OpenRouter (PRIMARY free-tier provider) ───────────────────────────────────
#  OpenRouter is an OpenAI-compatible gateway; a single key fronts many models.
#  FREE (non-paying) users are routed here in preference to DeepSeek/Gemini.
#  Premium users keep Claude. Model is env-driven so swapping a ":free" model
#  never needs a code change.
# The key comes ONLY from the environment — never hardcode a live secret in the
# repo (GitHub secret-scanning blocks it, and a public repo would leak it). Set
# OPENROUTER_API_KEY in Railway Variables (and .env locally). If it is somehow
# missing, the provider gate degrades gracefully instead of crashing the app.
OPENROUTER_API_KEY      = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL     = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
# FREE tier → ultra-cheap Gemini Flash-Lite. PREMIUM tier → OpenAI GPT-4o mini:
# far cheaper than Gemini 2.5 Pro (the old premium that dominated the cost logs)
# yet clearly smarter than the free Flash-Lite, so the paid quality jump is
# visible. Both env-driven — swapping a model never needs a code change.
OPENROUTER_MODEL         = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite")
OPENROUTER_MODEL_PREMIUM = os.getenv("OPENROUTER_MODEL_PREMIUM", "openai/gpt-4o-mini")
OPENROUTER_FREE_ENABLED = os.getenv("OPENROUTER_FREE_ENABLED", "1") != "0"
OPENROUTER_MAX_TOKENS   = int(os.getenv("OPENROUTER_MAX_TOKENS", "1000") or "1000")
OPENROUTER_TIMEOUT      = float(os.getenv("OPENROUTER_TIMEOUT", "45") or "45")
# Optional ranking headers OpenRouter recommends (harmless if unset).
OPENROUTER_REFERER      = os.getenv("OPENROUTER_REFERER", "https://t.me/PolyGlotty_bot")
OPENROUTER_TITLE        = os.getenv("OPENROUTER_TITLE", "PolyGlotty")

# ── FREE-model rotation + paid safety-net ─────────────────────────────────────
#  A single OpenRouter key fronts many ":free" models. For FREE users we try a
#  list of free models in order; when one is rate-limited (429) or transiently
#  unavailable we fall through to the NEXT model, and finally to a cheap PAID
#  fallback (google/gemma-3-27b-it) so free chat never dead-ends.
#
#  HONEST NOTE: OpenRouter's free daily quota is ACCOUNT-WIDE (shared across all
#  ":free" variants), so rotating free models buys RELIABILITY (it dodges a
#  single model's outage / per-model throttle), NOT extra daily quota. The real
#  overflow valve is OPENROUTER_MODEL_FALLBACK — a paid model that keeps serving
#  once every free model is exhausted. Set it to "" to disable the paid fallback
#  (free chat then shows the daily-limit card instead of spending owner balance).
#  Curated general-purpose chat models (excludes code-only, guardrail/safety,
#  uncensored, and tiny <=3B variants — none suit a language tutor). Ordered
#  proven-slug-first so the chain keeps working even if a newer slug 404s.
_DEFAULT_FREE_MODELS = ",".join([
    "meta-llama/llama-3.3-70b-instruct:free",   # strong, multilingual, reliable
    "openai/gpt-oss-120b:free",                 # high-reasoning general purpose
    "qwen/qwen3-next-80b-a3b-instruct:free",    # fast, multilingual
    "nvidia/nemotron-nano-9b-v2:free",          # light, quick fallback
    "google/gemma-4-31b-it:free",               # 140+ languages
    "openai/gpt-oss-20b:free",                  # lightweight last resort
])
OPENROUTER_FREE_MODELS   = [m.strip() for m in os.getenv("OPENROUTER_FREE_MODELS", _DEFAULT_FREE_MODELS).split(",") if m.strip()]
OPENROUTER_MODEL_FALLBACK = os.getenv("OPENROUTER_MODEL_FALLBACK", "google/gemma-3-27b-it").strip()

if OPENROUTER_API_KEY:
    logger.info(f"✅ OPENROUTER_API_KEY loaded (model={OPENROUTER_MODEL}, base={OPENROUTER_BASE_URL}, starts with {OPENROUTER_API_KEY[:12]}...)")


class OpenRouterError(GeminiError):
    """Any non-recoverable OpenRouter failure (bad request, 5xx, network)."""


class OpenRouterRateLimit(GeminiRateLimit):
    """OpenRouter quota / rate-limit hit (HTTP 429)."""


def openrouter_available() -> bool:
    return bool(OPENROUTER_FREE_ENABLED and OPENROUTER_API_KEY)


def should_use_openrouter(is_paid: bool = False) -> bool:
    """Route ALL users to OpenRouter when configured. The whole PolyGlotty AI
    ecosystem runs on Gemini now: FREE users get Flash, PREMIUM users get Pro
    (the tier→model split is applied in server.handle_chat). `is_paid` is kept
    for signature compatibility but no longer changes routing."""
    return openrouter_available()


def openrouter_model_for(is_paid: bool) -> str:
    """Pick the Gemini model for the tier: paid → flagship Pro, free → Flash."""
    return OPENROUTER_MODEL_PREMIUM if is_paid else OPENROUTER_MODEL


async def openrouter_generate(system: str, messages: list[dict],
                              max_tokens: int | None = None,
                              timeout: float | None = None,
                              model: str | None = None) -> dict:
    """Call OpenRouter (OpenAI-compatible /chat/completions) and return
    {"text": str, "usage": {...}}. Raises OpenRouterRateLimit on 429,
    OpenRouterError otherwise. Never returns an empty success silently.
    `model` overrides the default (FREE Flash); pass OPENROUTER_MODEL_PREMIUM
    for paid users on the flagship Pro model."""
    if not openrouter_available():
        raise OpenRouterError("OpenRouter not configured")

    url = f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": model or OPENROUTER_MODEL,
        "messages": _to_openai_messages(system, messages),
        "max_tokens": int(max_tokens or OPENROUTER_MAX_TOKENS),
        "temperature": 0.7,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if OPENROUTER_REFERER:
        headers["HTTP-Referer"] = OPENROUTER_REFERER
    if OPENROUTER_TITLE:
        headers["X-Title"] = OPENROUTER_TITLE
    try:
        async with httpx.AsyncClient(timeout=timeout or OPENROUTER_TIMEOUT) as client:
            r = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise OpenRouterError(f"network: {e}") from e

    if r.status_code == 429:
        raise OpenRouterRateLimit("OpenRouter quota / rate limit (HTTP 429)")
    if r.status_code >= 400:
        raise OpenRouterError(f"HTTP {r.status_code}: {r.text[:200]}")

    try:
        data = r.json()
    except Exception as e:
        raise OpenRouterError(f"bad json (status {r.status_code})") from e

    # OpenRouter can return an error object WITH HTTP 200 (e.g. upstream model
    # outage / no free capacity). Treat it as a hard error, not an empty reply.
    if isinstance(data, dict) and data.get("error"):
        err = data.get("error") or {}
        code = err.get("code") if isinstance(err, dict) else None
        msg = err.get("message") if isinstance(err, dict) else str(err)
        if code == 429:
            raise OpenRouterRateLimit(f"OpenRouter: {msg}")
        raise OpenRouterError(f"OpenRouter error: {msg}")

    try:
        choices = data.get("choices") or []
        msg = (choices[0].get("message") or {}) if choices else {}
        text = str(msg.get("content") or "").strip()
    except Exception as e:
        raise OpenRouterError(f"parse: {e}") from e

    if not text:
        raise OpenRouterError("empty completion")

    u = data.get("usage") or {}
    usage = {
        "input_tokens": int(u.get("prompt_tokens") or 0),
        "output_tokens": int(u.get("completion_tokens") or 0),
        "total_tokens": int(u.get("total_tokens") or 0),
    }
    return {"text": text, "usage": usage}


def openrouter_free_chain() -> list[str]:
    """Ordered model list for FREE users: every configured ":free" model, then
    the paid fallback (if set) as the final safety net. Never empty when
    OpenRouter is configured — falls back to the base OPENROUTER_MODEL."""
    chain = list(OPENROUTER_FREE_MODELS) or [OPENROUTER_MODEL]
    if OPENROUTER_MODEL_FALLBACK and OPENROUTER_MODEL_FALLBACK not in chain:
        chain.append(OPENROUTER_MODEL_FALLBACK)
    return chain


async def openrouter_generate_free(system: str, messages: list[dict],
                                   max_tokens: int | None = None,
                                   timeout: float | None = None) -> dict:
    """FREE-tier OpenRouter call with model rotation + paid fallback.

    Tries each model in ``openrouter_free_chain()`` in order. A 429 / rate-limit
    or a transient failure on one model moves on to the NEXT model; the last
    entry is the cheap paid fallback so a free user still gets an answer once all
    the ":free" models are exhausted.

    Raises OpenRouterRateLimit only if EVERY model (including the paid fallback)
    is rate-limited — so the caller shows the daily-limit card. Raises
    OpenRouterError if all models fail for other reasons (→ calm retry card).
    Signature matches ``openrouter_generate`` minus ``model`` so it can be a
    drop-in provider in the free-tier dispatch table."""
    if not openrouter_available():
        raise OpenRouterError("OpenRouter not configured")
    last_rate: Exception | None = None
    last_err: Exception | None = None
    for m in openrouter_free_chain():
        try:
            return await openrouter_generate(system, messages, max_tokens, timeout, model=m)
        except OpenRouterRateLimit as e:
            last_rate = e
            logger.info("OpenRouter free model rate-limited (%s) — trying next", m)
            continue
        except OpenRouterError as e:
            last_err = e
            logger.warning("OpenRouter free model failed (%s): %s", m, e)
            continue
    # Every model in the chain failed. If at least one non-rate error occurred we
    # surface that (retry card); if it was purely rate-limits, surface the
    # rate-limit so the caller shows the daily-limit card.
    if last_err is not None:
        raise last_err
    raise (last_rate or OpenRouterError("no OpenRouter free models configured"))


# ── Friendly rate-limit message (i18n) ────────────────────────────────────────
# Subtle inline "upgrade" text-link appended to the compact limit notice — NOT a
# full purchase card. Tapping it opens Premium; by default the user just reads
# the notice and waits. Lower-case, quiet, non-shouty.
_LIMIT_UPSELL = {
    "ru": "обновить подписку", "en": "upgrade", "es": "mejorar plan",
    "pt": "assinar premium", "de": "Premium holen", "fr": "passer à Premium",
    "uk": "оновити підписку", "tr": "yükselt", "zh": "升级会员",
    "ar": "ترقية الاشتراك",
}
# Short, upstream-quota variant of the free-limit notice ("come back later").
_LIMIT_TXT_MINI = {
    "ru": "Бесплатные лимиты ALEX на сегодня исчерпаны — возвращайся завтра.",
    "en": "Today's free ALEX limits are used up — come back tomorrow.",
    "es": "Los límites gratis de ALEX se agotaron por hoy — vuelve mañana.",
    "pt": "Os limites grátis do ALEX acabaram por hoje — volte amanhã.",
    "de": "Die kostenlosen ALEX-Limits sind heute aufgebraucht — komm morgen wieder.",
    "fr": "Les limites gratuites d’ALEX sont épuisées aujourd’hui — reviens demain.",
    "uk": "Безкоштовні ліміти ALEX на сьогодні вичерпано — повертайся завтра.",
    "tr": "ALEX’in bugünkü ücretsiz limitleri doldu — yarın tekrar gel.",
    "zh": "今日免费 ALEX 额度已用完 — 明天再来。",
    "ar": "انتهت حدود ALEX المجانية لليوم — عُد غدًا.",
}


def _limit_mini(txt: str, upsell: str) -> str:
    """Compact, quiet inline limit notice (NOT a full-screen purchase menu).
    A single muted line: what happened + a subtle text-link to upgrade. Renders
    as HTML inside the chat bubble (the WebApp detects the leading <div>)."""
    return (
        '<div class="limit-mini">'
        '<svg viewBox="0 0 24 24" class="limit-mini-ic"><circle cx="12" cy="12" r="9"/>'
        '<path d="M12 7.5v5l3.2 2"/></svg>'
        f'<span>{txt} '
        f'<button type="button" class="limit-mini-link" onclick="openPremium()">{upsell}</button>'
        '</span></div>'
    )


def gemini_free_limit_message(lang: str = "ru") -> str:
    """Compact inline notice when the free upstream quota is exhausted."""
    txt = _LIMIT_TXT_MINI.get(lang, _LIMIT_TXT_MINI["en"])
    upsell = _LIMIT_UPSELL.get(lang, _LIMIT_UPSELL["en"])
    return _limit_mini(txt, upsell)


# ── Per-user daily-cap message (i18n) ─────────────────────────────────────────
# Shown when a FREE user spends their AI_FREE_DAILY messages for the UTC day —
# distinct from the upstream provider-quota notice above (which is global).
# Compact daily-cap text (no trailing "Premium" — the subtle link carries that).
_DAILY_TXT_MINI = {
    "ru": "Бесплатные сообщения на сегодня закончились. Новые — в 00:00 UTC.",
    "en": "You're out of free messages for today. New ones arrive at 00:00 UTC.",
    "es": "Se acabaron tus mensajes gratis por hoy. Llegan más a las 00:00 UTC.",
    "pt": "Seus mensagens grátis acabaram por hoje. Chegam mais às 00:00 UTC.",
    "de": "Deine kostenlosen Nachrichten für heute sind aufgebraucht. Neue um 00:00 UTC.",
    "fr": "Tu n'as plus de messages gratuits aujourd'hui. De nouveaux à 00:00 UTC.",
    "uk": "Безкоштовні повідомлення на сьогодні вичерпано. Нові — о 00:00 UTC.",
    "tr": "Bugünlük ücretsiz mesajların bitti. Yenileri 00:00 UTC'de gelir.",
    "zh": "今日免费消息已用完。新消息将于 UTC 00:00 发放。",
    "ar": "نفدت رسائلك المجانية لهذا اليوم. تصل رسائل جديدة عند 00:00 UTC.",
}


def ai_daily_limit_message(lang: str = "ru") -> str:
    """Compact inline notice when a free user runs out of daily messages — a quiet
    'limit reached, wait or upgrade' line, NOT a full-screen purchase menu."""
    txt = _DAILY_TXT_MINI.get(lang, _DAILY_TXT_MINI["en"])
    upsell = _LIMIT_UPSELL.get(lang, _LIMIT_UPSELL["en"])
    return _limit_mini(txt, upsell)


# ── Transient "service busy" message (i18n) ───────────────────────────────────
# Shown when every free provider transiently fails (network blip / upstream 5xx).
# A calm "try again" card — NEVER a scary 503 — and the user is NOT charged.
_BUSY_TXT = {
    "ru": "ALEX сейчас перегружен. Попробуй ещё раз через пару секунд 🙏",
    "en": "ALEX is a bit busy right now. Please try again in a few seconds 🙏",
    "es": "ALEX está un poco ocupado ahora. Inténtalo de nuevo en unos segundos 🙏",
    "pt": "O ALEX está um pouco ocupado agora. Tente novamente em alguns segundos 🙏",
    "de": "ALEX ist gerade etwas ausgelastet. Bitte versuche es in ein paar Sekunden erneut 🙏",
    "fr": "ALEX est un peu occupé là. Réessaie dans quelques secondes 🙏",
    "uk": "ALEX зараз трохи перевантажений. Спробуй ще раз за кілька секунд 🙏",
    "tr": "ALEX şu an biraz meşgul. Birkaç saniye sonra tekrar dene 🙏",
    "zh": "ALEX 现在有点忙，请过几秒再试一次 🙏",
    "ar": "ALEX مشغول قليلاً الآن. حاول مرة أخرى بعد بضع ثوانٍ 🙏",
}


def ai_busy_message(lang: str = "ru") -> str:
    """Plain-text transient-error reply for free chat (no charge, retry-friendly)."""
    return _BUSY_TXT.get(lang, _BUSY_TXT["en"])

"""
billing_config.py — runtime-tunable AI billing config for PolyGlotty.

Precedence (lowest → highest):
    1. _DEFAULTS in this file
    2. environment variables (BILLING_*)              ← set in Railway
    3. rows in the `app_config` DB table               ← change LIVE, no deploy

Everything the billing logic needs to make money decisions lives here so the
owner can retune prices / limits / allowances without shipping code. Values are
cached in-process for CONFIG_TTL seconds so a DB read isn't done on every chat.

Money model (all amounts in *credits* unless noted):
  - TOKENS_PER_CREDIT  : how many Anthropic tokens 1 credit buys.
  - MODELS[m].in/out_per_1k : real Anthropic price (USD) per 1K tokens.
  - MARKUP             : target gross margin on sold credits (0.6 = 60%).
  - PLATFORM_FEE       : Telegram Stars commission we lose on top-ups (~0.30).
  - SUBSCRIPTION_ALLOWANCE : monthly credits a subscriber gets (used before
                             their prepaid credits). Separate pool.
"""

from __future__ import annotations

import os
import json
import time
import math

CONFIG_TTL = 60  # seconds

_DEFAULTS = {
    # Real Anthropic API prices, USD per 1K tokens. Keep a safety cushion above
    # the public rate so a price hike upstream doesn't silently eat the margin.
    "MODELS": {
        "haiku":  {"in_per_1k": 0.0008, "out_per_1k": 0.0040},
        "sonnet": {"in_per_1k": 0.0030, "out_per_1k": 0.0150},
    },
    "TOKENS_PER_CREDIT": 1000,
    "MARKUP": 0.6,
    "PLATFORM_FEE": 0.30,
    # Real money the owner RECEIVES on withdrawal per 1 Telegram Star, AFTER
    # Telegram's commission — NOT the ~$0.0145 retail price a user pays. Margin
    # and admin revenue are computed from THIS so the numbers reflect payout
    # reality, not the sticker price. Owner can refine without a deploy.
    "STAR_PAYOUT_USD": 0.013,
    # Blended selling rate across the credit packs (stars charged per credit).
    # Used only to estimate owner revenue in the admin panel from credits sold.
    # Packs: 149⭐/100, 599⭐/500, 1990⭐/2000 → ≈1.0–1.49 ⭐/cr, blended ~1.2.
    "SELL_STARS_PER_CREDIT": 1.2,
    # Which logical model each chat mode runs on. Plain conversation → cheap
    # Haiku; graded / exam / grammar work → Sonnet. Owner-overridable.
    "MODEL_BY_MODE": {
        "correction":  "haiku",
        "speaking":    "haiku",
        "vocab":       "haiku",
        "grammar":     "sonnet",
        "lesson_help": "sonnet",
        "test":        "sonnet",
        "toefl":       "sonnet",
        "tone_editor": "sonnet",
    },
    # ── Flat per-message price (budget guard) ──────────────────────────
    # ALEX is sold at a STRICT flat price per reply: every message costs exactly
    # ALEX_MESSAGE_COST credits, drawn from allowance first then prepaid credits.
    # When BILLING_FLAT_COST is True the reserve/reconcile path holds and charges
    # this flat amount (no token-based refund) so the cost is 100% predictable
    # for both the user and the owner. Set False to fall back to token-metered
    # billing (reserve sized on tokens, refund the unused part).
    "BILLING_FLAT_COST": True,
    "ALEX_MESSAGE_COST": 6,            # credits per ALEX reply (flat)
    # Referral reward — credits granted to the inviter per VALID new referral
    # (0.5× the price of one message). Anti-abuse gates live in apply_referral /
    # cmd_start (one ref per Telegram ID, brand-new users only).
    "REFERRAL_REWARD_CREDITS": 3,
    # Hard limits — the owner can never go negative.
    "MAX_TOKENS_PER_REPLY": 250,       # caps Anthropic max_tokens (OUTPUT only)
    # Upper bound on INPUT tokens (system prompt + cached context + history) used
    # ONLY to size the up-front reserve so it covers the worst case. The reserve
    # is refunded down to the real total afterwards, so over-estimating here is
    # safe — it just holds a few extra credits for a moment.
    "MAX_INPUT_TOKENS": 8000,
    "VOICE_SURCHARGE_CREDITS": 5,      # flat add for voice (STT+TTS) requests
    "DAILY_TOKENS_PER_USER": 120000,   # anti-abuse / runaway bot guard
    "GLOBAL_DAILY_BUDGET_USD": 25.0,   # kill-switch across ALL users per day
    # ── Saved-words dictionary caps (anti-hoarding / DB-load guard) ─────
    # Hard ceiling on how many words a user may hold in the active SRS review
    # queue at once. Forces learning old words instead of endlessly piling new
    # ones (and bounds per-user rows / list payloads). Tunable live, no deploy.
    "VOCAB_QUEUE_FREE": 50,            # max saved words for free users
    "VOCAB_QUEUE_PREMIUM": 200,        # max saved words for Premium users
    "VOCAB_PAGE_SIZE": 15,             # rows the dictionary list returns per page
    # ── Hearts (free-tier lesson lives) ────────────────────────────────
    # Free users get a small pool of "lives": a wrong answer in a lesson costs
    # 1 heart. Hearts regenerate on the SERVER clock (1 per HEARTS_REGEN_HOURS)
    # or can be refilled instantly via Telegram Stars. Premium = unlimited
    # (never decremented). All values tunable live, no deploy.
    "HEARTS_MAX": 5,                   # max lives for a free user
    "HEARTS_REGEN_HOURS": 2,           # hours to regenerate 1 heart
    "HEARTS_REFILL_STARS": 30,         # ⭐ to instantly refill to full
    # ── Daily lesson limit (free-tier) ─────────────────────────────────
    # Free users may successfully COMPLETE this many lessons per UTC day;
    # starting the next one is blocked by a compact paywall. The day boundary
    # is the SERVER clock (UTC 00:00) so it can't be farmed by the device clock.
    # Premium = unlimited. Tunable live (BILLING_LESSONS_FREE_DAILY), no deploy.
    "LESSONS_FREE_DAILY": 3,           # lessons/day a free user can complete
    # ── Daily free-AI limit (ALEX anti-drain) ──────────────────────────
    # Free users share ONE project AI key (DeepSeek/Gemini). A free user may send
    # this many ALEX messages per UTC day; the (N+1)-th is blocked with a 429 →
    # "come back tomorrow / go Premium" card. Premium users go to Claude and are
    # NOT metered here. Tunable live (BILLING_AI_FREE_DAILY), no deploy.
    "AI_FREE_DAILY": 4,                # free ALEX messages/day before paywall
    # ── Free ALEX credit wallet (daily-replenishing, Gemini-only) ───────
    # Free users get a tiny CREDIT balance instead of a raw request counter:
    # +FREE_CREDITS_DAILY topped up at UTC 00:00, accumulating up to
    # FREE_CREDITS_CAP. One free Gemini reply costs 1 credit. Separate bucket
    # from prepaid `chat_credits` (which never expire / are never capped here).
    # FREE_AI_MAX_CHARS bounds the prompt (system + trimmed history + message)
    # a free reply may carry, so a single huge message can't burn the shared key.
    "FREE_CREDITS_DAILY": 2,           # credits granted each UTC day
    "FREE_CREDITS_CAP": 10,            # max accumulated free credits
    "FREE_AI_MAX_CHARS": 6000,         # server-side context-size cap for free chat
    # ── Subscription request-counter caps (premium_type FREE/MONTH_1/MONTH_6) ──
    # The new monetization model: a paid plan grants a WHOLE-PERIOD pool of ALEX
    # requests (SUB_TOTAL_REQUESTS) plus a hard DAILY ceiling (SUB_DAILY_LIMIT).
    # Both are checked before every premium ALEX call (database.check_ai_access)
    # and tunable live / via env without a deploy.
    "SUB_DAILY_LIMIT":    {"MONTH_1": 50,   "MONTH_6": 75},
    "SUB_TOTAL_REQUESTS": {"MONTH_1": 1500, "MONTH_6": 13500},
    # Premium chat memory depth: how many recent messages the Pro model sees so
    # it perfectly remembers a long, complex conversation.
    "PREMIUM_HISTORY": 18,             # last N messages sent for paid users
    # ── Daily flashcard limit (economy guard, both tiers) ──────────────
    # Number of flashcards a user may COMPLETE (Skip or Save) per UTC day.
    # Free users hit a hard wall → paywall. Premium users hit a softer cap
    # and get a "great work, rest now" message (retention, not monetization).
    # Day boundary = SERVER clock (UTC 00:00) so it can't be device-farmed.
    # Tunable live (BILLING_CARDS_FREE_DAILY / BILLING_CARDS_PREMIUM_DAILY).
    "CARDS_FREE_DAILY": 15,            # cards/day for a free user (then paywall)
    "CARDS_PREMIUM_DAILY": 60,         # cards/day for Premium (then rest message)
    # Subscription
    # The Platform subscription does NOT include any "unlimited" or monthly AI
    # pool — its ALEX value is the flat starter-credits grant handled at purchase
    # time (PLATFORM_PLANS[...].starter_credits in bot.py). Keep this at 0 so the
    # monthly allowance refill never tops a subscriber up for free.
    "SUBSCRIPTION_ALLOWANCE": 0,       # credits granted each period (0 = none)
    "ALLOWANCE_DAYS": 30,
    # Master switch for the v2 reserve→reconcile path. If False, chat falls
    # back to the legacy per-message spend_credits behaviour.
    "BILLING_V2_ENABLED": True,
}

# ENV var name → (config key, caster). Nested structures use JSON env vars.
_ENV_SCALARS = {
    "BILLING_TOKENS_PER_CREDIT": ("TOKENS_PER_CREDIT", int),
    "BILLING_MARKUP": ("MARKUP", float),
    "BILLING_PLATFORM_FEE": ("PLATFORM_FEE", float),
    "BILLING_STAR_PAYOUT_USD": ("STAR_PAYOUT_USD", float),
    "BILLING_SELL_STARS_PER_CREDIT": ("SELL_STARS_PER_CREDIT", float),
    "BILLING_MAX_TOKENS_PER_REPLY": ("MAX_TOKENS_PER_REPLY", int),
    "BILLING_MAX_INPUT_TOKENS": ("MAX_INPUT_TOKENS", int),
    "BILLING_ALEX_MESSAGE_COST": ("ALEX_MESSAGE_COST", int),
    "BILLING_REFERRAL_REWARD_CREDITS": ("REFERRAL_REWARD_CREDITS", int),
    "BILLING_VOICE_SURCHARGE_CREDITS": ("VOICE_SURCHARGE_CREDITS", int),
    "BILLING_DAILY_TOKENS_PER_USER": ("DAILY_TOKENS_PER_USER", int),
    "BILLING_GLOBAL_DAILY_BUDGET_USD": ("GLOBAL_DAILY_BUDGET_USD", float),
    "BILLING_SUBSCRIPTION_ALLOWANCE": ("SUBSCRIPTION_ALLOWANCE", int),
    "BILLING_ALLOWANCE_DAYS": ("ALLOWANCE_DAYS", int),
    "BILLING_VOCAB_QUEUE_FREE": ("VOCAB_QUEUE_FREE", int),
    "BILLING_VOCAB_QUEUE_PREMIUM": ("VOCAB_QUEUE_PREMIUM", int),
    "BILLING_VOCAB_PAGE_SIZE": ("VOCAB_PAGE_SIZE", int),
    "BILLING_HEARTS_MAX": ("HEARTS_MAX", int),
    "BILLING_HEARTS_REGEN_HOURS": ("HEARTS_REGEN_HOURS", int),
    "BILLING_HEARTS_REFILL_STARS": ("HEARTS_REFILL_STARS", int),
    "BILLING_LESSONS_FREE_DAILY": ("LESSONS_FREE_DAILY", int),
    "BILLING_AI_FREE_DAILY": ("AI_FREE_DAILY", int),
    "BILLING_FREE_CREDITS_DAILY": ("FREE_CREDITS_DAILY", int),
    "BILLING_FREE_CREDITS_CAP": ("FREE_CREDITS_CAP", int),
    "BILLING_FREE_AI_MAX_CHARS": ("FREE_AI_MAX_CHARS", int),
    "BILLING_CARDS_FREE_DAILY": ("CARDS_FREE_DAILY", int),
    "BILLING_CARDS_PREMIUM_DAILY": ("CARDS_PREMIUM_DAILY", int),
    "BILLING_PREMIUM_HISTORY": ("PREMIUM_HISTORY", int),
}
_ENV_JSON = {
    "BILLING_MODELS": "MODELS",
    "BILLING_MODEL_BY_MODE": "MODEL_BY_MODE",
    "BILLING_SUB_DAILY_LIMIT": "SUB_DAILY_LIMIT",
    "BILLING_SUB_TOTAL_REQUESTS": "SUB_TOTAL_REQUESTS",
}

_cache = {"data": None, "ts": 0.0}


def _deep_copy(d):
    return json.loads(json.dumps(d))


def _coerce_bool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _apply_env(cfg: dict):
    for env_name, (key, cast) in _ENV_SCALARS.items():
        raw = os.getenv(env_name)
        if raw is None or raw == "":
            continue
        try:
            cfg[key] = cast(raw)
        except Exception:
            pass
    if os.getenv("BILLING_V2_ENABLED") is not None:
        cfg["BILLING_V2_ENABLED"] = _coerce_bool(os.getenv("BILLING_V2_ENABLED"))
    if os.getenv("BILLING_FLAT_COST") is not None:
        cfg["BILLING_FLAT_COST"] = _coerce_bool(os.getenv("BILLING_FLAT_COST"))
    for env_name, key in _ENV_JSON.items():
        raw = os.getenv(env_name)
        if not raw:
            continue
        try:
            val = json.loads(raw)
            if isinstance(cfg.get(key), dict) and isinstance(val, dict):
                cfg[key].update(val)
            else:
                cfg[key] = val
        except Exception:
            pass


def _apply_kv(cfg: dict, key: str, raw_value: str):
    """Apply one app_config row. Values are JSON-encoded; we fall back to a few
    sensible string casts so a hand-typed value still works."""
    if key not in _DEFAULTS:
        return
    cur = cfg.get(key)
    val = None
    try:
        val = json.loads(raw_value)
    except Exception:
        # Not JSON — coerce against the default's type.
        if isinstance(cur, bool):
            val = _coerce_bool(raw_value)
        elif isinstance(cur, int):
            try: val = int(raw_value)
            except Exception: return
        elif isinstance(cur, float):
            try: val = float(raw_value)
            except Exception: return
        else:
            val = raw_value
    if isinstance(cur, dict) and isinstance(val, dict):
        cur.update(val)
    else:
        cfg[key] = val


async def load_config(force: bool = False) -> dict:
    """Return the merged config dict (cached). Async because the DB layer is."""
    now = time.time()
    if not force and _cache["data"] is not None and (now - _cache["ts"]) < CONFIG_TTL:
        return _cache["data"]
    cfg = _deep_copy(_DEFAULTS)
    _apply_env(cfg)
    try:
        from database import config_get_all
        rows = await config_get_all()
        for k, v in (rows or {}).items():
            _apply_kv(cfg, k, v)
    except Exception:
        # DB unavailable → ENV+defaults are still a safe, conservative config.
        pass
    _cache["data"] = cfg
    _cache["ts"] = now
    return cfg


def invalidate_cache():
    _cache["data"] = None
    _cache["ts"] = 0.0


# ── Derived economics (pure functions over a loaded cfg) ───────────
def cost_per_credit(cfg: dict, model: str = "sonnet") -> float:
    """Conservative USD cost of one credit's worth of tokens, priced at the
    model's OUTPUT rate (the expensive side) so we never under-price."""
    tpc = max(1, int(cfg.get("TOKENS_PER_CREDIT", 1000)))
    m = cfg.get("MODELS", {}).get(model) or cfg["MODELS"]["sonnet"]
    return (tpc / 1000.0) * float(m["out_per_1k"])


def price_per_credit(cfg: dict, model: str = "sonnet") -> float:
    """USD a user must pay per credit to hit MARKUP after Telegram's fee.

        price = (cost / (1 - MARKUP)) / (1 - PLATFORM_FEE)
    """
    cpc = cost_per_credit(cfg, model)
    markup = float(cfg.get("MARKUP", 0.6))
    fee = float(cfg.get("PLATFORM_FEE", 0.30))
    markup = min(max(markup, 0.0), 0.95)
    fee = min(max(fee, 0.0), 0.95)
    return (cpc / (1.0 - markup)) / (1.0 - fee)


def stars_per_credit(cfg: dict, model: str = "sonnet", usd_per_star: float | None = None) -> float:
    """Convert price_per_credit (USD) into Telegram Stars. usd_per_star defaults
    to the configured STAR_PAYOUT_USD (real per-star payout). Owner can refine."""
    if usd_per_star is None:
        usd_per_star = float(cfg.get("STAR_PAYOUT_USD", 0.013))
    return price_per_credit(cfg, model) / max(1e-6, usd_per_star)


def owner_revenue_usd(cfg: dict, credits_sold: int) -> float:
    """Real USD the owner nets from selling `credits_sold` credits, based on the
    blended pack rate (stars/credit) and the configured per-star PAYOUT (after
    Telegram's cut) — not the retail sticker price. This is the number the admin
    margin is measured against."""
    sell_rate = float(cfg.get("SELL_STARS_PER_CREDIT", 1.2))
    payout = float(cfg.get("STAR_PAYOUT_USD", 0.013))
    return max(0, int(credits_sold)) * sell_rate * payout

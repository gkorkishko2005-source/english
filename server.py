"""
PolyGlotty - API server v3
"""
import os, json, logging, hmac, hashlib, time, re
from pathlib import Path
from urllib.parse import parse_qsl
from aiohttp import web
import httpx

logger      = logging.getLogger(__name__)
WEBAPP_DIR  = Path(__file__).parent / "webapp"
RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
BOT_NAME    = os.getenv("BOT_NAME", "PolyGlotty_bot")
# Hard ceiling on ALEX OUTPUT tokens per reply — applied to EVERY chat path
# (credit, legacy and grandfathered) so a single reply can never blow the
# budget. Owner-tunable via env without a deploy.
MAX_REPLY_TOKENS_HARD = int(os.getenv("BILLING_MAX_TOKENS_PER_REPLY", "250") or "250")
ANT_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
REQUIRE_TG_INIT_DATA = os.getenv("REQUIRE_TG_INIT_DATA", "1") != "0"
# ── Mandatory channel-subscription gate ────────────────────────────────
# The user must be a member of TG_CHANNEL before the bot/Mini App unlocks.
# Verified server-side via Telegram getChatMember (bot must be channel admin).
TG_CHANNEL_USERNAME = os.getenv("TG_CHANNEL_USERNAME", "@polyglotty_daily").strip()
TG_CHANNEL_ID       = os.getenv("TG_CHANNEL_ID", "-1003987215459").strip()
TG_CHANNEL_URL      = os.getenv("OFFICIAL_CHANNEL_URL",
                                "https://t.me/" + TG_CHANNEL_USERNAME.lstrip("@")).strip()
REQUIRE_CHANNEL_SUB = os.getenv("REQUIRE_CHANNEL_SUB", "1") != "0"
MODEL       = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
SONNET_MODEL = os.getenv("CLAUDE_BASIC_SONNET_MODEL", "claude-sonnet-4-20250514")
SONNET_PLUS_MODEL = os.getenv("CLAUDE_PRO_SONNET_MODEL", "claude-sonnet-4-6")
OPUS_MODEL   = os.getenv("CLAUDE_OPUS_MODEL", "claude-opus-4-1-20250805")
OPUS_PLUS_MODEL = os.getenv("CLAUDE_OPUS_PLUS_MODEL", "claude-opus-4-7")
OPUS_MAX_MODEL  = os.getenv("CLAUDE_OPUS_MAX_MODEL", "claude-opus-4-8")

MODEL_ECONOMY = {
    "haiku": {
        "model": MODEL,
        "weight": 1,
        "max_tokens": {"free": 500, "basic": 650, "pro": 750, "ultimate": 850},
        # Approx public Anthropic API prices per 1M tokens.
        "input_per_m": 0.80,
        "output_per_m": 4.00,
    },
    "sonnet": {
        "model": SONNET_PLUS_MODEL,
        "weight": 5,
        "max_tokens": {"free": 750, "basic": 850, "pro": 1050, "ultimate": 1300},
        "input_per_m": 3.00,
        "output_per_m": 15.00,
    },
    "sonnet4": {
        "model": SONNET_MODEL,
        "weight": 4,
        "max_tokens": {"basic": 700, "pro": 900, "ultimate": 1100},
        "input_per_m": 3.00,
        "output_per_m": 15.00,
    },
    "opus41": {
        "model": OPUS_MODEL,
        "weight": 12,
        "max_tokens": {"ultimate": 1100},
        "input_per_m": 15.00,
        "output_per_m": 75.00,
    },
    "opus": {
        "model": OPUS_PLUS_MODEL,
        "weight": 14,
        "max_tokens": {"ultimate": 1100},
        "input_per_m": 15.00,
        "output_per_m": 75.00,
    },
    "opus48": {
        "model": OPUS_MAX_MODEL,
        "weight": 18,
        "max_tokens": {"ultimate": 1200},
        "input_per_m": 15.00,
        "output_per_m": 75.00,
    },
}

TIER_ECONOMY = {
    "free":     {"quota": 5,   "models": ("haiku",),                                                   "daily_budget": 0.025, "history": 18, "burst_gap": 5.0},
    "basic":    {"quota": 45,  "models": ("haiku", "sonnet4"),                                         "daily_budget": 0.12,  "history": 35, "burst_gap": 2.0},
    "pro":      {"quota": 110, "models": ("haiku", "sonnet4", "sonnet"),                               "daily_budget": 0.35,  "history": 55, "burst_gap": 1.5},
    "ultimate": {"quota": 260, "models": ("haiku", "sonnet4", "sonnet", "opus41", "opus", "opus48"),    "daily_budget": 0.95,  "history": 90, "burst_gap": 1.2},
    # Single modular subscription: chat is paid per-message in ALEX credits,
    # so every model is unlocked. Credits are the real gate (checked + spent
    # below), hence quota / daily_budget are effectively uncapped here.
    "platform": {"quota": 10**9, "models": ("haiku", "sonnet4", "sonnet", "opus41", "opus", "opus48"),  "daily_budget": 10**9, "history": 90, "burst_gap": 1.2},
}

# ══ ADMIN WHITELIST ══════════════════════════════════════════════════════════
# Add Telegram user IDs here for admin tools. Premium is database-driven only.
# To find your ID: message @userinfobot in Telegram
ADMIN_IDS = {
    1738695057,
    5399839500,
    725259177,
    1241890707,
    1428437531,
}

# Primary provider is OpenRouter (Gemini Flash for FREE, Gemini Pro for Premium).
# The app no longer depends on Anthropic — ANTHROPIC_API_KEY is an optional legacy
# safety-net only, so its absence must NEVER block startup or a chat request.
try:
    from ai_router import openrouter_available as _or_ok
    if _or_ok():
        logger.info("✅ OpenRouter configured — ALEX runs on Gemini via OpenRouter.")
    else:
        logger.error("❌ OPENROUTER_API_KEY is not set! ALEX chat will be unavailable.")
except Exception as _e:
    logger.warning("ai_router availability check failed: %s", _e)

_histories: dict = {}
_msg_counts: dict = {}  # daily quota points per user
_daily_ai_costs: dict = {}  # estimated daily Anthropic cost per user
_last_msg_ts: dict = {}  # last-message timestamp per uid (anti-burst throttle)


def _throttle_wait(uid, gap: float, bucket: str = "chat") -> int:
    """Shared per-user rate limiter. Returns the seconds a caller must wait
    before its next request, or 0 if it may proceed. On a green light it also
    records 'now' so the next call is measured from this one.

    One in-memory dict keyed by (uid, bucket) lets different endpoints
    (chat, exam grading) keep independent cooldowns without extra plumbing.
    Admins bypass. Backed by process memory (single-worker aiohttp); swap the
    dict for Redis if the app is ever scaled horizontally."""
    try:
        if not uid or int(uid) in ADMIN_IDS:
            return 0
    except Exception:
        pass
    import time as _t
    now = _t.time()
    key = (uid, bucket)
    last = _last_msg_ts.get(key, 0.0)
    if now - last < gap:
        return max(1, int(gap - (now - last)) + 1)
    _last_msg_ts[key] = now
    return 0

# Anti-deficit input guard. Premium conversations can grow without bound; a long
# history is billed on EVERY turn (input tokens), so we cap the context we send.
PREMIUM_HISTORY_TOKEN_BUDGET = int(os.getenv("PREMIUM_HISTORY_TOKEN_BUDGET", "4000") or "4000")


def _approx_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate (~4 chars / token, English-biased)."""
    return max(1, (len(text or "") + 3) // 4)


def _trim_history_by_tokens(messages: list, budget: int = PREMIUM_HISTORY_TOKEN_BUDGET) -> list:
    """Keep only the most RECENT messages whose combined size fits `budget`
    tokens. Older turns are dropped (archived out of the live context) so the
    per-turn input cost stays bounded even for Premium users. The latest message
    is always kept, even if it alone exceeds the budget."""
    if not messages:
        return []
    kept: list = []
    used = 0
    for m in reversed(messages):
        cost = _approx_tokens(str(m.get("content") or ""))
        if kept and used + cost > budget:
            break
        kept.append(m)
        used += cost
    kept.reverse()
    return kept

def _is_local_request(request) -> bool:
    host = (request.host or "").split(":")[0]
    peer = request.transport.get_extra_info("peername") if request.transport else None
    ip = peer[0] if peer else ""
    return host in {"localhost", "127.0.0.1", "::1"} or ip in {"127.0.0.1", "::1"}

def _verify_telegram_init_data(init_data: str, expected_uid: int | None = None, max_age: int = 86400):
    if not BOT_TOKEN:
        return False, "bot token missing", None
    if not init_data:
        return False, "missing init data", None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", "")
        if not received_hash:
            return False, "missing hash", None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, received_hash):
            return False, "bad signature", None
        auth_date = int(pairs.get("auth_date") or 0)
        if auth_date and time.time() - auth_date > max_age:
            return False, "expired init data", None
        user_raw = pairs.get("user") or "{}"
        tg_user = json.loads(user_raw)
        signed_uid = int(tg_user.get("id") or 0)
        if expected_uid and signed_uid != int(expected_uid):
            return False, "uid mismatch", signed_uid
        return True, "", signed_uid
    except Exception as e:
        return False, str(e), None

# ── PREMIUM ACCESS GUARD (Middleware) ─────────────────────────────────────────
# Endpoints listed in GATED_PREMIUM_ROUTES require an active subscription
# (or admin). Everyone else gets a paywall card with a ⭐️ CTA — the upstream
# (Anthropic) call never fires. The set is env-driven so the wall can be turned
# on/off per route WITHOUT a code edit. Default gates the two unbilled
# AI-generation endpoints (/api/lesson, /api/test), which have no client caller
# and would otherwise be an uncharged, unauthenticated LLM hole.
import functools
GATED_PREMIUM_ROUTES = set(
    r for r in (os.getenv("GATED_PREMIUM_ROUTES", "lesson,test").replace(" ", "").split(",")) if r
)

def _paywall_json(lang: str = "ru") -> dict:
    ru = (lang == "ru")
    card = (
        '<div class="limit-card">'
        '<div class="limit-kicker">PolyGlotty ⭐️</div>'
        f'<div class="limit-title">{"Нужна подписка" if ru else "Subscription required"}</div>'
        f'<div class="limit-text">{"Эта функция доступна по подписке PolyGlotty. Оформи подписку, чтобы открыть доступ." if ru else "This feature is available with a PolyGlotty subscription. Subscribe to unlock it."}</div>'
        f'<button class="chip" onclick="openPremium()">{"Оформить" if ru else "Subscribe"}</button>'
        '</div>'
    )
    return {"reply": card, "premium_required": True}

async def _guard_identity(request):
    """Pull a verified uid (+ lang) from a request body/headers without
    consuming it for the wrapped handler (aiohttp caches the read body)."""
    uid = 0
    lang = "ru"
    init_data = ""
    try:
        if request.method == "POST" and request.can_read_body:
            body = await request.json()
            uid = int(body.get("uid", 0) or 0)
            lang = str(body.get("lang", "ru") or "ru")
            init_data = str(body.get("init_data") or "")
    except Exception:
        pass
    init_data = init_data or str(request.headers.get("X-Telegram-Init-Data") or "")
    if REQUIRE_TG_INIT_DATA and BOT_TOKEN and not _is_local_request(request):
        ok, _reason, signed_uid = _verify_telegram_init_data(init_data, uid)
        if not ok:
            return 0, lang  # unsigned → no premium → paywall
        if signed_uid and not uid:
            uid = signed_uid
    return uid, lang

def require_premium(name: str):
    """Decorator factory. Wrap a handler so non-subscribers hit a paywall.
    Routes absent from GATED_PREMIUM_ROUTES pass straight through (gate off)."""
    def deco(handler):
        @functools.wraps(handler)
        async def wrapped(request):
            if name not in GATED_PREMIUM_ROUTES:
                return await handler(request)
            uid, lang = await _guard_identity(request)
            if uid in ADMIN_IDS:
                return await handler(request)
            try:
                # check_platform covers ALL paid paths (active Platform sub,
                # grandfathered bundle AND active legacy premium). Using the
                # legacy-only check_premium here wrongly paywalled Platform-only
                # subscribers on gated routes (lesson/test).
                from database import check_platform
                if uid and await check_platform(uid):
                    return await handler(request)
            except Exception as e:
                logger.warning("require_premium check failed route=%s uid=%s: %s", name, uid, e)
            return web.json_response(
                _paywall_json(lang), status=402,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        return wrapped
    return deco

def _estimate_ai_cost(model_key: str, usage: dict) -> float:
    cfg = MODEL_ECONOMY.get(model_key, MODEL_ECONOMY["haiku"])
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    billable_input = max(0, input_tokens - cache_creation - cache_read)
    input_cost = (
        billable_input * cfg["input_per_m"]
        + cache_creation * cfg["input_per_m"] * 1.25
        + cache_read * cfg["input_per_m"] * 0.10
    ) / 1_000_000
    output_cost = output_tokens * cfg["output_per_m"] / 1_000_000
    return round(input_cost + output_cost, 6)

def _limit_message(lang: str, tier: str, used: int, quota: int) -> str:
    import random
    paid = tier != "free"
    copy = {
        "ru": {
            "free": [
                "ALEX Chat открывается по подписке. В Free остаются карточки, задания, путь и прогресс.",
                "Бесплатная практика доступна без чата. Чтобы писать ALEX, открой любую подписку.",
                "Free помогает учиться каждый день, а живой чат с ALEX начинается с подписки.",
            ],
            "paid": [
                "Ты сегодня сделал много прогресса. Лимит quota points закончился — можно спокойно погулять, мозгу тоже нужно закрепление.",
                "Дневной лимит достигнут. Новые слова лучше закрепятся после паузы, продолжим завтра.",
                "Практика на сегодня выполнена. Если хочется больше, можно поднять план.",
            ],
            "btn_free": "Открыть тарифы",
            "btn_paid": "Увеличить лимит",
            "meta": f"{used}/{quota} quota points",
        },
        "en": {
            "free": [
                "ALEX Chat starts with a subscription. Free still includes cards, tasks, paths and progress.",
                "Free practice is available without chat. Upgrade to any subscription to write to ALEX.",
                "Free keeps daily study open; live ALEX chat starts with a subscription.",
            ],
            "paid": [
                "You made a lot of progress today. Quota points are done — take a walk and let it settle.",
                "Daily limit reached. A short break helps new English stick; continue tomorrow.",
                "Today's practice is complete. Upgrade if you want a higher daily ceiling.",
            ],
            "btn_free": "Open plans",
            "btn_paid": "Increase limit",
            "meta": f"{used}/{quota} quota points",
        },
    }
    t = copy.get(lang, copy["en"])
    title = "Дневной лимит" if lang == "ru" else "Daily limit"
    body = random.choice(t["paid" if paid else "free"])
    btn = "" if tier == "ultimate" else f'<button class="chip" onclick="openPremium()">{t["btn_paid" if paid else "btn_free"]}</button>'
    return (
        '<div class="limit-card">'
        f'<div class="limit-kicker">{t["meta"]}</div>'
        f'<div class="limit-title">{title}</div>'
        f'<div class="limit-text">{body}</div>'
        f'{btn}'
        '</div>'
    )

def _billing_block_message(lang: str, reason: str) -> dict:
    """Response payload when AI billing v2 blocks a request BEFORE any API call.
    reason ∈ insufficient_funds | daily_token_limit | global_budget."""
    ru = lang == "ru"
    if reason == "insufficient_funds":
        if ru:
            card = ('<div class="limit-card"><div class="limit-kicker">ALEX Chat</div>'
                    '<div class="limit-title">Нужны кредиты ALEX</div>'
                    '<div class="limit-text">Закончились кредиты и месячный лимит подписки. '
                    'Пополни баланс, чтобы продолжить — кредиты не сгорают.</div>'
                    '<button class="chip" onclick="openPremium()">Купить кредиты</button></div>')
        else:
            card = ('<div class="limit-card"><div class="limit-kicker">ALEX Chat</div>'
                    '<div class="limit-title">ALEX credits required</div>'
                    '<div class="limit-text">Your subscription allowance and credits are used up. '
                    'Top up to keep chatting — credits never expire.</div>'
                    '<button class="chip" onclick="openPremium()">Buy credits</button></div>')
        return {"reply": card, "premium_required": True, "credits_required": True}
    if reason == "daily_token_limit":
        card = ('<div class="limit-card"><div class="limit-kicker">ALEX Chat</div>'
                '<div class="limit-title">Дневной лимит достигнут</div>'
                '<div class="limit-text">Сегодня ты много занимался. Продолжим завтра — '
                'паузы помогают закреплять материал.</div></div>') if ru else (
                '<div class="limit-card"><div class="limit-kicker">ALEX Chat</div>'
                '<div class="limit-title">Daily limit reached</div>'
                '<div class="limit-text">You\'ve studied a lot today. Let\'s continue tomorrow — '
                'breaks help things stick.</div></div>')
        return {"reply": card, "limit": True}
    # global_budget
    card = ('<div class="limit-card"><div class="limit-kicker">ALEX Chat</div>'
            '<div class="limit-title">ALEX немного перегружен</div>'
            '<div class="limit-text">Сейчас слишком много запросов. Загляни чуть позже — '
            'скоро всё освободится.</div></div>') if ru else (
            '<div class="limit-card"><div class="limit-kicker">ALEX Chat</div>'
            '<div class="limit-title">ALEX is busy right now</div>'
            '<div class="limit-text">Too many requests at the moment. Please try again a bit '
            'later.</div></div>')
    return {"reply": card, "limit": True, "busy": True}

# ── STATIC ──────────────────────────────────────────────────────────────────
async def handle_index(request):
    html_path = WEBAPP_DIR / "index.html"
    if not html_path.exists():
        return web.Response(text="WebApp not found", status=404)
    html = html_path.read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html", charset="utf-8",
                        headers={
                            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                            "Pragma": "no-cache",
                            "Expires": "0",
                        })

async def handle_sw(request):
    """Serve the Service Worker from the site root so its scope covers the whole
    app. We send it with no-cache so a new sw.js is always picked up on the next
    visit (the SW's own activate handler then cleans up stale caches), and with
    Service-Worker-Allowed:/ so the root scope is permitted."""
    sw_path = WEBAPP_DIR / "sw.js"
    if not sw_path.exists():
        return web.Response(text="// sw missing", content_type="application/javascript",
                            status=404)
    js = sw_path.read_text(encoding="utf-8")
    return web.Response(text=js, content_type="application/javascript", charset="utf-8",
                        headers={
                            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                            "Service-Worker-Allowed": "/",
                        })

# Долгий кэш для редко меняющейся статики (иконки, аватарка, шрифты).
# index.html и /api/* не трогаем — они должны быть всегда свежими.
_STATIC_CACHE_EXT = (".png",".jpg",".jpeg",".webp",".gif",".svg",".ico",
                     ".woff",".woff2",".ttf",".otf")

@web.middleware
async def cache_static_mw(request, handler):
    resp = await handler(request)
    try:
        path = request.path.lower()
        if (resp.status == 200 and not path.startswith("/api/")
                and path.endswith(_STATIC_CACHE_EXT)
                and "Cache-Control" not in resp.headers):
            resp.headers["Cache-Control"] = "public, max-age=604800"
    except Exception as e:
        logger.warning(f"cache_static_mw: {e}")
    return resp

# Content types worth gzipping (text-like, highly compressible). Binary assets
# (images, fonts, audio) are already compressed — skip them.
_COMPRESSIBLE_CT = ("text/", "application/json", "application/javascript",
                    "application/xml", "image/svg", "application/manifest")

@web.middleware
async def gzip_mw(request, handler):
    """Transparent gzip/deflate for text responses. Cuts the ~1MB index.html
    and large JSON payloads (lessons, vocab) by 70-85% over the wire — the
    single biggest win for cold-start load time and battery on mobile."""
    resp = await handler(request)
    try:
        ae = (request.headers.get("Accept-Encoding") or "").lower()
        ct = (resp.headers.get("Content-Type") or "").lower()
        # Only compress when the client supports it, the body is text-like,
        # it is not already encoded, and it is a buffered (non-streaming) body.
        if ("gzip" in ae
                and any(ct.startswith(p) or p in ct for p in _COMPRESSIBLE_CT)
                and "Content-Encoding" not in resp.headers
                and getattr(resp, "body", None) is not None
                and len(resp.body) >= 600):
            resp.enable_compression()
    except Exception as e:
        logger.warning(f"gzip_mw: {e}")
    return resp

# ── USER DATA ────────────────────────────────────────────────────────────────
async def handle_user(request):
    try:
        uid = int(request.match_info["uid"])
    except Exception:
        return web.json_response({"error": "invalid uid"}, status=400)
    try:
        from database import (
            get_user, get_level, get_xp, get_streak_count,
            get_word_count, get_session_count, get_test_count,
            get_mistake_count, get_all_interests, get_due_words,
            get_profession, get_lang, upsert_user, check_platform,
            enforce_subscription, get_weekly_xp, get_ai_access_state
        )
        user = await get_user(uid)
        if not user:
            # Auto-create user from WebApp
            await upsert_user(uid, "Student")
            user = await get_user(uid)
        if not user:
            # Fallback - return minimal data
            return web.json_response({
                "uid": uid, "name": "Student", "level": "B1", "xp": 0,
                "streak": 0, "plant_tonus": 100, "sessions": 0, "words": 0, "tests": 0, "errors": 0,
                "lang": "ru", "profession": "", "remind_time": "",
                "referrals": 0,
                "interests": [], "weekly": [0]*7, "toefl_scores": [], "due_words": [],
                "is_premium": False,
                "is_admin": uid in ADMIN_IDS,
                "available_daily_requests": 0, "premium_type": "FREE",
            }, headers={"Access-Control-Allow-Origin": "*"})
        xp         = await get_xp(uid) or 0
        level      = await get_level(uid) or "B1"
        streak     = await get_streak_count(uid) or 0
        words      = await get_word_count(uid) or 0
        sessions   = await get_session_count(uid) or 0
        tests      = await get_test_count(uid) or 0
        errors     = await get_mistake_count(uid) or 0
        interests  = await get_all_interests(uid) or []
        due_words  = await get_due_words(uid, 10) or []
        profession = await get_profession(uid) or ""
        lang_db    = await get_lang(uid) or "ru"
        # Subscription guard runs first: self-heals a stale is_premium flag in the
        # DB (strict UTC expiry check) before we report status to the WebApp.
        await enforce_subscription(uid)
        # Use check_platform (covers Platform sub + grandfathered + legacy) instead
        # of legacy-only check_premium, so the profile payload matches /api/premium.
        is_prem    = await check_platform(uid)
        try:
            plant_tonus = max(0, min(100, int(user.get("plant_tonus") if (isinstance(user, dict) and user.get("plant_tonus") is not None) else 100)))
        except Exception:
            plant_tonus = 100
        # Real per-day XP for the CURRENT week (Mon..Sun), server-clock based —
        # no client cache, no stale mock. Falls back to zeros only on error.
        try:
            weekly = await get_weekly_xp(uid)
        except Exception as _we:
            logger.warning(f"weekly xp failed for {uid}: {_we}")
            weekly = [0]*7
        # ── Header limits payload ─────────────────────────────────────────
        # available_daily_requests = what the top bar shows.
        #   FREE  → free-credit balance (0..cap).
        #   PAID  → max(0, daily_limit − daily_used); forced to 0 once the
        #           whole-period pool (total_requests_remaining) is exhausted.
        ai_state = {}
        available_daily = 0
        try:
            ai_state = await get_ai_access_state(uid) or {}
            if str(ai_state.get("premium_type") or "FREE") == "FREE":
                available_daily = int(ai_state.get("free_credits") or 0)
            else:
                total_rem = int(ai_state.get("total_remaining") or 0)
                available_daily = 0 if total_rem <= 0 else int(ai_state.get("daily_remaining") or 0)
        except Exception as _ae:
            logger.warning(f"ai access state failed for {uid}: {_ae}")
        return web.json_response({
            "uid": uid,
            "name": user.get("name", "Student") if isinstance(user, dict) else "Student",
            "level": level,
            "xp": xp,
            "streak": streak,
            "plant_tonus": plant_tonus,
            "sessions": sessions,
            "words": words,
            "tests": tests,
            "errors": errors,
            "lang": lang_db,
            "profession": profession,
            "remind_time": user.get("remind_time", "") if isinstance(user, dict) else "",
            "referrals": int(user.get("referrals") or 0) if isinstance(user, dict) else 0,
            "interests": [i["name"] for i in interests] if interests else [],
            "weekly": weekly,
            "toefl_scores": [],
            "due_words": [],
            "is_premium": is_prem,
            "is_admin": uid in ADMIN_IDS,
            # Header limits (request-counter model)
            "available_daily_requests": available_daily,
            "premium_type": ai_state.get("premium_type", "FREE"),
            "ai_daily_limit": ai_state.get("daily_limit"),
            "ai_daily_used": ai_state.get("daily_used"),
            "ai_total_remaining": ai_state.get("total_remaining"),
            "ai_reset_in": ai_state.get("reset_in"),
        }, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"handle_user error: {e}")
        # Return minimal working data instead of 500
        return web.json_response({
            "uid": uid, "name": "Student", "level": "B1", "xp": 0,
            "streak": 0, "plant_tonus": 100, "sessions": 0, "words": 0, "tests": 0, "errors": 0,
            "lang": "ru", "profession": "", "remind_time": "",
            "referrals": 0,
            "interests": [], "weekly": [0]*7, "toefl_scores": [], "due_words": [],
            "is_premium": False,
        }, headers={"Access-Control-Allow-Origin": "*"})

# ── REFERRAL LINK ──────────────────────────────────────────────────────────────
async def handle_referral(request):
    """Generate the user's referral deep link + current stats.

    GET /api/referral/{uid} → {link, count, reward, premium_days, premium_cap_days, premium_left_days, capped}
        link             : t.me/<bot>?start=ref_<uid>  (share this; the inviter is
                           rewarded when a BRAND-NEW user opens the bot through it)
        count            : how many valid referrals this user already has
        reward           : ALEX credits the inviter gets once the Premium cap is hit
        premium_days     : Premium days granted per valid referral (while under cap)
        premium_cap_days : lifetime cap on referral-granted Premium days
        premium_left_days: Premium days still available under the cap for this user
        capped           : True once the lifetime Premium cap is exhausted
    """
    try:
        uid = int(request.match_info["uid"])
    except Exception:
        return web.json_response({"error": "invalid uid"}, status=400,
                                 headers={"Access-Control-Allow-Origin": "*"})
    link = f"https://t.me/{BOT_NAME.lstrip('@')}?start=ref_{uid}"
    count = 0
    used_days = 0
    try:
        from database import get_referral_count, get_user
        count = await get_referral_count(uid)
        u = await get_user(uid) or {}
        used_days = int(u.get("ref_premium_days") or 0)
    except Exception as e:
        logger.warning("referral stats read failed uid=%s: %s", uid, e)
    reward = 3
    from database import REFERRAL_PREMIUM_DAYS, REFERRAL_PREMIUM_CAP_DAYS
    premium_days = REFERRAL_PREMIUM_DAYS
    cap_days = REFERRAL_PREMIUM_CAP_DAYS
    try:
        from billing_config import load_config
        reward = int((await load_config()).get("REFERRAL_REWARD_CREDITS", 3))
    except Exception:
        pass
    left_days = max(0, cap_days - used_days)
    return web.json_response({
        "link": link, "count": int(count), "reward": reward,
        "premium_days": premium_days, "premium_cap_days": cap_days,
        "premium_left_days": left_days, "capped": left_days <= 0,
    }, headers={"Access-Control-Allow-Origin": "*"})

# ── CHAT ─────────────────────────────────────────────────────────────────────
async def handle_chat(request):
    try:
        body    = await request.json()
        uid     = int(body.get("uid", 0))
        message = str(body.get("message", "")).strip()
        if not message:
            return web.json_response({"error": "empty"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400, headers={"Access-Control-Allow-Origin":"*"})

    init_data = str(body.get("init_data") or request.headers.get("X-Telegram-Init-Data") or "")
    if REQUIRE_TG_INIT_DATA and BOT_TOKEN and not _is_local_request(request):
        ok, reason, signed_uid = _verify_telegram_init_data(init_data, uid)
        if not ok:
            logger.warning("blocked unsigned chat uid=%s signed_uid=%s reason=%s", uid, signed_uid, reason)
            return web.json_response({
                "error": "Telegram session check failed. Reopen the bot app and try again."
            }, status=403, headers={"Access-Control-Allow-Origin":"*"})
        if signed_uid and not uid:
            uid = signed_uid

    # Provider gate: ALEX runs on OpenRouter (Gemini). We only hard-fail when NO
    # provider at all is configured — OpenRouter OR the legacy Anthropic fallback.
    # This must check OpenRouter (the primary) so a missing ANTHROPIC_API_KEY can
    # never block chat.
    try:
        from ai_router import openrouter_available as _or_ok
        _provider_ready = _or_ok() or bool(ANT_KEY)
    except Exception:
        _provider_ready = bool(ANT_KEY)
    if not _provider_ready:
        logger.error("No AI provider configured (OPENROUTER_API_KEY missing).")
        return web.json_response({"error": "AI provider not configured. Add OPENROUTER_API_KEY to Railway variables."}, status=500, headers={"Access-Control-Allow-Origin":"*"})

    level="B1"; lang="ru"; interests=""; profession=""
    if uid:
        try:
            from database import get_level, get_lang, get_interests, get_profession
            level      = await get_level(uid)
            lang       = await get_lang(uid)
            interests  = await get_interests(uid)
            profession = await get_profession(uid)
        except Exception as e:
            logger.warning(f"user data error: {e}")

    # Client may request a specific tutoring mode (e.g. "Ask ALEX" inside a
    # lesson/exam sends mode="lesson_help"). Whitelist to known modes so a
    # spoofed value can never inject prompt behaviour. Default stays "correction".
    _ALLOWED_MODES = {
        "correction", "lesson_help", "grammar", "vocab", "speaking",
        "tone_editor", "test", "toefl",
    }
    req_mode = str(body.get("mode", "") or "").strip().lower()
    chat_mode = req_mode if req_mode in _ALLOWED_MODES else "correction"

    try:
        from prompts import build_system
        system = build_system(level, lang, interests, profession, chat_mode)
    except Exception:
        system = f"You are ALEX, a friendly English tutor. The student's level is {level}."

    bl = str(body.get("bot_lang", "Respond in Russian."))

    # ── ALEX REPLY-LANGUAGE HARD LOCK ─────────────────────────────────────────
    # The "Ответы ALEX" setting decides which language ALEX explains in. We do
    # NOT trust the free-text bot_lang alone (older clients leaked 'ar'): we
    # whitelist a language code (answer_lang from the client, else the user's
    # stored UI lang) and build the directive server-side. Explanations go in the
    # chosen language; English examples/target words always stay English.
    _LANG_NAMES = {
        "ru": "Russian", "en": "English", "es": "Spanish", "pt": "Portuguese",
        "de": "German", "fr": "French", "uk": "Ukrainian", "tr": "Turkish",
        "zh": "Chinese", "ar": "Arabic", "ja": "Japanese", "ko": "Korean",
    }
    ans_code = str(body.get("answer_lang", "") or "").strip().lower()
    if ans_code not in _LANG_NAMES:
        ans_code = lang if lang in _LANG_NAMES else "ru"
    ans_name = _LANG_NAMES[ans_code]
    lang_lock = (
        "\n\nLANGUAGE LOCK (highest priority — overrides any other instruction):\n"
        f"- Write ALL explanations, feedback, corrections and questions in {ans_name}.\n"
        "- Keep English example sentences, target vocabulary and quoted student text in English.\n"
        f"- Never switch the explanation language to anything other than {ans_name}, "
        "even if the student writes in a different language.\n"
    )

    fmt = (
        "\n\nFORMATTING (mandatory):\n"
        "- **bold** NOT <b> tags\n"
        "- *italic* NOT <i> tags\n"
        "- `code` NOT <code> tags\n"
        "- Bullet: • or -\n"
        "- 💡 for tips, ✅ for corrections\n"
        "- NEVER use HTML tags\n"
        "- Keep responses concise and engaging\n"
    )
    system = system + fmt + "\n" + bl + lang_lock

    # Check premium for model selection and limits
    user_premium = False
    user_tier = ""
    chat_credits = 0
    grandfathered = ""
    uses_credits = False    # True = new modular path; False = legacy bundle path
    reservation = None      # v2 reserve→reconcile hold (None = legacy/no-charge path)
    bcfg = None             # loaded billing config (v2)
    billing_v2 = False      # v2 reserve→reconcile active for this request
    bill_model_key = None   # logical model chosen by determine_model (v2)
    if uid:
        try:
            from database import check_premium, get_premium_info, get_credits, grandfather_legacy_tier
            info = await get_premium_info(uid)
            user_premium = info.get("is_premium", False)
            user_tier = info.get("tier", "")
            try:
                grandfathered = await grandfather_legacy_tier(uid)
            except Exception:
                grandfathered = ""
            chat_credits = await get_credits(uid)
        except Exception:
            pass

    # ── AI PROVIDER ROUTER ────────────────────────────────────────────────────
    # Paid users (premium / active Platform sub / grandfathered bundle) keep
    # using Anthropic Claude (the full path below). FREE users are routed to
    # Google Gemini 1.5 Flash to offload the Anthropic balance. Decision is
    # server-side only — the client cannot influence which provider is used.
    is_paid = bool(user_premium) or bool(grandfathered)
    if uid and not is_paid:
        try:
            is_paid = await _is_premium_uid(uid)   # active Platform subscription counts as paid
        except Exception:
            is_paid = False
    try:
        from ai_router import should_use_openrouter
        # Whole ecosystem is locked to OpenRouter + Google Gemini ONLY. All ALEX
        # traffic (Free = Gemini Flash, Premium = Gemini Pro) goes strictly through
        # the single OpenRouter key. Third-party providers (DeepSeek, direct Gemini
        # API) are fully excluded — they are never routed to.
        # PREMIUM users are handled by the dedicated Gemini-Pro branch below
        # (request-counter gated), so the free chain is restricted to non-paying.
        route_openrouter = (not is_paid) and should_use_openrouter()
        route_deepseek = False
        route_gemini = False
    except Exception as e:
        logger.warning("ai_router import failed: %s", e)
        route_openrouter = False
        route_deepseek = False
        route_gemini = False

    # ── PREMIUM ALEX (Gemini Pro, request-counter gated) ──────────────────────
    # Paid plans (MONTH_1 / MONTH_6) run on the flagship Gemini Pro model via
    # OpenRouter — the whole ecosystem is Gemini now, no Claude in the chat path.
    # A two-phase gate (whole-period pool → daily ceiling) protects the shared key
    # BEFORE any API call; a successful reply consumes exactly 1 request. If
    # OpenRouter is somehow unconfigured we fall through to the legacy Claude path
    # as a safety net (never leave a paying user without ALEX).
    prem_or = False
    try:
        from ai_router import openrouter_available as _or_avail
        prem_or = bool(is_paid and uid and _or_avail())
    except Exception:
        prem_or = False
    if prem_or:
        from database import check_ai_access, consume_ai_request
        from ai_router import (openrouter_generate, OPENROUTER_MODEL_PREMIUM,
                               GeminiRateLimit, ai_busy_message)
        # Anti-flood: paid users share the same OpenRouter key, so a scripted
        # burst from one premium account still drains the wallet. Enforce a
        # per-user cooldown (lighter than free's 5s so paid chat stays snappy).
        _pg = float(os.getenv("PREMIUM_BURST_GAP", "2.0") or "2.0")
        _pw = _throttle_wait(uid, _pg, "chat")
        if _pw:
            _slow = {"ru": f"⏳ Слишком быстро — подожди {_pw} с.",
                     "es": f"⏳ Demasiado rápido — espera {_pw} s.",
                     "pt": f"⏳ Rápido demais — espere {_pw} s."}
            return web.json_response(
                {"reply": _slow.get(lang, f"⏳ Slow down — wait {_pw} s."),
                 "rate_limited": True, "retry_after": _pw},
                status=429,
                headers={"Access-Control-Allow-Origin": "*", "Retry-After": str(_pw)})
        access = await check_ai_access(uid)
        if not access.get("allowed"):
            reason = access.get("reason")
            dl = int(access.get("daily_limit") or 0)
            if reason == "total":
                msg = {"ru": "🚫 Вы исчерпали общий лимит подписки на этот период.",
                       "en": "🚫 You've used your subscription's total request limit for this period.",
                       "es": "🚫 Has agotado el límite total de solicitudes de tu suscripción para este período.",
                       "pt": "🚫 Você esgotou o limite total de solicitações da sua assinatura neste período."}
            else:  # 'daily'
                msg = {"ru": f"⏳ Дневной лимит ({dl} запросов) исчерпан. Обнуление в 00:00 UTC.",
                       "en": f"⏳ Daily limit ({dl} requests) reached. Resets at 00:00 UTC.",
                       "es": f"⏳ Límite diario ({dl} solicitudes) alcanzado. Se reinicia a las 00:00 UTC.",
                       "pt": f"⏳ Limite diário ({dl} solicitações) atingido. Zera às 00:00 UTC."}
            return web.json_response(
                {"reply": msg.get(lang, msg["en"]), "limit": True, "sub_limit": True,
                 "limit_reason": reason, "daily_used": access.get("daily_used"),
                 "daily_limit": dl, "total_remaining": access.get("total_remaining"),
                 "reset_in": access.get("reset_in")},
                status=429, headers={"Access-Control-Allow-Origin": "*"})
        # Expert premium persona — deep, pedagogical, no brevity cap.
        prem_directive = (
            "\n\nYou are ALEX, an elite English tutor powered by an advanced model, "
            "working with a PREMIUM student. Teach at an expert level:\n"
            "- Answer complex grammar questions in depth — the rule, its nuances "
            "and 2–3 natural example sentences.\n"
            "- For corrections, show the fix AND explain WHY, offer a more "
            "native-like phrasing, and note register/tone.\n"
            "- Proactively enrich: suggest idioms, collocations, synonyms and "
            "better alternatives when relevant.\n"
            "- Run immersive roleplay/scenarios fully in character when asked, then "
            "add a short feedback note at the end.\n"
            "- Use sound pedagogy: scaffold, check understanding, adapt to the "
            "student's level, and remember earlier turns so a long conversation "
            "stays coherent.\n"
            "- Be thorough and well-structured with formatting and examples — you "
            "are NOT limited to a few sentences."
        )
        prem_system = system + prem_directive
        try:
            from billing_config import load_config as _lc
            _pc = await _lc()
            prem_hist_n = max(6, int(_pc.get("PREMIUM_HISTORY", 18) or 18))
        except Exception:
            prem_hist_n = 18
        h = _histories.setdefault(uid, [])
        h.append({"role": "user", "content": message})
        if len(h) > 40:                       # bound the in-memory store
            _histories[uid] = h[-40:]
        # Input guard: take the last N turns, then hard-trim by a token budget so
        # a very long conversation can never inflate the per-turn input cost. Even
        # Premium history is archived down to the freshest PREMIUM_HISTORY_TOKEN_BUDGET.
        prem_send = _trim_history_by_tokens(_histories[uid][-prem_hist_n:])
        # Output guard: cap a single Gemini Pro reply at 350 tokens so ALEX can't
        # generate a balance-burning wall of text. Env-overridable but defaults low.
        prem_max = int(os.getenv("OPENROUTER_PREMIUM_MAX_TOKENS", "350") or "350")
        reply = ""
        try:
            result = await openrouter_generate(prem_system, prem_send, prem_max,
                                               model=OPENROUTER_MODEL_PREMIUM)
            reply = re.sub(r"\n[ \t]*\n+", "\n", (result.get("text") or "").strip())
        except GeminiRateLimit:
            reply = ""
        except Exception as e:
            logger.warning("premium openrouter failed uid=%s: %s", uid, e)
            reply = ""
        if not reply:
            try: _histories[uid].pop()        # keep history clean, do NOT charge
            except Exception: pass
            return web.json_response({"reply": ai_busy_message(lang)},
                                     headers={"Access-Control-Allow-Origin": "*"})
        _histories[uid].append({"role": "assistant", "content": reply})
        cons = {}
        try:
            cons = await consume_ai_request(uid)     # charge exactly 1 on success
        except Exception as e:
            logger.warning("consume_ai_request failed uid=%s: %s", uid, e)
        if uid:
            try:
                from database import log_session
                await log_session(uid, "webapp_chat")
            except Exception:
                pass
        # Header counter, authoritative post-charge: 0 once the whole-period pool
        # is gone, else what's left of today's ceiling.
        _tot = int(cons.get("total_remaining") or 0)
        _dl = int(cons.get("daily_limit") or 0)
        _du = int(cons.get("daily_used") or 0)
        _avail = 0 if _tot <= 0 else max(0, _dl - _du)
        return web.json_response(
            {"reply": reply, "provider": "openrouter", "ai_provider": "Powered by ALEX Pro",
             "premium_type": cons.get("premium_type"),
             "daily_used": cons.get("daily_used"), "daily_limit": cons.get("daily_limit"),
             "total_remaining": cons.get("total_remaining"),
             "remaining_requests": cons.get("total_remaining"),
             "available_daily_requests": _avail},
            headers={"Access-Control-Allow-Origin": "*"})

    if route_openrouter or route_deepseek or route_gemini:
        free_provider = ("openrouter" if route_openrouter
                         else "deepseek" if route_deepseek else "gemini")
        # FREE TIER — Gemini-only, never Claude. Bounded by: (a) a burst gap,
        # (b) a daily-replenishing CREDIT wallet (+FREE_CREDITS_DAILY at UTC 00:00,
        # capped, 1 credit / reply) and (c) a server-side context-size guard. A
        # transient provider failure is NEVER surfaced as a 503 — we fall back
        # across any other configured free provider and, only if all fail, return
        # a calm "try again" card WITHOUT spending a credit.
        if uid and uid not in ADMIN_IDS:
            import time as _time
            now_ts = _time.time()
            gap = float(TIER_ECONOMY["free"]["burst_gap"])
            last = _last_msg_ts.get(uid, 0.0)
            if now_ts - last < gap:
                wait = max(1, int(gap - (now_ts - last)))
                slow_map = {
                    "ru": f"⏳ Подожди {wait} с — слишком быстро.",
                    "es": f"⏳ Espera {wait} s — demasiado rápido.",
                    "pt": f"⏳ Espere {wait} s — rápido demais.",
                }
                # Anti-abuse: a free user must not chat more often than once per
                # `burst_gap` seconds. Scripted floods get a hard HTTP 429 (with a
                # Retry-After hint) instead of a friendly 200 — no API call, no
                # credit spent.
                return web.json_response(
                    {"reply": slow_map.get(lang, f"⏳ Slow down — wait {wait} s."),
                     "rate_limited": True, "retry_after": wait},
                    status=429,
                    headers={"Access-Control-Allow-Origin": "*",
                             "Retry-After": str(wait)})
            _last_msg_ts[uid] = now_ts
            # Free-credit balance gate (UTC-day grant applied lazily inside the
            # read). Block at 0 with a paywall card BEFORE any API call — and
            # BEFORE charging, so a blocked turn is always free.
            try:
                from database import get_free_credits_state
                fc = await get_free_credits_state(uid)
                if int(fc.get("balance", 0)) <= 0:
                    from ai_router import ai_daily_limit_message
                    return web.json_response(
                        {"reply": ai_daily_limit_message(lang), "limit": True,
                         "free_ai_limit": True, "daily_ai_limit": True,
                         "free_credits": 0, "free_credits_cap": fc.get("cap"),
                         "remaining_requests": 0, "reset_in": fc.get("reset_in")},
                        status=429, headers={"Access-Control-Allow-Origin":"*"})
            except Exception as e:
                logger.warning("free-credit gate check failed uid=%s: %s", uid, e)
        # History depth for FREE. Kept deliberately short: free chat runs on the
        # SHARED OpenRouter key, so a long glued-together context is the cheapest
        # way for an abuser to inflate per-turn input cost ("context-window
        # exploit"). Default 6 turns (~3 exchanges); override via FREE_HISTORY_MSGS
        # (set 4 for the tightest anti-drain, higher for more coherent free chat).
        h = _histories.setdefault(uid, [])
        h.append({"role": "user", "content": message})
        free_hist = max(2, int(os.getenv("FREE_HISTORY_MSGS", "6") or "6"))
        if len(h) > free_hist:
            _histories[uid] = h[-free_hist:]
        # ── Server-side context-size validation (anti-drain on the shared key) ──
        # Trim oldest turns until the whole prompt (system + history) fits the
        # configured char budget; if a SINGLE message still blows the budget,
        # refuse politely without spending a credit.
        try:
            from billing_config import load_config as _load_cfg
            _fcfg = await _load_cfg()
            max_chars = max(1000, int(_fcfg.get("FREE_AI_MAX_CHARS", 6000) or 6000))
        except Exception:
            max_chars = 6000
        convo = _histories[uid]
        def _ctx_chars():
            return len(system) + sum(len(str(m.get("content") or "")) for m in convo)
        while len(convo) > 1 and _ctx_chars() > max_chars:
            convo.pop(0)
        _histories[uid] = convo
        if _ctx_chars() > max_chars:
            try: convo.pop()   # drop the oversized user turn; keep history clean
            except Exception: pass
            big_map = {
                "ru": "✂️ Сообщение слишком длинное для бесплатного чата. Сократи его или подключи Premium.",
                "en": "✂️ That message is too long for free chat. Please shorten it, or go Premium.",
                "es": "✂️ Ese mensaje es demasiado largo para el chat gratis. Acórtalo o pásate a Premium.",
                "pt": "✂️ Essa mensagem é longa demais para o chat grátis. Encurte-a ou assine o Premium.",
            }
            return web.json_response(
                {"reply": big_map.get(lang, big_map["en"]), "context_too_large": True},
                headers={"Access-Control-Allow-Origin":"*"})
        g_max = min(MAX_REPLY_TOKENS_HARD, int(os.getenv("GEMINI_MAX_TOKENS", "600") or "600"))
        # Build the attempt order: the routed provider first, then any OTHER
        # configured free provider as a fallback (so a single upstream blip can't
        # break free chat). Production runs OpenRouter-only → order == [openrouter].
        from ai_router import (gemini_generate, gemini_free_limit_message,
                               ai_busy_message, GeminiRateLimit, deepseek_generate,
                               openrouter_generate, should_use_openrouter,
                               should_use_deepseek, should_use_gemini)
        _GEN = {"openrouter": openrouter_generate, "deepseek": deepseek_generate,
                "gemini": gemini_generate}
        _LABEL = {"openrouter": ("openrouter", "Powered by ALEX Basic"),
                  "deepseek": ("deepseek", "Powered by ALEX Basic"),
                  "gemini": ("gemini", "Powered by ALEX Basic")}
        order = [free_provider]
        for _name, _chk in (("openrouter", should_use_openrouter),
                            ("deepseek", should_use_deepseek),
                            ("gemini", should_use_gemini)):
            try:
                if _name not in order and _chk(is_paid):
                    order.append(_name)
            except Exception:
                pass
        # ── FREE-TIER CONTEXT HARD-CAP (anti-drain) ───────────────────────────
        # No matter how long the local dialogue grows, only the LAST 4 messages
        # are ever sent upstream. This caps input tokens on the shared free key
        # so a user can't "burn" credits/balance with a long conversation.
        FREE_SEND_TURNS = 4
        free_send = _histories[uid][-FREE_SEND_TURNS:]
        # ── ALEX free-tier persona (concise, token-thrifty) ───────────────────
        # Strict-but-supportive tutor; capped at 3–4 short sentences so the cheap
        # Flash model stays cheap on both input and output tokens.
        alex_free = (
            "\n\nYou are ALEX — a strict but supportive English tutor. "
            "Be encouraging yet hold the student to a high standard: gently fix "
            "every real mistake and briefly explain why. Keep EVERY reply short: "
            "at most 3–4 sentences. No filler, no repeating the question — get "
            "straight to the correction and one useful tip."
        )
        free_system = system + alex_free
        reply = ""
        prov_tag = prov_label = None
        all_rate_limited = True
        for _name in order:
            try:
                result = await _GEN[_name](free_system, free_send, g_max)
                r = (result.get("text") or "").strip()
                r = re.sub(r"\n[ \t]*\n+", "\n", r)
                if not r:
                    all_rate_limited = False
                    continue
                reply = r
                prov_tag, prov_label = _LABEL[_name]
                break
            except GeminiRateLimit:
                # Provider quota hit — try the next configured fallback, if any.
                continue
            except Exception as e:
                all_rate_limited = False
                logger.warning("%s generate failed uid=%s: %s", _name, uid, e)
                continue
        if not reply:
            # Nothing produced a reply. Drop the user turn (keep history clean) and
            # DO NOT charge. Rate-limit → upstream-quota card; otherwise a calm
            # retry card (never a 503).
            try: _histories[uid].pop()
            except Exception: pass
            if all_rate_limited:
                return web.json_response(
                    {"reply": gemini_free_limit_message(lang), "limit": True, "free_ai_limit": True},
                    headers={"Access-Control-Allow-Origin":"*"})
            return web.json_response({"reply": ai_busy_message(lang)},
                                     headers={"Access-Control-Allow-Origin":"*"})
        _histories[uid].append({"role": "assistant", "content": reply})
        # Charge exactly 1 free credit on a successful reply (failed/blocked turns
        # stay free). The atomic spend returns the fresh wallet so we can echo the
        # live balance back to the Mini App (no client clock involved).
        fc_balance = fc_cap = fc_reset = None
        if uid and uid not in ADMIN_IDS:
            try:
                from database import spend_free_credit
                sp = await spend_free_credit(uid, 1)
                fc_balance = sp.get("balance")
                fc_cap = sp.get("cap")
                fc_reset = sp.get("reset_in")
            except Exception as e:
                logger.warning("spend_free_credit failed uid=%s: %s", uid, e)
        if uid:
            try:
                from database import log_session
                await log_session(uid, "webapp_chat")
            except Exception:
                pass
        resp = {"reply": reply, "provider": prov_tag, "ai_provider": prov_label}
        if fc_balance is not None:
            resp["free_credits"] = fc_balance
            resp["free_credits_cap"] = fc_cap
            resp["remaining_requests"] = fc_balance   # legacy field the client reads
            resp["available_daily_requests"] = max(0, int(fc_balance))  # header counter (FREE)
            if fc_cap is not None:
                resp["used"] = max(0, int(fc_cap) - int(fc_balance))
                resp["ai_limit"] = fc_cap
            resp["reset_in"] = fc_reset
        return web.json_response(resp, headers={"Access-Control-Allow-Origin":"*"})

    # Model picker (paid tiers only). Server is source of truth — client
    # cannot upgrade beyond their tier by spoofing chosen_model.
    tier_key = user_tier if user_tier in TIER_ECONOMY else "free"

    # ── NEW MODULAR PATH ────────────────────────────────────────────────
    # If the user has no active legacy bundle AND no grandfathered tier,
    # they are on the new model: ALEX is purely credit-based.
    if tier_key == "free" and not grandfathered:
        uses_credits = True
        # Load billing config once and decide v2 vs legacy. If v2 is enabled we
        # run reserve→reconcile (allowance first, then prepaid credits); else we
        # fall back to the legacy per-message spend_credits path below.
        try:
            from billing_config import load_config
            bcfg = await load_config()
            billing_v2 = bool(bcfg.get("BILLING_V2_ENABLED"))
        except Exception as e:
            logger.warning("billing config load failed uid=%s: %s", uid, e)
            bcfg = None
            billing_v2 = False

        is_voice = bool(body.get("voice"))
        if billing_v2 and bcfg:
            # v2: subscription allowance OR prepaid credits, both with margin.
            from ai_billing import (ensure_allowance, determine_model,
                                    reserve_amount, can_spend, reserve as _reserve)
            try:
                await ensure_allowance(uid, bcfg)   # refill monthly pool if due
            except Exception as e:
                logger.warning("ensure_allowance failed uid=%s: %s", uid, e)
            bill_model_key = determine_model(bcfg, chat_mode)   # chat→haiku, exam→sonnet
            need = reserve_amount(bcfg, voice=is_voice)
            ok, why = await can_spend(uid, bcfg, need)
            if not ok:
                # Funds / daily-token / global-budget gate failed → clear error,
                # NO API call (owner never goes negative).
                return web.json_response(_billing_block_message(lang, why),
                                         headers={"Access-Control-Allow-Origin":"*"})
            reservation = await _reserve(uid, bcfg, voice=is_voice)
            if reservation is None:
                return web.json_response(_billing_block_message(lang, "insufficient_funds"),
                                         headers={"Access-Control-Allow-Origin":"*"})
        elif chat_credits <= 0:
            # Legacy credit path: hard gate on empty wallet.
            msg = (
                '<div class="limit-card">'
                '<div class="limit-kicker">ALEX Chat</div>'
                '<div class="limit-title">Нужны кредиты ALEX</div>'
                '<div class="limit-text">Кредиты тратятся за каждое сообщение и не сгорают. Купи пакет, чтобы продолжить общение.</div>'
                '<button class="chip" onclick="openPremium()">Купить кредиты</button>'
                '</div>'
                if lang == "ru" else
                '<div class="limit-card">'
                '<div class="limit-kicker">ALEX Chat</div>'
                '<div class="limit-title">ALEX credits required</div>'
                '<div class="limit-text">Credits are spent per message and never expire. Top up to keep chatting.</div>'
                '<button class="chip" onclick="openPremium()">Buy credits</button>'
                '</div>'
            )
            return web.json_response({"reply": msg, "premium_required": True, "credits_required": True, "chat_credits": 0},
                                     headers={"Access-Control-Allow-Origin":"*"})
        # Credit-based users get the full model pool — every model is paid
        # for in credits (priced per model), so nothing is tier-gated.
        tier_key = "platform"
    tier_cfg = TIER_ECONOMY[tier_key]
    # ALEX model: v2 picks per-mode (Haiku for chat, Sonnet for graded work) via
    # determine_model; legacy stays on Sonnet. chosen_model from the client is
    # ignored either way so a user cannot request a different (pricier) model.
    model_key = bill_model_key if (billing_v2 and uses_credits and bill_model_key) else "sonnet"

    model_cfg = MODEL_ECONOMY[model_key]
    # "platform" reuses the "ultimate" token budgets (full model pool parity).
    mt_tier = "ultimate" if tier_key == "platform" else tier_key
    chat_model = model_cfg.get("model_by_tier", {}).get(tier_key, model_cfg["model"])
    max_tokens = model_cfg["max_tokens"].get(mt_tier, 500)
    # v2 caps OUTPUT at the configured MAX_TOKENS_PER_REPLY so the up-front
    # reserve (sized on that cap) always covers the real reply.
    if billing_v2 and bcfg:
        max_tokens = min(max_tokens, int(bcfg.get("MAX_TOKENS_PER_REPLY", 1000)))
    # Hard ceiling for EVERY path (incl. legacy/grandfathered) — a reply can
    # never exceed this regardless of tier, so output cost stays bounded.
    max_tokens = min(int(max_tokens), MAX_REPLY_TOKENS_HARD)
    weight = int(model_cfg["weight"])
    msg_limit = int(tier_cfg["quota"])

    # Check daily message limit (skip for admins)
    if uid not in ADMIN_IDS:
        # Anti-burst: min gap between messages — 5s free, 2s paid.
        import time as _time
        now_ts = _time.time()
        gap = float(tier_cfg["burst_gap"])
        last = _last_msg_ts.get(uid, 0.0)
        if now_ts - last < gap:
            wait = max(1, int(gap - (now_ts - last)))
            slow_map = {
                "ru": f"⏳ Подожди {wait} с — слишком быстро.",
                "es": f"⏳ Espera {wait} s — demasiado rápido.",
                "pt": f"⏳ Espere {wait} s — rápido demais.",
            }
            slow_msg = slow_map.get(lang, f"⏳ Slow down — wait {wait} s.")
            return web.json_response({"reply": slow_msg},
                                     headers={"Access-Control-Allow-Origin":"*"})
        _last_msg_ts[uid] = now_ts
        add_quota_usage_fn = None
        try:
            from database import get_quota_usage, add_quota_usage
            quota_usage = await get_quota_usage(uid)
            add_quota_usage_fn = add_quota_usage
        except Exception as e:
            logger.warning("quota DB read failed uid=%s: %s", uid, e)
            quota_usage = {"quota_used": _msg_counts.get(f"msgs:{uid}:{__import__('datetime').date.today()}", 0), "ai_cost": 0.0}
        today_key = f"msgs:{uid}:{__import__('datetime').date.today()}"
        msg_count = int(quota_usage.get("quota_used") or 0)
        cost_key = f"cost:{uid}:{__import__('datetime').date.today()}"
        used_cost = max(float(quota_usage.get("ai_cost") or 0.0), _daily_ai_costs.get(cost_key, 0.0))
        # Single-model world: there is no cheaper model to fall back to, so
        # when a legacy/grandfathered user exceeds their daily AI-cost budget
        # we stop for the day instead of downgrading. Credit users have an
        # effectively unlimited budget, so this never fires for them.
        if used_cost >= float(tier_cfg["daily_budget"]):
            limit_msg = _limit_message(lang, tier_key, msg_count, msg_limit)
            return web.json_response({"reply": limit_msg, "limit": True, "limit_tier": tier_key}, headers={"Access-Control-Allow-Origin":"*"})
        if msg_count + weight > msg_limit:
            limit_msg = _limit_message(lang, tier_key, msg_count, msg_limit)
            return web.json_response({"reply": limit_msg, "limit": True, "limit_tier": tier_key}, headers={"Access-Control-Allow-Origin":"*"})
        _msg_counts[today_key] = msg_count + weight
        if add_quota_usage_fn:
            try:
                await add_quota_usage_fn(uid, points=weight, ai_cost=0.0)
            except Exception as e:
                logger.warning("quota DB reserve failed uid=%s: %s", uid, e)

    h = _histories.setdefault(uid, [])
    h.append({"role": "user", "content": message})
    history_limit = int(tier_cfg["history"])
    if len(h) > history_limit:
        _histories[uid] = h[-history_limit:]

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANT_KEY,
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "prompt-caching-2024-07-31",
                    "content-type": "application/json",
                },
                json={
                    "model": chat_model,
                    "max_tokens": max_tokens,
                    # Prompt caching: системный промпт стабильный → большая скидка
                    # (до 90% input cost) на cache hits в пределах ~5 минут TTL.
                    "system": [{"type": "text", "text": system,
                                "cache_control": {"type": "ephemeral"}}],
                    "messages": _histories[uid],
                },
            )
            data = r.json()
            if "error" in data:
                logger.error(f"Anthropic error: {data['error']}")
                # No reply was produced → fully refund the v2 reserve.
                if reservation is not None:
                    try:
                        from ai_billing import cancel as _bill_cancel
                        await _bill_cancel(uid, reservation, meta="api_error")
                    except Exception as ce:
                        logger.warning("reserve cancel failed uid=%s: %s", uid, ce)
                return web.json_response({"error": data["error"].get("message","API error")[:200]}, status=500)
            reply = data["content"][0]["text"].strip()
            # Dense layout: collapse blank lines between paragraphs so ALEX
            # replies render tight (matches the prompt's LAYOUT rule).
            reply = re.sub(r"\n[ \t]*\n+", "\n", reply)
            if uid not in ADMIN_IDS:
                cost_key = f"cost:{uid}:{__import__('datetime').date.today()}"
                ai_cost = _estimate_ai_cost(model_key, data.get("usage") or {})
                _daily_ai_costs[cost_key] = round(_daily_ai_costs.get(cost_key, 0.0) + ai_cost, 6)
                try:
                    from database import add_quota_usage
                    await add_quota_usage(uid, points=0, ai_cost=ai_cost)
                except Exception as e:
                    logger.warning("quota DB cost update failed uid=%s: %s", uid, e)
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        # Exception before a usable reply → fully refund the v2 reserve.
        if reservation is not None:
            try:
                from ai_billing import cancel as _bill_cancel
                await _bill_cancel(uid, reservation, meta="exception")
            except Exception as ce:
                logger.warning("reserve cancel failed uid=%s: %s", uid, ce)
        return web.json_response({"error": str(e)[:150]}, status=500)

    _histories[uid].append({"role": "assistant", "content": reply})
    if uid:
        try:
            from database import log_session
            await log_session(uid, "webapp_chat")
        except Exception:
            pass

    # ── Credit deduction (new modular path only) ────────────────────────
    # Grandfathered bundle holders keep using the legacy quota system and
    # are NOT charged credits. New users pay per-message in credits priced
    # by model (Haiku=1, Sonnet4=4, etc.) via credits_for_message().
    new_balance = None
    if uses_credits and uid and billing_v2 and reservation is not None and bcfg:
        # v2: reconcile the reserve against real token usage (refund the unused
        # part, allowance first) and write the authoritative usage_log row.
        try:
            from ai_billing import reconcile
            from database import get_wallet
            real_cost = _estimate_ai_cost(model_key, data.get("usage") or {})
            await reconcile(uid, bcfg, model_key, reservation,
                            data.get("usage") or {}, real_cost)
            w = await get_wallet(uid)
            new_balance = int(w.get("allowance", 0)) + int(w.get("credits", 0))
        except Exception as e:
            logger.warning("v2 reconcile failed uid=%s: %s", uid, e)
    elif uses_credits and uid:
        # Legacy per-message spend (v2 disabled or config unavailable).
        try:
            from database import credits_for_message, spend_credits, get_credits
            cost = credits_for_message(model_key, voice=bool(body.get("voice")))
            await spend_credits(uid, cost)
            new_balance = await get_credits(uid)
        except Exception as e:
            logger.warning("credit deduction failed uid=%s: %s", uid, e)

    # Debug marker: this branch always answered via Anthropic Claude (paid users).
    resp = {"reply": reply, "provider": "anthropic", "ai_provider": "Powered by ALEX Pro"}
    if new_balance is not None:
        resp["chat_credits"] = new_balance
    return web.json_response(resp, headers={"Access-Control-Allow-Origin": "*"})

# ── CHAT RESET ───────────────────────────────────────────────────────────────
async def handle_chat_reset(request):
    try:
        uid = int(request.match_info["uid"])
        _histories.pop(uid, None)
    except Exception:
        pass
    return web.json_response({"ok": True}, headers={"Access-Control-Allow-Origin": "*"})

# ── UNIFIED CONTENT GENERATION (Gemini-first) ────────────────────────────────
# The whole PolyGlotty ecosystem runs on Gemini now. Content endpoints (lesson,
# test, listening task, exam generation) route through OpenRouter (Gemini Pro)
# instead of Anthropic Claude. Claude remains only as a safety-net fallback for
# the case where OpenRouter is unconfigured, so a missing key never takes the
# whole content surface offline. Returns (text, usage_dict).
async def _gen_text(system: str, user_content: str, max_tokens: int = 800,
                    fallback_model: str | None = None) -> tuple:
    try:
        from ai_router import (openrouter_available, openrouter_generate,
                               OPENROUTER_MODEL_PREMIUM)
        if openrouter_available():
            result = await openrouter_generate(
                system, [{"role": "user", "content": user_content}],
                max_tokens=max_tokens, model=OPENROUTER_MODEL_PREMIUM)
            return (result.get("text") or "").strip(), (result.get("usage") or {})
    except Exception as e:
        logger.warning("_gen_text Gemini path failed, falling back to Claude: %s", e)
    # Safety-net fallback: Anthropic Claude (only if OpenRouter is unavailable).
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANT_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": fallback_model or SONNET_MODEL, "max_tokens": max_tokens,
                  "system": system, "messages": [{"role": "user", "content": user_content}]})
        data = r.json()
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(str(data["error"]))
    text = (data.get("content") or [{}])[0].get("text", "").strip()
    return text, (data.get("usage") or {})

# ── LESSON ───────────────────────────────────────────────────────────────────
async def handle_lesson(request):
    try:
        body  = await request.json()
        uid   = int(body.get("uid", 0))
        topic = str(body.get("topic", "Present Simple"))
        lang  = str(body.get("lang", "ru"))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)
    system = (
        f"You are ALEX, an English tutor. Teach a grammar lesson about '{topic}'. "
        f"Respond in {'Russian' if lang=='ru' else 'English'}. "
        "Format: brief explanation, 3 examples, 2 practice exercises. Use **bold** for key terms."
    )
    try:
        content, _ = await _gen_text(system, f"Teach me about {topic}", max_tokens=800)
    except Exception as e:
        return web.json_response({"error":str(e)[:150]},status=500)
    return web.json_response({"content":content},headers={"Access-Control-Allow-Origin":"*"})

# ── TEST ─────────────────────────────────────────────────────────────────────
async def handle_test(request):
    try:
        body=await request.json()
        uid=int(body.get("uid",0)); level=str(body.get("level","B1")); lang=str(body.get("lang","ru"))
    except Exception as e:
        return web.json_response({"error":str(e)},status=400)
    system=(
        f"Generate a 5-question English grammar test for level {level}. "
        f"Respond in {'Russian' if lang=='ru' else 'English'}. "
        "Format: numbered questions with 4 options A/B/C/D and correct answer. Use clear markdown."
    )
    try:
        content, _ = await _gen_text(system, "Generate test", max_tokens=800)
    except Exception as e:
        return web.json_response({"error":str(e)[:150]},status=500)
    return web.json_response({"content":content},headers={"Access-Control-Allow-Origin":"*"})

# ── RATE CARD ─────────────────────────────────────────────────────────────────
async def handle_rate(request):
    try:
        body=await request.json()
        uid=int(body.get("uid",0)); word_id=int(body.get("word_id",0)); quality=int(body.get("quality",3))
    except Exception as e:
        return web.json_response({"error":str(e)},status=400)
    if uid and word_id:
        try:
            from database import update_word_review, upsert_user
            await upsert_user(uid, "Student")
            await update_word_review(word_id,quality)
        except Exception as e:
            logger.warning(f"rate_card failed uid={uid}: {e}")
    return web.json_response({"ok":True},headers={"Access-Control-Allow-Origin":"*"})

# ── ADD XP ────────────────────────────────────────────────────────────────────
async def handle_add_xp(request):
    try:
        body=await request.json()
        uid=int(body.get("uid",0)); xp=int(body.get("xp",0))
    except Exception:
        return web.json_response({"error":"bad request"},status=400)
    if uid and xp>0:
        try:
            from database import add_xp, upsert_user
            await upsert_user(uid, "Student")  # гарантируем строку, иначе UPDATE затронет 0 строк
            await add_xp(uid,min(xp,100))
        except Exception as e:
            logger.warning(f"add_xp failed uid={uid}: {e}")
    return web.json_response({"ok":True},headers={"Access-Control-Allow-Origin":"*"})

# ── SET PROFESSION ────────────────────────────────────────────────────────────
async def handle_set_profession(request):
    try:
        body=await request.json()
        uid=int(body.get("uid",0)); profession=str(body.get("profession",""))[:500]
    except Exception:
        return web.json_response({"error":"bad"},status=400)
    if uid and profession:
        try:
            from database import set_profession, upsert_user
            await upsert_user(uid, "Student")
            await set_profession(uid,profession)
        except Exception as e:
            logger.warning(f"set_profession failed uid={uid}: {e}")
    return web.json_response({"ok":True},headers={"Access-Control-Allow-Origin":"*"})

# ── SET REMINDER ──────────────────────────────────────────────────────────────
async def handle_set_reminder(request):
    try:
        body=await request.json()
        uid=int(body.get("uid",0)); remind_time=str(body.get("remind_time",""))
    except Exception:
        return web.json_response({"error":"bad"},status=400)
    if uid:
        try:
            from database import set_reminder, upsert_user
            await upsert_user(uid, "Student")
            await set_reminder(uid,remind_time)
        except Exception as e:
            logger.warning(f"set_reminder failed uid={uid}: {e}")
    return web.json_response({"ok":True},headers={"Access-Control-Allow-Origin":"*"})

# ── COMPLETE DAY (server-authoritative streak) ─────────────────────────────────
async def handle_complete_day(request):
    """Count today's streak on the SERVER. The day boundary is computed from
    server UTC + the user's timezone offset, so the reminder time / device
    clock cannot inflate the streak. Bumps at most once per server-day."""
    if request.method == "OPTIONS":
        return web.json_response({"ok":True},headers={
            "Access-Control-Allow-Origin":"*",
            "Access-Control-Allow-Methods":"POST, OPTIONS",
            "Access-Control-Allow-Headers":"Content-Type"})
    try:
        body = await request.json()
        uid = int(body.get("uid",0) or 0)
        tz = int(body.get("tz_offset_min",0) or 0)
    except Exception:
        return web.json_response({"ok":False},status=400,headers={"Access-Control-Allow-Origin":"*"})
    if not uid:
        return web.json_response({"ok":False},headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import complete_day, upsert_user
        try:
            await upsert_user(uid, "Student")
        except Exception:
            pass
        res = await complete_day(uid, tz)
        return web.json_response({"ok":True, **res},headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"complete_day error uid={uid}: {e}")
        return web.json_response({"ok":False},headers={"Access-Control-Allow-Origin":"*"})

# ── AUDIO TASK ────────────────────────────────────────────────────────────────
async def handle_audio_task(request):
    try:
        body=await request.json()
        uid=int(body.get("uid",0)); lang=str(body.get("lang","ru"))
    except Exception:
        return web.json_response({"error":"bad"},status=400)
    ru=lang=="ru"
    # Strictly-academic listening task. Pull a random academic topic from the
    # shared exam pool so the content is never everyday/conversational and never
    # repeats the same lecture. Falls back to a generic academic instruction if
    # the pool module is unavailable.
    try:
        from exam_content import pick_topics, DOMAIN_LABEL, ACADEMIC_RULES
        _t = pick_topics(1)[0]
        _topic_line = (f'Topic: "{_t["title"]}" — focus on: {_t.get("angle","")}. '
                       f'Domain: {DOMAIN_LABEL.get(_t["domain"],"academic")}.\n')
        _rules = ACADEMIC_RULES + "\n"
    except Exception:
        _topic_line = "Topic: a random academic subject (science, history, or economics).\n"
        _rules = ("STRICTLY ACADEMIC content only — university-lecture register. "
                  "NEVER everyday/conversational material.\n")
    system=(
        _rules + _topic_line +
        "Generate a TOEFL/IELTS academic listening task. Return ONLY valid JSON (no markdown):\n"
        '{"topic":"...", "transcript":"4-6 sentence academic lecture excerpt in English, '
        'C1-C2, with passive voice and discipline terminology", '
        '"questions":[{"q":"...","options":["A","B","C","D"],"correct":0},'
        '{"q":"...","options":["A","B","C","D"],"correct":1},'
        '{"q":"...","options":["A","B","C","D"],"correct":2}]}\n'
        f'Questions in {"Russian" if ru else "English"}. Transcript always in English.'
    )
    try:
        raw, _ = await _gen_text(system, "Generate", max_tokens=800)
        raw=raw.replace("```json","").replace("```","").strip()
        task=json.loads(raw)
    except Exception as e:
        task={"topic":"Academic Lecture","transcript":"The Industrial Revolution began in Britain in the late 18th century, transforming manufacturing through steam power and mechanization, fundamentally changing economic and social structures worldwide.","questions":[{"q":"When did the Industrial Revolution begin?" if not ru else "Когда началась промышленная революция?","options":["Early 17th century","Late 18th century","Early 19th century","Mid 20th century"],"correct":1},{"q":"Where did it begin?" if not ru else "Где она началась?","options":["France","Germany","Britain","USA"],"correct":2},{"q":"What powered new manufacturing?" if not ru else "Что питало новое производство?","options":["Wind power","Water wheels","Steam power","Electricity"],"correct":2}]}
    return web.json_response(task,headers={"Access-Control-Allow-Origin":"*"})

# ── HEALTH ────────────────────────────────────────────────────────────────────
async def handle_health(request):
    return web.json_response({"status":"ok","model":MODEL},headers={"Access-Control-Allow-Origin":"*"})

# ── CHECK PREMIUM ─────────────────────────────────────────────────────────────
async def handle_check_premium(request):
    """Returns detailed premium status for a user — includes modular billing fields.

    Response shape:
      {
        is_premium, tier, until, lifetime,       # legacy bundle (kept for back-compat)
        platform_active, platform_until, platform_lifetime,
        chat_credits,
        grandfathered_tier,
        access_kind                              # 'platform' | 'grandfathered' | 'none'
      }
    The WebApp picks the right paywall based on access_kind.
    """
    try:
        uid = int(request.match_info["uid"])
    except Exception:
        return web.json_response({"error":"invalid uid"},status=400)

    try:
        from database import enforce_subscription, get_platform_info, get_credits, grandfather_legacy_tier
        # Subscription guard (self-healing): strict UTC comparison; if the legacy
        # premium flag is set but premium_until has passed, this writes
        # is_premium=FALSE to the DB *before* we build the status payload, so the
        # response the WebApp trusts can never report a stale-active subscription.
        legacy = await enforce_subscription(uid)
        # Snapshot grandfathered tier the first time we see an active legacy
        # bundle alongside empty grandfathered_tier — idempotent.
        try:
            await grandfather_legacy_tier(uid)
        except Exception:
            pass
        platform = await get_platform_info(uid)
        credits = await get_credits(uid)
        gf = platform.get("grandfathered_tier","") or ""
        if platform.get("active"):
            kind = "platform"
        elif gf:
            kind = "grandfathered"
        else:
            kind = "none"
        # Hearts piggy-back on this call (the WebApp already fetches it on load)
        # so the heart-bar renders without an extra round-trip. Premium →
        # unlimited; free → live pool with regen applied.
        try:
            from database import get_hearts_state
            hearts = await get_hearts_state(uid, kind != "none")
        except Exception:
            hearts = {"hearts":5,"max":5,"next_in":0,"full":True,"unlimited":(kind!="none")}
        # Daily-lesson budget piggy-backs too, for the same reason.
        try:
            from database import get_daily_lessons_state
            lessons = await get_daily_lessons_state(uid, kind != "none")
        except Exception:
            lessons = {"used":0,"limit":3,"remaining":3,"unlimited":(kind!="none"),"reset_in":0}
        # Daily-flashcard budget piggy-backs too (metered for both tiers).
        try:
            from database import get_daily_cards_state
            cards = await get_daily_cards_state(uid, kind != "none")
        except Exception:
            cards = {"used":0,"limit":(60 if kind!="none" else 15),"remaining":(60 if kind!="none" else 15),"reset_in":0,"is_premium":(kind!="none")}
        # Free ALEX credit wallet piggy-backs too (applies the daily UTC grant on
        # read), so the chat header can show today's remaining free messages
        # without a separate round-trip. Paid users ignore it.
        try:
            from database import get_free_credits_state
            free_wallet = await get_free_credits_state(uid)
        except Exception:
            free_wallet = {"balance":0,"cap":10,"daily":2,"reset_in":0}
        info = {
            **legacy,
            "platform_active": platform.get("active", False),
            "platform_until": platform.get("until"),
            "platform_lifetime": platform.get("lifetime", False),
            "chat_credits": credits,
            "grandfathered_tier": gf,
            "access_kind": kind,
            "hearts": hearts,
            "lessons": lessons,
            "cards": cards,
            "free_credits": free_wallet,
            "source": "database",
        }
        return web.json_response(info, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        return web.json_response({"is_premium":False,"tier":"","error":str(e)},
                                  headers={"Access-Control-Allow-Origin":"*"})

# ── GRANT PLATFORM SUB (bot → server after payment) ───────────────────────────
async def handle_grant_platform(request):
    try:
        body = await request.json()
        uid = int(body.get("uid",0))
        period = str(body.get("period","1m"))
        secret = body.get("secret","")
    except Exception:
        return web.json_response({"error":"bad request"},status=400)
    if period not in {"1m","6m","lifetime"}:
        return web.json_response({"error":"invalid period"},status=400)
    BOT_SECRET = os.getenv("BOT_SECRET","polyglotty_secret_2025")
    if secret != BOT_SECRET:
        return web.json_response({"error":"unauthorized"},status=403)
    try:
        from database import grant_platform
        await grant_platform(uid, period)
        return web.json_response({"ok":True,"uid":uid,"period":period},
                                  headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        return web.json_response({"error":str(e)},status=500,headers={"Access-Control-Allow-Origin":"*"})

# ── GRANT CREDITS (bot → server after credit pack purchase) ───────────────────
async def handle_grant_credits(request):
    try:
        body = await request.json()
        uid = int(body.get("uid",0))
        credits = int(body.get("credits",0))
        secret = body.get("secret","")
    except Exception:
        return web.json_response({"error":"bad request"},status=400)
    if credits <= 0 or credits > 100000:
        return web.json_response({"error":"invalid credits"},status=400)
    BOT_SECRET = os.getenv("BOT_SECRET","polyglotty_secret_2025")
    if secret != BOT_SECRET:
        return web.json_response({"error":"unauthorized"},status=403)
    try:
        # Route through ai_billing.topup so the purchase is also recorded in the
        # billing ledger (type=topup) — that's what the admin revenue metric and
        # per-user cost-vs-revenue alerts read from. Falls back to a raw
        # add_credits if the billing module is unavailable.
        meta = str(body.get("meta") or "stars")
        try:
            from ai_billing import topup
            new_balance = await topup(uid, credits, meta=meta)
        except Exception as e:
            logger.warning("ai_billing.topup failed uid=%s, falling back: %s", uid, e)
            from database import add_credits, get_credits
            await add_credits(uid, credits)
            new_balance = await get_credits(uid)
        return web.json_response({"ok":True,"uid":uid,"credits":credits,"balance":new_balance},
                                  headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        return web.json_response({"error":str(e)},status=500,headers={"Access-Control-Allow-Origin":"*"})

# ── GRANT PREMIUM (called by bot after successful payment) ────────────────────
async def handle_grant_premium(request):
    """Called internally by bot after Telegram Stars payment confirmed."""
    try:
        body = await request.json()
        uid = int(body.get("uid",0))
        months = int(body.get("months",1))
        tier = str(body.get("tier","basic")).lower()
        secret = body.get("secret","")
    except Exception:
        return web.json_response({"error":"bad request"},status=400)
    if tier not in {"basic", "pro", "ultimate"}:
        return web.json_response({"error":"invalid tier"},status=400)

    BOT_SECRET = os.getenv("BOT_SECRET","polyglotty_secret_2025")
    if secret != BOT_SECRET:
        return web.json_response({"error":"unauthorized"},status=403)

    try:
        from database import set_premium
        await set_premium(uid, months, tier)
        return web.json_response({"ok":True,"uid":uid,"months":months,"tier":tier},
                                  headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        return web.json_response({"error":str(e)},status=500,headers={"Access-Control-Allow-Origin":"*"})

# ── TTS ──────────────────────────────────────────────────────────────────────
async def handle_tts(request):
    """Text-to-speech endpoint. Returns MP3 audio bytes."""
    try:
        body = await request.json()
        text = str(body.get("text","")).strip()[:500]  # limit 500 chars
        tts_lang = str(body.get("lang","en"))[:5]
        # Context-aware actor: teacher | casual | academic | default.
        tts_role = str(body.get("role","") or "default")[:16].lower()
        if tts_role not in ("teacher","casual","academic","default"):
            tts_role = "default"
        if not text:
            return web.json_response({"error":"empty text"},status=400)
    except Exception:
        return web.json_response({"error":"bad request"},status=400)

    try:
        from tts import text_to_speech
        audio = await text_to_speech(text, lang=tts_lang, role=tts_role)
        if audio:
            return web.Response(
                body=audio,
                content_type="audio/mpeg",
                headers={
                    "Access-Control-Allow-Origin":"*",
                    "Cache-Control":"public, max-age=86400",
                }
            )
        return web.json_response({"error":"TTS failed"},status=500,headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"TTS error: {e}")
        return web.json_response({"error":str(e)[:200]},status=500,headers={"Access-Control-Allow-Origin":"*"})

# ── STT ──────────────────────────────────────────────────────────────────────
async def handle_transcribe(request):
    """Speech-to-text endpoint for Telegram WebView, where Web Speech API is unreliable."""
    max_bytes = 8 * 1024 * 1024
    audio = b""
    filename = "voice.webm"
    lang = "en"
    try:
        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            async for part in reader:
                if part.name == "lang":
                    lang = (await part.text()).strip()[:5] or "en"
                elif part.name in ("audio", "file", "voice"):
                    filename = part.filename or filename
                    chunks = []
                    size = 0
                    while True:
                        chunk = await part.read_chunk()
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            return web.json_response({"error":"audio too large"},status=413,headers={"Access-Control-Allow-Origin":"*"})
                        chunks.append(chunk)
                    audio = b"".join(chunks)
        else:
            audio = await request.read()
            filename = request.query.get("filename", filename)
            lang = request.query.get("lang", lang)[:5] or "en"
            if len(audio) > max_bytes:
                return web.json_response({"error":"audio too large"},status=413,headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.warning(f"transcribe parse error: {e}")
        return web.json_response({"error":"bad audio request"},status=400,headers={"Access-Control-Allow-Origin":"*"})

    if not audio:
        return web.json_response({"error":"empty audio"},status=400,headers={"Access-Control-Allow-Origin":"*"})

    try:
        from tts import transcribe_audio
        text = await transcribe_audio(audio, filename=filename, lang=lang)
        if text:
            return web.json_response({"text":text},headers={"Access-Control-Allow-Origin":"*"})
        return web.json_response({"error":"speech not recognized"},status=422,headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"transcribe error: {e}")
        return web.json_response({"error":str(e)[:200]},status=500,headers={"Access-Control-Allow-Origin":"*"})

# ── MISTAKES / ERROR DIARY ───────────────────────────────────────────────────
def _auth_uid_from_request(request, uid: int, init_data: str):
    if REQUIRE_TG_INIT_DATA and BOT_TOKEN and not _is_local_request(request):
        ok, reason, signed_uid = _verify_telegram_init_data(init_data, uid)
        if not ok:
            logger.warning("blocked mistakes uid=%s signed_uid=%s reason=%s", uid, signed_uid, reason)
            return False, reason, uid
        if signed_uid and not uid:
            uid = signed_uid
    return True, "", uid

def _mistake_row(row):
    d = dict(row)
    for key in ("date", "created_at"):
        if d.get(key) is not None and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    return {
        "id": d.get("id"),
        "original": d.get("original") or "",
        "corrected": d.get("corrected") or "",
        "explanation": d.get("explanation") or "",
        "category": d.get("category") or "grammar",
        "date": str(d.get("date") or "")[:10],
        "created_at": d.get("created_at"),
    }

async def handle_mistakes_get(request):
    try:
        uid = int(request.match_info.get("uid", "0"))
        limit = max(1, min(100, int(request.query.get("limit", "50"))))
    except Exception:
        return web.json_response({"error":"bad request"},status=400,headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(request.headers.get("X-Telegram-Init-Data") or request.query.get("init_data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."},status=403,headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import get_mistakes, get_mistake_count
        rows = await get_mistakes(uid, limit=limit)
        count = await get_mistake_count(uid)
        return web.json_response({"mistakes":[_mistake_row(r) for r in rows], "count": count},
                                 headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"mistakes get error: {e}")
        return web.json_response({"mistakes":[],"count":0},headers={"Access-Control-Allow-Origin":"*"})

async def handle_mistakes_post(request):
    try:
        body = await request.json()
        uid = int(body.get("uid",0))
    except Exception:
        return web.json_response({"error":"bad request"},status=400,headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(body.get("init_data") or request.headers.get("X-Telegram-Init-Data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."},status=403,headers={"Access-Control-Allow-Origin":"*"})
    original = str(body.get("original") or body.get("wrong") or "").strip()
    corrected = str(body.get("corrected") or body.get("right") or "").strip()
    explanation = str(body.get("explanation") or "").strip()
    category = str(body.get("category") or "grammar").strip()[:40] or "grammar"
    if not uid or not original or not corrected:
        return web.json_response({"error":"empty mistake"},status=400,headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import db, log_mistake, get_mistake_count, upsert_user
        try:
            await upsert_user(uid, "Student")
        except Exception:
            pass
        exists = await db(
            "SELECT id FROM mistakes WHERE uid=? AND lower(original)=lower(?) AND lower(corrected)=lower(?) LIMIT 1",
            uid, original[:300], corrected[:300], fetch="one"
        )
        if not exists:
            await log_mistake(uid, original, corrected, explanation, category)
        count = await get_mistake_count(uid)
        mid = exists["id"] if exists else None
        return web.json_response({"ok":True,"id":mid,"count":count},headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"mistakes post error: {e}")
        return web.json_response({"error":str(e)[:160]},status=500,headers={"Access-Control-Allow-Origin":"*"})

# ── SAVED-WORDS DICTIONARY (capped queue + pagination) ──────────────────────────
async def _vocab_caps(uid: int):
    """Return (cap, is_premium, page_size) for this user from live config.
    Premium = active Platform sub OR grandfathered OR active legacy premium."""
    from billing_config import load_config
    cfg = await load_config()
    try:
        from database import check_platform
        is_prem = bool(await check_platform(uid))
    except Exception:
        is_prem = False
    cap = int(cfg.get("VOCAB_QUEUE_PREMIUM", 200) if is_prem else cfg.get("VOCAB_QUEUE_FREE", 50))
    page = int(cfg.get("VOCAB_PAGE_SIZE", 15))
    return cap, is_prem, page

def _vocab_row(row):
    d = dict(row)
    for key in ("next_review", "created_at"):
        if d.get(key) is not None and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    return {
        "id": d.get("id"),
        "word": d.get("word") or "",
        "translation": d.get("translation") or "",
        "example": d.get("example") or "",
        "topic": d.get("topic") or "general",
        "next_review": str(d.get("next_review") or "")[:10],
        "reviews": int(d.get("reviews") or 0),
    }

async def handle_save_word(request):
    """Save one word into the personal dictionary, enforcing the per-tier queue
    cap. 409 {error:'vocab_full'} when the queue is full so the client can show
    the 'learn old words to free space' toast."""
    try:
        body = await request.json()
        uid = int(body.get("uid", 0))
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(body.get("init_data") or request.headers.get("X-Telegram-Init-Data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    word = str(body.get("word") or "").strip()[:120]
    translation = str(body.get("translation") or "").strip()[:300]
    example = str(body.get("example") or "").strip()[:500]
    topic = (str(body.get("topic") or "general").strip()[:40] or "general")
    if not uid or not word:
        return web.json_response({"error":"empty word"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import add_word_capped, get_word_count, upsert_user
        try:
            await upsert_user(uid, "Student")
        except Exception:
            pass
        cap, is_prem, _ = await _vocab_caps(uid)
        status = await add_word_capped(uid, word, translation, example, topic, cap)
        count = await get_word_count(uid)
        if status == "full":
            return web.json_response({"error":"vocab_full","limit":cap,"count":count,"premium":is_prem},
                                     status=409, headers={"Access-Control-Allow-Origin":"*"})
        return web.json_response({"ok":True,"status":status,"count":count,"limit":cap,"premium":is_prem},
                                 headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"save_word error: {e}")
        return web.json_response({"error":str(e)[:160]}, status=500, headers={"Access-Control-Allow-Origin":"*"})

# ── DICTIONARY WORD-BREAKDOWN CACHE ───────────────────────────────────────────
# The webapp "3 examples in context" feature normally pays Claude Sonnet for
# three contextual sentences per word. These two endpoints add a DB cache:
#   GET  /api/word_examples?word=X&lang=Y  → cached [{en,tr}] or {hit:false}
#   POST /api/word_examples                → store examples the client just got
# On a cache HIT the client renders instantly and skips the paid /api/chat call.
async def handle_word_examples_get(request):
    word = str(request.query.get("word") or "").strip()
    lang = str(request.query.get("lang") or "en").strip()[:8]
    if not word:
        return web.json_response({"hit": False}, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import get_word_examples
        examples = await get_word_examples(word, lang)
    except Exception as e:
        logger.warning(f"word_examples get failed: {e}")
        examples = None
    if examples:
        return web.json_response({"hit": True, "examples": examples},
                                 headers={"Access-Control-Allow-Origin":"*"})
    return web.json_response({"hit": False}, headers={"Access-Control-Allow-Origin":"*"})

async def handle_word_examples_save(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    word = str(body.get("word") or "").strip()
    lang = str(body.get("lang") or "en").strip()[:8]
    raw = body.get("examples")
    # Normalise to a clean list of {en,tr} dicts, max 3.
    examples = []
    if isinstance(raw, list):
        for it in raw[:3]:
            if isinstance(it, dict):
                en = str(it.get("en") or "").strip()[:300]
                tr = str(it.get("tr") or "").strip()[:300]
                if en:
                    examples.append({"en": en, "tr": tr})
    if not word or not examples:
        return web.json_response({"ok": False}, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import save_word_examples
        await save_word_examples(word, lang, examples)
    except Exception as e:
        logger.warning(f"word_examples save failed: {e}")
        return web.json_response({"ok": False}, headers={"Access-Control-Allow-Origin":"*"})
    return web.json_response({"ok": True}, headers={"Access-Control-Allow-Origin":"*"})

async def handle_vocab_list(request):
    """Paginated saved-words list (newest first). Never returns the whole table:
    strictly VOCAB_PAGE_SIZE rows per call + has_more/next offset for infinite
    scroll on the client."""
    try:
        uid = int(request.match_info.get("uid", "0"))
        offset = max(0, int(request.query.get("offset", "0")))
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(request.headers.get("X-Telegram-Init-Data") or request.query.get("init_data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import get_vocab_page, get_word_count
        cap, is_prem, page = await _vocab_caps(uid)
        rows, has_more = await get_vocab_page(uid, offset=offset, limit=page)
        total = await get_word_count(uid)
        return web.json_response({
            "words": [_vocab_row(r) for r in rows],
            "has_more": has_more,
            "offset": offset + len(rows),
            "total": total,
            "limit": cap,
            "premium": is_prem,
        }, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"vocab_list error: {e}")
        return web.json_response({"words":[],"has_more":False,"offset":offset,"total":0},
                                 headers={"Access-Control-Allow-Origin":"*"})

async def handle_vocab_delete(request):
    """Remove one word (frees a queue slot). Scoped by the authenticated uid."""
    try:
        uid = int(request.match_info.get("uid", "0"))
        word_id = int(request.match_info.get("word_id", "0"))
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(request.headers.get("X-Telegram-Init-Data") or request.query.get("init_data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    if not uid or not word_id:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import delete_word, get_word_count
        await delete_word(uid, word_id)
        count = await get_word_count(uid)
        return web.json_response({"ok":True,"count":count}, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"vocab_delete error: {e}")
        return web.json_response({"error":str(e)[:160]}, status=500, headers={"Access-Control-Allow-Origin":"*"})

# ── HEARTS (free-tier lesson lives) ─────────────────────────────────────────────
async def _is_premium_uid(uid: int) -> bool:
    try:
        from database import check_platform
        return bool(await check_platform(uid))
    except Exception:
        return False

async def handle_hearts_get(request):
    """Current heart pool (with regen applied). Premium → unlimited."""
    try:
        uid = int(request.match_info.get("uid", "0"))
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(request.headers.get("X-Telegram-Init-Data") or request.query.get("init_data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import get_hearts_state
        prem = await _is_premium_uid(uid)
        st = await get_hearts_state(uid, prem)
        return web.json_response(st, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"hearts_get error: {e}")
        return web.json_response({"hearts":5,"max":5,"next_in":0,"full":True,"unlimited":False},
                                 headers={"Access-Control-Allow-Origin":"*"})

async def handle_hearts_lose(request):
    """Deduct one heart for a lesson mistake (server-authoritative). Premium is
    never decremented; returns the live pool so the client can sync the UI."""
    try:
        body = await request.json()
        uid = int(body.get("uid", 0))
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(body.get("init_data") or request.headers.get("X-Telegram-Init-Data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    if not uid:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import lose_heart, upsert_user
        try:
            await upsert_user(uid, "Student")
        except Exception:
            pass
        prem = await _is_premium_uid(uid)
        st = await lose_heart(uid, prem)
        return web.json_response(st, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"hearts_lose error: {e}")
        return web.json_response({"error":str(e)[:160]}, status=500, headers={"Access-Control-Allow-Origin":"*"})

async def handle_hearts_refill(request):
    """Reward-based top-up. Body {amount}: amount<=0 → full refill (reserved for
    server-verified Stars purchases done in bot.py); amount>0 → add N hearts.
    This endpoint only grants the bounded ad-reward (+1) so it can't be abused to
    self-grant a full pool; the full refill path lives behind the Stars payment."""
    try:
        body = await request.json()
        uid = int(body.get("uid", 0))
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(body.get("init_data") or request.headers.get("X-Telegram-Init-Data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    if not uid:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import refill_hearts
        # Client-triggered refills are bounded to a single heart (ad reward).
        st = await refill_hearts(uid, amount=1)
        return web.json_response(st, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"hearts_refill error: {e}")
        return web.json_response({"error":str(e)[:160]}, status=500, headers={"Access-Control-Allow-Origin":"*"})

# ── DAILY LESSON LIMIT (free-tier economy guard) ──────────────────────────────
async def handle_lessons_limit_get(request):
    """Current daily-lesson budget {used,limit,remaining,unlimited,reset_in}.
    Premium → unlimited. Read-only (no mutation)."""
    try:
        uid = int(request.match_info.get("uid", "0"))
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(request.headers.get("X-Telegram-Init-Data") or request.query.get("init_data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import get_daily_lessons_state
        prem = await _is_premium_uid(uid)
        st = await get_daily_lessons_state(uid, prem)
        return web.json_response(st, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"lessons_limit_get error: {e}")
        return web.json_response({"used":0,"limit":5,"remaining":5,"unlimited":False,"reset_in":0},
                                 headers={"Access-Control-Allow-Origin":"*"})

async def handle_lessons_done(request):
    """Record a genuinely-new lesson completion against the per-UTC-day counter
    (free users only) and return the fresh budget. The client calls this once per
    NEW completion; Premium is never metered."""
    try:
        body = await request.json()
        uid = int(body.get("uid", 0))
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(body.get("init_data") or request.headers.get("X-Telegram-Init-Data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    if not uid:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import record_lesson_done, upsert_user
        try:
            await upsert_user(uid, "Student")
        except Exception:
            pass
        prem = await _is_premium_uid(uid)
        st = await record_lesson_done(uid, prem)
        return web.json_response(st, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"lessons_done error: {e}")
        return web.json_response({"error":str(e)[:160]}, status=500, headers={"Access-Control-Allow-Origin":"*"})

async def handle_cards_limit_get(request):
    """Current daily-flashcard budget {used,limit,remaining,reset_in,is_premium}.
    Both tiers are metered (Premium just has a larger cap). Read-only."""
    try:
        uid = int(request.match_info.get("uid", "0"))
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(request.headers.get("X-Telegram-Init-Data") or request.query.get("init_data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import get_daily_cards_state
        prem = await _is_premium_uid(uid)
        st = await get_daily_cards_state(uid, prem)
        return web.json_response(st, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"cards_limit_get error: {e}")
        return web.json_response({"used":0,"limit":15,"remaining":15,"reset_in":0,"is_premium":False},
                                 headers={"Access-Control-Allow-Origin":"*"})

async def handle_cards_done(request):
    """Record one finished flashcard against the per-UTC-day counter (both tiers)
    and return the fresh budget. The client calls this once per card finished
    (Skip or Save)."""
    try:
        body = await request.json()
        uid = int(body.get("uid", 0))
    except Exception:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    init_data = str(body.get("init_data") or request.headers.get("X-Telegram-Init-Data") or "")
    ok, reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error":"Telegram session check failed. Reopen the bot app and try again."}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    if not uid:
        return web.json_response({"error":"bad request"}, status=400, headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import record_card_done, upsert_user
        try:
            await upsert_user(uid, "Student")
        except Exception:
            pass
        prem = await _is_premium_uid(uid)
        st = await record_card_done(uid, prem)
        return web.json_response(st, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"cards_done error: {e}")
        return web.json_response({"error":str(e)[:160]}, status=500, headers={"Access-Control-Allow-Origin":"*"})

# ── EXAM SIMULATOR (premium TOEFL/IELTS mock + AI grading + certificate) ──────
async def _exam_auth(request):
    """Authenticate + enforce an ACTIVE subscription for every exam route.
    Returns (uid, lang, body, None) on success, or (uid, lang, body, response)
    where `response` is a ready 402/403/400 to return immediately."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    uid = int(body.get("uid", 0) or 0)
    lang = str(body.get("lang", "ru") or "ru")
    init_data = str(body.get("init_data") or request.headers.get("X-Telegram-Init-Data") or "")
    ok, _reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return 0, lang, body, web.json_response(
            {"error": "Telegram session check failed. Reopen the bot app and try again."},
            status=403, headers={"Access-Control-Allow-Origin": "*"})
    if not uid:
        return 0, lang, body, web.json_response(
            {"error": "bad request"}, status=400, headers={"Access-Control-Allow-Origin": "*"})
    if uid not in ADMIN_IDS and not await _is_premium_uid(uid):
        return uid, lang, body, web.json_response(
            _paywall_json(lang), status=402, headers={"Access-Control-Allow-Origin": "*"})
    return uid, lang, body, None

async def _exam_log_ai_cost(uid: int, usage: dict):
    """Best-effort: charge AI-grading cost to the daily quota ledger (Sonnet)."""
    if not usage or uid in ADMIN_IDS:
        return
    try:
        from database import add_quota_usage
        await add_quota_usage(uid, points=0, ai_cost=_estimate_ai_cost("sonnet", usage))
    except Exception as e:
        logger.warning("exam ai-cost log failed uid=%s: %s", uid, e)

async def handle_exam_start(request):
    """Open a new exam session. body: {uid, exam_type:'toefl'|'ielts'}."""
    uid, lang, body, err = await _exam_auth(request)
    if err:
        return err
    exam_type = str(body.get("exam_type") or "toefl").lower()
    try:
        from database import create_exam_session, upsert_user
        try:
            await upsert_user(uid, "Student")
        except Exception:
            pass
        sid = await create_exam_session(uid, exam_type)
        scale = 9.0 if exam_type == "ielts" else 120.0
        return web.json_response(
            {"session_id": sid, "exam_type": ("ielts" if exam_type == "ielts" else "toefl"),
             "scale_max": scale, "section_max": (9 if exam_type == "ielts" else 30)},
            headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"exam_start error: {e}")
        return web.json_response({"error": str(e)[:160]}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})

async def handle_exam_section(request):
    """Record an OBJECTIVE section (reading|listening) scored client-side from the
    answer key. body: {uid, session_id, section, raw_score, max_score, answers?}."""
    uid, lang, body, err = await _exam_auth(request)
    if err:
        return err
    section = str(body.get("section") or "").lower()
    if section not in ("reading", "listening"):
        return web.json_response({"error": "bad section"}, status=400,
                                 headers={"Access-Control-Allow-Origin": "*"})
    try:
        sid = int(body.get("session_id") or 0)
        max_score = max(0.0, float(body.get("max_score") or 0))
        raw = max(0.0, min(float(body.get("raw_score") or 0), max_score or 1e9))
    except Exception:
        return web.json_response({"error": "bad request"}, status=400,
                                 headers={"Access-Control-Allow-Origin": "*"})
    try:
        from database import get_exam_session, save_section_result
        s = await get_exam_session(sid, uid)
        if not s:
            return web.json_response({"error": "no session"}, status=404,
                                     headers={"Access-Control-Allow-Origin": "*"})
        exam_type = "ielts" if str(s["exam_type"]).lower() == "ielts" else "toefl"
        # Map objective correct-ratio onto the official per-section scale.
        smax = 9 if exam_type == "ielts" else 30
        ratio = (raw / max_score) if max_score else 0.0
        section_score = round(ratio * smax * 2) / 2.0 if exam_type == "ielts" else int(round(ratio * smax))
        await save_section_result(sid, uid, section, section_score, smax,
                                  {"correct": raw, "total": max_score,
                                   "answers": (body.get("answers") or [])[:60]})
        return web.json_response({"ok": True, "section": section,
                                  "score": section_score, "max_score": smax},
                                 headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"exam_section error: {e}")
        return web.json_response({"error": str(e)[:160]}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})

def _exam_norm_txt(s) -> str:
    """Whitespace/case-insensitive normaliser for comparing answer option text."""
    return " ".join(str(s or "").lower().split())


def _exam_public_item(item: dict) -> dict:
    """Return a client-safe copy of a generated exam item: the correct-answer
    index (`a`) and the `explanation` are stripped from every question so the
    answer key NEVER leaves the server. Each question keeps a stable `qi` (its
    position) so the client can later report which question a choice belongs to,
    and the server can grade it against the stored template."""
    if not isinstance(item, dict):
        return {}
    pub = {k: v for k, v in item.items() if k != "questions"}
    qs = []
    for i, q in enumerate(item.get("questions") or []):
        if not isinstance(q, dict):
            continue
        cq = {"qi": i, "q": q.get("q", ""), "o": list(q.get("o") or [])}
        if q.get("qtype"):
            cq["qtype"] = q["qtype"]
        qs.append(cq)
    pub["questions"] = qs
    return pub


async def handle_exam_grade_section(request):
    """Server-authoritative grading for an OBJECTIVE section (reading|listening)
    built from AI-generated items. The client sends ONLY the option text it
    chose per question — never a score. The server reads the correct answer from
    the stored template payload (which it never shipped to the client) and
    computes the section score itself.

    body: {uid, session_id, section, responses:[{template_id, qi, choice}]}.
    """
    uid, lang, body, err = await _exam_auth(request)
    if err:
        return err
    section = str(body.get("section") or "").lower()
    if section not in ("reading", "listening"):
        return web.json_response({"error": "bad section"}, status=400,
                                 headers={"Access-Control-Allow-Origin": "*"})
    try:
        sid = int(body.get("session_id") or 0)
    except Exception:
        return web.json_response({"error": "bad request"}, status=400,
                                 headers={"Access-Control-Allow-Origin": "*"})
    responses = body.get("responses")
    if not isinstance(responses, list):
        responses = []
    try:
        from database import (get_exam_session, save_section_result,
                              get_exam_template_by_id)
        # sid > 0 → a persisted certificate exam: bind to the session and save
        # the result. sid == 0 → an unscored practice drill: grade and return
        # the score but persist nothing.
        s = None
        if sid > 0:
            s = await get_exam_session(sid, uid)
            if not s:
                return web.json_response({"error": "no session"}, status=404,
                                         headers={"Access-Control-Allow-Origin": "*"})
            exam_type = "ielts" if str(s["exam_type"]).lower() == "ielts" else "toefl"
        else:
            exam_type = "ielts" if str(body.get("exam_type") or "toefl").lower() == "ielts" else "toefl"
        smax = 9 if exam_type == "ielts" else 30
        tcache = {}
        correct = 0
        total = 0
        for r in responses[:80]:
            if not isinstance(r, dict):
                continue
            try:
                tid = int(r.get("template_id") or 0)
                qi = int(r.get("qi"))
            except Exception:
                continue
            choice = _exam_norm_txt(r.get("choice"))
            if tid not in tcache:
                row = await get_exam_template_by_id(tid)
                try:
                    tcache[tid] = json.loads(row["payload"]) if row else None
                except Exception:
                    tcache[tid] = None
            item = tcache.get(tid)
            if not isinstance(item, dict):
                continue
            qs = item.get("questions") or []
            if qi < 0 or qi >= len(qs):
                continue
            q = qs[qi] or {}
            opts = q.get("o") or []
            ai = q.get("a")
            total += 1
            if isinstance(ai, int) and 0 <= ai < len(opts):
                if choice and choice == _exam_norm_txt(opts[ai]):
                    correct += 1
        ratio = (correct / total) if total else 0.0
        section_score = (round(ratio * smax * 2) / 2.0 if exam_type == "ielts"
                         else int(round(ratio * smax)))
        # Persist only for a real certificate session; drills (sid==0) return the
        # score but store nothing.
        if sid > 0:
            await save_section_result(sid, uid, section, section_score, smax,
                                      {"correct": correct, "total": total,
                                       "server_graded": True})
        return web.json_response({"ok": True, "section": section,
                                  "score": section_score, "max_score": smax,
                                  "correct": correct, "total": total},
                                 headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"exam_grade_section error: {e}")
        return web.json_response({"error": str(e)[:160]}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def handle_exam_generate(request):
    """Serve ONE strictly-academic exam item (Reading or Listening) for the
    simulator. Cache-first: returns a random cached variant the learner has not
    seen; otherwise generates a fresh one with Claude Sonnet from the academic
    topic pool, caches it (reused across users) and marks it seen.

    When the client sends `secure:1`, the correct answers are stripped before
    the item is sent (see _exam_public_item) and grading happens server-side in
    handle_exam_grade_section using the full payload kept in the DB. Legacy
    clients that omit `secure` still receive the answer key inline, so this is a
    fully backward-compatible rollout.

    body: {uid, exam_type:'toefl'|'ielts', section:'reading'|'listening',
           level?:'C1'|'C2', secure?:1}
    """
    uid, lang, body, err = await _exam_auth(request)
    if err:
        return err
    secure = bool(body.get("secure"))
    _pub = _exam_public_item if secure else (lambda x: x)
    exam_type = "ielts" if str(body.get("exam_type") or "toefl").lower() == "ielts" else "toefl"
    section = str(body.get("section") or "reading").lower()
    if section not in ("reading", "listening"):
        return web.json_response({"error": "bad section"}, status=400,
                                 headers={"Access-Control-Allow-Origin": "*"})
    level = str(body.get("level") or "C1").upper()
    try:
        from database import (get_unseen_exam_template, seen_topic_ids,
                              save_exam_template, mark_exam_template_seen)
        # 1) Cache-first: hand back an unseen, already-generated variant.
        cached = await get_unseen_exam_template(uid, exam_type, section)
        if cached:
            try:
                item = json.loads(cached["payload"])
            except Exception:
                item = None
            if item:
                await mark_exam_template_seen(uid, int(cached["id"]))
                return web.json_response(
                    {"template_id": int(cached["id"]), "exam_type": exam_type,
                     "section": section, "cached": True, **_pub(item)},
                    headers={"Access-Control-Allow-Origin": "*"})

        # 2) Generate a fresh academic variant on a topic this user hasn't seen.
        from exam_content import pick_topics, build_exam_system_prompt
        seen = await seen_topic_ids(uid)
        topic = pick_topics(1, exclude_ids=seen, exam_type=exam_type)[0]
        system = build_exam_system_prompt(exam_type, section, level, topic, lang)
        try:
            raw, _usage = await _gen_text(system, f"Generate one {section} item now.",
                                          max_tokens=1400)
        except Exception as e:
            logger.error("exam_generate AI error: %s", e)
            return web.json_response({"error": "generation_failed"}, status=502,
                                     headers={"Access-Control-Allow-Origin": "*"})
        raw = raw.replace("```json", "").replace("```", "").strip()
        item = json.loads(raw)
        # Stamp provenance so the client can show the academic topic title.
        item.setdefault("topic", topic["title"])
        item["topic_id"] = topic["id"]
        item["domain"] = topic["domain"]
        await _exam_log_ai_cost(uid, (_usage or {}))
        tid = await save_exam_template(exam_type, section, topic["id"], level, item)
        if tid:
            await mark_exam_template_seen(uid, tid)
        return web.json_response(
            {"template_id": tid, "exam_type": exam_type, "section": section,
             "cached": False, **_pub(item)},
            headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error("exam_generate error: %s", e)
        return web.json_response({"error": str(e)[:160]}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})


async def handle_exam_writing(request):
    """AI-graded Writing section. body: {uid, session_id, prompt, essay}."""
    uid, lang, body, err = await _exam_auth(request)
    if err:
        return err
    prompt = str(body.get("prompt") or "").strip()[:2000]
    essay = str(body.get("essay") or "").strip()[:6000]
    if len(essay.split()) < 20:
        return web.json_response({"error": "too_short",
                                  "message": ("Эссе слишком короткое для проверки." if lang == "ru"
                                              else "The essay is too short to grade.")},
                                 status=400, headers={"Access-Control-Allow-Origin": "*"})
    try:
        from database import get_exam_session, save_section_result
        from exam_grader import grade_writing
        sid = int(body.get("session_id") or 0)
        s = await get_exam_session(sid, uid)
        if not s:
            return web.json_response({"error": "no session"}, status=404,
                                     headers={"Access-Control-Allow-Origin": "*"})
        exam_type = "ielts" if str(s["exam_type"]).lower() == "ielts" else "toefl"
        audit = await grade_writing(exam_type, prompt, essay)
        await _exam_log_ai_cost(uid, audit.get("usage") or {})
        await save_section_result(sid, uid, "writing", audit["score"], audit["max_score"],
                                  {"prompt": prompt, "essay": essay,
                                   "feedback": audit["feedback"], "errors": audit["errors"],
                                   "model_answer": audit["model_answer"],
                                   "word_count": audit["word_count"]})
        audit.pop("usage", None)
        return web.json_response(audit, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"exam_writing error: {e}")
        return web.json_response({"error": str(e)[:160]}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})

async def handle_exam_speaking(request):
    """AI-graded Speaking section on an STT transcript.
    body: {uid, session_id, question, transcript}. (Audio → text via /api/transcribe.)"""
    uid, lang, body, err = await _exam_auth(request)
    if err:
        return err
    question = str(body.get("question") or "").strip()[:1000]
    transcript = str(body.get("transcript") or "").strip()[:4000]
    if len(transcript.split()) < 3:
        return web.json_response({"error": "too_short",
                                  "message": ("Ответ слишком короткий для оценки." if lang == "ru"
                                              else "The answer is too short to grade.")},
                                 status=400, headers={"Access-Control-Allow-Origin": "*"})
    try:
        from database import get_exam_session, save_section_result
        from exam_grader import grade_speaking
        sid = int(body.get("session_id") or 0)
        s = await get_exam_session(sid, uid)
        if not s:
            return web.json_response({"error": "no session"}, status=404,
                                     headers={"Access-Control-Allow-Origin": "*"})
        exam_type = "ielts" if str(s["exam_type"]).lower() == "ielts" else "toefl"
        audit = await grade_speaking(exam_type, question, transcript)
        await _exam_log_ai_cost(uid, audit.get("usage") or {})
        await save_section_result(sid, uid, "speaking", audit["score"], audit["max_score"],
                                  {"question": question, "transcript": transcript,
                                   "feedback": audit["feedback"], "fluency": audit["fluency"],
                                   "pronunciation": audit["pronunciation"],
                                   "grammar": audit["grammar"]})
        audit.pop("usage", None)
        return web.json_response(audit, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"exam_speaking error: {e}")
        return web.json_response({"error": str(e)[:160]}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})

async def handle_exam_finish(request):
    """Finalise a session: scale the four sections → total + mint certificate.
    body: {uid, session_id}."""
    uid, lang, body, err = await _exam_auth(request)
    if err:
        return err
    try:
        from database import finalize_exam_session
        sid = int(body.get("session_id") or 0)
        result = await finalize_exam_session(sid, uid)
        if not result:
            return web.json_response({"error": "no session"}, status=404,
                                     headers={"Access-Control-Allow-Origin": "*"})
        result["certificate_url"] = f"/api/exam/certificate/{result['cert_code']}"
        return web.json_response(result, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"exam_finish error: {e}")
        return web.json_response({"error": str(e)[:160]}, status=500,
                                 headers={"Access-Control-Allow-Origin": "*"})

async def handle_exam_history(request):
    """GET /api/exam/history/{uid} — completed sessions for the Progress screen."""
    try:
        uid = int(request.match_info.get("uid", "0"))
    except Exception:
        return web.json_response({"error": "bad request"}, status=400,
                                 headers={"Access-Control-Allow-Origin": "*"})
    init_data = str(request.headers.get("X-Telegram-Init-Data") or request.query.get("init_data") or "")
    ok, _reason, uid = _auth_uid_from_request(request, uid, init_data)
    if not ok:
        return web.json_response({"error": "Telegram session check failed."}, status=403,
                                 headers={"Access-Control-Allow-Origin": "*"})
    try:
        from database import get_exam_history
        rows = await get_exam_history(uid, 20)
        out = []
        for r in (rows or []):
            d = dict(r)
            ca = d.get("completed_at")
            out.append({"id": d.get("id"), "exam_type": d.get("exam_type"),
                        "total_score": d.get("total_score"), "scale_max": d.get("scale_max"),
                        "completed_at": (ca.isoformat() if hasattr(ca, "isoformat") else str(ca or ""))})
        return web.json_response({"history": out}, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"exam_history error: {e}")
        return web.json_response({"history": []}, headers={"Access-Control-Allow-Origin": "*"})

def _exam_cert_html(s: dict, name: str = "") -> str:
    """Self-contained, printable/shareable certificate page (no PDF dependency:
    the user can 'Save as PDF' from the browser/Telegram share sheet)."""
    import html as _html
    exam_type = "ielts" if str(s.get("exam_type")).lower() == "ielts" else "toefl"
    is_ielts = exam_type == "ielts"
    total = s.get("total_score") or 0
    scale = s.get("scale_max") or (9 if is_ielts else 120)
    total_str = (f"{float(total):.1f}" if is_ielts else str(int(round(float(total)))))
    scale_str = (f"{float(scale):.0f}" if not is_ielts else f"{float(scale):.0f}")
    title = "IELTS Academic" if is_ielts else "TOEFL iBT"
    code = _html.escape(str(s.get("cert_code") or ""))
    nm = _html.escape(name or "PolyGlotty Learner")
    ca = s.get("completed_at")
    date = (ca.isoformat()[:10] if hasattr(ca, "isoformat") else str(ca or "")[:10])
    rows = ""
    labels = {"reading": "Reading", "listening": "Listening",
              "writing": "Writing", "speaking": "Speaking"}
    for key, lbl in labels.items():
        v = s.get(f"{key}_score")
        if v is None:
            continue
        vv = (f"{float(v):.1f}" if is_ielts else str(int(round(float(v)))))
        rows += f'<div class="sec"><span>{lbl}</span><b>{vv}</b></div>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PolyGlotty Certificate · {code}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f1115;color:#e7ebf2;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
.cert{{width:100%;max-width:560px;background:linear-gradient(160deg,#171a21,#11131a);
border:1px solid #2a3140;border-radius:24px;padding:34px 30px;
box-shadow:0 24px 80px rgba(0,0,0,.5);position:relative;overflow:hidden}}
.cert:before{{content:"";position:absolute;inset:0;background:
radial-gradient(120% 60% at 50% -10%,rgba(110,168,254,.18),transparent 60%);pointer-events:none}}
.brand{{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:#6ea8fe;font-weight:700}}
.kick{{color:#9aa3b2;font-size:13px;margin-top:18px}}
.name{{font-size:26px;font-weight:800;margin:4px 0 2px}}
.exam{{color:#9aa3b2;font-size:15px;margin-bottom:22px}}
.scorebox{{text-align:center;padding:18px 0 6px}}
.score{{font-size:64px;font-weight:900;line-height:1;color:#fff}}
.score span{{font-size:24px;color:#9aa3b2;font-weight:700}}
.scorelbl{{color:#6ea8fe;font-size:13px;letter-spacing:.16em;text-transform:uppercase;margin-top:8px;font-weight:700}}
.secs{{margin:22px 0 6px;border-top:1px solid #232838;padding-top:16px}}
.sec{{display:flex;justify-content:space-between;font-size:15px;padding:7px 0;color:#cdd4e0}}
.sec b{{color:#fff}}
.foot{{display:flex;justify-content:space-between;color:#7e8798;font-size:12px;margin-top:22px;
border-top:1px solid #232838;padding-top:14px}}
.btn{{display:block;width:100%;margin-top:22px;padding:13px;border-radius:14px;border:0;
background:#6ea8fe;color:#0b0d12;font-weight:700;font-size:15px;cursor:pointer}}
@media print{{body{{background:#fff}}.btn{{display:none}}.cert{{box-shadow:none;border-color:#ccc}}}}
</style></head><body>
<div class="cert">
  <div class="brand">✦ PolyGlotty</div>
  <div class="kick">This certifies that</div>
  <div class="name">{nm}</div>
  <div class="exam">completed a full {title} simulation</div>
  <div class="scorebox">
    <div class="score">{total_str}<span>/{scale_str}</span></div>
    <div class="scorelbl">{'Overall band' if is_ielts else 'Total score'}</div>
  </div>
  <div class="secs">{rows}</div>
  <div class="foot"><span>Certificate {code}</span><span>{date}</span></div>
  <button class="btn" onclick="window.print()">Download / Print PDF</button>
</div></body></html>"""

async def handle_exam_certificate(request):
    """GET /api/exam/certificate/{code} — public shareable certificate page."""
    code = str(request.match_info.get("code", "")).strip()[:32]
    if not code:
        return web.Response(text="Not found", status=404)
    try:
        from database import get_exam_by_cert
        s = await get_exam_by_cert(code)
        if not s:
            return web.Response(text="Certificate not found", status=404,
                                content_type="text/plain")
        s = dict(s)
        name = ""
        try:
            from database import get_user
            u = await get_user(int(s.get("uid") or 0))
            if u:
                name = str(dict(u).get("first_name") or dict(u).get("name") or "")
        except Exception:
            pass
        return web.Response(text=_exam_cert_html(s, name), content_type="text/html",
                            headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"exam_certificate error: {e}")
        return web.Response(text="Error", status=500, content_type="text/plain")

# ── SYNC STATS ────────────────────────────────────────────────────────────────
async def handle_sync_stats(request):
    """Sync local stats from webapp to server."""
    try:
        body = await request.json()
        uid = int(body.get("uid",0))
        if not uid:
            return web.json_response({"ok":True},headers={"Access-Control-Allow-Origin":"*"})
    except Exception:
        return web.json_response({"ok":True},headers={"Access-Control-Allow-Origin":"*"})
    try:
        from database import db, upsert_user
        # Ensure user exists
        try:
            await upsert_user(uid, "Student")
        except Exception:
            pass
        xp = int(body.get("xp",0))
        sessions = int(body.get("sessions",0))
        words = int(body.get("words",0))
        tests = int(body.get("tests",0))
        errors = int(body.get("errors",0))
        level = body.get("level","B1")
        # Update with GREATEST to never decrease. NOTE: streak and last_active
        # are deliberately NOT written here — they are owned exclusively by the
        # server-authoritative /api/complete_day endpoint. Letting the client
        # push a streak (or stamp last_active=today on every sync) is exactly
        # what allowed the reminder/clock streak-inflation exploit.
        # Detect a genuine XP increase BEFORE the GREATEST clamp so we only
        # refill the plant tonus on real progress (not on idle re-syncs).
        prior_xp = 0
        try:
            from database import get_user
            pu = await get_user(uid)
            prior_xp = int((pu or {}).get("xp") or 0)
        except Exception:
            prior_xp = 0
        try:
            await db("UPDATE users SET xp=GREATEST(COALESCE(xp,0),?), sessions=GREATEST(COALESCE(sessions,0),?), words=GREATEST(COALESCE(words,0),?), tests=GREATEST(COALESCE(tests,0),?), mistakes=GREATEST(COALESCE(mistakes,0),?), level=? WHERE uid=?", xp, sessions, words, tests, errors, level, uid)
        except Exception as e:
            logger.debug(f"sync update: {e}")
        if xp > prior_xp:
            try:
                from database import register_activity
                await register_activity(uid)
            except Exception as e:
                logger.debug(f"tonus refresh on sync: {e}")
        return web.json_response({"ok":True},headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        logger.error(f"sync_stats error: {e}")
        return web.json_response({"ok":True},headers={"Access-Control-Allow-Origin":"*"})

# ── ADMIN STATS ───────────────────────────────────────────────────────────────
async def handle_admin_stats(request):
    uid = int(request.headers.get("X-UID", "0"))
    if uid not in ADMIN_IDS:
        return web.json_response({"error": "forbidden"}, status=403, headers={"Access-Control-Allow-Origin":"*"})
    from database import db
    import datetime as _dt
    _today_date = _dt.date.today()
    result = {"total_users": "--", "today_active": "--", "premium_users": "--", "today_messages": 0}
    # Each query in its own try so a single missing column doesn't kill the whole panel.
    try:
        r = await db("SELECT COUNT(*) as c FROM users", fetch="one")
        if r is not None: result["total_users"] = r["c"]
    except Exception as e:
        logger.warning(f"admin total_users: {e}")
    try:
        r = await db("SELECT COUNT(*) as c FROM users WHERE last_active >= ?", _today_date, fetch="one")
        if r is not None: result["today_active"] = r["c"]
    except Exception as e:
        logger.warning(f"admin today_active: {e}")
    try:
        r = await db("SELECT COUNT(*) as c FROM users WHERE premium_tier != '' AND premium_tier IS NOT NULL", fetch="one")
        if r is not None: result["premium_users"] = r["c"]
    except Exception as e:
        logger.warning(f"admin premium_users: {e}")
    try:
        result["today_messages"] = sum(v for k,v in _msg_counts.items() if str(_today_date) in k)
    except Exception:
        pass

    # ── AI billing metrics (v2) ─────────────────────────────────────────────
    # Revenue/cost/margin for today + heaviest token users + budget alerts.
    billing = {}
    try:
        from billing_config import load_config, price_per_credit, owner_revenue_usd
        from database import (usage_today_cost_global, usage_today_credits_charged,
                              ledger_topups_today_credits, top_token_users_today,
                              user_cost_vs_revenue_today)
        bcfg = await load_config()
        cost_usd = await usage_today_cost_global()
        topup_credits = await ledger_topups_today_credits()
        charged_credits = await usage_today_credits_charged()
        # Real owner revenue: credits sold × blended pack rate × per-star PAYOUT
        # (after Telegram's cut) — measured against actual Anthropic spend.
        ppc = price_per_credit(bcfg, "haiku")  # credits priced at Haiku economics
        revenue_usd = round(owner_revenue_usd(bcfg, topup_credits), 4)
        margin_usd = round(revenue_usd - cost_usd, 4)
        margin_pct = round((margin_usd / revenue_usd * 100.0), 1) if revenue_usd > 0 else None
        budget = float(bcfg.get("GLOBAL_DAILY_BUDGET_USD", 25.0))
        budget_used_pct = round((cost_usd / budget * 100.0), 1) if budget > 0 else 0.0
        alerts = []
        if budget_used_pct >= 80.0:
            alerts.append({"type": "global_budget", "level": "warn" if budget_used_pct < 100 else "crit",
                           "msg": f"Global daily budget at {budget_used_pct}% (${cost_usd:.2f}/${budget:.2f})"})
        # Flag users whose Anthropic cost today exceeds their top-up revenue.
        try:
            for row in await user_cost_vs_revenue_today(20):
                rev = round(owner_revenue_usd(bcfg, row["topup_credits"]), 4)
                if row["cost_usd"] > rev and row["cost_usd"] >= 0.01:
                    alerts.append({"type": "user_unprofitable", "level": "warn", "uid": row["uid"],
                                   "msg": f"uid {row['uid']}: cost ${row['cost_usd']:.2f} > revenue ${rev:.2f}"})
        except Exception as e:
            logger.warning(f"admin user_cost_vs_revenue: {e}")
        billing = {
            "cost_usd_today": round(cost_usd, 4),
            "revenue_usd_today": revenue_usd,
            "margin_usd_today": margin_usd,
            "margin_pct_today": margin_pct,
            "credits_sold_today": topup_credits,
            "credits_charged_today": charged_credits,
            "global_budget_usd": budget,
            "global_budget_used_pct": budget_used_pct,
            "price_per_credit_usd": round(ppc, 5),
            "top_token_users": await top_token_users_today(10),
            "alerts": alerts,
            "v2_enabled": bool(bcfg.get("BILLING_V2_ENABLED")),
        }
    except Exception as e:
        logger.warning(f"admin billing metrics: {e}")
    result["billing"] = billing
    return web.json_response(result, headers={"Access-Control-Allow-Origin":"*"})

# ── CORS ──────────────────────────────────────────────────────────────────────
async def handle_options(request):
    return web.Response(headers={"Access-Control-Allow-Origin":"*","Access-Control-Allow-Methods":"GET,POST,DELETE,OPTIONS","Access-Control-Allow-Headers":"Content-Type,X-Telegram-Init-Data,X-UID"})

# ── MANDATORY CHANNEL-SUBSCRIPTION GATE ────────────────────────────────────────
async def handle_check_subscription(request):
    """Tell the Mini App whether the (verified) user is subscribed to our
    Telegram channel. Membership is read straight from Telegram via
    getChatMember — the client can't spoof it. Subscribed statuses:
    creator / administrator / member. Anything else (left/kicked/restricted)
    → subscribed:false and the WebApp shows a blocking join modal.

    Fail-open philosophy: if the gate is disabled, the channel isn't configured,
    the caller is an admin, or Telegram errors transiently, we DON'T lock the
    user out — a channel-check outage must never brick the whole product."""
    cors = {"Access-Control-Allow-Origin": "*"}
    payload = {
        "channel_url": TG_CHANNEL_URL,
        "channel_username": TG_CHANNEL_USERNAME,
    }
    # Gate switched off, or channel not configured → everybody passes.
    if not REQUIRE_CHANNEL_SUB or not (BOT_TOKEN and TG_CHANNEL_ID):
        return web.json_response({**payload, "subscribed": True, "status": "disabled"}, headers=cors)
    uid, _lang = await _guard_identity(request)
    if uid in ADMIN_IDS:
        return web.json_response({**payload, "subscribed": True, "status": "administrator"}, headers=cors)
    if not uid:
        # Unverified caller (no/invalid initData) → cannot confirm → block.
        return web.json_response({**payload, "subscribed": False, "status": "unverified"}, headers=cors)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
                params={"chat_id": TG_CHANNEL_ID, "user_id": uid},
            )
        data = r.json() if r is not None else {}
        status = ""
        if isinstance(data, dict) and data.get("ok"):
            status = str(((data.get("result") or {}).get("status")) or "")
        subscribed = status in ("creator", "administrator", "member")
        return web.json_response({**payload, "subscribed": subscribed, "status": status}, headers=cors)
    except Exception as e:
        logger.warning("check-subscription failed uid=%s: %s", uid, e)
        # Transient Telegram/network error → fail open so we never hard-lock users.
        return web.json_response({**payload, "subscribed": True, "status": "error"}, headers=cors)

# ── APP ───────────────────────────────────────────────────────────────────────
def create_app():
    app=web.Application(middlewares=[gzip_mw, cache_static_mw])
    app.router.add_get("/",handle_index)
    app.router.add_get("/sw.js",handle_sw)
    app.router.add_get("/api/user/{uid}",handle_user)
    app.router.add_get("/api/referral/{uid}",handle_referral)
    app.router.add_post("/api/chat",handle_chat)
    app.router.add_delete("/api/chat/{uid}",handle_chat_reset)
    app.router.add_post("/api/lesson",require_premium("lesson")(handle_lesson))
    app.router.add_post("/api/test",require_premium("test")(handle_test))
    app.router.add_post("/api/rate_card",handle_rate)
    app.router.add_post("/api/save_word",handle_save_word)
    app.router.add_get("/api/word_examples",handle_word_examples_get)
    app.router.add_post("/api/word_examples",handle_word_examples_save)
    app.router.add_get("/api/vocab/{uid}",handle_vocab_list)
    app.router.add_delete("/api/vocab/{uid}/{word_id}",handle_vocab_delete)
    app.router.add_get("/api/hearts/{uid}",handle_hearts_get)
    app.router.add_post("/api/hearts/lose",handle_hearts_lose)
    app.router.add_post("/api/hearts/refill",handle_hearts_refill)
    app.router.add_get("/api/lessons/limit/{uid}",handle_lessons_limit_get)
    app.router.add_post("/api/lessons/done",handle_lessons_done)
    app.router.add_get("/api/cards/limit/{uid}",handle_cards_limit_get)
    app.router.add_post("/api/cards/done",handle_cards_done)
    # Premium exam simulator (subscription-gated inside each handler).
    app.router.add_post("/api/exam/start",handle_exam_start)
    app.router.add_post("/api/exam/section",handle_exam_section)
    app.router.add_post("/api/exam/grade-section",handle_exam_grade_section)
    app.router.add_post("/api/exam/generate",handle_exam_generate)
    app.router.add_post("/api/exam/writing",handle_exam_writing)
    app.router.add_post("/api/exam/speaking",handle_exam_speaking)
    app.router.add_post("/api/exam/finish",handle_exam_finish)
    app.router.add_get("/api/exam/history/{uid}",handle_exam_history)
    app.router.add_get("/api/exam/certificate/{code}",handle_exam_certificate)
    app.router.add_post("/api/add_xp",handle_add_xp)
    app.router.add_post("/api/set_profession",handle_set_profession)
    app.router.add_post("/api/set_reminder",handle_set_reminder)
    app.router.add_post("/api/complete_day",handle_complete_day)
    app.router.add_route("OPTIONS","/api/complete_day",handle_complete_day)
    app.router.add_post("/api/audio_task",handle_audio_task)
    app.router.add_get("/api/premium/{uid}",handle_check_premium)
    app.router.add_post("/api/premium/grant",handle_grant_premium)
    app.router.add_post("/api/platform/grant",handle_grant_platform)
    app.router.add_post("/api/credits/grant",handle_grant_credits)
    app.router.add_post("/api/tts",handle_tts)
    app.router.add_post("/api/transcribe",handle_transcribe)
    app.router.add_get("/api/mistakes/{uid}",handle_mistakes_get)
    app.router.add_post("/api/mistakes",handle_mistakes_post)
    app.router.add_post("/api/sync_stats",handle_sync_stats)
    app.router.add_post("/api/check-subscription",handle_check_subscription)
    app.router.add_get("/api/check-subscription",handle_check_subscription)
    app.router.add_get("/health",handle_health)
    app.router.add_get("/api/admin/stats",handle_admin_stats)
    app.router.add_route("OPTIONS","/{tail:.*}",handle_options)
    app.router.add_static("/",WEBAPP_DIR,show_index=False)
    return app

async def start_server():
    port=int(os.getenv("PORT",8080))
    app=create_app()
    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner,"0.0.0.0",port)
    await site.start()
    logger.info(f"🌐 Server on port {port}")
    return runner

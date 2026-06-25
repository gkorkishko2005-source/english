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

if not ANT_KEY:
    logger.error("❌ ANTHROPIC_API_KEY is not set!")
else:
    logger.info(f"✅ ANTHROPIC_API_KEY loaded (starts with {ANT_KEY[:8]}...)")

_histories: dict = {}
_msg_counts: dict = {}  # daily quota points per user
_daily_ai_costs: dict = {}  # estimated daily Anthropic cost per user
_last_msg_ts: dict = {}  # last-message timestamp per uid (anti-burst throttle)

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
                from database import check_premium
                if uid and await check_premium(uid):
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
            get_profession, get_lang, upsert_user, check_premium
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
        is_prem    = await check_premium(uid)
        try:
            plant_tonus = max(0, min(100, int(user.get("plant_tonus") if (isinstance(user, dict) and user.get("plant_tonus") is not None) else 100)))
        except Exception:
            plant_tonus = 100
        weekly = [0]*7
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

    if not ANT_KEY:
        logger.error("ANTHROPIC_API_KEY is not set!")
        return web.json_response({"error": "API key not configured. Add ANTHROPIC_API_KEY to Railway variables."}, status=500, headers={"Access-Control-Allow-Origin":"*"})

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

    resp = {"reply": reply}
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
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key":ANT_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":MODEL,"max_tokens":800,"system":system,"messages":[{"role":"user","content":f"Teach me about {topic}"}]},
            )
            data=r.json()
            content=data["content"][0]["text"].strip()
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
        async with httpx.AsyncClient(timeout=45) as client:
            r=await client.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key":ANT_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":MODEL,"max_tokens":800,"system":system,"messages":[{"role":"user","content":"Generate test"}]})
            data=r.json(); content=data["content"][0]["text"].strip()
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
    system=(
        "Generate a TOEFL listening task. Return ONLY valid JSON (no markdown):\n"
        '{"topic":"...", "transcript":"3-4 sentence academic paragraph in English", '
        '"questions":[{"q":"...","options":["A","B","C","D"],"correct":0},'
        '{"q":"...","options":["A","B","C","D"],"correct":1},'
        '{"q":"...","options":["A","B","C","D"],"correct":2}]}\n'
        f'Questions in {"Russian" if ru else "English"}. Transcript always in English.'
    )
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r=await client.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key":ANT_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":MODEL,"max_tokens":800,"system":system,"messages":[{"role":"user","content":"Generate"}]})
            data=r.json(); raw=data["content"][0]["text"].strip()
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
        from database import get_premium_info, get_platform_info, get_credits, grandfather_legacy_tier
        legacy = await get_premium_info(uid)
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
        info = {
            **legacy,
            "platform_active": platform.get("active", False),
            "platform_until": platform.get("until"),
            "platform_lifetime": platform.get("lifetime", False),
            "chat_credits": credits,
            "grandfathered_tier": gf,
            "access_kind": kind,
            "hearts": hearts,
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
        if not text:
            return web.json_response({"error":"empty text"},status=400)
    except Exception:
        return web.json_response({"error":"bad request"},status=400)

    try:
        from tts import text_to_speech
        audio = await text_to_speech(text, lang=tts_lang)
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

# ── APP ───────────────────────────────────────────────────────────────────────
def create_app():
    app=web.Application(middlewares=[cache_static_mw])
    app.router.add_get("/",handle_index)
    app.router.add_get("/api/user/{uid}",handle_user)
    app.router.add_get("/api/referral/{uid}",handle_referral)
    app.router.add_post("/api/chat",handle_chat)
    app.router.add_delete("/api/chat/{uid}",handle_chat_reset)
    app.router.add_post("/api/lesson",require_premium("lesson")(handle_lesson))
    app.router.add_post("/api/test",require_premium("test")(handle_test))
    app.router.add_post("/api/rate_card",handle_rate)
    app.router.add_post("/api/save_word",handle_save_word)
    app.router.add_get("/api/vocab/{uid}",handle_vocab_list)
    app.router.add_delete("/api/vocab/{uid}/{word_id}",handle_vocab_delete)
    app.router.add_get("/api/hearts/{uid}",handle_hearts_get)
    app.router.add_post("/api/hearts/lose",handle_hearts_lose)
    app.router.add_post("/api/hearts/refill",handle_hearts_refill)
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

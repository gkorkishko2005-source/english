"""
LinguaMax - API server v3
"""
import os, json, logging, hmac, hashlib, time
from pathlib import Path
from urllib.parse import parse_qsl
from aiohttp import web
import httpx

logger      = logging.getLogger(__name__)
WEBAPP_DIR  = Path(__file__).parent / "webapp"
RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
BOT_NAME    = os.getenv("BOT_NAME", "PolyGlotty_bot")
ANT_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
REQUIRE_TG_INIT_DATA = os.getenv("REQUIRE_TG_INIT_DATA", "1") != "0"
MODEL       = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
SONNET_MODEL = os.getenv("CLAUDE_BASIC_SONNET_MODEL", "claude-sonnet-4-20250514")
SONNET_PLUS_MODEL = os.getenv("CLAUDE_PRO_SONNET_MODEL", "claude-sonnet-4-6")
OPUS_MODEL   = os.getenv("CLAUDE_OPUS_MODEL", "claude-opus-4-1-20250805")

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
        "model": SONNET_MODEL,
        "model_by_tier": {"pro": SONNET_PLUS_MODEL, "ultimate": SONNET_PLUS_MODEL},
        "weight": 5,
        "max_tokens": {"basic": 700, "pro": 1050, "ultimate": 1300},
        "input_per_m": 3.00,
        "output_per_m": 15.00,
    },
    "opus": {
        "model": OPUS_MODEL,
        "weight": 12,
        "max_tokens": {"ultimate": 1100},
        "input_per_m": 15.00,
        "output_per_m": 75.00,
    },
}

TIER_ECONOMY = {
    "free":     {"quota": 5,   "models": ("haiku",),                  "daily_budget": 0.025, "history": 18, "burst_gap": 5.0},
    "basic":    {"quota": 45,  "models": ("haiku", "sonnet"),         "daily_budget": 0.12,  "history": 35, "burst_gap": 2.0},
    "pro":      {"quota": 110, "models": ("haiku", "sonnet"),         "daily_budget": 0.35,  "history": 55, "burst_gap": 1.5},
    "ultimate": {"quota": 260, "models": ("haiku", "sonnet", "opus"), "daily_budget": 0.95,  "history": 90, "burst_gap": 1.2},
}

# ══ ADMIN / FREE PREMIUM WHITELIST ══════════════════════════════════════════
# Add your Telegram user IDs here — they get lifetime free premium
# To find your ID: message @userinfobot in Telegram
ADMIN_IDS = {
    1738695057,
    5399839500,
    725259177,
    1241890707,
    1428437531,
}
# Any username in this set also gets free premium
ADMIN_USERNAMES = {
    # "utiqo",
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
                "Free-лимит на сегодня закончился. Ты уже сделал практику — завтра будет новый запас.",
                "На сегодня бесплатные сообщения закончились. Можно отдохнуть или открыть больше практики.",
                "Хорошая сессия. Free даёт 5 сообщений в день, дальше нужен тариф с quota points.",
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
                "Free limit reached for today. You already practiced — tomorrow brings a fresh set.",
                "Your free messages are done today. Rest now or unlock more practice.",
                "Good session. Free includes 5 messages/day; more practice uses quota points.",
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

# ── STATIC ──────────────────────────────────────────────────────────────────
async def handle_index(request):
    html_path = WEBAPP_DIR / "index.html"
    if not html_path.exists():
        return web.Response(text="WebApp not found", status=404)
    html = html_path.read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-cache"})

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
                "streak": 0, "sessions": 0, "words": 0, "tests": 0, "errors": 0,
                "lang": "ru", "profession": "", "remind_time": "",
                "referrals": 0,
                "interests": [], "weekly": [0]*7, "toefl_scores": [], "due_words": [],
                "is_premium": uid in ADMIN_IDS,
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
        is_prem    = uid in ADMIN_IDS or await check_premium(uid)
        weekly = [0]*7
        return web.json_response({
            "uid": uid,
            "name": user.get("name", "Student") if isinstance(user, dict) else "Student",
            "level": level,
            "xp": xp,
            "streak": streak,
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
        }, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"handle_user error: {e}")
        # Return minimal working data instead of 500
        return web.json_response({
            "uid": uid, "name": "Student", "level": "B1", "xp": 0,
            "streak": 0, "sessions": 0, "words": 0, "tests": 0, "errors": 0,
            "lang": "ru", "profession": "", "remind_time": "",
            "referrals": 0,
            "interests": [], "weekly": [0]*7, "toefl_scores": [], "due_words": [],
            "is_premium": uid in ADMIN_IDS,
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

    try:
        from prompts import build_system
        system = build_system(level, lang, interests, profession, "correction")
    except Exception:
        system = f"You are ALEX, a friendly English tutor. The student's level is {level}."

    bl = str(body.get("bot_lang", "Respond in Russian."))

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
    system = system + fmt + "\n" + bl

    # Check premium for model selection and limits
    user_premium = False
    user_tier = ""
    is_trial = False
    if uid in ADMIN_IDS:
        user_premium = True
        user_tier = "ultimate"
    elif uid:
        try:
            from database import check_premium, get_premium_info
            info = await get_premium_info(uid)
            user_premium = info.get("is_premium", False)
            user_tier = info.get("tier", "")
        except Exception:
            pass
        # 3-day Basic trial for new users
        if not user_premium and uid:
            try:
                from database import db
                row = await db("SELECT created_at FROM users WHERE uid=?", uid, fetch="one")
                if row and row.get("created_at"):
                    import datetime
                    created = row["created_at"]
                    if hasattr(created, 'date'):
                        days_since = (datetime.datetime.now(created.tzinfo if created.tzinfo else None) - created).days
                    else:
                        days_since = 999
                    if days_since <= 3:
                        user_tier = "basic"
                        is_trial = True
                        logger.info(f"Trial active for {uid}, day {days_since+1}/3")
            except Exception as e:
                logger.debug(f"trial check: {e}")

    # Model picker (paid tiers only). Server is source of truth — client
    # cannot upgrade beyond their tier by spoofing chosen_model.
    tier_key = user_tier if user_tier in TIER_ECONOMY else "free"
    tier_cfg = TIER_ECONOMY[tier_key]
    chosen = str(body.get("chosen_model", "haiku")).lower()
    if chosen not in ("haiku", "sonnet", "opus", "auto"):
        chosen = "haiku"

    is_complex = any(kw in message.lower() for kw in [
        "correct", "grammar", "explain", "mistake", "essay", "toefl",
        "ошибк", "грамматик", "исправ", "объясни", "анализ", "разбор",
        "почему", "правило", "эссе", "тест",
    ])
    if chosen == "auto":
        model_key = "sonnet" if tier_key in ("basic", "pro", "ultimate") and is_complex else "haiku"
    else:
        model_key = chosen
    if model_key not in tier_cfg["models"]:
        model_key = "sonnet" if "sonnet" in tier_cfg["models"] and chosen == "opus" else "haiku"

    model_cfg = MODEL_ECONOMY[model_key]
    chat_model = model_cfg.get("model_by_tier", {}).get(tier_key, model_cfg["model"])
    max_tokens = model_cfg["max_tokens"].get(tier_key, 500)
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
        if used_cost >= float(tier_cfg["daily_budget"]) and model_key != "haiku":
            model_key = "haiku"
            model_cfg = MODEL_ECONOMY["haiku"]
            chat_model = model_cfg["model"]
            max_tokens = model_cfg["max_tokens"].get(tier_key, 600)
            weight = model_cfg["weight"]
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
                return web.json_response({"error": data["error"].get("message","API error")[:200]}, status=500)
            reply = data["content"][0]["text"].strip()
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
        return web.json_response({"error": str(e)[:150]}, status=500)

    _histories[uid].append({"role": "assistant", "content": reply})
    if uid:
        try:
            from database import log_session
            await log_session(uid, "webapp_chat")
        except Exception:
            pass

    return web.json_response({"reply": reply}, headers={"Access-Control-Allow-Origin": "*"})

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
            await update_word_review(uid,word_id,quality)
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
    """Returns detailed premium status for a user."""
    try:
        uid = int(request.match_info["uid"])
    except Exception:
        return web.json_response({"error":"invalid uid"},status=400)

    # Check whitelist first (free lifetime premium for admins)
    if uid in ADMIN_IDS:
        return web.json_response({
            "is_premium":True,"tier":"ultimate","until":None,"lifetime":True,"source":"admin"
        }, headers={"Access-Control-Allow-Origin":"*"})

    try:
        from database import get_premium_info
        info = await get_premium_info(uid)
        info["source"] = "database"
        return web.json_response(info, headers={"Access-Control-Allow-Origin":"*"})
    except Exception as e:
        return web.json_response({"is_premium":False,"tier":"","error":str(e)},
                                  headers={"Access-Control-Allow-Origin":"*"})

# ── GRANT PREMIUM (called by bot after successful payment) ────────────────────
async def handle_grant_premium(request):
    """Called internally by bot after Telegram Stars payment confirmed."""
    try:
        body = await request.json()
        uid = int(body.get("uid",0))
        months = int(body.get("months",1))
        tier = str(body.get("tier","pro"))
        secret = body.get("secret","")
    except Exception:
        return web.json_response({"error":"bad request"},status=400)

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
        from database import db, update_streak, upsert_user
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
        streak = int(body.get("streak",0))
        level = body.get("level","B1")
        # Update with GREATEST to never decrease
        try:
            await db("UPDATE users SET xp=GREATEST(COALESCE(xp,0),?), sessions=GREATEST(COALESCE(sessions,0),?), words=GREATEST(COALESCE(words,0),?), tests=GREATEST(COALESCE(tests,0),?), mistakes=GREATEST(COALESCE(mistakes,0),?), streak=GREATEST(COALESCE(streak,0),?), level=?, last_active=CURRENT_DATE WHERE uid=?", xp, sessions, words, tests, errors, streak, level, uid)
        except Exception as e:
            logger.debug(f"sync update: {e}")
        try:
            await update_streak(uid)
        except Exception:
            pass
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
    return web.json_response(result, headers={"Access-Control-Allow-Origin":"*"})

# ── CORS ──────────────────────────────────────────────────────────────────────
async def handle_options(request):
    return web.Response(headers={"Access-Control-Allow-Origin":"*","Access-Control-Allow-Methods":"GET,POST,DELETE,OPTIONS","Access-Control-Allow-Headers":"Content-Type,X-Telegram-Init-Data,X-UID"})

# ── APP ───────────────────────────────────────────────────────────────────────
def create_app():
    app=web.Application(middlewares=[cache_static_mw])
    app.router.add_get("/",handle_index)
    app.router.add_get("/api/user/{uid}",handle_user)
    app.router.add_post("/api/chat",handle_chat)
    app.router.add_delete("/api/chat/{uid}",handle_chat_reset)
    app.router.add_post("/api/lesson",handle_lesson)
    app.router.add_post("/api/test",handle_test)
    app.router.add_post("/api/rate_card",handle_rate)
    app.router.add_post("/api/add_xp",handle_add_xp)
    app.router.add_post("/api/set_profession",handle_set_profession)
    app.router.add_post("/api/set_reminder",handle_set_reminder)
    app.router.add_post("/api/audio_task",handle_audio_task)
    app.router.add_get("/api/premium/{uid}",handle_check_premium)
    app.router.add_post("/api/premium/grant",handle_grant_premium)
    app.router.add_post("/api/tts",handle_tts)
    app.router.add_post("/api/transcribe",handle_transcribe)
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

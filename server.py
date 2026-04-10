"""
LinguaMax · API сервер v2
Все функции работают прямо в WebApp — без открытия бота
"""

import os, json, logging
from pathlib import Path
from aiohttp import web
import httpx

logger     = logging.getLogger(__name__)
WEBAPP_DIR  = Path(__file__).parent / "webapp"
RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
BOT_NAME    = os.getenv("BOT_NAME", "PolyGlotty_bot")
ANT_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
MODEL       = "claude-haiku-4-5"

if not ANT_KEY:
    logger.error("❌ ANTHROPIC_API_KEY is not set!")
else:
    logger.info(f"✅ ANTHROPIC_API_KEY loaded (starts with {ANT_KEY[:8]}...)")

# ══════════════════════════════════════════════════════════════════
#  STATIC
# ══════════════════════════════════════════════════════════════════

async def handle_index(request: web.Request) -> web.Response:
    html_path = WEBAPP_DIR / "index.html"
    if not html_path.exists():
        return web.Response(text="WebApp not found", status=404)
    html = html_path.read_text(encoding="utf-8")
    if RAILWAY_URL:
        html = html.replace("window.API_URL||''", f"'https://{RAILWAY_URL}'")
    html = html.replace("window.BOT_NAME||'PolyGlotty_bot'", f"'{BOT_NAME}'")
    return web.Response(text=html, content_type="text/html", charset="utf-8")

# ══════════════════════════════════════════════════════════════════
#  USER DATA
# ══════════════════════════════════════════════════════════════════

async def handle_user(request: web.Request) -> web.Response:
    from database import (
        get_user, get_lang, get_level, get_xp, get_rank,
        get_streak_count, get_word_count, get_session_count,
        get_test_count, get_mistake_count, get_toefl_count,
        get_toefl_scores, get_all_interests, get_due_words,
        get_profession, db
    )
    from datetime import datetime, timedelta
    try:
        uid = int(request.match_info["uid"])
    except Exception:
        return web.json_response({"error": "invalid uid"}, status=400)

    user = await get_user(uid)
    if not user:
        return web.json_response({"error": "not found"}, status=404)

    xp       = await get_xp(uid)
    interests= await get_all_interests(uid)
    tscores  = await get_toefl_scores(uid)
    due      = await get_due_words(uid, limit=15)

    weekly = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        row = await db("SELECT COUNT(*) as c FROM sessions WHERE uid=? AND date=?", uid, day, fetch="one")
        weekly.append(row["c"] if row else 0)

    return web.json_response({
        "name":        user.get("name","Student"),
        "level":       await get_level(uid),
        "xp":          xp,
        "rank":        get_rank(xp),
        "streak":      await get_streak_count(uid),
        "sessions":    await get_session_count(uid),
        "words":       await get_word_count(uid),
        "tests":       await get_test_count(uid),
        "errors":      await get_mistake_count(uid),
        "toefl":       await get_toefl_count(uid),
        "lang":        await get_lang(uid),
        "profession":  await get_profession(uid),
        "remind_time": user.get("remind_time") or "",
        "interests":   [r["interest"] for r in interests],
        "weekly":      weekly,
        "toefl_scores":[{"section":r["section"],"score":int(r["best"]or 0),"max":6} for r in tscores] if tscores else [],
        "due_words":   [{"id":w["id"],"word":w["word"],"phonetic":"","translation":w["translation"],"example":w["example"]or""} for w in due] if due else [],
    }, headers={"Access-Control-Allow-Origin":"*"})

# ══════════════════════════════════════════════════════════════════
#  CHAT — разговор с ALEX прямо в WebApp
# ══════════════════════════════════════════════════════════════════

# История разговоров в памяти (uid → list)
_histories: dict[int, list] = {}

def _get_history(uid: int) -> list:
    return _histories.setdefault(uid, [])

def _add_msg(uid: int, role: str, text: str):
    h = _get_history(uid)
    h.append({"role": role, "content": text})
    if len(h) > 30:
        _histories[uid] = h[-30:]

async def handle_chat(request: web.Request) -> web.Response:
    try:
        body    = await request.json()
        uid     = int(body.get("uid", 0))
        message = str(body.get("message", "")).strip()
        if not message:
            return web.json_response({"error": "empty message"}, status=400)
    except Exception as e:
        return web.json_response({"error": f"bad request: {e}"}, status=400)

    # Получаем данные пользователя если uid известен
    level = "B1"; lang = "ru"; interests = ""; profession = ""
    if uid:
        try:
            from database import get_level, get_lang, get_interests, get_profession
            level      = await get_level(uid)
            lang       = await get_lang(uid)
            interests  = await get_interests(uid)
            profession = await get_profession(uid)
        except Exception as e:
            logger.warning(f"Could not get user data for {uid}: {e}")

    try:
        from prompts import build_system
        system = build_system(level, lang, interests, profession, "correction")
    except Exception as e:
        logger.error(f"build_system failed: {e}")
        system = "You are ALEX, a friendly English tutor. Help the student with English. Explain in Russian."

    # История в памяти
    h = _histories.setdefault(uid, [])
    h.append({"role": "user", "content": message})
    if len(h) > 30: _histories[uid] = h[-30:]

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANT_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": MODEL,
                    "max_tokens": 1024,
                    "system": system,
                    "messages": _histories[uid]
                },
            )
            data = r.json()
            if "error" in data:
                logger.error(f"Anthropic error: {data['error']}")
                return web.json_response(
                    {"error": data["error"].get("message", "API error")[:200]},
                    status=500
                )
            reply = data["content"][0]["text"].strip()
    except Exception as e:
        logger.error(f"Chat request failed: {e}")
        return web.json_response({"error": str(e)[:150]}, status=500)

    _histories[uid].append({"role": "assistant", "content": reply})

    if uid:
        try:
            from database import log_session
            await log_session(uid, "webapp_chat")
        except Exception: pass

    return web.json_response(
        {"reply": reply},
        headers={"Access-Control-Allow-Origin": "*"}
    )

async def handle_chat_reset(request: web.Request) -> web.Response:
    try:
        uid = int(request.match_info["uid"])
        _histories.pop(uid, None)
        return web.json_response({"ok": True})
    except Exception:
        return web.json_response({"error": "bad uid"}, status=400)

# ══════════════════════════════════════════════════════════════════
#  LESSON — генерация урока по теме
# ══════════════════════════════════════════════════════════════════

async def handle_lesson(request: web.Request) -> web.Response:
    from database import get_level, get_lang, get_interests, get_profession, log_session
    from prompts import build_system, LESSON_PROMPTS
    try:
        body  = await request.json()
        uid   = int(body.get("uid", 0))
        topic = str(body.get("topic", "lesson_tenses"))
    except Exception:
        return web.json_response({"error": "bad request"}, status=400)

    level     = await get_level(uid)
    lang      = await get_lang(uid)
    interests = await get_interests(uid)
    profession= await get_profession(uid)
    system    = build_system(level, lang, interests, profession, "grammar")
    prompt    = LESSON_PROMPTS.get(topic, LESSON_PROMPTS["lesson_tenses"])

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANT_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": 1500, "system": system,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            data = r.json()
            if "error" in data:
                return web.json_response({"error": data["error"].get("message","")}, status=500)
            content = data["content"][0]["text"].strip()
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    await log_session(uid, f"webapp_{topic}")
    return web.json_response({"content": content}, headers={"Access-Control-Allow-Origin":"*"})

# ══════════════════════════════════════════════════════════════════
#  TEST — генерация теста
# ══════════════════════════════════════════════════════════════════

async def handle_test(request: web.Request) -> web.Response:
    from database import get_level, get_lang, get_interests, get_profession, log_session
    from prompts import build_system, TEST_PROMPTS
    try:
        body = await request.json()
        uid  = int(body.get("uid", 0))
        kind = str(body.get("kind", "test_grammar"))
    except Exception:
        return web.json_response({"error": "bad request"}, status=400)

    level     = await get_level(uid)
    lang      = await get_lang(uid)
    interests = await get_interests(uid)
    profession= await get_profession(uid)
    system    = build_system(level, lang, interests, profession, "test")
    prompt    = TEST_PROMPTS.get(kind, TEST_PROMPTS["test_grammar"])

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANT_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": 1500, "system": system,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            data = r.json()
            if "error" in data:
                return web.json_response({"error": data["error"].get("message","")}, status=500)
            content = data["content"][0]["text"].strip()
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    await log_session(uid, f"webapp_{kind}")
    return web.json_response({"content": content}, headers={"Access-Control-Allow-Origin":"*"})

# ══════════════════════════════════════════════════════════════════
#  FLASHCARD RATE
# ══════════════════════════════════════════════════════════════════

async def handle_rate(request: web.Request) -> web.Response:
    from database import update_word_review, add_xp
    try:
        body    = await request.json()
        word_id = int(body["word_id"])
        quality = int(body["quality"])
        uid     = int(body.get("uid", 0))
    except Exception:
        return web.json_response({"error": "bad body"}, status=400)
    await update_word_review(word_id, quality)
    if uid: await add_xp(uid, 3)
    return web.json_response({"ok": True}, headers={"Access-Control-Allow-Origin":"*"})

# ══════════════════════════════════════════════════════════════════
#  HEALTH
# ══════════════════════════════════════════════════════════════════

async def handle_health(request):
    return web.json_response({"status": "ok"})

async def handle_options(request):
    return web.Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,X-Telegram-Init-Data",
    })

# ══════════════════════════════════════════════════════════════════
#  APP
# ══════════════════════════════════════════════════════════════════

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/",                     handle_index)
    app.router.add_get("/api/user/{uid}",       handle_user)
    app.router.add_post("/api/chat",            handle_chat)
    app.router.add_delete("/api/chat/{uid}",    handle_chat_reset)
    app.router.add_post("/api/lesson",          handle_lesson)
    app.router.add_post("/api/test",            handle_test)
    app.router.add_post("/api/rate_card",       handle_rate)
    app.router.add_get("/health",               handle_health)
    app.router.add_route("OPTIONS", "/{tail:.*}", handle_options)
    return app

async def start_server():
    port   = int(os.getenv("PORT", 8080))
    app    = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", f"localhost:{port}")
    logger.info(f"🌐 WebApp: https://{domain}")
    return runner

"""
LinguaMax · Веб-сервер
Запускается рядом с ботом, отдаёт WebApp и API данные
"""

import os
import json
import logging
from pathlib import Path
from aiohttp import web

logger = logging.getLogger(__name__)

RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
WEBAPP_DIR  = Path(__file__).parent / "webapp"


async def handle_index(request: web.Request) -> web.Response:
    """Отдаёт HTML файл WebApp."""
    html_path = WEBAPP_DIR / "index.html"
    if not html_path.exists():
        return web.Response(text="WebApp not found", status=404)

    html = html_path.read_text(encoding="utf-8")

    # Подставляем Railway URL и имя бота
    bot_name = os.getenv("BOT_NAME", "PolyGlotty_bot")
    if RAILWAY_URL:
        html = html.replace(
            "window.API_URL || ''",
            f"window.API_URL || 'https://{RAILWAY_URL}'"
        )
    html = html.replace(
        "window.BOT_NAME || 'PolyGlotty_bot'",
        f"window.BOT_NAME || '{bot_name}'"
    )

    return web.Response(text=html, content_type="text/html", charset="utf-8")


async def handle_user_api(request: web.Request) -> web.Response:
    """
    GET /api/user/{uid}
    Возвращает данные пользователя для WebApp.
    """
    from database import (
        get_user, get_lang, get_level, get_xp, get_rank,
        get_streak_count, get_word_count, get_session_count,
        get_test_count, get_mistake_count, get_toefl_count,
        get_toefl_scores, get_all_interests, get_due_words, get_profession
    )

    try:
        uid = int(request.match_info["uid"])
    except (ValueError, KeyError):
        return web.json_response({"error": "invalid uid"}, status=400)

    user = await get_user(uid)
    if not user:
        return web.json_response({"error": "user not found"}, status=404)

    xp      = await get_xp(uid)
    streak  = await get_streak_count(uid)
    words   = await get_word_count(uid)
    sessions= await get_session_count(uid)
    tests   = await get_test_count(uid)
    errors  = await get_mistake_count(uid)
    toefl_c = await get_toefl_count(uid)
    interests_rows = await get_all_interests(uid)
    toefl_scores   = await get_toefl_scores(uid)
    due_words      = await get_due_words(uid, limit=10)

    # Активность за последние 7 дней
    from database import db
    from datetime import datetime, timedelta
    weekly = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        row = await db(
            "SELECT COUNT(*) as c FROM sessions WHERE uid=? AND date=?",
            uid, day, fetch="one"
        )
        weekly.append(row["c"] if row else 0)

    data = {
        "name":        user.get("name", "Student"),
        "level":       await get_level(uid),
        "xp":          xp,
        "rank":        get_rank(xp),
        "streak":      streak,
        "sessions":    sessions,
        "words":       words,
        "tests":       tests,
        "errors":      errors,
        "toefl":       toefl_c,
        "lang":        await get_lang(uid),
        "profession":  await get_profession(uid),
        "remind_time": user.get("remind_time") or "",
        "interests":   [r["interest"] for r in interests_rows],
        "weekly":      weekly,
        "toefl_scores": [
            {"section": r["section"], "score": int(r["best"] or 0), "max": 6}
            for r in toefl_scores
        ] if toefl_scores else [],
        "due_words": [
            {
                "id":          w["id"],
                "word":        w["word"],
                "phonetic":    "",
                "translation": w["translation"],
                "example":     w["example"] or ""
            }
            for w in due_words
        ] if due_words else [],
    }

    return web.json_response(data, headers={
        "Access-Control-Allow-Origin": "*"
    })


async def handle_rate_card(request: web.Request) -> web.Response:
    """
    POST /api/rate_card
    Обновляет SM-2 оценку слова из WebApp.
    """
    from database import update_word_review, add_xp
    try:
        body     = await request.json()
        word_id  = int(body["word_id"])
        quality  = int(body["quality"])
        uid      = int(body.get("uid", 0))
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    await update_word_review(word_id, quality)
    if uid: await add_xp(uid, 3)

    return web.json_response({"ok": True})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "LinguaMax"})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/",                    handle_index)
    app.router.add_get("/api/user/{uid}",      handle_user_api)
    app.router.add_post("/api/rate_card",      handle_rate_card)
    app.router.add_get("/health",              handle_health)
    return app


async def start_server():
    """Запускает aiohttp сервер рядом с ботом."""
    port = int(os.getenv("PORT", 8080))
    app  = create_app()

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", f"localhost:{port}")
    logger.info(f"🌐 WebApp server running: https://{domain}")

    return runner

"""
LinguaMax · API сервер v3
"""
import os, json, logging
from pathlib import Path
from aiohttp import web
import httpx

logger      = logging.getLogger(__name__)
WEBAPP_DIR  = Path(__file__).parent / "webapp"
RAILWAY_URL = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
BOT_NAME    = os.getenv("BOT_NAME", "PolyGlotty_bot")
ANT_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
MODEL       = "claude-haiku-4-5-20251001"   # ← FIXED model name

if not ANT_KEY:
    logger.error("❌ ANTHROPIC_API_KEY is not set!")
else:
    logger.info(f"✅ ANTHROPIC_API_KEY loaded (starts with {ANT_KEY[:8]}...)")

_histories: dict = {}

# ── STATIC ──────────────────────────────────────────────────────────────────
async def handle_index(request):
    html_path = WEBAPP_DIR / "index.html"
    if not html_path.exists():
        return web.Response(text="WebApp not found", status=404)
    html = html_path.read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html", charset="utf-8")

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
            get_profession, get_lang, db
        )
        user = await get_user(uid)
        if not user:
            return web.json_response({"error": "not found"}, status=404)
        xp         = await get_xp(uid)
        level      = await get_level(uid)
        streak     = await get_streak_count(uid)
        words      = await get_word_count(uid)
        sessions   = await get_session_count(uid)
        tests      = await get_test_count(uid)
        errors     = await get_mistake_count(uid)
        interests  = await get_all_interests(uid)
        due_words  = await get_due_words(uid, 10)
        profession = await get_profession(uid)
        lang_db    = await get_lang(uid)
        # Weekly XP
        from datetime import datetime, timedelta
        weekly = []
        today = datetime.now().date()
        week_start = today - timedelta(days=today.weekday())
        for i in range(7):
            d = week_start + timedelta(days=i)
            try:
                async with db.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM xp_log WHERE user_id=? AND DATE(created_at)=?",
                    (uid, str(d))
                ) as cur:
                    row = await cur.fetchone()
                    weekly.append(int(row[0]) if row else 0)
            except Exception:
                weekly.append(0)
        # TOEFL scores
        toefl_scores = []
        try:
            async with db.execute(
                "SELECT section, score, max_score FROM toefl_scores WHERE user_id=? ORDER BY created_at DESC LIMIT 4",
                (uid,)
            ) as cur:
                rows = await cur.fetchall()
                toefl_scores = [{"section": r[0], "score": r[1], "max": r[2]} for r in rows]
        except Exception:
            pass
        return web.json_response({
            "uid": uid,
            "name": user.get("name", "Student"),
            "level": level or "B1",
            "xp": xp or 0,
            "streak": streak or 0,
            "sessions": sessions or 0,
            "words": words or 0,
            "tests": tests or 0,
            "errors": errors or 0,
            "lang": lang_db or "ru",
            "profession": profession or "",
            "remind_time": user.get("remind_time", ""),
            "interests": [i["name"] for i in interests] if interests else [],
            "weekly": weekly,
            "toefl_scores": toefl_scores,
            "due_words": [{"id": w["id"], "word": w["word"], "translation": w["translation"], "phonetic": w.get("phonetic",""), "example": w.get("example","")} for w in (due_words or [])],
        }, headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        logger.error(f"handle_user error: {e}")
        return web.json_response({"error": str(e)[:200]}, status=500)

# ── CHAT ─────────────────────────────────────────────────────────────────────
async def handle_chat(request):
    try:
        body    = await request.json()
        uid     = int(body.get("uid", 0))
        message = str(body.get("message", "")).strip()
        if not message:
            return web.json_response({"error": "empty"}, status=400)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

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

    persona_prompt = str(body.get("persona_prompt", ""))
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
    if persona_prompt:
        system = persona_prompt + "\n\n" + system
    system = system + fmt + "\n" + bl

    h = _histories.setdefault(uid, [])
    h.append({"role": "user", "content": message})
    if len(h) > 20:
        _histories[uid] = h[-20:]

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANT_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODEL,
                    "max_tokens": 800,
                    "system": system,
                    "messages": _histories[uid],
                },
            )
            data = r.json()
            if "error" in data:
                logger.error(f"Anthropic error: {data['error']}")
                return web.json_response({"error": data["error"].get("message","API error")[:200]}, status=500)
            reply = data["content"][0]["text"].strip()
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
            from database import update_word_review
            await update_word_review(uid,word_id,quality)
        except Exception: pass
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
            from database import add_xp
            await add_xp(uid,min(xp,100))
        except Exception: pass
    return web.json_response({"ok":True},headers={"Access-Control-Allow-Origin":"*"})

# ── SET PROFESSION ────────────────────────────────────────────────────────────
async def handle_set_profession(request):
    try:
        body=await request.json()
        uid=int(body.get("uid",0)); profession=str(body.get("profession",""))[:100]
    except Exception:
        return web.json_response({"error":"bad"},status=400)
    if uid and profession:
        try:
            from database import set_profession
            await set_profession(uid,profession)
        except Exception: pass
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
            from database import set_reminder
            await set_reminder(uid,remind_time)
        except Exception: pass
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

# ── CORS ──────────────────────────────────────────────────────────────────────
async def handle_options(request):
    return web.Response(headers={"Access-Control-Allow-Origin":"*","Access-Control-Allow-Methods":"GET,POST,DELETE,OPTIONS","Access-Control-Allow-Headers":"Content-Type,X-Telegram-Init-Data"})

# ── APP ───────────────────────────────────────────────────────────────────────
def create_app():
    app=web.Application()
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
    app.router.add_get("/health",handle_health)
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

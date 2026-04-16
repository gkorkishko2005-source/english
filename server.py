"""
LinguaMax - API server v3
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
_msg_counts: dict = {}  # daily message counts per user

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
                row = await db("SELECT created_at FROM users WHERE uid=$1", uid, fetch="one")
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

    # Model routing by tier:
    # Free = Haiku (10 msgs/day)
    # Basic = Haiku (30 msgs/day)
    # Pro = Haiku default + Sonnet for complex tasks (60 msgs/day)
    # Ultimate = Sonnet default (80 msgs/day)
    chat_model = MODEL  # default Haiku
    max_tokens = 600
    msg_limit = 10  # free users
    if user_tier == "basic":
        chat_model = MODEL  # Haiku
        max_tokens = 800
        msg_limit = 30
    elif user_tier == "pro":
        # Pro: use Sonnet for grammar/correction, Haiku for casual chat
        is_complex = any(kw in message.lower() for kw in [
            'correct','grammar','explain','ошибк','грамматик','исправ','объясни',
            'toefl','test','тест','анализ','разбор','why','почему','правило'
        ])
        chat_model = "claude-sonnet-4-6" if is_complex else MODEL
        max_tokens = 1200
        msg_limit = 60
    elif user_tier == "ultimate":
        chat_model = "claude-sonnet-4-6"
        max_tokens = 1500
        msg_limit = 80

    # Check daily message limit (skip for admins)
    if uid not in ADMIN_IDS:
        today_key = f"msgs:{uid}:{__import__('datetime').date.today()}"
        msg_count = _msg_counts.get(today_key, 0)
        if msg_count >= msg_limit:
            import random
            if not user_premium:
                grace_ru = [
                    "Ты сегодня отлично позанимался! 🎉 Хочешь больше? Premium = 30 сообщений/день",
                    "Лимит на сегодня исчерпан, но ты молодец! 💪 Premium откроет ещё больше",
                    "10 сообщений пролетели! Завтра будет ещё 10, или бери Premium 🚀",
                    "ALEX устал бесплатно 😅 Шутка! Приходи завтра или оформи Premium",
                    "Сегодня ты уже выполнил норму! 📚 Premium = больше обучения",
                    "Каждый день по 10 сообщений — уже прогресс! 🔥 Premium = в 3 раза больше",
                    "ALEX скучает когда ты уходишь 😢 С Premium он будет рядом дольше!",
                    "Знаешь, что 30 минут в день = B2 за 6 месяцев? Premium поможет 📈",
                ]
                grace_en = [
                    "Great work today! 🎉 Want more? Premium = 30 messages/day",
                    "Daily limit reached, but you did amazing! 💪 Premium unlocks more",
                    "10 messages flew by! Come back tomorrow or get Premium 🚀",
                    "ALEX needs rest 😅 Just kidding! Come back tomorrow or go Premium",
                    "You've hit today's limit! 📚 Premium = more learning",
                    "10 messages a day = real progress! 🔥 Premium = 3x more",
                    "ALEX misses you when you leave 😢 Premium keeps him longer!",
                    "Did you know? 30 min/day = B2 in 6 months! Premium helps 📈",
                ]
                limit_msg = random.choice(grace_ru if lang=="ru" else grace_en)
            else:
                grace_prem_ru = [
                    "Ты сегодня выжал максимум! Отдохни и приходи завтра 💪",
                    "Лимит на сегодня — но какой продуктивный день! 🌟 До завтра!",
                    "ALEX тоже нужен сон 😴 Продолжим завтра!",
                    "Мозгу нужен отдых чтобы запомнить всё новое 🧠 До завтра!",
                    "Ты среди самых активных учеников! 🏆 Увидимся завтра!",
                ]
                grace_prem_en = [
                    "You maxed out today! Rest up, see you tomorrow 💪",
                    "Today's limit reached — what a productive day! 🌟",
                    "Even ALEX needs sleep 😴 See you tomorrow!",
                    "Your brain needs rest to absorb everything 🧠 See you tomorrow!",
                    "You're among the most active students! 🏆 See you tomorrow!",
                ]
                limit_msg = random.choice(grace_prem_ru if lang=="ru" else grace_prem_en)
            return web.json_response({"reply": limit_msg}, headers={"Access-Control-Allow-Origin":"*"})
        _msg_counts[today_key] = msg_count + 1

    h = _histories.setdefault(uid, [])
    h.append({"role": "user", "content": message})
    history_limit = 20 if not user_premium else (30 if user_tier=="basic" else 50 if user_tier=="pro" else 80)
    if len(h) > history_limit:
        _histories[uid] = h[-history_limit:]

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
                    "model": chat_model,
                    "max_tokens": max_tokens,
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
        level = body.get("level","B1")
        # Update with GREATEST to never decrease
        try:
            await db("UPDATE users SET xp=GREATEST(COALESCE(xp,0),$1), sessions=GREATEST(COALESCE(sessions,0),$2), words=GREATEST(COALESCE(words,0),$3), tests=GREATEST(COALESCE(tests,0),$4), mistakes=GREATEST(COALESCE(mistakes,0),$5), level=$6, last_active=CURRENT_DATE WHERE uid=$7", xp, sessions, words, tests, errors, level, uid)
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
    try:
        from database import db
        import datetime as _dt
        _today_date = _dt.date.today()
        total = await db("SELECT COUNT(*) as c FROM users", fetch="one")
        today = await db("SELECT COUNT(*) as c FROM users WHERE last_active >= ?", _today_date, fetch="one")
        premium = await db("SELECT COUNT(*) as c FROM users WHERE premium_tier != '' AND premium_tier IS NOT NULL", fetch="one")
        result = {
            "total_users": total["c"] if total else 0,
            "today_active": today["c"] if today else 0,
            "premium_users": premium["c"] if premium else 0,
            "today_messages": sum(v for k,v in _msg_counts.items() if str(_today_date) in k),
        }
    except Exception as e:
        logger.warning(f"admin stats error: {e}")
        result = {"total_users": "--", "today_active": "--", "premium_users": "--", "today_messages": sum(_msg_counts.values())}
    return web.json_response(result, headers={"Access-Control-Allow-Origin":"*"})

# ── CORS ──────────────────────────────────────────────────────────────────────
async def handle_options(request):
    return web.Response(headers={"Access-Control-Allow-Origin":"*","Access-Control-Allow-Methods":"GET,POST,DELETE,OPTIONS","Access-Control-Allow-Headers":"Content-Type,X-Telegram-Init-Data,X-UID"})

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
    app.router.add_get("/api/premium/{uid}",handle_check_premium)
    app.router.add_post("/api/premium/grant",handle_grant_premium)
    app.router.add_post("/api/tts",handle_tts)
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

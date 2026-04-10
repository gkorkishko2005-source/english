"""
LinguaMax · База данных v4 — PostgreSQL + SQLite fallback
"""

import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")  # Railway PostgreSQL
USE_POSTGRES  = bool(DATABASE_URL)

# ── Инициализация ─────────────────────────────────────────────────

if USE_POSTGRES:
    import asyncpg
    _pool = None

    async def get_pool():
        global _pool
        if _pool is None:
            _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        return _pool

    async def db(query: str, *params, fetch: str = "none"):
        pool = await get_pool()
        # asyncpg uses $1,$2 instead of ?
        query = _convert_query(query)
        async with pool.acquire() as conn:
            if fetch == "all":
                rows = await conn.fetch(query, *params)
                return [dict(r) for r in rows]
            elif fetch == "one":
                row = await conn.fetchrow(query, *params)
                return dict(row) if row else None
            else:
                await conn.execute(query, *params)
                return None

    def _convert_query(q: str) -> str:
        """Convert SQLite ? placeholders to PostgreSQL $1, $2..."""
        count = 0
        result = []
        for ch in q:
            if ch == "?":
                count += 1
                result.append(f"${count}")
            else:
                result.append(ch)
        return "".join(result)

else:
    # SQLite fallback для локальной разработки
    import sqlite3

    def _db_sync(query: str, params=(), fetch: str = "none"):
        con = sqlite3.connect("linguamax.db")
        con.row_factory = sqlite3.Row
        c = con.cursor()
        c.execute(query, params)
        result = None
        if fetch == "all":
            result = [dict(r) for r in c.fetchall()]
        elif fetch == "one":
            row = c.fetchone()
            result = dict(row) if row else None
        con.commit()
        con.close()
        return result

    async def db(query: str, *params, fetch: str = "none"):
        return _db_sync(query, params, fetch)


# ── DDL — создание таблиц ─────────────────────────────────────────

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS users (
    uid             BIGINT PRIMARY KEY,
    name            TEXT,
    lang            TEXT DEFAULT 'ru',
    level           TEXT DEFAULT 'B1',
    interests       TEXT DEFAULT '',
    profession      TEXT DEFAULT '',
    streak          INTEGER DEFAULT 0,
    last_active     DATE,
    xp              INTEGER DEFAULT 0,
    remind_time     TEXT,
    complex_streak  INTEGER DEFAULT 0,
    simple_streak   INTEGER DEFAULT 0,
    auto_level      BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    id         SERIAL PRIMARY KEY,
    uid        BIGINT,
    type       TEXT,
    date       DATE,
    score      INTEGER DEFAULT 0,
    total      INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vocabulary (
    id          SERIAL PRIMARY KEY,
    uid         BIGINT,
    word        TEXT,
    translation TEXT,
    example     TEXT,
    topic       TEXT DEFAULT 'general',
    next_review DATE,
    interval    INTEGER DEFAULT 1,
    ease        FLOAT DEFAULT 2.5,
    reviews     INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mistakes (
    id          SERIAL PRIMARY KEY,
    uid         BIGINT,
    original    TEXT,
    corrected   TEXT,
    explanation TEXT,
    category    TEXT DEFAULT 'grammar',
    date        DATE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS toefl_scores (
    id        SERIAL PRIMARY KEY,
    uid       BIGINT,
    section   TEXT,
    score     INTEGER,
    max_score INTEGER,
    date      DATE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS interests_log (
    id         SERIAL PRIMARY KEY,
    uid        BIGINT,
    interest   TEXT,
    source     TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS story_progress (
    id         SERIAL PRIMARY KEY,
    uid        BIGINT,
    story_type TEXT,
    chapter    INTEGER DEFAULT 1,
    hp         INTEGER DEFAULT 100,
    score      INTEGER DEFAULT 0,
    active     BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS idioms (
    id          SERIAL PRIMARY KEY,
    uid         BIGINT,
    idiom       TEXT,
    meaning     TEXT,
    example     TEXT,
    next_review DATE,
    interval    INTEGER DEFAULT 1,
    ease        FLOAT DEFAULT 2.5,
    reviews     INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tone_history (
    id         SERIAL PRIMARY KEY,
    uid        BIGINT,
    original   TEXT,
    analysis   TEXT,
    date       DATE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vocab_uid_review ON vocabulary(uid, next_review);
CREATE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(uid);
CREATE INDEX IF NOT EXISTS idx_mistakes_uid ON mistakes(uid);
"""

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    uid INTEGER PRIMARY KEY, name TEXT, lang TEXT DEFAULT 'ru',
    level TEXT DEFAULT 'B1', interests TEXT DEFAULT '', profession TEXT DEFAULT '',
    streak INTEGER DEFAULT 0, last_active TEXT, xp INTEGER DEFAULT 0,
    remind_time TEXT, complex_streak INTEGER DEFAULT 0, simple_streak INTEGER DEFAULT 0,
    auto_level INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, type TEXT,
    date TEXT, score INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS vocabulary (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, word TEXT,
    translation TEXT, example TEXT, topic TEXT DEFAULT 'general',
    next_review TEXT, interval INTEGER DEFAULT 1, ease REAL DEFAULT 2.5,
    reviews INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS mistakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, original TEXT,
    corrected TEXT, explanation TEXT, category TEXT DEFAULT 'grammar',
    date TEXT, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS toefl_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, section TEXT,
    score INTEGER, max_score INTEGER, date TEXT
);
CREATE TABLE IF NOT EXISTS interests_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, interest TEXT,
    source TEXT, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS story_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, story_type TEXT,
    chapter INTEGER DEFAULT 1, hp INTEGER DEFAULT 100, score INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS idioms (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, idiom TEXT,
    meaning TEXT, example TEXT, next_review TEXT,
    interval INTEGER DEFAULT 1, ease REAL DEFAULT 2.5, reviews INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tone_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, original TEXT,
    analysis TEXT, date TEXT, created_at TEXT DEFAULT (datetime('now'))
);
"""


async def db_init():
    schema = SCHEMA_POSTGRES if USE_POSTGRES else SCHEMA_SQLITE
    statements = [s.strip() for s in schema.split(";") if s.strip()]
    for stmt in statements:
        try:
            await db(stmt)
        except Exception as e:
            logger.warning(f"Schema statement warning: {e}")
    logger.info(f"DB initialized ({'PostgreSQL' if USE_POSTGRES else 'SQLite'})")


# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def _days_later(n: int) -> str:
    return (datetime.now() + timedelta(days=n)).strftime("%Y-%m-%d")


# ── Пользователи ──────────────────────────────────────────────────

async def get_user(uid: int) -> dict | None:
    return await db("SELECT * FROM users WHERE uid=?", uid, fetch="one")

async def get_lang(uid: int) -> str:
    u = await get_user(uid); return (u.get("lang") or "ru") if u else "ru"

async def get_level(uid: int) -> str:
    u = await get_user(uid); return (u.get("level") or "B1") if u else "B1"

async def get_interests(uid: int) -> str:
    u = await get_user(uid); return (u.get("interests") or "") if u else ""

async def get_profession(uid: int) -> str:
    u = await get_user(uid); return (u.get("profession") or "") if u else ""

async def upsert_user(uid: int, name: str):
    if USE_POSTGRES:
        await db(
            "INSERT INTO users (uid, name, last_active) VALUES (?, ?, ?) "
            "ON CONFLICT (uid) DO NOTHING",
            uid, name, _today()
        )
    else:
        await db(
            "INSERT OR IGNORE INTO users (uid, name, last_active) VALUES (?, ?, ?)",
            uid, name, _today()
        )

async def update_user(uid: int, **kwargs):
    for k, v in kwargs.items():
        await db(f"UPDATE users SET {k}=? WHERE uid=?", v, uid)

async def add_xp(uid: int, amount: int):
    await db("UPDATE users SET xp=xp+? WHERE uid=?", amount, uid)

async def get_xp(uid: int) -> int:
    u = await get_user(uid); return (u.get("xp") or 0) if u else 0

async def update_streak(uid: int):
    user = await get_user(uid)
    if not user: return
    today = _today()
    last  = str(user.get("last_active") or "")[:10]
    if last == today: return
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if last == yesterday:
        await db("UPDATE users SET streak=streak+1, last_active=?, xp=xp+10 WHERE uid=?", today, uid)
    else:
        await db("UPDATE users SET streak=1, last_active=? WHERE uid=?", today, uid)

async def get_streak_count(uid: int) -> int:
    u = await get_user(uid); return (u.get("streak") or 0) if u else 0

def get_rank(xp: int) -> str:
    if xp < 100:   return "🌱 Seedling"
    if xp < 300:   return "📗 Beginner"
    if xp < 600:   return "📘 Elementary"
    if xp < 1000:  return "📙 Pre-Intermediate"
    if xp < 1500:  return "⭐ Intermediate"
    if xp < 2500:  return "🌟 Upper-Intermediate"
    if xp < 4000:  return "💫 Advanced"
    return "🏆 Master"

LEVEL_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

async def track_complexity(uid: int, is_complex: bool) -> str | None:
    user = await get_user(uid)
    if not user or not user.get("auto_level", True): return None
    if is_complex:
        cs = (user.get("complex_streak") or 0) + 1
        await db("UPDATE users SET complex_streak=?, simple_streak=0 WHERE uid=?", cs, uid)
        if cs >= 3:
            current = await get_level(uid)
            idx = LEVEL_ORDER.index(current) if current in LEVEL_ORDER else 2
            if idx < len(LEVEL_ORDER) - 1:
                new_level = LEVEL_ORDER[idx + 1]
                await update_user(uid, level=new_level, complex_streak=0)
                return f"up:{new_level}"
    else:
        ss = (user.get("simple_streak") or 0) + 1
        await db("UPDATE users SET simple_streak=?, complex_streak=0 WHERE uid=?", ss, uid)
        if ss >= 5:
            current = await get_level(uid)
            idx = LEVEL_ORDER.index(current) if current in LEVEL_ORDER else 2
            if idx > 0:
                new_level = LEVEL_ORDER[idx - 1]
                await update_user(uid, level=new_level, simple_streak=0)
                return f"down:{new_level}"
    return None


# ── Интересы ──────────────────────────────────────────────────────

async def save_interest(uid: int, interest: str, source: str = "auto") -> bool:
    existing = await db(
        "SELECT id FROM interests_log WHERE uid=? AND LOWER(interest)=LOWER(?)",
        uid, interest, fetch="one"
    )
    if existing: return False
    await db("INSERT INTO interests_log (uid, interest, source) VALUES (?,?,?)", uid, interest, source)
    user = await get_user(uid)
    current = user.get("interests","") if user else ""
    parts = [p.strip() for p in current.split(",") if p.strip()]
    if interest.lower() not in [p.lower() for p in parts]:
        parts.append(interest)
        await update_user(uid, interests=", ".join(parts[:10]))
    return True

async def get_all_interests(uid: int):
    return await db("SELECT interest, source FROM interests_log WHERE uid=? ORDER BY created_at DESC", uid, fetch="all")


# ── Словарь SM-2 ──────────────────────────────────────────────────

async def add_word(uid: int, word: str, translation: str, example: str, topic: str = "general") -> bool:
    exists = await db("SELECT id FROM vocabulary WHERE uid=? AND LOWER(word)=LOWER(?)", uid, word, fetch="one")
    if exists: return False
    await db(
        "INSERT INTO vocabulary (uid,word,translation,example,topic,next_review) VALUES (?,?,?,?,?,?)",
        uid, word, translation, example, topic, _days_later(1)
    )
    await add_xp(uid, 5)
    return True

async def get_due_words(uid: int, limit: int = 5):
    return await db(
        "SELECT * FROM vocabulary WHERE uid=? AND next_review<=? ORDER BY next_review LIMIT ?",
        uid, _today(), limit, fetch="all"
    )

async def update_word_review(word_id: int, quality: int):
    row = await db("SELECT ease, interval FROM vocabulary WHERE id=?", word_id, fetch="one")
    if not row: return
    ease = row["ease"]; interval = row["interval"]
    if quality >= 3:
        if interval == 1:    new_interval = 6
        elif interval == 6:  new_interval = 15
        else:                new_interval = round(interval * ease)
        new_ease = max(1.3, ease + (0.1 - (5-quality)*(0.08+(5-quality)*0.02)))
    else:
        new_interval = 1; new_ease = ease
    await db(
        "UPDATE vocabulary SET interval=?, ease=?, next_review=?, reviews=reviews+1 WHERE id=?",
        new_interval, new_ease, _days_later(new_interval), word_id
    )

async def get_word_count(uid: int) -> int:
    row = await db("SELECT COUNT(*) as c FROM vocabulary WHERE uid=?", uid, fetch="one")
    return row["c"] if row else 0


# ── Идиомы SM-2 ───────────────────────────────────────────────────

async def add_idiom(uid: int, idiom: str, meaning: str, example: str) -> bool:
    exists = await db("SELECT id FROM idioms WHERE uid=? AND LOWER(idiom)=LOWER(?)", uid, idiom, fetch="one")
    if exists: return False
    await db(
        "INSERT INTO idioms (uid,idiom,meaning,example,next_review) VALUES (?,?,?,?,?)",
        uid, idiom, meaning, example, _days_later(1)
    )
    return True

async def get_due_idioms(uid: int, limit: int = 3):
    return await db(
        "SELECT * FROM idioms WHERE uid=? AND next_review<=? ORDER BY next_review LIMIT ?",
        uid, _today(), limit, fetch="all"
    )


# ── Ошибки ────────────────────────────────────────────────────────

async def log_mistake(uid: int, original: str, corrected: str, explanation: str, category: str = "grammar"):
    await db(
        "INSERT INTO mistakes (uid,original,corrected,explanation,category,date) VALUES (?,?,?,?,?,?)",
        uid, original[:300], corrected[:300], explanation[:600], category, _today()
    )

async def get_mistakes(uid: int, limit: int = 10):
    return await db("SELECT * FROM mistakes WHERE uid=? ORDER BY created_at DESC LIMIT ?", uid, limit, fetch="all")

async def get_mistake_count(uid: int) -> int:
    row = await db("SELECT COUNT(*) as c FROM mistakes WHERE uid=?", uid, fetch="one")
    return row["c"] if row else 0


# ── Сессии ────────────────────────────────────────────────────────

async def log_session(uid: int, stype: str, score: int = 0, total: int = 0):
    await db("INSERT INTO sessions (uid,type,date,score,total) VALUES (?,?,?,?,?)",
             uid, stype, _today(), score, total)
    await add_xp(uid, 15)
    await update_streak(uid)

async def get_session_count(uid: int) -> int:
    row = await db("SELECT COUNT(*) as c FROM sessions WHERE uid=?", uid, fetch="one")
    return row["c"] if row else 0

async def get_test_count(uid: int) -> int:
    row = await db("SELECT COUNT(*) as c FROM sessions WHERE uid=? AND type LIKE '%test%'", uid, fetch="one")
    return row["c"] if row else 0

async def get_toefl_count(uid: int) -> int:
    row = await db("SELECT COUNT(*) as c FROM sessions WHERE uid=? AND type LIKE '%toefl%'", uid, fetch="one")
    return row["c"] if row else 0


# ── TOEFL ─────────────────────────────────────────────────────────

async def log_toefl(uid: int, section: str, score: int, max_score: int):
    await db("INSERT INTO toefl_scores (uid,section,score,max_score,date) VALUES (?,?,?,?,?)",
             uid, section, score, max_score, _today())

async def get_toefl_scores(uid: int):
    return await db(
        "SELECT section, AVG(score) as avg_s, MAX(score) as best, COUNT(*) as cnt "
        "FROM toefl_scores WHERE uid=? GROUP BY section", uid, fetch="all"
    )


# ── Story ─────────────────────────────────────────────────────────

async def start_story(uid: int, story_type: str):
    await db("UPDATE story_progress SET active=? WHERE uid=?", False if USE_POSTGRES else 0, uid)
    await db("INSERT INTO story_progress (uid, story_type, chapter, hp, score) VALUES (?,?,1,100,0)",
             uid, story_type)

async def get_active_story(uid: int):
    val = True if USE_POSTGRES else 1
    return await db("SELECT * FROM story_progress WHERE uid=? AND active=? ORDER BY id DESC LIMIT 1",
                    uid, val, fetch="one")

async def update_story(uid: int, **kwargs):
    for k, v in kwargs.items():
        val = True if USE_POSTGRES else 1
        await db(f"UPDATE story_progress SET {k}=? WHERE uid=? AND active=?", v, uid, val)


# ── Tone history ──────────────────────────────────────────────────

async def log_tone(uid: int, original: str, analysis: str):
    await db("INSERT INTO tone_history (uid, original, analysis, date) VALUES (?,?,?,?)",
             uid, original[:500], analysis[:2000], _today())


# ── Полная статистика ─────────────────────────────────────────────

async def get_full_stats(uid: int) -> dict:
    return {
        "sessions": await get_session_count(uid),
        "tests":    await get_test_count(uid),
        "words":    await get_word_count(uid),
        "errors":   await get_mistake_count(uid),
        "toefl":    await get_toefl_count(uid),
        "streak":   await get_streak_count(uid),
        "xp":       await get_xp(uid),
        "rank":     get_rank(await get_xp(uid)),
        "level":    await get_level(uid),
    }

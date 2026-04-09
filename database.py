"""
LinguaMax · База данных v3
Новое: adaptive difficulty, interest auto-save, debate log, story progress
"""

import sqlite3
from datetime import datetime, timedelta

DB = "linguamax.db"


def db_init():
    con = sqlite3.connect(DB)
    c   = con.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        uid              INTEGER PRIMARY KEY,
        name             TEXT,
        lang             TEXT DEFAULT 'ru',
        level            TEXT DEFAULT 'B1',
        interests        TEXT DEFAULT '',
        streak           INTEGER DEFAULT 0,
        last_active      TEXT,
        xp               INTEGER DEFAULT 0,
        remind_time      TEXT,
        -- Адаптивная сложность
        complex_streak   INTEGER DEFAULT 0,
        simple_streak    INTEGER DEFAULT 0,
        auto_level       INTEGER DEFAULT 1,
        created_at       TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER,
        type       TEXT,
        date       TEXT,
        score      INTEGER DEFAULT 0,
        total      INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS vocabulary (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        uid          INTEGER,
        word         TEXT,
        translation  TEXT,
        example      TEXT,
        topic        TEXT DEFAULT 'general',
        next_review  TEXT,
        interval     INTEGER DEFAULT 1,
        ease         REAL DEFAULT 2.5,
        reviews      INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS mistakes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        uid         INTEGER,
        original    TEXT,
        corrected   TEXT,
        explanation TEXT,
        category    TEXT DEFAULT 'grammar',
        date        TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS toefl_scores (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        uid       INTEGER,
        section   TEXT,
        score     INTEGER,
        max_score INTEGER,
        date      TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS interests_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER,
        interest   TEXT,
        source     TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS story_progress (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER,
        story_type TEXT,
        chapter    INTEGER DEFAULT 1,
        hp         INTEGER DEFAULT 100,
        score      INTEGER DEFAULT 0,
        active     INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now'))
    )""")

    con.commit()
    con.close()


def db(query: str, params=(), fetch=False):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    c = con.cursor()
    c.execute(query, params)
    result = c.fetchall() if fetch else None
    con.commit()
    con.close()
    return result


# ── Пользователи ──────────────────────────────────────────────────

def get_user(uid: int):
    rows = db("SELECT * FROM users WHERE uid=?", (uid,), fetch=True)
    return dict(rows[0]) if rows else None

def get_lang(uid: int) -> str:
    u = get_user(uid); return (u.get("lang") or "ru") if u else "ru"

def get_level(uid: int) -> str:
    u = get_user(uid); return (u.get("level") or "B1") if u else "B1"

def get_interests(uid: int) -> str:
    u = get_user(uid); return (u.get("interests") or "") if u else ""

def upsert_user(uid: int, name: str):
    db("INSERT OR IGNORE INTO users (uid, name, last_active) VALUES (?,?,?)",
       (uid, name, datetime.now().strftime("%Y-%m-%d")))

def update_user(uid: int, **kwargs):
    for k, v in kwargs.items():
        db(f"UPDATE users SET {k}=? WHERE uid=?", (v, uid))

def add_xp(uid: int, amount: int):
    db("UPDATE users SET xp=xp+? WHERE uid=?", (amount, uid))

def update_streak(uid: int):
    user = get_user(uid)
    if not user: return
    today = datetime.now().strftime("%Y-%m-%d")
    last  = user.get("last_active") or ""
    if last == today: return
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if last == yesterday:
        db("UPDATE users SET streak=streak+1, last_active=?, xp=xp+10 WHERE uid=?", (today, uid))
    else:
        db("UPDATE users SET streak=1, last_active=? WHERE uid=?", (today, uid))

def get_streak_count(uid: int) -> int:
    u = get_user(uid); return (u.get("streak") or 0) if u else 0

def get_xp(uid: int) -> int:
    u = get_user(uid); return (u.get("xp") or 0) if u else 0

def get_rank(xp: int) -> str:
    if xp < 100:   return "🌱 Seedling"
    if xp < 300:   return "📗 Beginner"
    if xp < 600:   return "📘 Elementary"
    if xp < 1000:  return "📙 Pre-Intermediate"
    if xp < 1500:  return "⭐ Intermediate"
    if xp < 2500:  return "🌟 Upper-Intermediate"
    if xp < 4000:  return "💫 Advanced"
    return "🏆 Master"


# ── Адаптивная сложность ──────────────────────────────────────────

LEVEL_ORDER = ["A1","A2","B1","B2","C1","C2"]

def track_complexity(uid: int, is_complex: bool):
    """Отслеживает сложность текстов и автоматически меняет уровень."""
    user = get_user(uid)
    if not user or not user.get("auto_level", 1): return None

    if is_complex:
        new_cs = (user.get("complex_streak") or 0) + 1
        db("UPDATE users SET complex_streak=?, simple_streak=0 WHERE uid=?", (new_cs, uid))
        if new_cs >= 3:  # 3 сложных подряд → повышаем уровень
            current = get_level(uid)
            idx = LEVEL_ORDER.index(current) if current in LEVEL_ORDER else 2
            if idx < len(LEVEL_ORDER) - 1:
                new_level = LEVEL_ORDER[idx + 1]
                update_user(uid, level=new_level, complex_streak=0)
                return f"up:{new_level}"
    else:
        new_ss = (user.get("simple_streak") or 0) + 1
        db("UPDATE users SET simple_streak=?, complex_streak=0 WHERE uid=?", (new_ss, uid))
        if new_ss >= 5:  # 5 простых подряд → понижаем
            current = get_level(uid)
            idx = LEVEL_ORDER.index(current) if current in LEVEL_ORDER else 2
            if idx > 0:
                new_level = LEVEL_ORDER[idx - 1]
                update_user(uid, level=new_level, simple_streak=0)
                return f"down:{new_level}"
    return None


# ── Интересы — автосохранение ─────────────────────────────────────

def save_interest(uid: int, interest: str, source: str = "auto"):
    """Сохраняет новый интерес и обновляет строку интересов пользователя."""
    # Проверяем дубликат
    existing = db("SELECT id FROM interests_log WHERE uid=? AND LOWER(interest)=LOWER(?)",
                  (uid, interest), fetch=True)
    if existing: return False

    db("INSERT INTO interests_log (uid, interest, source) VALUES (?,?,?)", (uid, interest, source))

    # Обновляем строку интересов
    user = get_user(uid)
    current = user.get("interests", "") if user else ""
    parts = [p.strip() for p in current.split(",") if p.strip()]
    if interest.lower() not in [p.lower() for p in parts]:
        parts.append(interest)
        update_user(uid, interests=", ".join(parts[:10]))  # max 10 интересов
    return True

def get_all_interests(uid: int):
    return db("SELECT interest, source FROM interests_log WHERE uid=? ORDER BY created_at DESC",
              (uid,), fetch=True)


# ── Словарь (SM-2) ────────────────────────────────────────────────

def add_word(uid: int, word: str, translation: str, example: str, topic: str = "general"):
    exists = db("SELECT id FROM vocabulary WHERE uid=? AND LOWER(word)=LOWER(?)", (uid, word), fetch=True)
    if exists: return False
    next_review = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    db("INSERT INTO vocabulary (uid,word,translation,example,topic,next_review) VALUES (?,?,?,?,?,?)",
       (uid, word, translation, example, topic, next_review))
    add_xp(uid, 5)
    return True

def get_due_words(uid: int, limit: int = 5):
    today = datetime.now().strftime("%Y-%m-%d")
    return db("SELECT * FROM vocabulary WHERE uid=? AND next_review<=? ORDER BY next_review LIMIT ?",
              (uid, today, limit), fetch=True)

def update_word_review(word_id: int, quality: int):
    rows = db("SELECT ease, interval FROM vocabulary WHERE id=?", (word_id,), fetch=True)
    if not rows: return
    ease = rows[0]["ease"]; interval = rows[0]["interval"]
    if quality >= 3:
        if interval == 1:    new_interval = 6
        elif interval == 6:  new_interval = 15
        else:                new_interval = round(interval * ease)
        new_ease = max(1.3, ease + (0.1 - (5-quality)*(0.08+(5-quality)*0.02)))
    else:
        new_interval = 1; new_ease = ease
    next_review = (datetime.now() + timedelta(days=new_interval)).strftime("%Y-%m-%d")
    db("UPDATE vocabulary SET interval=?, ease=?, next_review=?, reviews=reviews+1 WHERE id=?",
       (new_interval, new_ease, next_review, word_id))

def get_word_count(uid: int) -> int:
    return db("SELECT COUNT(*) as c FROM vocabulary WHERE uid=?", (uid,), fetch=True)[0]["c"]


# ── Ошибки ────────────────────────────────────────────────────────

def log_mistake(uid: int, original: str, corrected: str, explanation: str, category: str = "grammar"):
    db("INSERT INTO mistakes (uid,original,corrected,explanation,category,date) VALUES (?,?,?,?,?,?)",
       (uid, original[:300], corrected[:300], explanation[:600], category,
        datetime.now().strftime("%Y-%m-%d")))

def get_mistakes(uid: int, limit: int = 10):
    return db("SELECT * FROM mistakes WHERE uid=? ORDER BY created_at DESC LIMIT ?", (uid, limit), fetch=True)

def get_mistake_count(uid: int) -> int:
    return db("SELECT COUNT(*) as c FROM mistakes WHERE uid=?", (uid,), fetch=True)[0]["c"]


# ── Сессии ────────────────────────────────────────────────────────

def log_session(uid: int, stype: str, score: int = 0, total: int = 0):
    db("INSERT INTO sessions (uid,type,date,score,total) VALUES (?,?,?,?,?)",
       (uid, stype, datetime.now().strftime("%Y-%m-%d"), score, total))
    add_xp(uid, 15)
    update_streak(uid)

def get_session_count(uid: int) -> int:
    return db("SELECT COUNT(*) as c FROM sessions WHERE uid=?", (uid,), fetch=True)[0]["c"]

def get_test_count(uid: int) -> int:
    return db("SELECT COUNT(*) as c FROM sessions WHERE uid=? AND type LIKE '%test%'", (uid,), fetch=True)[0]["c"]

def get_toefl_count(uid: int) -> int:
    return db("SELECT COUNT(*) as c FROM sessions WHERE uid=? AND type LIKE '%toefl%'", (uid,), fetch=True)[0]["c"]


# ── TOEFL ─────────────────────────────────────────────────────────

def log_toefl(uid: int, section: str, score: int, max_score: int):
    db("INSERT INTO toefl_scores (uid,section,score,max_score,date) VALUES (?,?,?,?,?)",
       (uid, section, score, max_score, datetime.now().strftime("%Y-%m-%d")))

def get_toefl_scores(uid: int):
    return db("SELECT section, AVG(score) as avg_s, MAX(score) as best, COUNT(*) as cnt "
              "FROM toefl_scores WHERE uid=? GROUP BY section", (uid,), fetch=True)


# ── Story ─────────────────────────────────────────────────────────

def start_story(uid: int, story_type: str):
    db("UPDATE story_progress SET active=0 WHERE uid=?", (uid,))
    db("INSERT INTO story_progress (uid, story_type, chapter, hp, score) VALUES (?,?,1,100,0)",
       (uid, story_type))

def get_active_story(uid: int):
    rows = db("SELECT * FROM story_progress WHERE uid=? AND active=1 ORDER BY id DESC LIMIT 1",
              (uid,), fetch=True)
    return dict(rows[0]) if rows else None

def update_story(uid: int, **kwargs):
    for k, v in kwargs.items():
        db(f"UPDATE story_progress SET {k}=? WHERE uid=? AND active=1", (v, uid))


# ── Статистика ────────────────────────────────────────────────────

def get_full_stats(uid: int) -> dict:
    return {
        "sessions": get_session_count(uid),
        "tests":    get_test_count(uid),
        "words":    get_word_count(uid),
        "errors":   get_mistake_count(uid),
        "toefl":    get_toefl_count(uid),
        "streak":   get_streak_count(uid),
        "xp":       get_xp(uid),
        "rank":     get_rank(get_xp(uid)),
        "level":    get_level(uid),
    }

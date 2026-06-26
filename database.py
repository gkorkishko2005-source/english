"""
PolyGlotty DB v4
"""
from __future__ import annotations

import os
import json
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
    plant_tonus     INTEGER DEFAULT 100,
    last_xp_date    DATE,
    xp              INTEGER DEFAULT 0,
    hearts          INTEGER DEFAULT 5,
    hearts_updated_at BIGINT DEFAULT 0,
    lessons_done_today INTEGER DEFAULT 0,
    lessons_done_date  TEXT DEFAULT '',
    cards_done_today   INTEGER DEFAULT 0,
    cards_done_date    TEXT DEFAULT '',
    remind_time     TEXT,
    complex_streak  INTEGER DEFAULT 0,
    simple_streak   INTEGER DEFAULT 0,
    auto_level      BOOLEAN DEFAULT TRUE,
    is_premium      BOOLEAN DEFAULT FALSE,
    premium_until   TIMESTAMPTZ,
    premium_tier    TEXT DEFAULT '',
    platform_until      TIMESTAMPTZ,
    platform_lifetime   BOOLEAN DEFAULT FALSE,
    chat_credits        INTEGER DEFAULT 0,
    grandfathered_tier  TEXT DEFAULT '',
    sub_exp_notified    TEXT DEFAULT '',
    ref_by          BIGINT,
    referrals       INTEGER DEFAULT 0,
    ref_premium_days INTEGER DEFAULT 0,
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
    stability   REAL DEFAULT 0,
    difficulty  REAL DEFAULT 0,
    state       TEXT DEFAULT 'new',
    lapses      INTEGER DEFAULT 0,
    last_review_dt TIMESTAMPTZ,
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

-- Premium exam simulator (full TOEFL/IELTS mock + certificate).
CREATE TABLE IF NOT EXISTS exam_sessions (
    id              SERIAL PRIMARY KEY,
    uid             BIGINT,
    exam_type       TEXT DEFAULT 'toefl',      -- 'toefl' | 'ielts'
    status          TEXT DEFAULT 'in_progress',-- in_progress | completed | abandoned
    reading_score   REAL,
    listening_score REAL,
    writing_score   REAL,
    speaking_score  REAL,
    total_score     REAL,
    scale_max       REAL,
    cert_code       TEXT,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS exam_section_results (
    id          SERIAL PRIMARY KEY,
    session_id  BIGINT,
    uid         BIGINT,
    section     TEXT,                          -- reading|listening|writing|speaking
    raw_score   REAL,
    max_score   REAL,
    payload     TEXT,                          -- JSON: answers / essay+audit / transcript+audit
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Cache of AI-generated dictionary word breakdowns (3 contextual examples).
-- Keyed by (word, lang) so repeat taps reuse the cached result instead of
-- paying for a fresh Anthropic call. `examples` is a JSON array of {en,tr}.
CREATE TABLE IF NOT EXISTS word_examples_cache (
    word       TEXT,
    lang       TEXT,
    examples   TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (word, lang)
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

CREATE TABLE IF NOT EXISTS quota_usage (
    uid        BIGINT,
    day        DATE,
    quota_used INTEGER DEFAULT 0,
    ai_cost    DOUBLE PRECISION DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (uid, day)
);

CREATE TABLE IF NOT EXISTS fsrs_review_log (
    id           SERIAL PRIMARY KEY,
    uid          BIGINT,
    word_id      BIGINT,
    rating       SMALLINT,                -- 1=Again 2=Hard 3=Good 4=Easy
    review_dt    TIMESTAMPTZ DEFAULT NOW(),
    elapsed_days REAL,
    scheduled_days REAL,
    state_before TEXT,
    duration_ms  INTEGER
);

-- ══ AI BILLING v2 (reserve→reconcile, layered on chat_credits wallet) ══
-- app_config: runtime-tunable billing config (no deploy needed). Values are
-- JSON-encoded strings keyed by the same names as billing_config defaults.
CREATE TABLE IF NOT EXISTS app_config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- billing_ledger: every money/credit movement. credits is SIGNED
-- (+topup/+allowance/+refund, -spend). meta is free-form context.
CREATE TABLE IF NOT EXISTS billing_ledger (
    id      BIGSERIAL PRIMARY KEY,
    uid     BIGINT,
    type    TEXT,                       -- topup | spend | refund | allowance
    credits INTEGER DEFAULT 0,
    meta    TEXT DEFAULT '',
    ts      TIMESTAMPTZ DEFAULT NOW()
);
-- ai_usage_log: authoritative per-request token accounting for v2 users.
CREATE TABLE IF NOT EXISTS ai_usage_log (
    id              BIGSERIAL PRIMARY KEY,
    uid             BIGINT,
    model           TEXT,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cost_usd        DOUBLE PRECISION DEFAULT 0,
    credits_charged INTEGER DEFAULT 0,
    ts              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vocab_uid_review ON vocabulary(uid, next_review);
CREATE INDEX IF NOT EXISTS idx_vocab_uid_state  ON vocabulary(uid, state);
-- Pagination of the saved-words dictionary (newest first, keyset/offset).
CREATE INDEX IF NOT EXISTS idx_vocab_uid_created ON vocabulary(uid, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(uid);
CREATE INDEX IF NOT EXISTS idx_mistakes_uid ON mistakes(uid);
CREATE INDEX IF NOT EXISTS idx_frl_uid_time ON fsrs_review_log(uid, review_dt DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_uid_ts ON billing_ledger(uid, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ausage_uid_ts ON ai_usage_log(uid, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ausage_ts ON ai_usage_log(ts);
"""

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    uid INTEGER PRIMARY KEY, name TEXT, lang TEXT DEFAULT 'ru',
    level TEXT DEFAULT 'B1', interests TEXT DEFAULT '', profession TEXT DEFAULT '',
    streak INTEGER DEFAULT 0, last_active TEXT, plant_tonus INTEGER DEFAULT 100, last_xp_date TEXT, xp INTEGER DEFAULT 0,
    hearts INTEGER DEFAULT 5, hearts_updated_at INTEGER DEFAULT 0,
    lessons_done_today INTEGER DEFAULT 0, lessons_done_date TEXT DEFAULT '',
    cards_done_today INTEGER DEFAULT 0, cards_done_date TEXT DEFAULT '',
    remind_time TEXT, complex_streak INTEGER DEFAULT 0, simple_streak INTEGER DEFAULT 0,
    auto_level INTEGER DEFAULT 1, is_premium INTEGER DEFAULT 0,
    premium_until TEXT, premium_tier TEXT DEFAULT '',
    platform_until TEXT, platform_lifetime INTEGER DEFAULT 0,
    chat_credits INTEGER DEFAULT 0, grandfathered_tier TEXT DEFAULT '',
    sub_exp_notified TEXT DEFAULT '',
    ref_by INTEGER, referrals INTEGER DEFAULT 0, ref_premium_days INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
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
    reviews INTEGER DEFAULT 0,
    stability REAL DEFAULT 0, difficulty REAL DEFAULT 0,
    state TEXT DEFAULT 'new', lapses INTEGER DEFAULT 0,
    last_review_dt TEXT, created_at TEXT DEFAULT (datetime('now'))
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
CREATE TABLE IF NOT EXISTS exam_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER,
    exam_type TEXT DEFAULT 'toefl', status TEXT DEFAULT 'in_progress',
    reading_score REAL, listening_score REAL, writing_score REAL, speaking_score REAL,
    total_score REAL, scale_max REAL, cert_code TEXT,
    started_at TEXT DEFAULT (datetime('now')), completed_at TEXT
);
CREATE TABLE IF NOT EXISTS exam_section_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, uid INTEGER,
    section TEXT, raw_score REAL, max_score REAL, payload TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS word_examples_cache (
    word TEXT, lang TEXT, examples TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (word, lang)
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
CREATE TABLE IF NOT EXISTS quota_usage (
    uid INTEGER,
    day TEXT,
    quota_used INTEGER DEFAULT 0,
    ai_cost REAL DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (uid, day)
);
CREATE TABLE IF NOT EXISTS fsrs_review_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER, word_id INTEGER,
    rating INTEGER, review_dt TEXT DEFAULT (datetime('now')),
    elapsed_days REAL, scheduled_days REAL,
    state_before TEXT, duration_ms INTEGER
);
CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS billing_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, type TEXT,
    credits INTEGER DEFAULT 0, meta TEXT DEFAULT '', ts TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ai_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, model TEXT,
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0, credits_charged INTEGER DEFAULT 0,
    ts TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_vocab_uid_created ON vocabulary(uid, created_at DESC, id DESC);
"""


async def db_init():
    schema = SCHEMA_POSTGRES if USE_POSTGRES else SCHEMA_SQLITE
    statements = [s.strip() for s in schema.split(";") if s.strip()]
    for stmt in statements:
        try:
            await db(stmt)
        except Exception as e:
            logger.warning(f"Schema statement warning: {e}")
    migrations = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_by BIGINT" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN ref_by INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referrals INTEGER DEFAULT 0" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN referrals INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_premium_days INTEGER DEFAULT 0" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN ref_premium_days INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS plant_tonus INTEGER DEFAULT 100" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN plant_tonus INTEGER DEFAULT 100",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_xp_date DATE" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN last_xp_date TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT FALSE" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_until TIMESTAMPTZ" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN premium_until TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_tier TEXT DEFAULT ''" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN premium_tier TEXT DEFAULT ''",
        # ── Modular billing (Platform sub + ALEX credits) ──────────────
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_until TIMESTAMPTZ" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN platform_until TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_lifetime BOOLEAN DEFAULT FALSE" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN platform_lifetime INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS chat_credits INTEGER DEFAULT 0" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN chat_credits INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS grandfathered_tier TEXT DEFAULT ''" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN grandfathered_tier TEXT DEFAULT ''",
        # ── AI billing v2: monthly subscription allowance (separate from credits) ──
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_allowance_left INTEGER DEFAULT 0" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN monthly_allowance_left INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS allowance_reset_at TIMESTAMPTZ" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN allowance_reset_at TEXT",
        # ── FSRS spaced-repetition fields (extends existing SM-2 vocab) ──
        # We keep the legacy `interval / ease / reviews` columns intact so
        # nothing breaks during the rollout; FSRS reads its own columns.
        "ALTER TABLE vocabulary ADD COLUMN IF NOT EXISTS stability REAL DEFAULT 0"  if USE_POSTGRES else "ALTER TABLE vocabulary ADD COLUMN stability REAL DEFAULT 0",
        "ALTER TABLE vocabulary ADD COLUMN IF NOT EXISTS difficulty REAL DEFAULT 0" if USE_POSTGRES else "ALTER TABLE vocabulary ADD COLUMN difficulty REAL DEFAULT 0",
        "ALTER TABLE vocabulary ADD COLUMN IF NOT EXISTS state TEXT DEFAULT 'new'"  if USE_POSTGRES else "ALTER TABLE vocabulary ADD COLUMN state TEXT DEFAULT 'new'",
        "ALTER TABLE vocabulary ADD COLUMN IF NOT EXISTS lapses INTEGER DEFAULT 0"  if USE_POSTGRES else "ALTER TABLE vocabulary ADD COLUMN lapses INTEGER DEFAULT 0",
        "ALTER TABLE vocabulary ADD COLUMN IF NOT EXISTS last_review_dt TIMESTAMPTZ" if USE_POSTGRES else "ALTER TABLE vocabulary ADD COLUMN last_review_dt TEXT",
        # ── Subscription-expiry reminders: marker of the last expiry push
        # sent ("<until-date>:<days-left>"), so we never re-send the same
        # 3/2/1-day notice and reset automatically when the sub is extended.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS sub_exp_notified TEXT DEFAULT ''" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN sub_exp_notified TEXT DEFAULT ''",
        # ── Hearts (free-tier lesson lives): pool + server-clock regen anchor
        # (epoch seconds). Premium users are never decremented; the anchor is
        # the timestamp from which the next +1 heart is counted.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS hearts INTEGER DEFAULT 5" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN hearts INTEGER DEFAULT 5",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS hearts_updated_at BIGINT DEFAULT 0" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN hearts_updated_at INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS lessons_done_today INTEGER DEFAULT 0" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN lessons_done_today INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS lessons_done_date TEXT DEFAULT ''" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN lessons_done_date TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS cards_done_today INTEGER DEFAULT 0" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN cards_done_today INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS cards_done_date TEXT DEFAULT ''" if USE_POSTGRES else "ALTER TABLE users ADD COLUMN cards_done_date TEXT DEFAULT ''",
    ]
    for stmt in migrations:
        try:
            await db(stmt)
        except Exception as e:
            logger.debug(f"Schema migration skipped: {e}")
    logger.info(f"DB initialized ({'PostgreSQL' if USE_POSTGRES else 'SQLite'})")


# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

def _today():
    if USE_POSTGRES:
        return datetime.now().date()  # asyncpg needs date object
    return datetime.now().strftime("%Y-%m-%d")

def _days_later(n: int):
    if USE_POSTGRES:
        return (datetime.now() + timedelta(days=n)).date()
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

# Credits granted to the inviter per VALID new referral (0.5× a message price).
# Env-overridable so the owner can retune without a deploy; mirrors
# billing_config REFERRAL_REWARD_CREDITS.
REFERRAL_REWARD_CREDITS = int(os.getenv("BILLING_REFERRAL_REWARD_CREDITS", os.getenv("REFERRAL_REWARD_CREDITS", "3")) or "3")
# Premium granted to the inviter per VALID new referral. The reward is now real
# subscription time (Platform) instead of credits — but capped lifetime so free
# value can't snowball: once REFERRAL_PREMIUM_CAP_DAYS of referral-granted Premium
# have been handed out, further referrals fall back to REFERRAL_REWARD_CREDITS
# ALEX credits only (no more free subscription days). Env-overridable.
REFERRAL_PREMIUM_DAYS = int(os.getenv("BILLING_REFERRAL_PREMIUM_DAYS", os.getenv("REFERRAL_PREMIUM_DAYS", "3")) or "3")
REFERRAL_PREMIUM_CAP_DAYS = int(os.getenv("BILLING_REFERRAL_PREMIUM_CAP_DAYS", os.getenv("REFERRAL_PREMIUM_CAP_DAYS", "14")) or "14")


async def apply_referral(new_uid: int, ref_uid: int) -> dict:
    """Attach a valid first referral once and reward the inviter.

    Anti-abuse: a Telegram ID can be referred at most once (ref_by is set under a
    guarded UPDATE and never overwritten), self-referral is rejected, and the
    caller (cmd_start) only invokes this for brand-new users so existing accounts
    can't be farmed.

    Reward (Unit 3 — limit free value):
      * The inviter gets REFERRAL_PREMIUM_DAYS days of Platform access, but only
        up to a lifetime cap of REFERRAL_PREMIUM_CAP_DAYS days (tracked in
        users.ref_premium_days). Beyond the cap the inviter instead receives
        REFERRAL_REWARD_CREDITS ALEX credits — never more free subscription days.
      * XP bonuses are unchanged (+150 inviter, +50 new user).

    Returns a result dict for the caller's user-facing message:
      {"ok": bool, "premium_days": int, "credits": int, "capped": bool}
    """
    fail = {"ok": False, "premium_days": 0, "credits": 0, "capped": False}
    if not new_uid or not ref_uid or new_uid == ref_uid:
        return fail
    await upsert_user(ref_uid, "Student")
    await upsert_user(new_uid, "Student")
    user = await get_user(new_uid)
    if not user or user.get("ref_by"):
        return fail
    # Guarded write: only attaches when ref_by is still empty. If a concurrent
    # call won the race, rowcount semantics differ per backend, so we re-read to
    # confirm WE are the one that attached this inviter before paying out.
    await db("UPDATE users SET ref_by=? WHERE uid=? AND (ref_by IS NULL OR ref_by=0)", ref_uid, new_uid)
    confirm = await get_user(new_uid)
    if not confirm or int(confirm.get("ref_by") or 0) != int(ref_uid):
        return fail
    await db("UPDATE users SET referrals=COALESCE(referrals,0)+1, xp=COALESCE(xp,0)+150 WHERE uid=?", ref_uid)
    await db("UPDATE users SET xp=COALESCE(xp,0)+50 WHERE uid=?", new_uid)
    # Decide the inviter's reward: Premium days while under the lifetime cap,
    # otherwise ALEX credits. The cap counter (ref_premium_days) is the source of
    # truth so the cap holds even across many invites.
    inviter = await get_user(ref_uid) or {}
    granted_days = int(inviter.get("ref_premium_days") or 0)
    cap = max(0, REFERRAL_PREMIUM_CAP_DAYS)
    want = max(0, REFERRAL_PREMIUM_DAYS)
    days_to_grant = max(0, min(want, cap - granted_days)) if cap > 0 else want
    result = {"ok": True, "premium_days": 0, "credits": 0, "capped": False}
    if days_to_grant > 0:
        try:
            await grant_platform_days(ref_uid, days_to_grant)
            await db("UPDATE users SET ref_premium_days=COALESCE(ref_premium_days,0)+? WHERE uid=?",
                     days_to_grant, ref_uid)
            await ledger_add(ref_uid, "referral", 0,
                             meta=f"ref new_uid={new_uid} premium_days={days_to_grant}")
            result["premium_days"] = days_to_grant
        except Exception as e:
            logger.warning("referral premium reward failed inviter=%s new=%s: %s", ref_uid, new_uid, e)
    else:
        # Cap reached — fall back to credits so the invite is still rewarded.
        result["capped"] = True
        reward = REFERRAL_REWARD_CREDITS
        if reward > 0:
            try:
                await add_credits(ref_uid, reward)
                await ledger_add(ref_uid, "referral", reward,
                                 meta=f"ref new_uid={new_uid} (cap reached, credits)")
                result["credits"] = reward
            except Exception as e:
                logger.warning("referral credit reward failed inviter=%s new=%s: %s", ref_uid, new_uid, e)
    return result

async def get_referral_count(uid: int) -> int:
    user = await get_user(uid)
    if not user:
        return 0
    return int(user.get("referrals") or 0)

async def get_quota_usage(uid: int) -> dict:
    row = await db(
        "SELECT quota_used, ai_cost FROM quota_usage WHERE uid=? AND day=?",
        uid, _today(), fetch="one"
    )
    if not row:
        return {"quota_used": 0, "ai_cost": 0.0}
    return {
        "quota_used": int(row.get("quota_used") or 0),
        "ai_cost": float(row.get("ai_cost") or 0.0),
    }

async def add_quota_usage(uid: int, points: int = 0, ai_cost: float = 0.0):
    if USE_POSTGRES:
        await db(
            "INSERT INTO quota_usage (uid, day, quota_used, ai_cost, updated_at) VALUES (?, ?, ?, ?, NOW()) "
            "ON CONFLICT (uid, day) DO UPDATE SET "
            "quota_used=quota_usage.quota_used+EXCLUDED.quota_used, "
            "ai_cost=quota_usage.ai_cost+EXCLUDED.ai_cost, updated_at=NOW()",
            uid, _today(), int(points or 0), float(ai_cost or 0.0)
        )
    else:
        await db(
            "INSERT INTO quota_usage (uid, day, quota_used, ai_cost, updated_at) VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(uid, day) DO UPDATE SET "
            "quota_used=quota_used+excluded.quota_used, "
            "ai_cost=ai_cost+excluded.ai_cost, updated_at=datetime('now')",
            uid, _today(), int(points or 0), float(ai_cost or 0.0)
        )

async def set_profession(uid: int, profession: str):
    """Save user's profession to DB (syncs across devices)."""
    await db("UPDATE users SET profession=? WHERE uid=?", profession, uid)

async def set_reminder(uid: int, remind_time: str):
    """Save user's reminder time. 'off' or empty = disabled."""
    if remind_time == "off":
        remind_time = ""
    await db("UPDATE users SET remind_time=? WHERE uid=?", remind_time, uid)

async def set_premium(uid: int, months: int = 1, tier: str = "pro"):
    """Grant premium access for given months with tier. 0 = revoke."""
    from datetime import datetime, timedelta, timezone
    tier = (tier or "basic").lower()
    if tier not in {"basic", "pro", "ultimate"}:
        logger.warning("invalid premium tier uid=%s tier=%r; falling back to basic", uid, tier)
        tier = "basic"
    await upsert_user(uid, "")
    if months <= 0:
        await db("UPDATE users SET is_premium=FALSE, premium_until=NULL, premium_tier='' WHERE uid=?", uid)
    else:
        months = min(int(months), 120)
        # Check if user already has active premium — extend from end date
        user = await get_user(uid)
        now = datetime.now(timezone.utc)
        current_until = None
        if user and user.get("premium_until"):
            try:
                raw_until = user["premium_until"]
                current_until = raw_until if isinstance(raw_until, datetime) else datetime.fromisoformat(str(raw_until))
                if current_until.tzinfo is None:
                    current_until = current_until.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.warning("premium_until parse failed uid=%s value=%r: %s", uid, user.get("premium_until"), e)
        # If current premium is still active, extend from its end
        base = current_until if (current_until and current_until > now) else now
        until = base + timedelta(days=30 * months)
        until_value = until if USE_POSTGRES else until.isoformat()
        await db("UPDATE users SET is_premium=TRUE, premium_until=?, premium_tier=? WHERE uid=?", until_value, tier, uid)

async def get_premium_info(uid: int) -> dict:
    """Returns detailed premium info for UI display."""
    from datetime import datetime, timezone
    user = await get_user(uid)
    if not user:
        return {"is_premium": False, "tier": "", "until": None, "lifetime": False}
    is_prem = bool(user.get("is_premium"))
    until_str = user.get("premium_until")
    tier = user.get("premium_tier", "") or ""
    lifetime = False
    until = None
    active = False
    if is_prem:
        if until_str:
            try:
                until = until_str if isinstance(until_str, datetime) else datetime.fromisoformat(str(until_str))
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                active = until > datetime.now(timezone.utc)
            except Exception as e:
                logger.warning("premium_until parse failed uid=%s value=%r: %s", uid, until_str, e)
                active = False
    return {
        "is_premium": active,
        "tier": tier if active else "",
        "until": until.isoformat() if until else None,
        "lifetime": lifetime,
    }

async def check_premium(uid: int) -> bool:
    """Returns True if user has active premium."""
    from datetime import datetime, timezone
    user = await get_user(uid)
    if not user: return False
    if not user.get("is_premium"): return False
    until = user.get("premium_until")
    if until is None: return False
    try:
        until_dt = until if isinstance(until, datetime) else datetime.fromisoformat(str(until))
        if until_dt.tzinfo is None:
            until_dt = until_dt.replace(tzinfo=timezone.utc)
        return until_dt > datetime.now(timezone.utc)
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════
#  MODULAR BILLING: Platform subscription + ALEX credits
# ══════════════════════════════════════════════════════════════════
#  We are splitting the legacy bundle (basic/pro/ultimate) into two
#  independent products:
#    1) "Platform" — flat subscription for course/exams/analytics/UI.
#       Periods: 1m, 6m, lifetime. Stored in platform_until + platform_lifetime.
#    2) "Chat credits" — prepaid pool spent per ALEX message, priced
#       per model (Haiku=1, Sonnet4=4, Sonnet4.6=5, Opus=12, voice +3).
#  Old Basic/Pro/Ultimate buyers are *grandfathered* into their bundle
#  via grandfathered_tier (snapshotted the first time the legacy fields
#  are seen alongside the new fields being empty).
# ══════════════════════════════════════════════════════════════════

PLATFORM_PERIOD_DAYS = {"1m": 30, "6m": 180}

async def grant_platform(uid: int, period: str = "1m"):
    """Grant Platform subscription. period ∈ {'1m','6m','lifetime'}.
    Stacks on top of existing platform_until (extends from the latest).
    'lifetime' sets platform_lifetime=TRUE permanently."""
    from datetime import datetime, timedelta, timezone
    await upsert_user(uid, "")
    if period == "lifetime":
        flag_val = True if USE_POSTGRES else 1
        await db("UPDATE users SET platform_lifetime=? WHERE uid=?", flag_val, uid)
        return
    days = PLATFORM_PERIOD_DAYS.get(period, 30)
    user = await get_user(uid) or {}
    now = datetime.now(timezone.utc)
    current_until = None
    raw = user.get("platform_until")
    if raw:
        try:
            current_until = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
            if current_until.tzinfo is None:
                current_until = current_until.replace(tzinfo=timezone.utc)
        except Exception as e:
            logger.warning("platform_until parse failed uid=%s value=%r: %s", uid, raw, e)
    base = current_until if (current_until and current_until > now) else now
    until = base + timedelta(days=days)
    until_value = until if USE_POSTGRES else until.isoformat()
    await db("UPDATE users SET platform_until=? WHERE uid=?", until_value, uid)


async def grant_platform_days(uid: int, days: int):
    """Extend Platform access by an arbitrary number of days (day-granular).

    Used by the referral reward, which grants short Premium windows (e.g. 3 days)
    rather than the fixed 1m/6m purchase periods. Stacks on top of any existing
    platform_until exactly like grant_platform — the new window starts from the
    later of (current_until, now) so days never overlap or get lost. A lifetime
    holder is left untouched (they already have everything)."""
    from datetime import datetime, timedelta, timezone
    days = int(days)
    if days <= 0:
        return
    await upsert_user(uid, "")
    user = await get_user(uid) or {}
    if bool(user.get("platform_lifetime")):
        return  # lifetime already covers it; nothing to extend
    now = datetime.now(timezone.utc)
    current_until = None
    raw = user.get("platform_until")
    if raw:
        try:
            current_until = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
            if current_until.tzinfo is None:
                current_until = current_until.replace(tzinfo=timezone.utc)
        except Exception as e:
            logger.warning("platform_until parse failed uid=%s value=%r: %s", uid, raw, e)
    base = current_until if (current_until and current_until > now) else now
    until = base + timedelta(days=days)
    until_value = until if USE_POSTGRES else until.isoformat()
    await db("UPDATE users SET platform_until=? WHERE uid=?", until_value, uid)


async def get_platform_info(uid: int) -> dict:
    """Return {active, until, lifetime} for the Platform subscription.
    Also surfaces grandfathered_tier for legacy bundle holders."""
    from datetime import datetime, timezone
    user = await get_user(uid)
    if not user:
        return {"active": False, "until": None, "lifetime": False, "grandfathered_tier": ""}
    lifetime = bool(user.get("platform_lifetime"))
    until_raw = user.get("platform_until")
    until = None
    active = lifetime
    if until_raw:
        try:
            until = until_raw if isinstance(until_raw, datetime) else datetime.fromisoformat(str(until_raw))
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until > datetime.now(timezone.utc):
                active = True
        except Exception as e:
            logger.warning("platform_until parse failed uid=%s value=%r: %s", uid, until_raw, e)
    return {
        "active": active,
        "until": until.isoformat() if until else None,
        "lifetime": lifetime,
        "grandfathered_tier": (user.get("grandfathered_tier") or "") or "",
    }


async def check_platform(uid: int) -> bool:
    """True if Platform subscription is active (lifetime OR until > now).
    Legacy bundle holders (grandfathered_tier set) ALSO pass — they paid
    for the full bundle and must keep platform access."""
    info = await get_platform_info(uid)
    if info["active"]:
        return True
    if info.get("grandfathered_tier"):
        return True
    # Also recognise still-active legacy premium that has not yet been
    # snapshotted into grandfathered_tier.
    return await check_premium(uid)


# ── ALEX credit pool ───────────────────────────────────────────────

async def get_credits(uid: int) -> int:
    user = await get_user(uid)
    if not user:
        return 0
    return int(user.get("chat_credits") or 0)


async def add_credits(uid: int, amount: int):
    """Top up ALEX credit pool. Credits never expire."""
    if amount <= 0:
        return
    await upsert_user(uid, "")
    await db("UPDATE users SET chat_credits=COALESCE(chat_credits,0)+? WHERE uid=?", int(amount), uid)


async def spend_credits(uid: int, amount: int) -> bool:
    """Atomically subtract credits. Returns True on success, False if balance is too low.
    Grandfathered bundle holders are exempt (still use the old quota system)."""
    if amount <= 0:
        return True
    user = await get_user(uid) or {}
    # Grandfathered users still ride the old quota system, not credits.
    if (user.get("grandfathered_tier") or "") and not user.get("chat_credits"):
        return True
    have = int(user.get("chat_credits") or 0)
    if have < amount:
        return False
    await db("UPDATE users SET chat_credits=chat_credits-? WHERE uid=? AND chat_credits>=?", amount, uid, amount)
    return True


# ── Grandfather snapshot ───────────────────────────────────────────

async def grandfather_legacy_tier(uid: int) -> str:
    """If the user has an active legacy premium_tier and no grandfathered_tier
    is recorded yet, snapshot it. Idempotent. Returns the resulting
    grandfathered_tier (may be '')."""
    user = await get_user(uid)
    if not user:
        return ""
    existing = (user.get("grandfathered_tier") or "")
    if existing:
        return existing
    if not user.get("is_premium"):
        return ""
    tier = (user.get("premium_tier") or "")
    if tier not in {"basic", "pro", "ultimate"}:
        return ""
    if not await check_premium(uid):
        return ""
    await db("UPDATE users SET grandfathered_tier=? WHERE uid=?", tier, uid)
    return tier


# ── Per-model credit cost (single source of truth) ─────────────────
#  Calibrated to keep ≥ 2× margin on the cheapest credit pack
#  (bulk = 2000 credits @ 1.0 ⭐/cr ≈ $0.0088 net after 30 % Telegram
#  commission) against measured Anthropic costs without prompt caching:
#     Haiku 4.5  ≈ $0.004 / message
#     Sonnet 4   ≈ $0.012
#     Sonnet 4.6 ≈ $0.012
#     Opus 4.1   ≈ $0.040
#     Opus 4.7   ≈ $0.061
#     Voice (TTS+STT) ≈ $0.021 surcharge
ALEX_CREDIT_COST = {
    "haiku":   1,
    "sonnet4": 5,
    "sonnet":  6,
    "opus41":  14,
    "opus":    18,
    "opus48":  22,
}
VOICE_CREDIT_SURCHARGE = 5


def credits_for_message(model: str, voice: bool = False) -> int:
    base = ALEX_CREDIT_COST.get((model or "").lower(), 1)
    return base + (VOICE_CREDIT_SURCHARGE if voice else 0)


# ══════════════════════════════════════════════════════════════════
#  AI BILLING v2 — atomic reserve / refund + ledger + usage + config
#  Layered ON TOP of the existing chat_credits wallet. The "wallet" a
#  user can draw on = monthly subscription allowance (used first) PLUS
#  prepaid credits (used next). Nothing here ever lets a balance go
#  negative, and every spend is a reserve that is reconciled against
#  the real token usage afterwards (refunding the unused part).
# ══════════════════════════════════════════════════════════════════

async def get_wallet(uid: int) -> dict:
    """Current spendable buckets for a user."""
    u = await get_user(uid) or {}
    return {
        "credits": int(u.get("chat_credits") or 0),
        "allowance": int(u.get("monthly_allowance_left") or 0),
        "allowance_reset_at": u.get("allowance_reset_at"),
    }


async def reserve_funds(uid: int, amount: int) -> dict | None:
    """Atomically reserve `amount` credits — drawing from the monthly allowance
    first, then the prepaid credit balance. Guarded so neither bucket can go
    negative. Returns the breakdown on success, or None if the user can't cover
    `amount` (or lost a concurrent race)."""
    if amount <= 0:
        return {"allowance_taken": 0, "credits_taken": 0, "allowance_left": None, "credits_left": None}
    u = await get_user(uid) or {}
    old_allow = int(u.get("monthly_allowance_left") or 0)
    old_cred = int(u.get("chat_credits") or 0)
    if old_allow + old_cred < amount:
        return None
    allowance_taken = min(old_allow, amount)
    credits_taken = amount - allowance_taken
    if USE_POSTGRES:
        row = await db(
            "UPDATE users SET monthly_allowance_left=monthly_allowance_left-?, "
            "chat_credits=chat_credits-? "
            "WHERE uid=? AND monthly_allowance_left>=? AND chat_credits>=? "
            "RETURNING monthly_allowance_left, chat_credits",
            allowance_taken, credits_taken, uid, allowance_taken, credits_taken,
            fetch="one",
        )
        if not row:
            return None
        new_allow = int(row.get("monthly_allowance_left") or 0)
        new_cred = int(row.get("chat_credits") or 0)
    else:
        await db(
            "UPDATE users SET monthly_allowance_left=monthly_allowance_left-?, "
            "chat_credits=chat_credits-? "
            "WHERE uid=? AND monthly_allowance_left>=? AND chat_credits>=?",
            allowance_taken, credits_taken, uid, allowance_taken, credits_taken,
        )
        u2 = await get_user(uid) or {}
        new_allow = int(u2.get("monthly_allowance_left") or 0)
        new_cred = int(u2.get("chat_credits") or 0)
        # Confirm the guarded update actually applied.
        if new_allow != old_allow - allowance_taken or new_cred != old_cred - credits_taken:
            return None
    return {
        "allowance_taken": allowance_taken, "credits_taken": credits_taken,
        "allowance_left": new_allow, "credits_left": new_cred,
    }


async def refund_funds(uid: int, to_allowance: int, to_credits: int):
    """Return unused reserved credits to the buckets they came from."""
    ta = int(max(0, to_allowance)); tc = int(max(0, to_credits))
    if ta == 0 and tc == 0:
        return
    await db(
        "UPDATE users SET monthly_allowance_left=COALESCE(monthly_allowance_left,0)+?, "
        "chat_credits=COALESCE(chat_credits,0)+? WHERE uid=?",
        ta, tc, uid,
    )


async def set_allowance(uid: int, credits: int, reset_at):
    await upsert_user(uid, "")
    await db("UPDATE users SET monthly_allowance_left=?, allowance_reset_at=? WHERE uid=?",
             int(credits), reset_at, uid)


async def ledger_add(uid: int, type_: str, credits: int, meta: str = ""):
    await db("INSERT INTO billing_ledger (uid, type, credits, meta) VALUES (?, ?, ?, ?)",
             uid, str(type_), int(credits), str(meta)[:500])


async def usage_log_add(uid: int, model: str, input_tokens: int,
                        output_tokens: int, cost_usd: float, credits_charged: int):
    await db(
        "INSERT INTO ai_usage_log (uid, model, input_tokens, output_tokens, cost_usd, credits_charged) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        uid, str(model), int(input_tokens), int(output_tokens), float(cost_usd), int(credits_charged),
    )


async def usage_today_tokens(uid: int) -> int:
    """Total tokens (in+out) this user has consumed today — for per-user limits."""
    if USE_POSTGRES:
        r = await db("SELECT COALESCE(SUM(input_tokens+output_tokens),0) AS t "
                     "FROM ai_usage_log WHERE uid=? AND ts::date=CURRENT_DATE", uid, fetch="one")
    else:
        r = await db("SELECT COALESCE(SUM(input_tokens+output_tokens),0) AS t "
                     "FROM ai_usage_log WHERE uid=? AND date(ts)=date('now')", uid, fetch="one")
    return int((r or {}).get("t") or 0)


async def usage_today_cost_global() -> float:
    """Total Anthropic spend across ALL users today — for the global budget gate."""
    if USE_POSTGRES:
        r = await db("SELECT COALESCE(SUM(cost_usd),0) AS c FROM ai_usage_log WHERE ts::date=CURRENT_DATE", fetch="one")
    else:
        r = await db("SELECT COALESCE(SUM(cost_usd),0) AS c FROM ai_usage_log WHERE date(ts)=date('now')", fetch="one")
    return float((r or {}).get("c") or 0.0)


async def ledger_topups_today_credits() -> int:
    """Total credits sold (topped up) across ALL users today — revenue proxy."""
    if USE_POSTGRES:
        r = await db("SELECT COALESCE(SUM(credits),0) AS c FROM billing_ledger "
                     "WHERE type='topup' AND ts::date=CURRENT_DATE", fetch="one")
    else:
        r = await db("SELECT COALESCE(SUM(credits),0) AS c FROM billing_ledger "
                     "WHERE type='topup' AND date(ts)=date('now')", fetch="one")
    return int((r or {}).get("c") or 0)


async def usage_today_credits_charged() -> int:
    """Total credits actually charged (reconciled) across ALL users today."""
    if USE_POSTGRES:
        r = await db("SELECT COALESCE(SUM(credits_charged),0) AS c FROM ai_usage_log "
                     "WHERE ts::date=CURRENT_DATE", fetch="one")
    else:
        r = await db("SELECT COALESCE(SUM(credits_charged),0) AS c FROM ai_usage_log "
                     "WHERE date(ts)=date('now')", fetch="one")
    return int((r or {}).get("c") or 0)


async def top_token_users_today(limit: int = 10) -> list:
    """Heaviest token consumers today (catch bots / runaway usage). Returns
    [{uid, tokens, cost_usd, msgs}] sorted by tokens desc."""
    if USE_POSTGRES:
        rows = await db(
            "SELECT uid, COALESCE(SUM(input_tokens+output_tokens),0) AS tokens, "
            "COALESCE(SUM(cost_usd),0) AS cost_usd, COUNT(*) AS msgs "
            "FROM ai_usage_log WHERE ts::date=CURRENT_DATE "
            "GROUP BY uid ORDER BY tokens DESC LIMIT ?", int(limit), fetch="all")
    else:
        rows = await db(
            "SELECT uid, COALESCE(SUM(input_tokens+output_tokens),0) AS tokens, "
            "COALESCE(SUM(cost_usd),0) AS cost_usd, COUNT(*) AS msgs "
            "FROM ai_usage_log WHERE date(ts)=date('now') "
            "GROUP BY uid ORDER BY tokens DESC LIMIT ?", int(limit), fetch="all")
    return [{"uid": int(r["uid"]), "tokens": int(r["tokens"] or 0),
             "cost_usd": float(r["cost_usd"] or 0.0), "msgs": int(r["msgs"] or 0)}
            for r in (rows or [])]


async def user_cost_vs_revenue_today(limit: int = 10) -> list:
    """Per-user today: API cost vs credits topped up (revenue proxy). Used to flag
    users whose Anthropic cost exceeds what they paid in. Returns rows where
    cost_usd > 0, sorted by cost desc."""
    if USE_POSTGRES:
        rows = await db(
            "SELECT u.uid AS uid, COALESCE(c.cost_usd,0) AS cost_usd, "
            "COALESCE(t.credits,0) AS topup_credits "
            "FROM (SELECT uid, SUM(cost_usd) AS cost_usd FROM ai_usage_log "
            "      WHERE ts::date=CURRENT_DATE GROUP BY uid) c "
            "LEFT JOIN (SELECT uid, SUM(credits) AS credits FROM billing_ledger "
            "      WHERE type='topup' AND ts::date=CURRENT_DATE GROUP BY uid) t ON t.uid=c.uid "
            "JOIN (SELECT DISTINCT uid FROM ai_usage_log WHERE ts::date=CURRENT_DATE) u ON u.uid=c.uid "
            "ORDER BY cost_usd DESC LIMIT ?", int(limit), fetch="all")
    else:
        rows = await db(
            "SELECT c.uid AS uid, COALESCE(c.cost_usd,0) AS cost_usd, "
            "COALESCE(t.credits,0) AS topup_credits "
            "FROM (SELECT uid, SUM(cost_usd) AS cost_usd FROM ai_usage_log "
            "      WHERE date(ts)=date('now') GROUP BY uid) c "
            "LEFT JOIN (SELECT uid, SUM(credits) AS credits FROM billing_ledger "
            "      WHERE type='topup' AND date(ts)=date('now') GROUP BY uid) t ON t.uid=c.uid "
            "ORDER BY c.cost_usd DESC LIMIT ?", int(limit), fetch="all")
    return [{"uid": int(r["uid"]), "cost_usd": float(r["cost_usd"] or 0.0),
             "topup_credits": int(r["topup_credits"] or 0)} for r in (rows or [])]


# ── app_config (runtime billing config, no deploy needed) ──────────
async def config_get_all() -> dict:
    rows = await db("SELECT key, value FROM app_config", fetch="all") or []
    return {r["key"]: r["value"] for r in rows}


async def config_set(key: str, value: str):
    if USE_POSTGRES:
        await db("INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, NOW()) "
                 "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                 str(key), str(value))
    else:
        await db("INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, datetime('now')) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
                 str(key), str(value))


async def add_xp(uid: int, amount: int):
    await db("UPDATE users SET xp=xp+? WHERE uid=?", amount, uid)
    # Any XP gain refills the plant tonus to 100 and stamps today's activity.
    if int(amount or 0) > 0:
        await register_activity(uid)

async def get_xp(uid: int) -> int:
    u = await get_user(uid); return (u.get("xp") or 0) if u else 0


# ── PLANT TONUS (regularity meter, server-clock only) ──────────────────────────
# Replaces the streak as the at-a-glance "are you keeping it up" signal. Stored
# as plant_tonus (0..100). Every XP-earning action refills it to 100; a daily
# UTC-00:00 cron drains 20 for each day with zero XP. ALL date math uses the
# SERVER clock (UTC) — the client never supplies a date, so the meter can't be
# farmed by spoofing the device clock/timezone.
def _utc_today():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date()


async def register_activity(uid: int):
    """Mark XP-earning activity for today (UTC) and refill tonus to 100."""
    today = _utc_today()
    store = today if USE_POSTGRES else today.isoformat()
    await db("UPDATE users SET plant_tonus=100, last_xp_date=? WHERE uid=?", store, uid)


async def get_plant_tonus(uid: int) -> int:
    u = await get_user(uid)
    if not u:
        return 100
    try:
        return max(0, min(100, int(u.get("plant_tonus") if u.get("plant_tonus") is not None else 100)))
    except Exception:
        return 100


async def decay_plant_tonus() -> int:
    """Daily UTC-00:00 job: drain 20 tonus from every user who earned NO XP
    yesterday (floor 0). Active users were already refilled to 100 at earn time,
    so only the inactive decay. Returns the number of rows touched.

    Pure server time: the boundary is yesterday's UTC date; a user whose
    last_xp_date is yesterday (or today, for a midnight edge) is spared. Anyone
    else — including never-active accounts (NULL) — loses 20."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    yest = (now - timedelta(days=1)).date()
    today = now.date()
    if USE_POSTGRES:
        y_val, t_val = yest, today
    else:
        y_val, t_val = yest.isoformat(), today.isoformat()
    # GREATEST(0, plant_tonus-20) without relying on GREATEST availability.
    await db(
        "UPDATE users SET plant_tonus = CASE WHEN COALESCE(plant_tonus,100)-20 < 0 THEN 0 "
        "ELSE COALESCE(plant_tonus,100)-20 END "
        "WHERE (last_xp_date IS NULL OR (last_xp_date <> ? AND last_xp_date <> ?))",
        y_val, t_val,
    )
    return 0

# ── HEARTS (free-tier lesson lives, server-clock regen) ───────────────────────
# Free users have a small pool of lives: a wrong answer in a lesson costs 1
# heart. Hearts regenerate on the SERVER clock (1 every HEARTS_REGEN_HOURS) or
# can be refilled instantly via Telegram Stars / an ad. Premium users have
# unlimited hearts (never decremented). The regen anchor (hearts_updated_at) is
# stored as epoch seconds, so all math is timezone-free and cannot be farmed by
# spoofing the device clock — the client never supplies a timestamp.
async def _hearts_cfg():
    """Return (max_hearts, regen_seconds). Reads the live billing config with a
    safe fallback so a config/DB hiccup never breaks the lesson flow."""
    try:
        from billing_config import load_config
        cfg = await load_config()
        mx = int(cfg.get("HEARTS_MAX", 5) or 5)
        hrs = float(cfg.get("HEARTS_REGEN_HOURS", 2) or 2)
    except Exception:
        mx, hrs = 5, 2.0
    mx = max(1, mx)
    regen = max(60, int(hrs * 3600))
    return mx, regen


def _now_epoch() -> int:
    from datetime import datetime, timezone
    return int(datetime.now(timezone.utc).timestamp())


async def _hearts_settle(uid: int):
    """Apply pending regeneration and persist. Returns a state dict
    {hearts, max, next_in, full}. Never decrements — read-only settle."""
    mx, regen = await _hearts_cfg()
    u = await get_user(uid)
    if not u:
        return {"hearts": mx, "max": mx, "next_in": 0, "full": True}
    now = _now_epoch()
    cur = u.get("hearts")
    cur = mx if cur is None else int(cur)
    cur = max(0, min(mx, cur))
    anchor = int(u.get("hearts_updated_at") or 0)
    if cur >= mx:
        # Already full — keep the anchor parked at now so a later loss starts a
        # fresh full-length timer.
        if anchor != now:
            await db("UPDATE users SET hearts_updated_at=? WHERE uid=?", now, uid)
        return {"hearts": mx, "max": mx, "next_in": 0, "full": True}
    if anchor <= 0:
        anchor = now
        await db("UPDATE users SET hearts_updated_at=? WHERE uid=?", anchor, uid)
    elapsed = max(0, now - anchor)
    gained = elapsed // regen
    if gained > 0:
        new = min(mx, cur + int(gained))
        new_anchor = now if new >= mx else anchor + int(gained) * regen
        await db("UPDATE users SET hearts=?, hearts_updated_at=? WHERE uid=?", new, new_anchor, uid)
        cur, anchor = new, new_anchor
    full = cur >= mx
    next_in = 0 if full else max(0, regen - (now - anchor))
    return {"hearts": cur, "max": mx, "next_in": int(next_in), "full": full}


async def get_hearts_state(uid: int, is_premium: bool = False) -> dict:
    """Public read of the heart pool. Premium → unlimited (capped display)."""
    if is_premium:
        mx, _ = await _hearts_cfg()
        return {"hearts": mx, "max": mx, "next_in": 0, "full": True, "unlimited": True}
    st = await _hearts_settle(uid)
    st["unlimited"] = False
    return st


async def lose_heart(uid: int, is_premium: bool = False) -> dict:
    """Deduct one heart for a lesson mistake. Premium is never decremented.
    Settles regen first, then removes one (flooring at 0). When dropping from a
    full pool, the regen timer is (re)anchored to now."""
    if is_premium:
        return await get_hearts_state(uid, True)
    mx, regen = await _hearts_cfg()
    st = await _hearts_settle(uid)
    cur = st["hearts"]
    if cur <= 0:
        st["unlimited"] = False
        return st
    was_full = cur >= mx
    new = cur - 1
    now = _now_epoch()
    if was_full:
        await db("UPDATE users SET hearts=?, hearts_updated_at=? WHERE uid=?", new, now, uid)
        anchor = now
    else:
        await db("UPDATE users SET hearts=? WHERE uid=?", new, uid)
        u = await get_user(uid)
        anchor = int((u or {}).get("hearts_updated_at") or now)
    full = new >= mx
    next_in = 0 if full else max(0, regen - (now - anchor))
    return {"hearts": new, "max": mx, "next_in": int(next_in), "full": full, "unlimited": False}


async def refill_hearts(uid: int, amount: int = 0) -> dict:
    """Add hearts: amount<=0 refills to full (Stars purchase); amount>0 adds
    that many (ad reward), capped at max. Resets the regen anchor to now when
    the pool reaches full."""
    mx, regen = await _hearts_cfg()
    await _hearts_settle(uid)
    u = await get_user(uid)
    cur = int((u or {}).get("hearts") or 0)
    new = mx if amount <= 0 else min(mx, cur + int(amount))
    now = _now_epoch()
    await db("UPDATE users SET hearts=?, hearts_updated_at=? WHERE uid=?", new, now, uid)
    full = new >= mx
    next_in = 0 if full else max(0, regen - 0)
    return {"hearts": new, "max": mx, "next_in": int(next_in), "full": full, "unlimited": False}


# ── DAILY LESSON LIMIT (free-tier economy guard) ──────────────────────────────
# A free user may successfully COMPLETE up to LESSONS_FREE_DAILY lessons per UTC
# day; the (N+1)-th start is blocked by a compact paywall in the WebApp. The day
# boundary is the SERVER clock (UTC 00:00) — never the device clock — so it can't
# be farmed. The per-day counter is settled lazily: when the stored date is not
# today, it is treated as 0 and reset on the next write. Premium = unlimited.
async def _lessons_daily_cap() -> int:
    try:
        from billing_config import load_config
        cfg = await load_config()
        return max(0, int(cfg.get("LESSONS_FREE_DAILY", 5) or 5))
    except Exception:
        return 5


def _utc_day_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _secs_to_utc_midnight() -> int:
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0, int((nxt - now).total_seconds()))


async def get_daily_lessons_state(uid: int, is_premium: bool = False) -> dict:
    """Read-only daily-lesson budget. Returns
    {used, limit, remaining, unlimited, reset_in}. Premium → unlimited."""
    cap = await _lessons_daily_cap()
    reset_in = _secs_to_utc_midnight()
    if is_premium:
        return {"used": 0, "limit": cap, "remaining": cap, "unlimited": True, "reset_in": reset_in}
    u = await get_user(uid)
    today = _utc_day_str()
    used = 0
    if u and str(u.get("lessons_done_date") or "") == today:
        used = max(0, int(u.get("lessons_done_today") or 0))
    remaining = max(0, cap - used)
    return {"used": used, "limit": cap, "remaining": remaining, "unlimited": False, "reset_in": reset_in}


async def record_lesson_done(uid: int, is_premium: bool = False) -> dict:
    """Increment the per-UTC-day completed-lesson counter for a free user and
    return the fresh state. Resets the counter when the stored date is not today.
    Premium is never metered. Idempotency for re-completing the SAME lesson is
    the caller's responsibility (the client reports only genuinely-new
    completions)."""
    cap = await _lessons_daily_cap()
    reset_in = _secs_to_utc_midnight()
    if is_premium:
        return {"used": 0, "limit": cap, "remaining": cap, "unlimited": True, "reset_in": reset_in}
    u = await get_user(uid)
    today = _utc_day_str()
    cur = 0
    if u and str(u.get("lessons_done_date") or "") == today:
        cur = max(0, int(u.get("lessons_done_today") or 0))
    new = cur + 1
    await db("UPDATE users SET lessons_done_today=?, lessons_done_date=? WHERE uid=?", new, today, uid)
    remaining = max(0, cap - new)
    return {"used": new, "limit": cap, "remaining": remaining, "unlimited": False, "reset_in": reset_in}


# ── DAILY FLASHCARD LIMIT (economy guard, BOTH tiers) ─────────────────────────
# Every card a user finishes (Skip or Save) counts toward a per-UTC-day budget.
# Free users get CARDS_FREE_DAILY (hard wall → paywall); Premium users get the
# larger CARDS_PREMIUM_DAILY (soft wall → "rest now" retention message). Unlike
# lessons, Premium is STILL metered here — it is an anti-abuse / pacing cap, not
# a paid feature. The day boundary is the SERVER clock (UTC 00:00), settled
# lazily: a stored date that is not today is treated as 0 and reset on the next
# write, so a single per-user row holds the whole day's count.
async def _cards_daily_cap(is_premium: bool = False) -> int:
    try:
        from billing_config import load_config
        cfg = await load_config()
        if is_premium:
            return max(0, int(cfg.get("CARDS_PREMIUM_DAILY", 60) or 60))
        return max(0, int(cfg.get("CARDS_FREE_DAILY", 15) or 15))
    except Exception:
        return 60 if is_premium else 15


async def get_daily_cards_state(uid: int, is_premium: bool = False) -> dict:
    """Read-only daily-flashcard budget. Returns
    {used, limit, remaining, reset_in, is_premium}. Both tiers are metered."""
    cap = await _cards_daily_cap(is_premium)
    reset_in = _secs_to_utc_midnight()
    u = await get_user(uid)
    today = _utc_day_str()
    used = 0
    if u and str(u.get("cards_done_date") or "") == today:
        used = max(0, int(u.get("cards_done_today") or 0))
    remaining = max(0, cap - used)
    return {"used": used, "limit": cap, "remaining": remaining,
            "reset_in": reset_in, "is_premium": bool(is_premium)}


async def record_card_done(uid: int, is_premium: bool = False) -> dict:
    """Increment the per-UTC-day finished-flashcard counter and return the fresh
    state. Resets the counter when the stored date is not today. Applies to both
    tiers (Premium just has a higher cap)."""
    cap = await _cards_daily_cap(is_premium)
    reset_in = _secs_to_utc_midnight()
    u = await get_user(uid)
    today = _utc_day_str()
    cur = 0
    if u and str(u.get("cards_done_date") or "") == today:
        cur = max(0, int(u.get("cards_done_today") or 0))
    new = cur + 1
    await db("UPDATE users SET cards_done_today=?, cards_done_date=? WHERE uid=?", new, today, uid)
    remaining = max(0, cap - new)
    return {"used": new, "limit": cap, "remaining": remaining,
            "reset_in": reset_in, "is_premium": bool(is_premium)}


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

async def complete_day(uid: int, tz_offset_min: int = 0) -> dict:
    """Server-authoritative daily streak.

    The "day" is derived from the SERVER clock (UTC) shifted by the user's
    timezone offset — never from the client's device clock and never from the
    reminder time. This closes the exploit where moving the reminder time (or
    the device clock/timezone) made the app count a day for the next day:
      * the boundary is decided here, on the server, from datetime.now(utc);
      * a spoofed tz offset is clamped to ±14h, so it can only shift the
        boundary by hours, never grant an extra calendar day;
      * the stored last_active date guarantees the streak is bumped AT MOST
        once per server-day (idempotent), and resets to 1 after a gap.
    Returns {"streak": int, "counted": bool}.
    """
    from datetime import datetime, timedelta, timezone
    user = await get_user(uid)
    if not user:
        return {"streak": 0, "counted": False}
    try:
        tz = max(-14 * 60, min(14 * 60, int(tz_offset_min)))
    except Exception:
        tz = 0
    now = datetime.now(timezone.utc) + timedelta(minutes=tz)
    today_d = now.date()
    today_s = today_d.isoformat()
    yest_s = (now - timedelta(days=1)).date().isoformat()
    last = str(user.get("last_active") or "")[:10]
    cur = int(user.get("streak") or 0)
    # Idempotent guard: already counted today → no change, no inflation.
    if last == today_s:
        return {"streak": cur, "counted": False}
    new = cur + 1 if last == yest_s else 1
    store = today_d if USE_POSTGRES else today_s
    await db("UPDATE users SET streak=?, last_active=? WHERE uid=?", new, store, uid)
    return {"streak": new, "counted": True}

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


# ── Capped dictionary (anti-hoarding queue cap + pagination) ───────
async def add_word_capped(uid: int, word: str, translation: str, example: str,
                          topic: str, max_words: int) -> str:
    """Insert one saved word, enforcing a hard ceiling on the active review
    queue. Returns one of:
        'ok'   — inserted (XP granted)
        'dup'  — already in the dictionary (no-op, not an error)
        'full' — queue at/over max_words; caller should surface the limit toast
    The cap is checked just before insert. This is a learning queue, not money,
    so a check-then-insert (not a hard atomic guard) is acceptable; the worst
    case under a race is one extra word, which the next save will reject."""
    word = (word or "").strip()
    if not word:
        return "dup"
    exists = await db("SELECT id FROM vocabulary WHERE uid=? AND LOWER(word)=LOWER(?)",
                      uid, word, fetch="one")
    if exists:
        return "dup"
    if await get_word_count(uid) >= int(max_words):
        return "full"
    await db(
        "INSERT INTO vocabulary (uid,word,translation,example,topic,next_review) VALUES (?,?,?,?,?,?)",
        uid, word, translation, example, topic, _days_later(1)
    )
    await add_xp(uid, 5)
    return "ok"


async def get_vocab_page(uid: int, offset: int = 0, limit: int = 15):
    """Paginated saved-words list, newest first. Fetches one extra row to tell
    the caller whether more pages exist without a second COUNT round-trip."""
    limit = max(1, min(50, int(limit)))
    offset = max(0, int(offset))
    rows = await db(
        "SELECT id, word, translation, example, topic, next_review, reviews, created_at "
        "FROM vocabulary WHERE uid=? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        uid, limit + 1, offset, fetch="all"
    )
    rows = list(rows or [])
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def delete_word(uid: int, word_id: int) -> bool:
    """Remove one word from the user's dictionary (frees a queue slot).
    Scoped by uid so a user can only delete their own rows."""
    await db("DELETE FROM vocabulary WHERE id=? AND uid=?", int(word_id), uid)
    return True


# ── FSRS audit & state writeback ───────────────────────────────────
# FSRS scheduling itself lives client-side for now (the WebApp already
# runs an SM-2 loop and we don't want to block reviews on a server hop);
# the server's job is to (a) accept the new card state from the client
# and (b) keep an audit log that the FSRS Optimizer can later replay.
async def fsrs_save_state(
    word_id: int,
    *,
    stability: float,
    difficulty: float,
    state: str,
    lapses: int,
    next_review_dt: str | None,
):
    """Persist the FSRS Card state computed by the client."""
    await db(
        "UPDATE vocabulary SET stability=?, difficulty=?, state=?, lapses=?, "
        "next_review=?, last_review_dt=? WHERE id=?",
        float(stability), float(difficulty), str(state)[:16], int(lapses),
        next_review_dt, _today(), word_id,
    )


async def fsrs_log_review(
    uid: int,
    word_id: int,
    rating: int,
    *,
    elapsed_days: float | None = None,
    scheduled_days: float | None = None,
    state_before: str | None = None,
    duration_ms: int | None = None,
):
    """Append a row to the FSRS review log. Cheap insert, never blocks."""
    try:
        await db(
            "INSERT INTO fsrs_review_log "
            "(uid, word_id, rating, elapsed_days, scheduled_days, state_before, duration_ms) "
            "VALUES (?,?,?,?,?,?,?)",
            uid, word_id, int(rating),
            elapsed_days, scheduled_days, state_before, duration_ms,
        )
    except Exception as e:
        logger.warning("fsrs_log_review failed uid=%s word_id=%s: %s", uid, word_id, e)


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


# ── EXAM SIMULATOR (premium TOEFL/IELTS full mock + certificate) ───
# A session holds the four academic sections. Reading/Listening are objective
# (raw correct-count → per-section scale); Writing/Speaking are AI-graded by
# exam_grader.py. finalize_exam_session() scales the four section scores onto
# the official total (TOEFL 0-120 sum / IELTS 0-9 mean→nearest .5) and mints a
# shareable certificate code. Both tiers metered? No — these routes are
# subscription-gated at the server layer; the DB layer just stores results.
import json as _exam_json
import secrets as _exam_secrets

_EXAM_SCALE_MAX = {"toefl": 120.0, "ielts": 9.0}
_EXAM_SECTION_MAX = {"toefl": 30, "ielts": 9}
_EXAM_SECTION_COL = {
    "reading": "reading_score", "listening": "listening_score",
    "writing": "writing_score", "speaking": "speaking_score",
}

def _exam_norm_type(exam_type: str) -> str:
    return "ielts" if str(exam_type or "").lower() == "ielts" else "toefl"

async def create_exam_session(uid: int, exam_type: str = "toefl") -> int:
    exam_type = _exam_norm_type(exam_type)
    await db("INSERT INTO exam_sessions (uid, exam_type, status, scale_max) VALUES (?,?,?,?)",
             uid, exam_type, "in_progress", _EXAM_SCALE_MAX[exam_type])
    row = await db("SELECT id FROM exam_sessions WHERE uid=? ORDER BY id DESC LIMIT 1",
                   uid, fetch="one")
    return int(row["id"]) if row else 0

async def get_exam_session(session_id: int, uid: int = None):
    if uid is not None:
        return await db("SELECT * FROM exam_sessions WHERE id=? AND uid=?",
                        session_id, uid, fetch="one")
    return await db("SELECT * FROM exam_sessions WHERE id=?", session_id, fetch="one")

async def save_section_result(session_id: int, uid: int, section: str,
                              raw_score: float, max_score: float,
                              payload: dict = None) -> bool:
    section = str(section or "").lower()
    if section not in _EXAM_SECTION_COL:
        return False
    # Re-submitting a section overwrites the previous attempt.
    await db("DELETE FROM exam_section_results WHERE session_id=? AND section=?",
             session_id, section)
    await db("INSERT INTO exam_section_results "
             "(session_id, uid, section, raw_score, max_score, payload) VALUES (?,?,?,?,?,?)",
             session_id, uid, section, float(raw_score), float(max_score),
             _exam_json.dumps(payload or {}, ensure_ascii=False)[:8000])
    col = _EXAM_SECTION_COL[section]
    await db(f"UPDATE exam_sessions SET {col}=? WHERE id=? AND uid=?",
             float(raw_score), session_id, uid)
    return True

async def get_section_results(session_id: int):
    return await db("SELECT * FROM exam_section_results WHERE session_id=? ORDER BY id",
                    session_id, fetch="all")

def _exam_scale_total(exam_type: str, sections: dict) -> float:
    """sections values are already on the official per-section scale
    (TOEFL 0-30 each → sum 0-120; IELTS band 0-9 each → mean → nearest 0.5)."""
    exam_type = _exam_norm_type(exam_type)
    vals = [float(sections[k]) for k in _EXAM_SECTION_COL
            if sections.get(k) is not None]
    if not vals:
        return 0.0
    if exam_type == "ielts":
        return round((sum(vals) / len(vals)) * 2) / 2.0
    return float(round(sum(vals)))

async def finalize_exam_session(session_id: int, uid: int) -> dict:
    s = await get_exam_session(session_id, uid)
    if not s:
        return {}
    exam_type = _exam_norm_type(s["exam_type"])
    sections = {k: s[v] for k, v in _EXAM_SECTION_COL.items()}
    total = _exam_scale_total(exam_type, sections)
    scale_max = _EXAM_SCALE_MAX[exam_type]
    cert_code = s["cert_code"] or ("PG-" + _exam_secrets.token_hex(4).upper())
    await db("UPDATE exam_sessions SET status=?, total_score=?, scale_max=?, "
             "cert_code=?, completed_at=? WHERE id=? AND uid=?",
             "completed", total, scale_max, cert_code, _today(), session_id, uid)
    # Mirror per-section scores into the legacy toefl_scores history for stats.
    try:
        per_max = _EXAM_SECTION_MAX[exam_type]
        for sec, val in sections.items():
            if val is not None:
                await log_toefl(uid, f"{exam_type}_{sec}", int(round(float(val))), per_max)
    except Exception as e:
        logger.debug(f"exam history log skipped: {e}")
    return {"session_id": session_id, "exam_type": exam_type, "total": total,
            "scale_max": scale_max, "cert_code": cert_code, "sections": sections}

async def get_exam_by_cert(cert_code: str):
    return await db("SELECT * FROM exam_sessions WHERE cert_code=? AND status=?",
                    cert_code, "completed", fetch="one")

async def get_exam_history(uid: int, limit: int = 20):
    limit = max(1, min(50, int(limit)))
    return await db("SELECT id, exam_type, status, total_score, scale_max, completed_at "
                    f"FROM exam_sessions WHERE uid=? AND status='completed' "
                    f"ORDER BY id DESC LIMIT {limit}", uid, fetch="all")


# ── Dictionary word-breakdown cache ───────────────────────────────
# The dictionary "3 examples in context" feature asks Claude Sonnet for three
# contextual example sentences. The result is deterministic enough to cache:
# the same word in the same UI language always yields equivalent examples, so we
# store them once and serve cached copies on every repeat tap — avoiding a paid
# Anthropic call each time the user reopens a saved word.

def _norm_word_key(word: str) -> str:
    return (word or "").strip().lower()[:120]


async def get_word_examples(word: str, lang: str):
    """Return the cached [{en,tr},...] list for (word, lang), or None on miss."""
    key = _norm_word_key(word)
    if not key:
        return None
    lang = (lang or "en")[:8]
    row = await db("SELECT examples FROM word_examples_cache WHERE word=? AND lang=?",
                   key, lang, fetch="one")
    if not row:
        return None
    raw = row["examples"] if isinstance(row, dict) else row[0]
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) and data else None
    except Exception:
        return None


async def save_word_examples(word: str, lang: str, examples: list):
    """Upsert the AI-generated examples for (word, lang). No-op on empty input."""
    key = _norm_word_key(word)
    if not key or not isinstance(examples, list) or not examples:
        return
    lang = (lang or "en")[:8]
    payload = json.dumps(examples[:3], ensure_ascii=False)
    if USE_POSTGRES:
        await db("INSERT INTO word_examples_cache (word, lang, examples) VALUES (?,?,?) "
                 "ON CONFLICT (word, lang) DO UPDATE SET examples=EXCLUDED.examples, "
                 "created_at=NOW()", key, lang, payload)
    else:
        await db("INSERT OR REPLACE INTO word_examples_cache (word, lang, examples) "
                 "VALUES (?,?,?)", key, lang, payload)


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

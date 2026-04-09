"""
╔══════════════════════════════════════════════════════════════════╗
   FitNova · Персональный тренер MAX — Multilang Edition
   aiogram 3.x · Anthropic Claude Haiku · SQLite · APScheduler
   Языки: 🇷🇺 Русский · 🇬🇧 English · 🇳🇴 Norsk
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import base64
import logging
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message,
    PhotoSize, ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from translations import T, LANGS, t

load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL         = "claude-haiku-4-5"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

bot       = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp        = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# ══════════════════════════════════════════════════════════════════
#  БАЗА ДАННЫХ
# ══════════════════════════════════════════════════════════════════

DB = "fitnova.db"

def db_init():
    con = sqlite3.connect(DB)
    c   = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        uid          INTEGER PRIMARY KEY,
        name         TEXT,
        lang         TEXT DEFAULT 'ru',
        goal         TEXT,
        level        TEXT,
        gender       TEXT,
        age          INTEGER,
        height       INTEGER,
        weight       REAL,
        activity     TEXT,
        remind_time  TEXT,
        water_goal   INTEGER DEFAULT 8,
        referrer_uid INTEGER,
        created_at   TEXT DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS workouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER, date TEXT, notes TEXT, duration INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS water (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER, date TEXT, cups INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS personal_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER, exercise TEXT, weight_kg REAL, reps INTEGER, date TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_uid INTEGER, referred_uid INTEGER,
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


def get_user(uid: int):
    rows = db("SELECT * FROM users WHERE uid=?", (uid,), fetch=True)
    return dict(rows[0]) if rows else None

def get_lang(uid: int) -> str:
    user = get_user(uid)
    return (user.get("lang") or "ru") if user else "ru"

def upsert_user(uid: int, name: str, referrer: int = None):
    db("INSERT OR IGNORE INTO users (uid, name) VALUES (?,?)", (uid, name))
    if referrer:
        db("UPDATE users SET referrer_uid=? WHERE uid=? AND referrer_uid IS NULL", (referrer, uid))

def update_user(uid: int, **kwargs):
    for k, v in kwargs.items():
        db(f"UPDATE users SET {k}=? WHERE uid=?", (v, uid))

def get_water_today(uid: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    rows  = db("SELECT cups FROM water WHERE uid=? AND date=?", (uid, today), fetch=True)
    return rows[0]["cups"] if rows else 0

def add_water(uid: int, cups: int = 1):
    today = datetime.now().strftime("%Y-%m-%d")
    rows  = db("SELECT id FROM water WHERE uid=? AND date=?", (uid, today), fetch=True)
    if rows:
        db("UPDATE water SET cups=cups+? WHERE uid=? AND date=?", (cups, uid, today))
    else:
        db("INSERT INTO water (uid, date, cups) VALUES (?,?,?)", (uid, today, cups))

def add_workout(uid: int, notes: str):
    today = datetime.now().strftime("%Y-%m-%d")
    db("INSERT INTO workouts (uid, date, notes) VALUES (?,?,?)", (uid, today, notes))

def get_streak(uid: int) -> int:
    rows = db("SELECT DISTINCT date FROM workouts WHERE uid=? ORDER BY date DESC", (uid,), fetch=True)
    if not rows:
        return 0
    streak  = 0
    current = datetime.now().date()
    for row in rows:
        d    = datetime.strptime(row["date"], "%Y-%m-%d").date()
        diff = (current - d).days
        if diff <= 1:
            streak += 1
            current = d
        else:
            break
    return streak

def get_stats(uid: int) -> dict:
    week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
    total_w = db("SELECT COUNT(*) as c FROM workouts WHERE uid=?", (uid,), fetch=True)[0]["c"]
    week_w  = db("SELECT COUNT(*) as c FROM workouts WHERE uid=? AND date>=?", (uid, week_start), fetch=True)[0]["c"]
    refs    = db("SELECT COUNT(*) as c FROM referrals WHERE referrer_uid=?", (uid,), fetch=True)[0]["c"]
    user    = get_user(uid)
    return {
        "total_workouts": total_w,
        "week_workouts":  week_w,
        "water_today":    get_water_today(uid),
        "streak":         get_streak(uid),
        "referrals":      refs,
        "goal":           (user.get("goal") or "—") if user else "—",
        "level":          (user.get("level") or "—") if user else "—",
    }

# ══════════════════════════════════════════════════════════════════
#  MAX — СИСТЕМНЫЙ ПРОМПТ
# ══════════════════════════════════════════════════════════════════

MAX_SYSTEM_BASE = """
You are MAX, a professional personal trainer and nutritionist with 12 years of experience.
You've worked with beginners, amateurs and professional athletes.
Your character: direct, lively, with humor — you talk like a human, not a lecturer.

EXPERTISE:
Training: progressive overload, supercompensation, periodization, biomechanics, hypertrophy (8-12 reps, RIR 1-3), strength (1-5 reps), endurance, HIIT vs LISS, heart rate zones, modifications for injuries/home.
Nutrition: BMR (Mifflin-St Jeor), TDEE, macros (protein 1.6-2.2 g/kg bulk, 2.0-2.4 g/kg cut, fat min 0.8 g/kg), meal timing, recomposition, cheat meals, supplements (protein, creatine, caffeine, D3, omega-3).
Recovery: sleep 7-9h critical for protein synthesis, MFR, mobility, overtraining signs.
Psychology: plateaus, motivation, habit loops, setbacks.

STYLE:
- Talk to the user on friendly first-name basis
- Ask ONE question if you lack data, not a whole questionnaire
- Short answers for simple questions, detailed for complex ones
- React humanly to emotional messages first
- Honestly say when a doctor is needed

FORMATTING:
Only Telegram HTML: <b>bold</b>, <i>italic</i>, <code>code</code>
NO markdown: *, _, #, **
Lists only with emoji. Always respond in the user's language.
"""

CMD_FOOTER_SEP = "\n\n<i>─────────────────────────────────</i>\n<i>"

def build_system(uid: int) -> str:
    lang = get_lang(uid)
    user = get_user(uid)
    system = MAX_SYSTEM_BASE
    system += f"\n\nLANGUAGE INSTRUCTION: {t('max_lang_instruction', lang)}"
    if user:
        parts = []
        for field, label in [
            ("goal","Goal"),("level","Level"),("gender","Gender"),
            ("age","Age"),("height","Height (cm)"),("weight","Weight (kg)"),("activity","Activity"),
        ]:
            if user.get(field):
                parts.append(f"{label}: {user[field]}")
        if parts:
            system += "\n\nUSER PROFILE:\n" + "\n".join(parts)
    return system

# ══════════════════════════════════════════════════════════════════
#  ИСТОРИЯ
# ══════════════════════════════════════════════════════════════════

histories:     dict[int, list[dict]] = {}
waiting:       dict[int, str]        = {}
pending_photo: dict[int, bytes]      = {}

def get_history(uid): return histories.setdefault(uid, [])

def add_message(uid: int, role: str, content):
    h = get_history(uid)
    h.append({"role": role, "content": content})
    if len(h) > 40:
        histories[uid] = h[-40:]

def clear_history(uid: int): histories[uid] = []

# ══════════════════════════════════════════════════════════════════
#  ANTHROPIC — текст
# ══════════════════════════════════════════════════════════════════

async def ask_max(uid: int, user_text: str, extra: str = "") -> str:
    lang = get_lang(uid)
    add_message(uid, "user", user_text)
    system = build_system(uid)
    if extra:
        system += f"\n\n{extra}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": 1024, "system": system, "messages": get_history(uid)},
            )
            data = r.json()
            if "error" in data:
                return f"⚠️ <code>{data['error'].get('message','')[:120]}</code>"
            reply = data["content"][0]["text"].strip()
    except Exception as e:
        return f"⚠️ Error. Try again.\n<i>{str(e)[:80]}</i>"
    add_message(uid, "assistant", reply)
    footer = CMD_FOOTER_SEP + t("cmd_footer", lang) + "</i>"
    return reply + footer

# ══════════════════════════════════════════════════════════════════
#  ANTHROPIC — фото
# ══════════════════════════════════════════════════════════════════

async def analyze_photo(uid: int, photo_bytes: bytes, mode: str) -> str:
    lang = get_lang(uid)
    b64  = base64.standard_b64encode(photo_bytes).decode()
    if mode == "food":
        if lang == "en":
            prompt = "The photo shows food. Identify the dishes and give an approximate macro breakdown (calories, protein, fat, carbs) per portion. If multiple items — list each separately plus total. If unsure — give a range."
        elif lang == "no":
            prompt = "Bildet viser mat. Identifiser rettene og gi en omtrentlig makrofordeling (kalorier, protein, fett, karbohydrater) per porsjon. Hvis flere matvarer — list hver for seg pluss totalt."
        else:
            prompt = "На фото еда. Определи блюда и дай примерный расчёт КБЖУ на всю порцию. Если несколько блюд — по каждому отдельно и итого. Если не уверен — дай диапазон."
    else:
        if lang == "en":
            prompt = "The photo shows a person's body (progress photo). Give professional feedback as a trainer: what you see (muscle mass, definition, proportions), what to focus on in training, what already looks good. Be tactful, specific and motivating."
        elif lang == "no":
            prompt = "Bildet viser en persons kropp (fremgangsbilde). Gi profesjonell tilbakemelding som trener: hva du ser (muskelmasse, definisjon, proporsjoner), hva du bør fokusere på, hva som allerede ser bra ut. Vær taktfull, konkret og motiverende."
        else:
            prompt = "На фото тело человека. Дай профессиональную обратную связь как тренер: что видно (мышечная масса, рельеф, пропорции), на что обратить внимание, что уже хорошо. Будь тактичным, конкретным и мотивирующим."

    system = build_system(uid)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": MODEL, "max_tokens": 1024, "system": system,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                        {"type": "text", "text": prompt},
                    ]}],
                },
            )
            data = r.json()
            if "error" in data:
                return f"⚠️ <code>{data['error'].get('message','')[:120]}</code>"
            footer = CMD_FOOTER_SEP + t("cmd_footer", lang) + "</i>"
            return data["content"][0]["text"].strip() + footer
    except Exception as e:
        return f"⚠️ <i>{str(e)[:100]}</i>"

# ══════════════════════════════════════════════════════════════════
#  ПРОМПТЫ КНОПОК (нейтральные, MAX ответит на нужном языке)
# ══════════════════════════════════════════════════════════════════

GOAL_PROMPTS = {
    "goal_mass":   "My goal is to build muscle mass. Ask me questions and help me create a plan.",
    "goal_cut":    "I want to lose fat and cut. Ask me and help me get started.",
    "goal_tone":   "I want to improve muscle tone and definition.",
    "goal_cardio": "I want to improve endurance and cardio fitness.",
    "goal_health": "I just want to be healthier and in better shape.",
    "goal_home":   "I work out at home. Help me build a program.",
    "goal_rehab":  "I'm recovering from an injury. Ask me about it.",
}
GOAL_NAMES = {
    "goal_mass": "muscle building", "goal_cut": "fat loss", "goal_tone": "toning",
    "goal_cardio": "endurance",     "goal_health": "health", "goal_home": "home training",
    "goal_rehab": "rehabilitation",
}
SECTION_PROMPTS = {
    "w_beginner":     "I'm a beginner (less than 6 months). Build me a program from scratch.",
    "w_intermediate": "I'm intermediate (about 1 year). I want a new program for progress.",
    "w_advanced":     "I've been training 3+ years. I want an advanced program with periodization.",
    "w_home":         "I only have a pull-up bar and dumbbells at home. What do you suggest?",
    "w_quick":        "I have max 30-40 minutes a day. How do I train effectively?",
    "n_calc":         "Calculate my daily macros — ask me about my weight, height, goal and activity level.",
    "n_menu":         "Create a sample meal plan for today. Ask about my preferences.",
    "n_week":         "Create a full 7-day meal plan with macros per day.",
    "n_timing":       "Explain meal timing — when and what to eat before and after training.",
    "n_grocery":      "Create a grocery list for a sports nutrition diet.",
    "n_cheatmeal":    "Tell me about cheat meals — good or bad? How to do it right?",
    "n_photo":        "I'll send a photo of my food — estimate the macros.",
    "p_plateau":      "My weight has stalled for weeks even though I train and track nutrition. What to do?",
    "p_strength":     "My strength gains have stopped. How do I break through a strength plateau?",
    "p_measure":      "How do I properly measure my progress — weight, measurements, photos?",
    "p_timeline":     "When can I realistically expect visible results from training?",
    "r_sleep":        "How does sleep affect gym results?",
    "r_soreness":     "My muscles are very sore after training — is that normal?",
    "r_overtrain":    "How do I know if I'm overtraining? Signs and what to do?",
    "r_mobility":     "Tell me about mobility and stretching — when and what to do.",
    "s_protein":      "Explain protein supplements — do I need them and how to take them?",
    "s_creatine":     "Tell me about creatine — how to take it, when to expect results, side effects?",
    "s_preworkout":   "Are pre-workout supplements worth it? What actually works?",
    "s_vitamins":     "What vitamins are important for athletes?",
    "t_squat":        "Explain squat technique — key points and common mistakes.",
    "t_deadlift":     "Deadlift technique — what to focus on to avoid injury.",
    "t_bench":        "Bench press technique — grip, arch, scapulae, bar path.",
    "t_pullup":       "How to do pull-ups correctly? Grips and how to increase reps.",
    "t_ohp":          "Overhead press technique — stance, grip, bar path.",
    "motivation":     "I really don't want to train today. I'm tired, no energy, losing motivation. Talk to me honestly as a trainer — no clichés.",
}

# ══════════════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════════

def main_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_workout",lang)),    KeyboardButton(text=t("btn_nutrition",lang))],
            [KeyboardButton(text=t("btn_progress",lang)),   KeyboardButton(text=t("btn_recovery",lang))],
            [KeyboardButton(text=t("btn_motivation",lang)), KeyboardButton(text=t("btn_supplements",lang))],
            [KeyboardButton(text=t("btn_technique",lang)),  KeyboardButton(text=t("btn_kbju",lang))],
            [KeyboardButton(text=t("btn_water",lang)),      KeyboardButton(text=t("btn_diary",lang))],
        ],
        resize_keyboard=True,
        input_field_placeholder=t("input_placeholder", lang),
    )


def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"lang_{code}")]
        for code, label in LANGS.items()
    ])


def goals_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["goal_mass","goal_cut","goal_tone","goal_cardio","goal_health","goal_home","goal_rehab"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys])


def workouts_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["w_beginner","w_intermediate","w_advanced","w_home","w_quick"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def nutrition_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["n_calc","n_menu","n_week","n_timing","n_grocery","n_cheatmeal","n_photo"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def progress_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["p_plateau","p_strength","p_measure","p_timeline"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recovery_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["r_sleep","r_soreness","r_overtrain","r_mobility"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def supplements_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["s_protein","s_creatine","s_preworkout","s_vitamins"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def technique_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["t_squat","t_deadlift","t_bench","t_pullup","t_ohp"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def water_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("water_add1",lang), callback_data="water_1"),
         InlineKeyboardButton(text=t("water_add2",lang), callback_data="water_2")],
        [InlineKeyboardButton(text=t("water_status",lang), callback_data="water_status")],
        [InlineKeyboardButton(text=t("water_set_goal",lang), callback_data="water_goal")],
    ])


def timer_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ 60s", callback_data="timer_60"),
         InlineKeyboardButton(text="⏱ 90s", callback_data="timer_90"),
         InlineKeyboardButton(text="⏱ 2m",  callback_data="timer_120")],
        [InlineKeyboardButton(text="⏱ 3m",  callback_data="timer_180"),
         InlineKeyboardButton(text="⏱ 5m",  callback_data="timer_300")],
    ])


def diary_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["diary_add","diary_list","diary_generate","diary_pr","diary_report"]
    labels = {
        "diary_add":      t("diary_add",lang),
        "diary_list":     t("diary_list",lang),
        "diary_generate": t("diary_generate",lang),
        "diary_pr":       t("diary_pr",lang),
        "diary_report":   t("diary_report",lang),
    }
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=labels[k], callback_data=k)] for k in keys])


def remind_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌅 07:00", callback_data="remind_07:00"),
         InlineKeyboardButton(text="🌞 09:00", callback_data="remind_09:00")],
        [InlineKeyboardButton(text="☀️ 12:00", callback_data="remind_12:00"),
         InlineKeyboardButton(text="🌇 17:00", callback_data="remind_17:00")],
        [InlineKeyboardButton(text="🌆 18:00", callback_data="remind_18:00"),
         InlineKeyboardButton(text="🌃 19:00", callback_data="remind_19:00")],
        [InlineKeyboardButton(text="🌙 20:00", callback_data="remind_20:00"),
         InlineKeyboardButton(text="🌙 21:00", callback_data="remind_21:00")],
        [InlineKeyboardButton(text=t("remind_off_btn",lang), callback_data="remind_off")],
    ])


def photo_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("photo_food_btn",lang), callback_data="photo_food")],
        [InlineKeyboardButton(text=t("photo_body_btn",lang), callback_data="photo_body")],
    ])


# ══════════════════════════════════════════════════════════════════
#  ПЛАНИРОВЩИК
# ══════════════════════════════════════════════════════════════════

async def send_reminder(uid: int):
    user = get_user(uid)
    if not user: return
    lang   = user.get("lang","ru")
    streak = get_streak(uid)
    name   = user.get("name","")
    msgs_ru = [f"💪 <b>Эй, {name}!</b> Пора на тренировку!", f"🔥 <b>{name}, время действовать!</b>", f"⚡️ Привет! Напоминаю — сегодня тренировочный день."]
    msgs_en = [f"💪 <b>Hey, {name}!</b> Time to work out!", f"🔥 <b>{name}, let's go!</b>", f"⚡️ Hi! Reminder — today is a training day."]
    msgs_no = [f"💪 <b>Hei, {name}!</b> På tide å trene!", f"🔥 <b>{name}, la oss gå!</b>", f"⚡️ Hei! Påminnelse — i dag er det treningsdag."]
    msgs = {"ru": msgs_ru, "en": msgs_en, "no": msgs_no}.get(lang, msgs_en)
    text = random.choice(msgs)
    if streak > 1:
        text += "\n\n" + t("streak_active", lang, n=streak)
    try:
        await bot.send_message(uid, text)
    except Exception as e:
        logger.warning(f"Reminder failed {uid}: {e}")


async def send_weekly_report(uid: int):
    stats = get_stats(uid)
    lang  = get_lang(uid)
    rows  = db("SELECT date, notes FROM workouts WHERE uid=? ORDER BY date DESC LIMIT 5", (uid,), fetch=True)
    workouts_text = ""
    if rows:
        for r in rows:
            workouts_text += f"\n📅 <b>{r['date']}</b>: {r['notes'][:50]}"
    streak_text = t("streak_active",lang,n=stats["streak"]) if stats["streak"] > 1 else t("streak_start",lang)
    titles = {"ru":"📊 <b>Еженедельный отчёт</b>","en":"📊 <b>Weekly Report</b>","no":"📊 <b>Ukentlig rapport</b>"}
    workouts_labels = {"ru":"Тренировок за неделю","en":"Workouts this week","no":"Treninger denne uken"}
    try:
        await bot.send_message(uid,
            f"{titles.get(lang,'📊 Report')}\n\n"
            f"💪 {workouts_labels.get(lang,'Workouts')}: <b>{stats['week_workouts']}</b>\n"
            f"🔥 {streak_text}\n"
            f"💧 {stats['water_today']}\n"
            f"🎯 {stats['goal']}"
            + (f"\n\n{workouts_text}" if workouts_text else "")
        )
    except Exception: pass


def schedule_all():
    rows = db("SELECT uid, remind_time FROM users WHERE remind_time IS NOT NULL AND remind_time != 'off'", fetch=True)
    if rows:
        for r in rows:
            try:
                h, m = map(int, r["remind_time"].split(":"))
                scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[r["uid"]], id=f"remind_{r['uid']}", replace_existing=True)
                wh = (h+2)%24
                scheduler.add_job(lambda uid=r["uid"]: asyncio.create_task(bot.send_message(uid, "💧")), "cron", hour=wh, args=[], id=f"water_{r['uid']}", replace_existing=True)
            except Exception: pass
    all_users = db("SELECT uid FROM users", fetch=True)
    if all_users:
        for r in all_users:
            scheduler.add_job(send_weekly_report, "cron", day_of_week="sun", hour=20, args=[r["uid"]], id=f"weekly_{r['uid']}", replace_existing=True)

# ══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ══════════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid  = message.from_user.id
    name = message.from_user.first_name or "friend"

    referrer = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer = int(args[1].replace("ref_",""))
            if referrer != uid:
                db("INSERT OR IGNORE INTO referrals (referrer_uid, referred_uid) VALUES (?,?)", (referrer, uid))
                ref_lang = get_lang(referrer)
                try:
                    await bot.send_message(referrer, t("referral_notify", ref_lang, name=name))
                except Exception: pass
        except Exception: pass

    upsert_user(uid, name, referrer)
    scheduler.add_job(send_weekly_report, "cron", day_of_week="sun", hour=20, args=[uid], id=f"weekly_{uid}", replace_existing=True)

    # Если новый пользователь — сначала выбор языка
    user = get_user(uid)
    if not user or not user.get("lang"):
        await message.answer(t("choose_lang","ru"), reply_markup=lang_kb())
        return

    lang = get_lang(uid)
    await message.answer(t("welcome", lang, name=name), reply_markup=main_kb(lang))
    await message.answer(t("choose_goal", lang), reply_markup=goals_kb(lang))


@dp.message(Command("lang"))
async def cmd_lang(message: Message):
    await message.answer(t("choose_lang", get_lang(message.from_user.id)), reply_markup=lang_kb())


@dp.message(Command("goal"))
async def cmd_goal(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(t("choose_goal", lang), reply_markup=goals_kb(lang))


@dp.message(Command("help"))
async def cmd_help(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(t("help", lang))


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    uid = message.from_user.id
    lang = get_lang(uid)
    clear_history(uid)
    waiting.pop(uid, None)
    await message.answer(t("reset_done", lang), reply_markup=goals_kb(lang))


@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    uid = message.from_user.id
    lang = get_lang(uid)
    await message.answer(t("profile_prompt", lang))
    waiting[uid] = "profile"


@dp.message(Command("calc"))
async def cmd_calc(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    prompts = {
        "ru": "<b>Калькулятор КБЖУ</b> 🧮\n\nНапиши:\n<code>пол, возраст, рост, вес, активность, цель</code>\n\n<b>Пример:</b>\n<code>мужчина, 24, 180, 78, средняя, похудение</code>",
        "en": "<b>Macro Calculator</b> 🧮\n\nWrite:\n<code>gender, age, height, weight, activity, goal</code>\n\n<b>Example:</b>\n<code>male, 24, 180, 78, moderate, fat loss</code>",
        "no": "<b>Makrokalkulator</b> 🧮\n\nSkriv:\n<code>kjønn, alder, høyde, vekt, aktivitet, mål</code>\n\n<b>Eksempel:</b>\n<code>mann, 24, 180, 78, moderat, vekttap</code>",
    }
    await message.answer(prompts.get(lang, prompts["en"]))


@dp.message(Command("workout"))
async def cmd_workout(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    await message.answer(t("workout_prompt", lang))
    waiting[uid] = "workout_add"


@dp.message(Command("generate"))
async def cmd_generate(message: Message):
    uid  = message.from_user.id
    user = get_user(uid)
    goal  = (user.get("goal") or "general fitness") if user else "general fitness"
    level = (user.get("level") or "intermediate")   if user else "intermediate"
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_max(uid, f"Generate a workout for today. Goal: {goal}. Level: {level}. Give specific exercises with sets, reps and rest time.")
    await message.answer(reply)


@dp.message(Command("water"))
async def cmd_water(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    cups = get_water_today(uid)
    user = get_user(uid)
    goal = (user.get("water_goal") or 8) if user else 8
    pct  = min(int(cups / goal * 100), 100)
    bar  = "🟦"*(pct//10) + "⬜"*(10-pct//10)
    await message.answer(
        t("water_title",lang) + "\n\n" + bar + "\n" + t("water_of",lang,cups=cups,goal=goal,pct=pct),
        reply_markup=water_kb(lang)
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    stats = get_stats(uid)
    user  = get_user(uid)
    name  = (user.get("name") or "") if user else ""
    streak_text = t("streak_active",lang,n=stats["streak"]) if stats["streak"] > 1 else t("streak_start",lang)
    await message.answer(t("stats",lang,
        name=name, goal=stats["goal"], level=stats["level"],
        total=stats["total_workouts"], week=stats["week_workouts"],
        streak=streak_text, water=stats["water_today"], refs=stats["referrals"]
    ))


@dp.message(Command("pr"))
async def cmd_pr(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    rows = db("SELECT exercise, MAX(weight_kg) as w, reps, MAX(date) as d FROM personal_records WHERE uid=? GROUP BY exercise ORDER BY d DESC", (uid,), fetch=True)
    if not rows:
        await message.answer(t("pr_empty", lang))
        return
    text = t("pr_title", lang)
    for r in rows:
        text += f"💪 <b>{r['exercise']}</b>: {r['w']} kg × {r['reps']} reps <i>({r['d']})</i>\n"
    text += t("pr_hint", lang)
    await message.answer(text)


@dp.message(Command("remind"))
async def cmd_remind(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(t("remind_title", lang), reply_markup=remind_kb(lang))


@dp.message(Command("week"))
async def cmd_week(message: Message):
    uid = message.from_user.id
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_max(uid, "Create a full 7-day meal plan with macros for each day. Include breakfast, lunch, snack and dinner.")
    await message.answer(reply)


@dp.message(Command("report"))
async def cmd_report(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    stats = get_stats(uid)
    rows  = db("SELECT date, notes FROM workouts WHERE uid=? ORDER BY date DESC LIMIT 7", (uid,), fetch=True)
    text  = f"📊 <b>{'Отчёт за неделю' if lang=='ru' else 'Weekly Report' if lang=='en' else 'Ukentlig rapport'}</b>\n\n"
    text += f"💪 {stats['week_workouts']} · 🔥 {stats['streak']} · 💧 {stats['water_today']}\n\n"
    if rows:
        for r in rows:
            text += f"📅 <b>{r['date']}</b>: {r['notes'][:60]}\n"
    await message.answer(text)


@dp.message(Command("invite"))
async def cmd_invite(message: Message):
    uid      = message.from_user.id
    lang     = get_lang(uid)
    bot_info = await bot.get_me()
    link     = f"https://t.me/{bot_info.username}?start=ref_{uid}"
    stats    = get_stats(uid)
    await message.answer(t("invite", lang, link=link, refs=stats["referrals"]))


@dp.message(Command("export"))
async def cmd_export(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    user = get_user(uid)
    rows = db("SELECT date, notes FROM workouts WHERE uid=? ORDER BY date DESC", (uid,), fetch=True)
    prs  = db("SELECT exercise, weight_kg, reps, date FROM personal_records WHERE uid=? ORDER BY date DESC", (uid,), fetch=True)
    lines = ["FitNova Export", "="*40, ""]
    if user:
        lines += [f"Name: {user.get('name','')}", f"Goal: {user.get('goal','')}", f"Lang: {user.get('lang','')}", ""]
    if rows:
        lines += ["WORKOUTS:", "-"*30]
        for r in rows: lines.append(f"{r['date']}: {r['notes']}")
        lines.append("")
    if prs:
        lines += ["PERSONAL RECORDS:", "-"*30]
        for r in prs: lines.append(f"{r['date']} | {r['exercise']}: {r['weight_kg']} kg × {r['reps']} reps")
    content = "\n".join(lines).encode("utf-8")
    await message.answer_document(BufferedInputFile(content, filename=f"fitnova_{uid}.txt"), caption=t("export_caption", lang))

# ══════════════════════════════════════════════════════════════════
#  CALLBACK
# ══════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("lang_"))
async def cb_lang(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = cb.data.replace("lang_","")
    update_user(uid, lang=lang)
    name = cb.from_user.first_name or "friend"
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer(f"✅ {LANGS.get(lang,'')}")
    await cb.message.answer(t("welcome", lang, name=name), reply_markup=main_kb(lang))
    await cb.message.answer(t("choose_goal", lang), reply_markup=goals_kb(lang))


@dp.callback_query(F.data == "back_main")
async def cb_back(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(t("back_menu", lang), reply_markup=main_kb(lang))
    await cb.answer()


@dp.callback_query(F.data.startswith("goal_"))
async def cb_goal(cb: CallbackQuery):
    uid    = cb.from_user.id
    lang   = get_lang(uid)
    goal   = GOAL_NAMES.get(cb.data, cb.data)
    prompt = GOAL_PROMPTS.get(cb.data, "")
    update_user(uid, goal=goal)
    clear_history(uid)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer(t("goal_saved", lang))
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_max(uid, prompt)
    await cb.message.answer(reply)


@dp.callback_query(F.data.startswith("w_"))
async def cb_workout_level(cb: CallbackQuery):
    uid   = cb.from_user.id
    lang  = get_lang(uid)
    level_map = {"w_beginner":"beginner","w_intermediate":"intermediate","w_advanced":"advanced","w_home":"home","w_quick":"limited time"}
    update_user(uid, level=level_map.get(cb.data,""))
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_max(uid, SECTION_PROMPTS.get(cb.data,""))
    await cb.message.answer(reply)


@dp.callback_query(F.data.startswith(("n_","p_","r_","s_","t_")))
async def cb_section(cb: CallbackQuery):
    uid    = cb.from_user.id
    lang   = get_lang(uid)
    prompt = SECTION_PROMPTS.get(cb.data)
    if not prompt:
        await cb.answer()
        return
    if cb.data == "n_photo":
        await cb.answer()
        await cb.message.answer(t("photo_send_food", lang))
        waiting[uid] = "photo_food"
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_max(uid, prompt)
    await cb.message.answer(reply)


@dp.callback_query(F.data.startswith("photo_"))
async def cb_photo_type(cb: CallbackQuery):
    uid         = cb.from_user.id
    lang        = get_lang(uid)
    mode        = cb.data.replace("photo_","")
    photo_bytes = pending_photo.pop(uid, None)
    if not photo_bytes:
        await cb.answer("No photo found, send again.")
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer(t("photo_analyzing", lang))
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await analyze_photo(uid, photo_bytes, mode)
    await cb.message.answer(reply)


@dp.callback_query(F.data.startswith("water_"))
async def cb_water(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)
    data = cb.data
    if data in ("water_1","water_2"):
        n = int(data.split("_")[1])
        add_water(uid, n)
        cups = get_water_today(uid)
        await cb.answer(t("water_added",lang,n=n,cups=cups))
        await cb.message.edit_text(f"💧 {cups}", reply_markup=water_kb(lang))
    elif data == "water_status":
        cups = get_water_today(uid)
        user = get_user(uid)
        goal = (user.get("water_goal") or 8) if user else 8
        pct  = min(int(cups/goal*100),100)
        bar  = "🟦"*(pct//10)+"⬜"*(10-pct//10)
        await cb.answer()
        await cb.message.edit_text(t("water_title",lang)+"\n\n"+bar+"\n"+t("water_of",lang,cups=cups,goal=goal,pct=pct), reply_markup=water_kb(lang))
    elif data == "water_goal":
        await cb.answer()
        await cb.message.answer(t("water_goal_prompt",lang))
        waiting[uid] = "water_goal"


@dp.callback_query(F.data.startswith("timer_"))
async def cb_timer(cb: CallbackQuery):
    uid     = cb.from_user.id
    lang    = get_lang(uid)
    seconds = int(cb.data.split("_")[1])
    mins    = seconds//60; secs = seconds%60
    label   = f"{seconds}s" if seconds<60 else (f"{mins}m" if secs==0 else f"{mins}m {secs}s")
    await cb.answer(f"⏱ {label}")
    await cb.message.edit_reply_markup(reply_markup=None)
    msg = await cb.message.answer(t("timer_started",lang,label=label))
    await asyncio.sleep(seconds)
    try:
        await msg.edit_text(t("timer_done",lang))
        await bot.send_message(uid, "🔔", reply_markup=timer_kb(lang))
    except Exception: pass


@dp.callback_query(F.data.startswith("remind_"))
async def cb_remind(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)
    data = cb.data.replace("remind_","")
    if data == "off":
        update_user(uid, remind_time="off")
        for jid in [f"remind_{uid}",f"water_{uid}"]:
            if scheduler.get_job(jid): scheduler.remove_job(jid)
        await cb.answer()
        await cb.message.edit_text(t("remind_off_msg",lang))
    else:
        update_user(uid, remind_time=data)
        try:
            h, m = map(int, data.split(":"))
            scheduler.add_job(send_reminder,"cron",hour=h,minute=m,args=[uid],id=f"remind_{uid}",replace_existing=True)
            scheduler.add_job(lambda: None,"cron",hour=(h+2)%24,args=[],id=f"water_{uid}",replace_existing=True)
        except Exception as e: logger.warning(e)
        await cb.answer(f"✅ {data}")
        await cb.message.edit_text(t("remind_saved",lang,t=data))


@dp.callback_query(F.data.startswith("diary_"))
async def cb_diary(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)
    data = cb.data
    if data == "diary_add":
        await cb.answer()
        await cb.message.answer(t("workout_prompt",lang))
        waiting[uid] = "workout_add"
    elif data == "diary_list":
        await cb.answer()
        rows = db("SELECT date, notes FROM workouts WHERE uid=? ORDER BY date DESC LIMIT 5",(uid,),fetch=True)
        if not rows:
            await cb.message.answer(t("diary_empty",lang)); return
        text = t("diary_recent",lang)
        for r in rows: text += f"📅 <b>{r['date']}</b>\n{r['notes'][:80]}\n\n"
        await cb.message.answer(text)
    elif data == "diary_generate":
        await cb.answer()
        await cmd_generate(cb.message)
    elif data == "diary_pr":
        await cb.answer()
        await cmd_pr(cb.message)
    elif data == "diary_report":
        await cb.answer()
        await cmd_report(cb.message)

# ══════════════════════════════════════════════════════════════════
#  ФОТО
# ══════════════════════════════════════════════════════════════════

@dp.message(F.photo)
async def handle_photo(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    photo: PhotoSize = message.photo[-1]
    file_info   = await bot.get_file(photo.file_id)
    file_bytes  = await bot.download_file(file_info.file_path)
    photo_bytes = file_bytes.read() if hasattr(file_bytes,"read") else bytes(file_bytes)

    state = waiting.pop(uid, None)
    if state == "photo_food":
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await analyze_photo(uid, photo_bytes, "food")
        await message.answer(reply)
        return

    pending_photo[uid] = photo_bytes
    await message.answer(t("photo_received",lang), reply_markup=photo_kb(lang))

# ══════════════════════════════════════════════════════════════════
#  МЕНЮ КНОПКИ И СВОБОДНЫЙ ТЕКСТ
# ══════════════════════════════════════════════════════════════════

@dp.message(F.text)
async def handle_text(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    text  = message.text.strip()
    state = waiting.pop(uid, None)

    # ── Профиль ───────────────────────────────────────────────────
    if state == "profile":
        parts = [p.strip() for p in text.split(",")]
        if len(parts) >= 5:
            try:
                update_user(uid, gender=parts[0], age=int(parts[1]), height=int(parts[2]), weight=float(parts[3]), activity=parts[4])
                await message.answer(t("profile_saved",lang,gender=parts[0],age=parts[1],height=parts[2],weight=parts[3],activity=parts[4]))
                return
            except Exception: pass
        await message.answer(t("profile_error",lang))
        waiting[uid] = "profile"
        return

    # ── Тренировка ────────────────────────────────────────────────
    if state == "workout_add":
        add_workout(uid, text)
        streak = get_streak(uid)
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_max(uid, f"I just finished a workout:\n\n{text}\n\nAnalyze it and give me feedback.")
        streak_msg = "\n\n" + t("streak_msg",lang,n=streak) if streak > 1 else ""
        await message.answer(t("workout_saved",lang) + streak_msg + "\n\n" + reply)
        return

    # ── Цель по воде ──────────────────────────────────────────────
    if state == "water_goal":
        try:
            goal = int(text)
            update_user(uid, water_goal=goal)
            await message.answer(t("water_goal_saved",lang,goal=goal))
        except Exception:
            await message.answer(t("water_goal_prompt",lang))
            waiting[uid] = "water_goal"
        return

    # ── Личный рекорд: "рекорд: жим 100 кг x 3" или "pr: bench 100 kg x 3" ──
    lower = text.lower()
    if lower.startswith(("рекорд:","пр:","pr:","record:")):
        try:
            body = text.split(":",1)[1].strip()
            m = re.search(r"([\w\s]+?)\s+(\d+[\.,]?\d*)\s*(?:кг|kg)?\s*[xхXХ×]\s*(\d+)", body, re.I)
            if m:
                ex = m.group(1).strip(); w = float(m.group(2).replace(",",".")); r = int(m.group(3))
                db("INSERT INTO personal_records (uid,exercise,weight_kg,reps,date) VALUES (?,?,?,?,?)", (uid,ex,w,r,datetime.now().strftime("%Y-%m-%d")))
                await message.answer(t("pr_saved",lang,ex=ex,w=w,r=r))
                return
        except Exception: pass

    # ── Таймер по тексту ──────────────────────────────────────────
    if any(w in lower for w in ["таймер","timer","отдых","rest","подход","set done","ferdig"]):
        await message.answer(t("timer_title",lang), reply_markup=timer_kb(lang))
        return

    # ── Кнопки главного меню ──────────────────────────────────────
    # Строим словарь кнопок динамически по текущему языку
    menu_map = {
        t("btn_workout",lang):     "workout",
        t("btn_nutrition",lang):   "nutrition",
        t("btn_progress",lang):    "progress",
        t("btn_recovery",lang):    "recovery",
        t("btn_motivation",lang):  "motivation",
        t("btn_supplements",lang): "supplements",
        t("btn_technique",lang):   "technique",
        t("btn_kbju",lang):        "calc",
        t("btn_water",lang):       "water",
        t("btn_diary",lang):       "diary",
    }
    action = menu_map.get(text)
    if action:
        if action == "workout":
            await message.answer(t("choose_level",lang), reply_markup=workouts_kb(lang))
        elif action == "nutrition":
            await message.answer(t("nutrition_menu",lang), reply_markup=nutrition_kb(lang))
        elif action == "progress":
            await message.answer(t("progress_menu",lang), reply_markup=progress_kb(lang))
        elif action == "recovery":
            await message.answer(t("recovery_menu",lang), reply_markup=recovery_kb(lang))
        elif action == "supplements":
            await message.answer(t("supplements_menu",lang), reply_markup=supplements_kb(lang))
        elif action == "technique":
            await message.answer(t("technique_menu",lang), reply_markup=technique_kb(lang))
        elif action == "calc":
            await cmd_calc(message)
        elif action == "water":
            await cmd_water(message)
        elif action == "diary":
            await message.answer(t("diary_menu",lang), reply_markup=diary_kb(lang))
        elif action == "motivation":
            await bot.send_chat_action(message.chat.id, "typing")
            reply = await ask_max(uid, SECTION_PROMPTS["motivation"])
            await message.answer(reply)
        return

    # ── Обычный чат ───────────────────────────────────────────────
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_max(uid, text)
    for i in range(0, len(reply), 4000):
        await message.answer(reply[i:i+4000])

# ══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════════

async def main():
    db_init()
    schedule_all()
    scheduler.start()
    logger.info("💪 FitNova MAX Multilang — запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

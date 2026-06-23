"""
PolyGlotty · Переводы интерфейса
Языки интерфейса: ru, en
(Обучение всегда на английском, но объяснения на языке интерфейса)
"""

T = {

"choose_lang": {
    "ru": "Выбери язык интерфейса:",
    "en": "Choose interface language:",
},

"welcome": {
    "ru": (
        "<b>Привет, {name}!</b> 👋\n\n"
        "Я <b>ALEX</b> — твой персональный репетитор английского на базе ИИ.\n\n"
        "Занимаюсь с тобой как живой преподаватель:\n"
        "📚 Грамматика с объяснениями\n"
        "💬 Разговорная практика\n"
        "📝 Словарный запас\n"
        "✅ Проверка и исправление ошибок\n"
        "🎓 Подготовка к TOEFL\n\n"
        "<i>Сначала определим твой уровень — это займёт 2 минуты.</i>"
    ),
    "en": (
        "<b>Hey, {name}!</b> 👋\n\n"
        "I'm <b>ALEX</b> — your personal AI English tutor.\n\n"
        "I work with you like a real teacher:\n"
        "📚 Grammar with clear explanations\n"
        "💬 Speaking practice\n"
        "📝 Vocabulary building\n"
        "✅ Error correction\n"
        "🎓 TOEFL preparation\n\n"
        "<i>First, let's determine your level — it takes 2 minutes.</i>"
    ),
},

"help": {
    "ru": (
        "<b>PolyGlotty ALEX · Справка</b>\n\n"
        "<b>Обучение:</b>\n"
        "/lesson — урок по грамматике\n"
        "/vocab — словарный тренажёр\n"
        "/talk — разговорная практика\n"
        "/dictation — диктант\n"
        "/idioms — идиомы и сленг\n"
        "/writing — проверка текста\n\n"
        "<b>Тесты:</b>\n"
        "/test — тест по уровню\n"
        "/toefl — подготовка к TOEFL\n"
        "/reading — тест на чтение\n"
        "/listening — тест на аудирование\n\n"
        "<b>Прогресс:</b>\n"
        "/stats — моя статистика\n"
        "/mistakes — мои ошибки\n"
        "/level — мой уровень\n"
        "/streak — стрик занятий\n\n"
        "<b>Прочее:</b>\n"
        "/lang — сменить язык\n"
        "/reset — начать заново\n"
        "/help — эта справка\n\n"
        "📸 Отправь фото текста — ALEX проверит и объяснит\n"
        "🎤 Напиши по-английски — ALEX исправит ошибки"
    ),
    "en": (
        "<b>PolyGlotty ALEX · Help</b>\n\n"
        "<b>Learning:</b>\n"
        "/lesson — grammar lesson\n"
        "/vocab — vocabulary trainer\n"
        "/talk — speaking practice\n"
        "/dictation — dictation\n"
        "/idioms — idioms & slang\n"
        "/writing — text correction\n\n"
        "<b>Tests:</b>\n"
        "/test — level test\n"
        "/toefl — TOEFL preparation\n"
        "/reading — reading comprehension\n"
        "/listening — listening test\n\n"
        "<b>Progress:</b>\n"
        "/stats — my statistics\n"
        "/mistakes — my errors\n"
        "/level — my level\n"
        "/streak — study streak\n\n"
        "<b>Other:</b>\n"
        "/lang — change language\n"
        "/reset — start over\n"
        "/help — this help\n\n"
        "📸 Send a photo of text — ALEX will explain it\n"
        "✍️ Write in English — ALEX will correct mistakes"
    ),
},

# Кнопки главного меню
"btn_lesson":    {"ru": "📚 Урок",           "en": "📚 Lesson"},
"btn_vocab":     {"ru": "📝 Словарь",        "en": "📝 Vocabulary"},
"btn_talk":      {"ru": "💬 Разговор",       "en": "💬 Speaking"},
"btn_test":      {"ru": "✅ Тест",           "en": "✅ Test"},
"btn_toefl":     {"ru": "🎓 TOEFL",          "en": "🎓 TOEFL"},
"btn_writing":   {"ru": "✍️ Проверка",       "en": "✍️ Check writing"},
"btn_idioms":    {"ru": "🗣 Идиомы",         "en": "🗣 Idioms"},
"btn_mistakes":  {"ru": "❌ Мои ошибки",     "en": "❌ My mistakes"},
"btn_stats":     {"ru": "📊 Прогресс",       "en": "📊 Progress"},
"btn_dictation": {"ru": "🎙 Диктант",        "en": "🎙 Dictation"},

# Уровни
"choose_level": {
    "ru": "🎯 <b>Выбери свой уровень или пройди тест:</b>",
    "en": "🎯 <b>Choose your level or take a placement test:</b>",
},
"level_test_btn": {"ru": "🔍 Определить уровень тестом", "en": "🔍 Take placement test"},
"btn_back":       {"ru": "← Назад",                     "en": "← Back"},

# Статус уровня
"level_info": {
    "ru": "🎯 Твой текущий уровень: <b>{level}</b>\n\nДля смены уровня: /level",
    "en": "🎯 Your current level: <b>{level}</b>\n\nTo change level: /level",
},

# Словарный тренажёр
"vocab_menu": {
    "ru": "📝 <b>Словарный тренажёр:</b>",
    "en": "📝 <b>Vocabulary trainer:</b>",
},
"vocab_new":        {"ru": "🆕 Новые слова",         "en": "🆕 New words"},
"vocab_review":     {"ru": "🔄 Повторение",          "en": "🔄 Review"},
"vocab_topic":      {"ru": "📂 По теме",             "en": "📂 By topic"},
"vocab_flashcards": {"ru": "🃏 Флэш-карточки",      "en": "🃏 Flashcards"},

# TOEFL меню
"toefl_menu": {
    "ru": (
        "🎓 <b>Подготовка к TOEFL</b>\n\n"
        "TOEFL iBT состоит из 4 секций:\n"
        "📖 Reading — 54-72 мин\n"
        "🎧 Listening — 41-57 мин\n"
        "🗣 Speaking — 17 мин\n"
        "✍️ Writing — 50 мин\n\n"
        "<i>Выбери секцию для тренировки:</i>"
    ),
    "en": (
        "🎓 <b>TOEFL Preparation</b>\n\n"
        "TOEFL iBT has 4 sections:\n"
        "📖 Reading — 54-72 min\n"
        "🎧 Listening — 41-57 min\n"
        "🗣 Speaking — 17 min\n"
        "✍️ Writing — 50 min\n\n"
        "<i>Choose a section to practice:</i>"
    ),
},
"toefl_reading":   {"ru": "📖 Reading",            "en": "📖 Reading"},
"toefl_listening": {"ru": "🎧 Listening",           "en": "🎧 Listening"},
"toefl_speaking":  {"ru": "🗣 Speaking",            "en": "🗣 Speaking"},
"toefl_writing":   {"ru": "✍️ Writing",             "en": "✍️ Writing"},
"toefl_full":      {"ru": "🏆 Полный мини-тест",    "en": "🏆 Full mini-test"},
"toefl_strategy":  {"ru": "💡 Стратегии и советы",  "en": "💡 Tips & strategies"},
"toefl_score":     {"ru": "📊 Мои баллы TOEFL",     "en": "📊 My TOEFL scores"},

# Тест
"test_menu": {
    "ru": "✅ <b>Тесты:</b>",
    "en": "✅ <b>Tests:</b>",
},
"test_grammar":    {"ru": "📐 Грамматика",          "en": "📐 Grammar"},
"test_vocab":      {"ru": "📝 Лексика",             "en": "📝 Vocabulary"},
"test_reading":    {"ru": "📖 Чтение",              "en": "📖 Reading"},
"test_mixed":      {"ru": "🎲 Смешанный",           "en": "🎲 Mixed"},
"test_placement":  {"ru": "🔍 Определение уровня",  "en": "🔍 Placement test"},

# Разговор
"talk_menu": {
    "ru": "💬 <b>Разговорная практика — выбери тему:</b>",
    "en": "💬 <b>Speaking practice — choose a topic:</b>",
},
"talk_daily":    {"ru": "☀️ Повседневная жизнь",   "en": "☀️ Daily life"},
"talk_travel":   {"ru": "✈️ Путешествия",          "en": "✈️ Travel"},
"talk_work":     {"ru": "💼 Работа и карьера",     "en": "💼 Work & career"},
"talk_culture":  {"ru": "🎭 Культура",             "en": "🎭 Culture"},
"talk_debate":   {"ru": "🗣 Дебаты",              "en": "🗣 Debate"},
"talk_business": {"ru": "🤝 Бизнес English",      "en": "🤝 Business English"},
"talk_free":     {"ru": "💭 Свободная беседа",    "en": "💭 Free conversation"},

# Урок грамматики
"lesson_menu": {
    "ru": "📚 <b>Уроки грамматики — выбери тему:</b>",
    "en": "📚 <b>Grammar lessons — choose a topic:</b>",
},
"lesson_tenses":     {"ru": "⏰ Времена глагола",       "en": "⏰ Verb tenses"},
"lesson_conditionals":{"ru":"🔀 Условные предложения",  "en": "🔀 Conditionals"},
"lesson_modal":      {"ru": "💭 Модальные глаголы",     "en": "💭 Modal verbs"},
"lesson_passive":    {"ru": "🔄 Пассивный залог",       "en": "🔄 Passive voice"},
"lesson_articles":   {"ru": "📌 Артикли",               "en": "📌 Articles"},
"lesson_prepositions":{"ru":"📍 Предлоги",              "en": "📍 Prepositions"},
"lesson_phrasal":    {"ru": "🔗 Фразовые глаголы",      "en": "🔗 Phrasal verbs"},
"lesson_reported":   {"ru": "💬 Косвенная речь",        "en": "💬 Reported speech"},

# Прогресс
"stats_title": {
    "ru": (
        "📊 <b>Твой прогресс</b>\n\n"
        "🎯 Уровень: <b>{level}</b>\n"
        "📅 Занятий всего: <b>{sessions}</b>\n"
        "🔥 Стрик: <b>{streak} дней</b>\n"
        "✅ Тестов пройдено: <b>{tests}</b>\n"
        "📝 Слов изучено: <b>{words}</b>\n"
        "❌ Ошибок исправлено: <b>{errors}</b>\n"
        "🎓 TOEFL сессий: <b>{toefl}</b>"
    ),
    "en": (
        "📊 <b>Your Progress</b>\n\n"
        "🎯 Level: <b>{level}</b>\n"
        "📅 Total sessions: <b>{sessions}</b>\n"
        "🔥 Streak: <b>{streak} days</b>\n"
        "✅ Tests passed: <b>{tests}</b>\n"
        "📝 Words learned: <b>{words}</b>\n"
        "❌ Errors corrected: <b>{errors}</b>\n"
        "🎓 TOEFL sessions: <b>{toefl}</b>"
    ),
},

# Сброс, разное
"reset_done": {
    "ru": "🔄 <b>Данные сброшены.</b> Начинаем заново!",
    "en": "🔄 <b>Reset done.</b> Starting fresh!",
},
"writing_prompt": {
    "ru": "✍️ Напиши или отправь текст на английском — ALEX проверит ошибки и объяснит каждую:",
    "en": "✍️ Write or send your English text — ALEX will correct errors and explain each one:",
},
"dictation_ready": {
    "ru": "🎙 <b>Диктант</b>\n\nALEX продиктует фразу — напиши её точно как слышишь.\n\n<i>Начинаем?</i>",
    "en": "🎙 <b>Dictation</b>\n\nALEX will give you a phrase — write it exactly as you hear it.\n\n<i>Ready?</i>",
},
"photo_received": {
    "ru": "📸 Анализирую текст на фото...",
    "en": "📸 Analyzing text in the photo...",
},
"cmd_footer": {
    "ru": "/lesson · /vocab · /test · /toefl · /talk · /stats · /help",
    "en": "/lesson · /vocab · /test · /toefl · /talk · /stats · /help",
},
"input_placeholder": {
    "ru": "Напиши по-английски — ALEX исправит...",
    "en": "Write in English — ALEX will correct...",
},
"mistakes_title": {
    "ru": "❌ <b>Твои частые ошибки:</b>\n\n",
    "en": "❌ <b>Your frequent mistakes:</b>\n\n",
},
"mistakes_empty": {
    "ru": "✅ Ошибок пока не записано. Напиши что-нибудь по-английски!",
    "en": "✅ No mistakes recorded yet. Try writing something in English!",
},
"streak_msg": {
    "ru": "🔥 <b>Стрик: {n} дней подряд!</b>",
    "en": "🔥 <b>Streak: {n} days in a row!</b>",
},

}


def t(key: str, lang: str, **kwargs) -> str:
    entry = T.get(key, {})
    text  = entry.get(lang) or entry.get("ru") or ""
    if kwargs:
        try: text = text.format(**kwargs)
        except Exception: pass
    return text

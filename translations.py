"""
FitNova · Переводы
Поддерживаемые языки: ru, en, no
"""

LANGS = {
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English",
    "no": "🇳🇴 Norsk",
}

T = {

# ══════════════════════════════════════════════════════════════════
#  ВЫБОР ЯЗЫКА
# ══════════════════════════════════════════════════════════════════

"choose_lang": {
    "ru": "Выбери язык:",
    "en": "Choose your language:",
    "no": "Velg språk:",
},

# ══════════════════════════════════════════════════════════════════
#  ПРИВЕТСТВИЕ И ПОМОЩЬ
# ══════════════════════════════════════════════════════════════════

"welcome": {
    "ru": (
        "<b>Привет, {name}!</b> 👋\n\n"
        "Я <b>MAX</b> — твой персональный ИИ-тренер.\n\n"
        "Составляю программы, считаю КБЖУ, анализирую фото еды, "
        "веду дневник тренировок и мотивирую каждый день.\n\n"
        "<i>Просто пиши как живому тренеру — разберёмся.</i>"
    ),
    "en": (
        "<b>Hey, {name}!</b> 👋\n\n"
        "I'm <b>MAX</b> — your personal AI trainer.\n\n"
        "I build workout programs, calculate macros, analyze food photos, "
        "keep your training journal and keep you motivated every day.\n\n"
        "<i>Just write like you'd talk to a real trainer — we'll figure it out.</i>"
    ),
    "no": (
        "<b>Hei, {name}!</b> 👋\n\n"
        "Jeg er <b>MAX</b> — din personlige AI-trener.\n\n"
        "Jeg lager treningsprogrammer, beregner makroer, analyserer matbilder, "
        "fører treningsdagbok og motiverer deg hver dag.\n\n"
        "<i>Bare skriv som til en ekte trener — vi løser det sammen.</i>"
    ),
},

"help": {
    "ru": (
        "<b>FitNova MAX · Справка</b>\n\n"
        "<b>Основные:</b>\n"
        "/start — главное меню\n"
        "/goal — выбрать цель\n"
        "/profile — заполнить профиль\n"
        "/lang — сменить язык\n\n"
        "<b>Тренировки:</b>\n"
        "/workout — записать тренировку\n"
        "/generate — тренировка на сегодня\n"
        "/pr — личные рекорды\n\n"
        "<b>Питание:</b>\n"
        "/calc — калькулятор КБЖУ\n"
        "/water — счётчик воды\n"
        "/week — план питания на неделю\n\n"
        "<b>Прогресс:</b>\n"
        "/stats — моя статистика\n"
        "/report — отчёт за неделю\n\n"
        "<b>Прочее:</b>\n"
        "/remind — напоминания\n"
        "/invite — пригласить друга\n"
        "/export — экспорт данных\n"
        "/reset — очистить диалог\n\n"
        "<b>📸 Фото:</b>\n"
        "Еда → MAX посчитает КБЖУ\n"
        "Тело → MAX оценит прогресс"
    ),
    "en": (
        "<b>FitNova MAX · Help</b>\n\n"
        "<b>Main:</b>\n"
        "/start — main menu\n"
        "/goal — set goal\n"
        "/profile — fill profile\n"
        "/lang — change language\n\n"
        "<b>Workouts:</b>\n"
        "/workout — log workout\n"
        "/generate — today's workout\n"
        "/pr — personal records\n\n"
        "<b>Nutrition:</b>\n"
        "/calc — macro calculator\n"
        "/water — water tracker\n"
        "/week — weekly meal plan\n\n"
        "<b>Progress:</b>\n"
        "/stats — my statistics\n"
        "/report — weekly report\n\n"
        "<b>Other:</b>\n"
        "/remind — reminders\n"
        "/invite — invite a friend\n"
        "/export — export data\n"
        "/reset — clear dialogue\n\n"
        "<b>📸 Photos:</b>\n"
        "Food → MAX calculates macros\n"
        "Body → MAX evaluates progress"
    ),
    "no": (
        "<b>FitNova MAX · Hjelp</b>\n\n"
        "<b>Hoved:</b>\n"
        "/start — hovedmeny\n"
        "/goal — velg mål\n"
        "/profile — fyll profil\n"
        "/lang — bytt språk\n\n"
        "<b>Trening:</b>\n"
        "/workout — logg trening\n"
        "/generate — dagens trening\n"
        "/pr — personlige rekorder\n\n"
        "<b>Ernæring:</b>\n"
        "/calc — makrokalkulator\n"
        "/water — vannteller\n"
        "/week — ukentlig kostplan\n\n"
        "<b>Fremgang:</b>\n"
        "/stats — min statistikk\n"
        "/report — ukentlig rapport\n\n"
        "<b>Annet:</b>\n"
        "/remind — påminnelser\n"
        "/invite — inviter en venn\n"
        "/export — eksporter data\n"
        "/reset — tøm samtale\n\n"
        "<b>📸 Bilder:</b>\n"
        "Mat → MAX beregner kalorier\n"
        "Kropp → MAX vurderer fremgang"
    ),
},

# ══════════════════════════════════════════════════════════════════
#  ГЛАВНОЕ МЕНЮ — КНОПКИ
# ══════════════════════════════════════════════════════════════════

"btn_workout":     {"ru": "💪 Тренировка",     "en": "💪 Workout",       "no": "💪 Trening"},
"btn_nutrition":   {"ru": "🥗 Питание",         "en": "🥗 Nutrition",     "no": "🥗 Ernæring"},
"btn_progress":    {"ru": "📈 Прогресс",         "en": "📈 Progress",      "no": "📈 Fremgang"},
"btn_recovery":    {"ru": "💤 Восстановление",   "en": "💤 Recovery",      "no": "💤 Restitusjon"},
"btn_motivation":  {"ru": "🔥 Мотивация",        "en": "🔥 Motivation",    "no": "🔥 Motivasjon"},
"btn_supplements": {"ru": "🧪 Спортпит",         "en": "🧪 Supplements",   "no": "🧪 Kosttilskudd"},
"btn_technique":   {"ru": "⚡️ Техника",          "en": "⚡️ Technique",    "no": "⚡️ Teknikk"},
"btn_kbju":        {"ru": "🧮 КБЖУ",             "en": "🧮 Macros",        "no": "🧮 Makroer"},
"btn_water":       {"ru": "💧 Вода",             "en": "💧 Water",         "no": "💧 Vann"},
"btn_diary":       {"ru": "📓 Дневник",          "en": "📓 Diary",         "no": "📓 Dagbok"},

# ══════════════════════════════════════════════════════════════════
#  ЦЕЛИ
# ══════════════════════════════════════════════════════════════════

"choose_goal": {
    "ru": "🎯 <b>Выбери свою цель:</b>",
    "en": "🎯 <b>Choose your goal:</b>",
    "no": "🎯 <b>Velg ditt mål:</b>",
},
"goal_saved": {
    "ru": "Цель сохранена!",
    "en": "Goal saved!",
    "no": "Mål lagret!",
},

"goal_mass":    {"ru": "🏋️  Набор мышечной массы",       "en": "🏋️  Build muscle mass",        "no": "🏋️  Bygge muskelmasse"},
"goal_cut":     {"ru": "🔥  Похудение и сушка",           "en": "🔥  Weight loss & cutting",     "no": "🔥  Vekttap og cutting"},
"goal_tone":    {"ru": "💪  Рельеф и тонус",              "en": "💪  Tone & definition",         "no": "💪  Tone og definisjon"},
"goal_cardio":  {"ru": "🏃  Выносливость",                "en": "🏃  Endurance",                 "no": "🏃  Utholdenhet"},
"goal_health":  {"ru": "🌿  Общее здоровье",              "en": "🌿  General health",            "no": "🌿  Generell helse"},
"goal_home":    {"ru": "🏠  Тренировки дома",             "en": "🏠  Home workouts",             "no": "🏠  Hjemmetrening"},
"goal_rehab":   {"ru": "🩺  Восстановление после травмы", "en": "🩺  Injury rehabilitation",     "no": "🩺  Skaderehabilitering"},

# ══════════════════════════════════════════════════════════════════
#  УРОВНИ ПОДГОТОВКИ
# ══════════════════════════════════════════════════════════════════

"choose_level": {
    "ru": "💪 <b>Выбери уровень подготовки:</b>",
    "en": "💪 <b>Choose your fitness level:</b>",
    "no": "💪 <b>Velg ditt treningsnivå:</b>",
},
"w_beginner":     {"ru": "🔰 Новичок",           "en": "🔰 Beginner",          "no": "🔰 Nybegynner"},
"w_intermediate": {"ru": "💪 Средний уровень",    "en": "💪 Intermediate",      "no": "💪 Mellomtrinnet"},
"w_advanced":     {"ru": "🏆 Продвинутый",        "en": "🏆 Advanced",          "no": "🏆 Avansert"},
"w_home":         {"ru": "🏠 Дома без железа",    "en": "🏠 Home, no equipment","no": "🏠 Hjemme uten utstyr"},
"w_quick":        {"ru": "⏱  30 минут/день",     "en": "⏱  30 min/day",       "no": "⏱  30 min/dag"},
"btn_back":       {"ru": "← Назад",              "en": "← Back",              "no": "← Tilbake"},

# ══════════════════════════════════════════════════════════════════
#  ПИТАНИЕ
# ══════════════════════════════════════════════════════════════════

"nutrition_menu": {
    "ru": "🥗 <b>Питание:</b>",
    "en": "🥗 <b>Nutrition:</b>",
    "no": "🥗 <b>Ernæring:</b>",
},
"n_calc":      {"ru": "🧮 Рассчитать КБЖУ",          "en": "🧮 Calculate macros",       "no": "🧮 Beregn makroer"},
"n_menu":      {"ru": "🍽  Меню на день",             "en": "🍽  Daily meal plan",       "no": "🍽  Dagsplan for mat"},
"n_week":      {"ru": "📅 Недельный план питания",    "en": "📅 Weekly meal plan",       "no": "📅 Ukentlig kostplan"},
"n_timing":    {"ru": "⏰ Тайминг питания",           "en": "⏰ Meal timing",            "no": "⏰ Mattiming"},
"n_grocery":   {"ru": "🛒 Список продуктов",          "en": "🛒 Grocery list",           "no": "🛒 Handleliste"},
"n_cheatmeal": {"ru": "🍕 Читмил",                    "en": "🍕 Cheat meal",             "no": "🍕 Jutemåltid"},
"n_photo":     {"ru": "📸 Анализ фото еды",           "en": "📸 Analyze food photo",     "no": "📸 Analyser matbilde"},

# ══════════════════════════════════════════════════════════════════
#  ПРОГРЕСС, ВОССТАНОВЛЕНИЕ, СПОРТПИТ, ТЕХНИКА
# ══════════════════════════════════════════════════════════════════

"progress_menu": {
    "ru": "📈 <b>Прогресс:</b>",
    "en": "📈 <b>Progress:</b>",
    "no": "📈 <b>Fremgang:</b>",
},
"p_plateau":  {"ru": "📉 Вес стоит — что делать?",  "en": "📉 Weight plateau",          "no": "📉 Vektplatå"},
"p_strength": {"ru": "💪 Силовое плато",             "en": "💪 Strength plateau",        "no": "💪 Styrkeplatå"},
"p_measure":  {"ru": "📏 Как замерять прогресс?",    "en": "📏 How to measure progress", "no": "📏 Hvordan måle fremgang"},
"p_timeline": {"ru": "📸 Когда ждать результат?",    "en": "📸 When to expect results",  "no": "📸 Når forvente resultater"},

"recovery_menu": {
    "ru": "💤 <b>Восстановление:</b>",
    "en": "💤 <b>Recovery:</b>",
    "no": "💤 <b>Restitusjon:</b>",
},
"r_sleep":     {"ru": "😴 Сон и гормоны",            "en": "😴 Sleep & hormones",        "no": "😴 Søvn og hormoner"},
"r_soreness":  {"ru": "🦵 Боль в мышцах — норма?",   "en": "🦵 Muscle soreness — normal?","no": "🦵 Muskelsmerte — normalt?"},
"r_overtrain": {"ru": "⚠️ Признаки перетрена",        "en": "⚠️ Overtraining signs",      "no": "⚠️ Overtreningssymptomer"},
"r_mobility":  {"ru": "🧘 Мобильность и растяжка",   "en": "🧘 Mobility & stretching",   "no": "🧘 Mobilitet og tøying"},

"supplements_menu": {
    "ru": "🧪 <b>Спортпит:</b>",
    "en": "🧪 <b>Supplements:</b>",
    "no": "🧪 <b>Kosttilskudd:</b>",
},
"s_protein":    {"ru": "🥛 Протеин",       "en": "🥛 Protein",       "no": "🥛 Protein"},
"s_creatine":   {"ru": "⚡️ Креатин",      "en": "⚡️ Creatine",     "no": "⚡️ Kreatin"},
"s_preworkout": {"ru": "☕️ Предтреники",   "en": "☕️ Pre-workouts",  "no": "☕️ Pre-workout"},
"s_vitamins":   {"ru": "🌞 Витамины",      "en": "🌞 Vitamins",      "no": "🌞 Vitaminer"},

"technique_menu": {
    "ru": "⚡️ <b>Техника:</b>",
    "en": "⚡️ <b>Technique:</b>",
    "no": "⚡️ <b>Teknikk:</b>",
},
"t_squat":    {"ru": "🏋️ Присед",        "en": "🏋️ Squat",         "no": "🏋️ Knebøy"},
"t_deadlift": {"ru": "🏋️ Становая тяга", "en": "🏋️ Deadlift",      "no": "🏋️ Markløft"},
"t_bench":    {"ru": "🏋️ Жим лёжа",      "en": "🏋️ Bench press",   "no": "🏋️ Benkpress"},
"t_pullup":   {"ru": "🏋️ Подтягивания",  "en": "🏋️ Pull-ups",      "no": "🏋️ Chins"},
"t_ohp":      {"ru": "🏋️ Жим стоя",      "en": "🏋️ Overhead press","no": "🏋️ Skulderpress"},

# ══════════════════════════════════════════════════════════════════
#  ВОДА
# ══════════════════════════════════════════════════════════════════

"water_title": {
    "ru": "💧 <b>Вода сегодня</b>",
    "en": "💧 <b>Water today</b>",
    "no": "💧 <b>Vann i dag</b>",
},
"water_of": {
    "ru": "Выпито: <b>{cups}</b> из <b>{goal}</b> стаканов ({pct}%)",
    "en": "Consumed: <b>{cups}</b> of <b>{goal}</b> glasses ({pct}%)",
    "no": "Drukket: <b>{cups}</b> av <b>{goal}</b> glass ({pct}%)",
},
"water_add1":     {"ru": "💧 +1 стакан",    "en": "💧 +1 glass",    "no": "💧 +1 glass"},
"water_add2":     {"ru": "💧💧 +2 стакана",  "en": "💧💧 +2 glasses", "no": "💧💧 +2 glass"},
"water_status":   {"ru": "📊 Статус",       "en": "📊 Status",      "no": "📊 Status"},
"water_set_goal": {"ru": "🎯 Изменить цель","en": "🎯 Change goal", "no": "🎯 Endre mål"},
"water_added": {
    "ru": "✅ +{n} стакан! Итого: {cups}",
    "en": "✅ +{n} glass! Total: {cups}",
    "no": "✅ +{n} glass! Totalt: {cups}",
},
"water_goal_prompt": {
    "ru": "Напиши сколько стаканов воды хочешь выпивать в день (например: <code>8</code>):",
    "en": "Write how many glasses of water you want to drink per day (e.g. <code>8</code>):",
    "no": "Skriv hvor mange glass vann du vil drikke per dag (f.eks. <code>8</code>):",
},
"water_goal_saved": {
    "ru": "✅ Цель по воде: <b>{goal} стаканов/день</b> 💧",
    "en": "✅ Water goal: <b>{goal} glasses/day</b> 💧",
    "no": "✅ Vannmål: <b>{goal} glass/dag</b> 💧",
},

# ══════════════════════════════════════════════════════════════════
#  ТАЙМЕР
# ══════════════════════════════════════════════════════════════════

"timer_title": {
    "ru": "⏱ <b>Таймер отдыха:</b>",
    "en": "⏱ <b>Rest timer:</b>",
    "no": "⏱ <b>Hviletimer:</b>",
},
"timer_started": {
    "ru": "⏱ <b>Отдых: {label}</b>\n<i>Расслабься...</i>",
    "en": "⏱ <b>Rest: {label}</b>\n<i>Relax...</i>",
    "no": "⏱ <b>Hvile: {label}</b>\n<i>Slapp av...</i>",
},
"timer_done": {
    "ru": "🔔 <b>Время! Следующий подход!</b>",
    "en": "🔔 <b>Time! Next set!</b>",
    "no": "🔔 <b>Tid! Neste sett!</b>",
},

# ══════════════════════════════════════════════════════════════════
#  ДНЕВНИК
# ══════════════════════════════════════════════════════════════════

"diary_menu": {
    "ru": "📓 <b>Дневник тренировок:</b>",
    "en": "📓 <b>Training diary:</b>",
    "no": "📓 <b>Treningsdagbok:</b>",
},
"diary_add":      {"ru": "✍️ Записать тренировку",      "en": "✍️ Log workout",           "no": "✍️ Logg trening"},
"diary_list":     {"ru": "📋 Последние тренировки",     "en": "📋 Recent workouts",       "no": "📋 Siste treninger"},
"diary_generate": {"ru": "🤖 Сгенерировать тренировку", "en": "🤖 Generate workout",      "no": "🤖 Generer trening"},
"diary_pr":       {"ru": "🏆 Личные рекорды",           "en": "🏆 Personal records",      "no": "🏆 Personlige rekorder"},
"diary_report":   {"ru": "📊 Отчёт за неделю",          "en": "📊 Weekly report",         "no": "📊 Ukentlig rapport"},

"workout_prompt": {
    "ru": "✍️ <b>Запись тренировки</b>\n\nОпиши что сделал — упражнения, веса, подходы.",
    "en": "✍️ <b>Log workout</b>\n\nDescribe what you did — exercises, weights, sets.",
    "no": "✍️ <b>Logg trening</b>\n\nBeskriv hva du gjorde — øvelser, vekter, sett.",
},
"workout_saved": {
    "ru": "✅ <b>Тренировка записана!</b>",
    "en": "✅ <b>Workout logged!</b>",
    "no": "✅ <b>Trening logget!</b>",
},
"streak_msg": {
    "ru": "🔥 <b>Стрик: {n} дней подряд!</b>",
    "en": "🔥 <b>Streak: {n} days in a row!</b>",
    "no": "🔥 <b>Streak: {n} dager på rad!</b>",
},
"diary_empty": {
    "ru": "📓 Дневник пуст. Напиши /workout чтобы записать тренировку.",
    "en": "📓 Diary is empty. Use /workout to log a workout.",
    "no": "📓 Dagboken er tom. Bruk /workout for å logge trening.",
},
"diary_recent": {
    "ru": "📓 <b>Последние тренировки:</b>\n\n",
    "en": "📓 <b>Recent workouts:</b>\n\n",
    "no": "📓 <b>Siste treninger:</b>\n\n",
},

# ══════════════════════════════════════════════════════════════════
#  ПРОФИЛЬ
# ══════════════════════════════════════════════════════════════════

"profile_prompt": {
    "ru": (
        "<b>Профиль</b> 👤\n\n"
        "Напиши данные:\n<code>пол, возраст, рост, вес, активность</code>\n\n"
        "<b>Пример:</b>\n<code>мужчина, 23, 178, 75, средняя</code>\n\n"
        "<b>Активность:</b> минимальная / низкая / средняя / высокая / очень высокая"
    ),
    "en": (
        "<b>Profile</b> 👤\n\n"
        "Write your data:\n<code>gender, age, height, weight, activity</code>\n\n"
        "<b>Example:</b>\n<code>male, 23, 178, 75, moderate</code>\n\n"
        "<b>Activity:</b> minimal / low / moderate / high / very high"
    ),
    "no": (
        "<b>Profil</b> 👤\n\n"
        "Skriv dataene dine:\n<code>kjønn, alder, høyde, vekt, aktivitet</code>\n\n"
        "<b>Eksempel:</b>\n<code>mann, 23, 178, 75, moderat</code>\n\n"
        "<b>Aktivitet:</b> minimal / lav / moderat / høy / veldig høy"
    ),
},
"profile_saved": {
    "ru": (
        "✅ <b>Профиль сохранён!</b>\n\n"
        "Пол: <b>{gender}</b> · Возраст: <b>{age} лет</b>\n"
        "Рост: <b>{height} см</b> · Вес: <b>{weight} кг</b>\n"
        "Активность: <b>{activity}</b>\n\n"
        "<i>MAX теперь учитывает твои данные в расчётах.</i>"
    ),
    "en": (
        "✅ <b>Profile saved!</b>\n\n"
        "Gender: <b>{gender}</b> · Age: <b>{age}</b>\n"
        "Height: <b>{height} cm</b> · Weight: <b>{weight} kg</b>\n"
        "Activity: <b>{activity}</b>\n\n"
        "<i>MAX will now use your data for calculations.</i>"
    ),
    "no": (
        "✅ <b>Profil lagret!</b>\n\n"
        "Kjønn: <b>{gender}</b> · Alder: <b>{age}</b>\n"
        "Høyde: <b>{height} cm</b> · Vekt: <b>{weight} kg</b>\n"
        "Aktivitet: <b>{activity}</b>\n\n"
        "<i>MAX vil nå bruke dataene dine i beregninger.</i>"
    ),
},
"profile_error": {
    "ru": "⚠️ Не смог распознать. Попробуй:\n<code>мужчина, 23, 178, 75, средняя</code>",
    "en": "⚠️ Couldn't parse. Try:\n<code>male, 23, 178, 75, moderate</code>",
    "no": "⚠️ Kunne ikke lese. Prøv:\n<code>mann, 23, 178, 75, moderat</code>",
},

# ══════════════════════════════════════════════════════════════════
#  СТАТИСТИКА
# ══════════════════════════════════════════════════════════════════

"stats": {
    "ru": (
        "📊 <b>Статистика — {name}</b>\n\n"
        "🎯 Цель: <b>{goal}</b>\n"
        "💪 Уровень: <b>{level}</b>\n\n"
        "🏋️ Тренировок всего: <b>{total}</b>\n"
        "📅 На этой неделе: <b>{week}</b>\n"
        "🔥 Стрик: {streak}\n"
        "💧 Воды сегодня: <b>{water} стаканов</b>\n"
        "👥 Приглашено: <b>{refs}</b>"
    ),
    "en": (
        "📊 <b>Stats — {name}</b>\n\n"
        "🎯 Goal: <b>{goal}</b>\n"
        "💪 Level: <b>{level}</b>\n\n"
        "🏋️ Total workouts: <b>{total}</b>\n"
        "📅 This week: <b>{week}</b>\n"
        "🔥 Streak: {streak}\n"
        "💧 Water today: <b>{water} glasses</b>\n"
        "👥 Referred: <b>{refs}</b>"
    ),
    "no": (
        "📊 <b>Statistikk — {name}</b>\n\n"
        "🎯 Mål: <b>{goal}</b>\n"
        "💪 Nivå: <b>{level}</b>\n\n"
        "🏋️ Treninger totalt: <b>{total}</b>\n"
        "📅 Denne uken: <b>{week}</b>\n"
        "🔥 Streak: {streak}\n"
        "💧 Vann i dag: <b>{water} glass</b>\n"
        "👥 Inviterte: <b>{refs}</b>"
    ),
},
"streak_active": {
    "ru": "🔥 <b>{n} дней подряд!</b>",
    "en": "🔥 <b>{n} days in a row!</b>",
    "no": "🔥 <b>{n} dager på rad!</b>",
},
"streak_start": {
    "ru": "Начни стрик сегодня!",
    "en": "Start your streak today!",
    "no": "Start streaken din i dag!",
},

# ══════════════════════════════════════════════════════════════════
#  ЛИЧНЫЕ РЕКОРДЫ
# ══════════════════════════════════════════════════════════════════

"pr_empty": {
    "ru": "🏆 <b>Личные рекорды пусты.</b>\n\nДобавь:\n<code>рекорд: жим лёжа 100 кг x 3</code>",
    "en": "🏆 <b>No personal records yet.</b>\n\nAdd one:\n<code>pr: bench press 100 kg x 3</code>",
    "no": "🏆 <b>Ingen personlige rekorder ennå.</b>\n\nLegg til:\n<code>pr: benkpress 100 kg x 3</code>",
},
"pr_title": {
    "ru": "🏆 <b>Твои личные рекорды:</b>\n\n",
    "en": "🏆 <b>Your personal records:</b>\n\n",
    "no": "🏆 <b>Dine personlige rekorder:</b>\n\n",
},
"pr_saved": {
    "ru": "🏆 <b>Рекорд сохранён!</b>\n\n💪 {ex}: <b>{w} кг × {r} повт.</b>\n\n<i>/pr — все рекорды</i>",
    "en": "🏆 <b>Record saved!</b>\n\n💪 {ex}: <b>{w} kg × {r} reps</b>\n\n<i>/pr — all records</i>",
    "no": "🏆 <b>Rekord lagret!</b>\n\n💪 {ex}: <b>{w} kg × {r} reps</b>\n\n<i>/pr — alle rekorder</i>",
},
"pr_hint": {
    "ru": "\n<i>Добавить: <code>рекорд: жим лёжа 100 кг x 5</code></i>",
    "en": "\n<i>Add: <code>pr: bench press 100 kg x 5</code></i>",
    "no": "\n<i>Legg til: <code>pr: benkpress 100 kg x 5</code></i>",
},

# ══════════════════════════════════════════════════════════════════
#  НАПОМИНАНИЯ
# ══════════════════════════════════════════════════════════════════

"remind_title": {
    "ru": "⏰ <b>Напоминания о тренировке</b>\n\nВыбери время:",
    "en": "⏰ <b>Workout reminders</b>\n\nChoose a time:",
    "no": "⏰ <b>Treningspåminnelser</b>\n\nVelg et tidspunkt:",
},
"remind_saved": {
    "ru": "✅ <b>Напоминание в {t}</b>\n\nБуду писать каждый день. 💪",
    "en": "✅ <b>Reminder set for {t}</b>\n\nI'll message you every day. 💪",
    "no": "✅ <b>Påminnelse satt til {t}</b>\n\nJeg sender deg melding hver dag. 💪",
},
"remind_off_btn": {"ru": "❌ Отключить", "en": "❌ Disable",  "no": "❌ Deaktiver"},
"remind_off_msg": {
    "ru": "❌ <b>Напоминания отключены.</b>",
    "en": "❌ <b>Reminders disabled.</b>",
    "no": "❌ <b>Påminnelser deaktivert.</b>",
},

# ══════════════════════════════════════════════════════════════════
#  ФОТО
# ══════════════════════════════════════════════════════════════════

"photo_received": {
    "ru": "📸 <b>Фото получено!</b>\n\nЧто мне с ним сделать?",
    "en": "📸 <b>Photo received!</b>\n\nWhat should I do with it?",
    "no": "📸 <b>Bilde mottatt!</b>\n\nHva skal jeg gjøre med det?",
},
"photo_food_btn": {
    "ru": "🍽 Это еда — посчитай КБЖУ",
    "en": "🍽 It's food — calculate macros",
    "no": "🍽 Det er mat — beregn kalorier",
},
"photo_body_btn": {
    "ru": "💪 Это фото тела — оцени прогресс",
    "en": "💪 It's a body photo — evaluate progress",
    "no": "💪 Det er et kroppsbilde — vurder fremgang",
},
"photo_analyzing": {
    "ru": "Анализирую...",
    "en": "Analyzing...",
    "no": "Analyserer...",
},
"photo_send_food": {
    "ru": "📸 Отправь фото еды — MAX посчитает примерный КБЖУ.",
    "en": "📸 Send a food photo — MAX will estimate the macros.",
    "no": "📸 Send et matbilde — MAX beregner kaloriene.",
},

# ══════════════════════════════════════════════════════════════════
#  ИНВАЙТ И ЭКСПОРТ
# ══════════════════════════════════════════════════════════════════

"invite": {
    "ru": (
        "👥 <b>Пригласи друга!</b>\n\n"
        "Твоя личная ссылка:\n<code>{link}</code>\n\n"
        "Приглашено: <b>{refs} чел.</b>"
    ),
    "en": (
        "👥 <b>Invite a friend!</b>\n\n"
        "Your personal link:\n<code>{link}</code>\n\n"
        "Referred: <b>{refs} people</b>"
    ),
    "no": (
        "👥 <b>Inviter en venn!</b>\n\n"
        "Din personlige lenke:\n<code>{link}</code>\n\n"
        "Inviterte: <b>{refs} personer</b>"
    ),
},
"referral_notify": {
    "ru": "🎉 <b>{name}</b> зарегистрировался по твоей ссылке!\n/invite — посмотреть статистику",
    "en": "🎉 <b>{name}</b> joined using your link!\n/invite — view stats",
    "no": "🎉 <b>{name}</b> registrerte seg via lenken din!\n/invite — se statistikk",
},
"export_caption": {
    "ru": "📄 <b>Твои данные из FitNova</b>",
    "en": "📄 <b>Your FitNova data</b>",
    "no": "📄 <b>Dine FitNova-data</b>",
},

# ══════════════════════════════════════════════════════════════════
#  СБРОС И РАЗНОЕ
# ══════════════════════════════════════════════════════════════════

"reset_done": {
    "ru": "🔄 <b>Диалог очищен.</b> Начинаем заново!",
    "en": "🔄 <b>Dialogue cleared.</b> Starting fresh!",
    "no": "🔄 <b>Samtalen er tømt.</b> Starter på nytt!",
},
"back_menu": {
    "ru": "Главное меню 👇",
    "en": "Main menu 👇",
    "no": "Hovedmeny 👇",
},
"input_placeholder": {
    "ru": "Напиши или отправь фото еды...",
    "en": "Write or send a food photo...",
    "no": "Skriv eller send et matbilde...",
},
"cmd_footer": {
    "ru": "/goal · /calc · /workout · /water · /stats · /pr · /help",
    "en": "/goal · /calc · /workout · /water · /stats · /pr · /help",
    "no": "/goal · /calc · /workout · /water · /stats · /pr · /help",
},

# ══════════════════════════════════════════════════════════════════
#  СИСТЕМНЫЙ ПРОМПТ ДЛЯ MAX — по языку
# ══════════════════════════════════════════════════════════════════

"max_lang_instruction": {
    "ru": "Отвечай ТОЛЬКО на русском языке.",
    "en": "Always respond in English only.",
    "no": "Svar KUN på norsk.",
},

}


def t(key: str, lang: str, **kwargs) -> str:
    """Получить перевод по ключу и языку."""
    entry = T.get(key, {})
    text  = entry.get(lang) or entry.get("ru") or ""
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text

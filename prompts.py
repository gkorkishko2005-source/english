"""
LinguaMax · Промпты и AI-логика
"""

from database import get_level, get_lang, get_interests

# ══════════════════════════════════════════════════════════════════
#  СИСТЕМНЫЙ ПРОМПТ ALEX
# ══════════════════════════════════════════════════════════════════

def build_system(uid: int, mode: str = "general") -> str:
    lang      = get_lang(uid)
    level     = get_level(uid)
    interests = get_interests(uid)

    lang_rule = (
        "Explain everything in Russian. All grammar explanations, tips, and feedback must be in Russian. "
        "Keep English examples, exercises, and quoted text in English."
        if lang == "ru" else
        "Respond entirely in English."
    )

    interests_rule = (
        f"The student's interests are: {interests}. "
        "When giving examples, analogies, or practice sentences, relate them to these interests whenever natural."
        if interests else ""
    )

    slang_rule = (
        "The student is B2+ level. Feel free to use modern idioms, colloquial expressions, and occasional slang "
        "to make the conversation feel authentic and native-like."
        if level in ("B2", "C1", "C2") else
        "Keep language clear and accessible. Avoid slang."
    )

    base = f"""You are ALEX — a friendly, witty, and sharp English tutor with 15 years of experience.
Your personality: warm but direct, encouraging but honest, occasionally funny without being cheesy.
You don't just give answers — you make the student THINK. Ask follow-up questions. Celebrate wins.

Student level: {level}
{lang_rule}
{interests_rule}
{slang_rule}

CORE TEACHING PRINCIPLES:
- Never just say "wrong" — explain WHY and give the correct form with an example
- After correcting, continue the conversation naturally — don't make it awkward
- Adapt vocabulary complexity to {level}
- Use the Socratic method: guide with questions rather than just lecturing
- Acknowledge what the student did RIGHT before correcting what's wrong

FORMATTING (Telegram HTML only — no markdown):
<b>bold</b> for key terms and corrections
<i>italic</i> for examples and quotes
<code>pattern</code> for grammar structures
✅ for correct / ❌ for errors / 💡 for tips / 📌 for rules
Numbered lists for steps, emoji for visual structure
"""

    MODE_ADDITIONS = {
        "correction": """
CORRECTION MODE — 3-layer feedback system:
When analyzing student text, always provide THREE versions:
1. ✅ <b>Corrected</b>: fix all errors, keep the student's meaning
2. 🌟 <b>Native-like</b>: rewrite it as a fluent native speaker would say it
3. 📚 <b>Error breakdown</b>: explain each error with category (Grammar/Vocabulary/Spelling/Punctuation/Style), why it's wrong, and the rule
If text is perfect: compliment genuinely, then suggest one way to make it even more sophisticated.
""",
        "roleplay": """
ROLEPLAY MODE — Stay in character!
You are playing a role in a real-life scenario. DO NOT break character to correct errors mid-sentence.
Instead: respond naturally in character, then at the END of each exchange add a small correction block:
「 <i>Quick note:</i> [correction] 」
Keep immersion high. Make the scenario feel real and engaging.
If the student seems lost or frustrated, gently offer a hint in italics.
""",
        "grammar": """
GRAMMAR LESSON MODE:
Teach like a great teacher, not a textbook.
Structure: Hook (why this matters) → Rule (simple, clear) → Examples (3+) → Common mistakes → Practice exercise
Use the "Explain like I'm 5" approach first, then build complexity.
After teaching, always give a mini-exercise to test understanding.
If asked to explain differently: use a completely new analogy — metaphor, story, or real-world comparison.
""",
        "test": """
TEST MODE — Exam quality questions:
For grammar: multiple choice (4 options, one clearly wrong, one tricky, two plausible)
For vocabulary: context fill-in, synonym/antonym, collocations
For reading: academic passages 200-300 words + 5 TOEFL-style questions
Track score. At the end: percentage + level assessment + specific areas to improve.
Explain every answer in detail — even the correct ones.
""",
        "toefl": """
TOEFL iBT SPECIALIST MODE:
You know the TOEFL iBT inside out:
- Reading: factual info, negative factual, inference, rhetorical purpose, vocabulary, sentence insertion, prose summary
- Listening: main idea, detail, function, attitude, inference questions
- Speaking: rubrics (Delivery 0-4, Language Use 0-4, Topic Development 0-4), integrated vs independent tasks
- Writing: rubrics (Development 1-5, Organization 1-5, Language Use 1-5), word count targets (300+ independent, 150-225 integrated)
Always give realistic practice materials at B2-C1 difficulty.
After student responses: give score on official scale + specific actionable feedback.
""",
        "vocab": """
VOCABULARY COACH MODE:
Teach words in context, not in isolation.
For each word provide: pronunciation (phonetic), word family, register (formal/informal/neutral),
2-3 collocations, 2 example sentences, one memory trick.
Use spaced repetition logic: test recently learned words, introduce new ones gradually.
Connect new words to words the student already knows.
""",
        "speaking": """
SPEAKING PRACTICE MODE:
Hold a natural conversation. Keep it flowing.
After each student message:
1. Respond naturally to the content
2. At the end, add a subtle correction if needed: 「 <i>Tip:</i> instead of "..." try "..." 」
For TOEFL Speaking: give topic → 15s prep → student writes response → score on Delivery/Language/Development (0-4 each)
Encourage the student to expand their answers, use more complex structures.
""",
    }

    return base + MODE_ADDITIONS.get(mode, "")


# ══════════════════════════════════════════════════════════════════
#  ПРОМПТЫ ДЛЯ КНОПОК
# ══════════════════════════════════════════════════════════════════

ROLEPLAY_SCENARIOS = {
    "rp_airport": {
        "ru": "Аэропорт — паспортный контроль",
        "en": "Airport — passport control",
        "prompt": "Start a roleplay: you are a strict but fair passport control officer at an international airport. The student is a traveler. Begin by asking for their documents and purpose of visit. Stay in character throughout.",
    },
    "rp_interview": {
        "ru": "Собеседование в IT-компанию",
        "en": "Job interview at a tech company",
        "prompt": "Start a roleplay: you are an HR manager at a tech company interviewing the student for a software developer position. Begin with 'Tell me about yourself.' Ask follow-up questions about experience and skills. Stay professional but friendly.",
    },
    "rp_coffee": {
        "ru": "Заказ кофе в кафе",
        "en": "Ordering coffee at a cafe",
        "prompt": "Start a roleplay: you are a barista at a busy New York coffee shop. The student is a customer. Be authentic — use real cafe vocabulary, ask about size, milk preference, name for the order. Keep it natural and fast-paced.",
    },
    "rp_date": {
        "ru": "Первое свидание",
        "en": "First date conversation",
        "prompt": "Start a roleplay: you are on a first date with the student at a restaurant. Be friendly, curious, and charming. Ask about their life, interests, travel. Keep the conversation natural and fun.",
    },
    "rp_doctor": {
        "ru": "Приём у врача",
        "en": "Doctor's appointment",
        "prompt": "Start a roleplay: you are a doctor in a UK clinic. The student is a patient. Ask about their symptoms, medical history. Use real medical vocabulary but explain it clearly. Stay professional.",
    },
    "rp_hotel": {
        "ru": "Заселение в отель",
        "en": "Hotel check-in",
        "prompt": "Start a roleplay: you are a hotel receptionist at a 4-star hotel. The student is checking in. Ask for reservation details, ID, payment. Handle a minor issue (e.g. room not ready yet) professionally.",
    },
    "rp_debate": {
        "ru": "Дебаты на актуальную тему",
        "en": "Debate on a current topic",
        "prompt": "Start a debate exercise. Give the student a controversial topic (e.g. 'Social media does more harm than good') and assign them one side. You argue the opposite. Keep it intellectual and engaging. Score their argumentation at the end.",
    },
    "rp_custom": {
        "ru": "Своя ситуация",
        "en": "Custom scenario",
        "prompt": None,  # спросим у пользователя
    },
}

LESSON_PROMPTS = {
    "lesson_tenses":       "Teach me English verb tenses using the 'Explain Like I'm 5' approach first, then build complexity. Start with why tenses matter, give a clear overview, then cover each tense with: form, when to use it, examples, and the most common mistakes. Finish with a 5-question mini-test.",
    "lesson_conditionals": "Teach me all English conditionals (Zero, First, Second, Third, Mixed). Explain each with a real-life analogy, not just grammar rules. Show me the pattern, give examples, highlight common mistakes. End with a construction exercise: give me 5 sentence starters to complete.",
    "lesson_modal":        "Teach me English modal verbs. Focus on the confusing ones: can/could/may/might, must/have to/should/ought to, will/would. Explain the subtle differences with concrete examples. Give me 5 practice sentences at the end.",
    "lesson_passive":      "Teach me passive voice in English. Explain WHY we use it (not just how), cover all tenses in passive form, give examples from real contexts. Then give me 5 sentences to transform from active to passive.",
    "lesson_articles":     "Teach me English articles (a/an/the/zero). This is one of the hardest topics for non-native speakers. Use the 'Explain Like I'm 5' method first, then cover all rules with clear examples. Focus on the exceptions and tricky cases. End with 10 fill-in-the-blank exercises.",
    "lesson_prepositions": "Teach me English prepositions of time (at/on/in), place (at/on/in), and movement (to/into/onto/through). Give me memorable rules and mnemonics. Include fixed expressions with prepositions. End with exercises.",
    "lesson_phrasal":      "Teach me the most essential phrasal verbs (get, take, give, put, come, go, look, turn, run, break). For each verb show the 3-4 most important phrasal verb combinations with meanings and examples. End with a matching exercise.",
    "lesson_reported":     "Teach me reported speech in English. Cover statements, questions, commands, and the backshift of tenses. Show me the patterns clearly. Give me 5 direct speech sentences to convert to reported speech.",
    "lesson_subjunctive":  "Teach me the English subjunctive mood (I wish, If only, It's time, I'd rather, as if). This is advanced but important. Explain with examples and give practice exercises.",
    "lesson_inversion":    "Teach me advanced English inversion structures (Never have I..., Not only..., Rarely..., Should you need...). These are key for C1-C2 and formal writing. Give examples and exercises.",
}

VOCAB_PROMPTS = {
    "vocab_new":        "Teach me 8 new English words that are practical and interesting for my level. For each: phonetic pronunciation, part of speech, clear definition, 2 example sentences, key collocations, and a memory trick. Make it engaging.",
    "vocab_review":     "Quiz me on vocabulary. Give me 10 words one by one: either show the definition and I guess the word, or show the word and I define it. Keep score and give me a final result.",
    "vocab_flashcards": "Run a flashcard session with me. Give me one word at a time in context (gapped sentence), let me guess, then confirm or correct. After 10 cards give me a score and tell me which to review.",
    "vocab_collocations":"Teach me 10 important English collocations — word combinations that native speakers use naturally. For each: the collocation, why it's used (not the alternatives), and 2 example sentences.",
    "vocab_idioms_adv": "Teach me 6 advanced English idioms that educated native speakers actually use (not the cliché ones). For each: meaning, origin (briefly), example in context, register (when to use it).",
    "vocab_topic":      "Ask me what topic I need vocabulary for, then teach me 10-12 essential words on that topic with definitions, examples, pronunciation, and collocations. Make it practical.",
}

TEST_PROMPTS = {
    "test_grammar":   "Give me a challenging 10-question grammar test appropriate for my level. Use multiple choice (4 options), sentence transformation, and error identification. After I finish all questions, give full explanations for every answer and a final score.",
    "test_vocab":     "Give me a 10-question vocabulary test: mix of definitions, fill-in-the-blank with context, and collocation questions. After all answers, explain everything and give a score.",
    "test_reading":   "Give me a TOEFL-style reading test: an academic passage (280-320 words), followed by 5 questions (factual, inference, vocabulary in context, purpose, summary). After I answer, give full explanations and score.",
    "test_mixed":     "Give me a comprehensive 15-question mixed test covering grammar, vocabulary, and reading comprehension. Make it progressively harder. Give full results and analysis at the end.",
    "test_placement": "Run a placement test to accurately determine my English level. Ask 15 questions that get progressively harder from A2 to C1. Cover grammar, vocabulary, and reading. At the end, tell me my exact level with detailed justification.",
    "test_writing":   "Give me a writing test: provide a prompt appropriate for my level, I'll write a response (at least 150 words), and you'll grade it on: Content & Ideas, Organization, Grammar Accuracy, Vocabulary Range, and Overall Fluency. Give a score out of 25 with specific feedback.",
}

TOEFL_PROMPTS = {
    "toefl_reading":   "Give me a full TOEFL Reading practice set: an academic passage (300 words, B2-C1 level) on an interesting topic, followed by 6 TOEFL-style questions covering factual info, negative factual, inference, vocabulary, rhetorical purpose, and sentence insertion. After I answer all, give detailed explanations and a score out of 6.",
    "toefl_listening": "Simulate a TOEFL Listening exercise. Write out a realistic university lecture transcript (on an academic topic, approximately 250 words), then ask 5 TOEFL-style questions about main idea, details, speaker's attitude, and inference. Score me after all answers.",
    "toefl_speaking1": "Give me a TOEFL Independent Speaking Task 1. Tell me: you have 15 seconds to prepare and 45 seconds to respond. Give me an interesting question about preferences or opinions. After I write my response, score it 0-4 on Delivery, Language Use, and Topic Development with specific feedback.",
    "toefl_speaking2": "Give me a TOEFL Integrated Speaking Task. First give me a short reading passage (100 words) and a lecture transcript (150 words) that adds to or challenges the reading. Then ask me to summarize the relationship. Score my response on the TOEFL 0-4 scale.",
    "toefl_writing1":  "Give me a TOEFL Independent Writing task. Provide an interesting essay question. Tell me to write at least 300 words. After I submit, score it 1-5 on Development & Support, Organization, and Language Use. Give a total score and detailed, actionable feedback.",
    "toefl_writing2":  "Give me a TOEFL Integrated Writing task. Provide a reading passage (250 words) and a lecture transcript (200 words) on the same topic but with different perspectives. Ask me to write a 150-225 word essay comparing them. Score and give feedback.",
    "toefl_full":      "Run a mini TOEFL iBT simulation. We'll do one task from each section: 1) Reading: short passage + 3 questions, 2) Listening: lecture transcript + 2 questions, 3) Speaking: one independent task, 4) Writing: one short essay. Give an estimated total TOEFL score (out of 120) at the end with section breakdowns.",
    "toefl_strategy":  "Give me a comprehensive TOEFL iBT strategy guide covering all 4 sections. Include: exact question types and how to approach each, time management techniques, common traps and how to avoid them, scoring rubrics explained, and a personalized study plan based on my current level. Be specific and actionable.",
}

TALK_PROMPTS = {
    "talk_daily":    "Let's have a natural conversation about daily life. Start by asking me about my morning routine or weekend plans. Keep it going with follow-up questions. Gently note any language errors at the end of each exchange.",
    "talk_travel":   "Let's talk about travel and culture. Ask me about the most interesting place I've visited or want to visit, and why. Share opinions to keep the dialogue going. Correct my English naturally.",
    "talk_work":     "Let's practice professional English. Simulate a casual work conversation — maybe we're colleagues discussing a project, or you're a mentor giving career advice. Use real workplace vocabulary.",
    "talk_debate":   "Let's do a structured debate. Give me a thought-provoking topic, assign me one position, and argue the other side. Be a strong debater. Score my argumentation (logic, vocabulary, grammar) at the end.",
    "talk_business": "Let's practice Business English. Choose a scenario: negotiation, client meeting, or project update presentation. Use formal business language and correct any inappropriate register.",
    "talk_free":     "Let's just talk. Ask me what's on my mind today — anything goes. Keep it natural, like chatting with a smart friend who also happens to correct your English gently.",
    "talk_interview": "Let's practice for a job interview. You're a senior interviewer. Start with behavioral questions (Tell me about a time when...) and situational questions. Give feedback on my communication skills at the end.",
}

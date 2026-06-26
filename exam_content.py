"""
exam_content.py — Academic content engine for the premium TOEFL / IELTS simulator.

PURPOSE
    The exam simulator must NEVER serve everyday / conversational material
    (shopping, small talk, daily routines). That content belongs to the FREE
    course. Exams are strictly Academic English: science, history, anthropology,
    economics, sociology — the registers a real iBT / Academic-band test uses.

WHAT THIS MODULE PROVIDES
    1. EXAM_TOPIC_POOL  — a curated pool of 60 academic topics, tagged by domain,
       used to seed the `exam_topics` DB table and to drive random, non-repeating
       variant generation (so a learner never sits the same passage twice).
    2. build_exam_system_prompt(...) — the System Prompt handed to Claude Sonnet
       to generate one Reading passage or one Listening lecture/discussion as
       strict, valid JSON.
    3. pick_topics(...) — random topic selection with exclusion (dedup) support.

DESIGN NOTES
    * Pure stdlib (random only) so it imports cleanly on the server and in tests.
    * The pool is intentionally large and domain-balanced; the DB layer caches
      generated variants per topic, so the effective bank grows past 50+/exam
      organically while AI cost is amortised across users.
"""

import random

# ── Academic domains (the ONLY domains allowed in the exam simulator) ─────────
DOMAIN_NATURAL_SCIENCE   = "natural_science"
DOMAIN_HISTORY_ANTHRO    = "history_anthropology"
DOMAIN_ECON_SOCIOLOGY    = "economics_sociology"

DOMAINS = (DOMAIN_NATURAL_SCIENCE, DOMAIN_HISTORY_ANTHRO, DOMAIN_ECON_SOCIOLOGY)

# Human-readable domain labels used inside the prompt to steer register.
DOMAIN_LABEL = {
    DOMAIN_NATURAL_SCIENCE: "Natural Sciences (astrophysics, marine biology, geology, palaeontology, climatology, chemistry, physics)",
    DOMAIN_HISTORY_ANTHRO:  "History & Anthropology (ancient civilisations, industrialisation, migration, archaeology)",
    DOMAIN_ECON_SOCIOLOGY:  "Economics & Sociology (globalisation, urbanisation, macroeconomic theory, social/behavioural science)",
}

# ── Topic pool ────────────────────────────────────────────────────────────────
# Each topic: stable `id` (used for DB dedup), `domain`, `title`, and an `angle`
# that pins a *specific* academic sub-question so two passages on the same topic
# still differ. 60 topics → with cached variants the effective bank exceeds the
# 50+/exam target the brief requires.
EXAM_TOPIC_POOL = [
    # ── Natural sciences ──────────────────────────────────────────────────────
    {"id": "ns_stellar_nucleosynthesis", "domain": DOMAIN_NATURAL_SCIENCE, "title": "Stellar nucleosynthesis", "angle": "how elements heavier than helium are forged inside stars and dispersed by supernovae"},
    {"id": "ns_exoplanet_detection",     "domain": DOMAIN_NATURAL_SCIENCE, "title": "Exoplanet detection methods", "angle": "the transit and radial-velocity methods and their respective limitations"},
    {"id": "ns_cosmic_microwave_bg",     "domain": DOMAIN_NATURAL_SCIENCE, "title": "The cosmic microwave background", "angle": "what it reveals about the early universe and why its uniformity posed a puzzle"},
    {"id": "ns_plate_tectonics",         "domain": DOMAIN_NATURAL_SCIENCE, "title": "Plate tectonics", "angle": "the lines of evidence that established the theory and its remaining open questions"},
    {"id": "ns_seafloor_spreading",      "domain": DOMAIN_NATURAL_SCIENCE, "title": "Seafloor spreading", "angle": "magnetic striping as evidence and how it constrains the age of oceanic crust"},
    {"id": "ns_glacial_cycles",          "domain": DOMAIN_NATURAL_SCIENCE, "title": "Glacial cycles and Milankovitch theory", "angle": "how orbital variations pace ice ages and the lags that complicate the model"},
    {"id": "ns_ocean_acidification",     "domain": DOMAIN_NATURAL_SCIENCE, "title": "Ocean acidification", "angle": "the carbonate chemistry involved and its consequences for calcifying organisms"},
    {"id": "ns_deep_sea_vents",          "domain": DOMAIN_NATURAL_SCIENCE, "title": "Hydrothermal vent ecosystems", "angle": "chemosynthesis as an energy base independent of sunlight"},
    {"id": "ns_coral_symbiosis",         "domain": DOMAIN_NATURAL_SCIENCE, "title": "Coral–algal symbiosis", "angle": "the zooxanthellae relationship and the mechanism of bleaching under stress"},
    {"id": "ns_whale_migration",         "domain": DOMAIN_NATURAL_SCIENCE, "title": "Cetacean migration and navigation", "angle": "competing hypotheses on how whales navigate across ocean basins"},
    {"id": "ns_mass_extinctions",        "domain": DOMAIN_NATURAL_SCIENCE, "title": "Mass extinction events", "angle": "the impact hypothesis for the end-Cretaceous extinction and rival explanations"},
    {"id": "ns_dinosaur_endothermy",     "domain": DOMAIN_NATURAL_SCIENCE, "title": "Dinosaur metabolism debate", "angle": "the evidence for and against warm-bloodedness in non-avian dinosaurs"},
    {"id": "ns_feathered_dinosaurs",     "domain": DOMAIN_NATURAL_SCIENCE, "title": "The dinosaur–bird transition", "angle": "how feathered fossils reshaped views on the origin of avian flight"},
    {"id": "ns_human_evolution",         "domain": DOMAIN_NATURAL_SCIENCE, "title": "Hominin evolution", "angle": "what the fossil and genetic record reveal about interbreeding among hominin species"},
    {"id": "ns_photosynthesis_c4",       "domain": DOMAIN_NATURAL_SCIENCE, "title": "C4 photosynthesis", "angle": "why the C4 pathway outperforms C3 in hot, arid conditions"},
    {"id": "ns_nitrogen_cycle",          "domain": DOMAIN_NATURAL_SCIENCE, "title": "The nitrogen cycle", "angle": "biological fixation and how human activity has altered global nitrogen flows"},
    {"id": "ns_antibiotic_resistance",   "domain": DOMAIN_NATURAL_SCIENCE, "title": "Antibiotic resistance", "angle": "how selective pressure drives resistance and why it spreads horizontally"},
    {"id": "ns_immune_memory",           "domain": DOMAIN_NATURAL_SCIENCE, "title": "Immunological memory", "angle": "how the adaptive immune system stores and reactivates a response"},
    {"id": "ns_crispr_mechanism",        "domain": DOMAIN_NATURAL_SCIENCE, "title": "CRISPR as a bacterial defence", "angle": "the natural function of CRISPR before its use as an editing tool"},
    {"id": "ns_supervolcanoes",          "domain": DOMAIN_NATURAL_SCIENCE, "title": "Supervolcanoes and caldera collapse", "angle": "how magma chambers build to a super-eruption and the climatic aftermath"},
    {"id": "ns_aurora_formation",        "domain": DOMAIN_NATURAL_SCIENCE, "title": "The physics of auroras", "angle": "how solar wind and the magnetosphere interact to produce auroral light"},
    {"id": "ns_dark_matter",             "domain": DOMAIN_NATURAL_SCIENCE, "title": "Evidence for dark matter", "angle": "galaxy rotation curves and gravitational lensing as indirect evidence"},

    # ── History & anthropology ────────────────────────────────────────────────
    {"id": "ha_mesopotamia_writing",     "domain": DOMAIN_HISTORY_ANTHRO, "title": "The origins of writing in Mesopotamia", "angle": "how cuneiform evolved from accounting tokens into a flexible script"},
    {"id": "ha_sumerian_cities",         "domain": DOMAIN_HISTORY_ANTHRO, "title": "The first Sumerian city-states", "angle": "how irrigation agriculture enabled urban concentration in the south"},
    {"id": "ha_code_hammurabi",          "domain": DOMAIN_HISTORY_ANTHRO, "title": "The Code of Hammurabi", "angle": "what the legal code reveals about social stratification in Babylon"},
    {"id": "ha_roman_concrete",          "domain": DOMAIN_HISTORY_ANTHRO, "title": "Roman concrete and engineering", "angle": "why Roman marine concrete outlasts modern equivalents"},
    {"id": "ha_roman_roads",             "domain": DOMAIN_HISTORY_ANTHRO, "title": "The Roman road network", "angle": "how road construction served military logistics and integrated the empire"},
    {"id": "ha_fall_of_rome",            "domain": DOMAIN_HISTORY_ANTHRO, "title": "The decline of the Western Roman Empire", "angle": "the competing economic and military explanations for its collapse"},
    {"id": "ha_maya_collapse",           "domain": DOMAIN_HISTORY_ANTHRO, "title": "The Classic Maya collapse", "angle": "how drought, warfare and agriculture interacted in the lowland decline"},
    {"id": "ha_maya_astronomy",          "domain": DOMAIN_HISTORY_ANTHRO, "title": "Maya astronomy and the calendar", "angle": "how astronomical observation underpinned the Long Count calendar"},
    {"id": "ha_indus_valley",            "domain": DOMAIN_HISTORY_ANTHRO, "title": "The Indus Valley civilisation", "angle": "what urban planning at Mohenjo-daro implies about its undeciphered society"},
    {"id": "ha_silk_road",               "domain": DOMAIN_HISTORY_ANTHRO, "title": "The Silk Road exchange", "angle": "how the routes transmitted goods, technologies and disease across Eurasia"},
    {"id": "ha_printing_press",          "domain": DOMAIN_HISTORY_ANTHRO, "title": "The printing press in Europe", "angle": "how standardisation, not literacy, was its first revolution"},
    {"id": "ha_columbian_exchange",      "domain": DOMAIN_HISTORY_ANTHRO, "title": "The Columbian Exchange", "angle": "how transatlantic transfer of crops and pathogens reshaped both hemispheres"},
    {"id": "ha_industrial_revolution",   "domain": DOMAIN_HISTORY_ANTHRO, "title": "The British Industrial Revolution", "angle": "why mechanised industry emerged first in Britain"},
    {"id": "ha_industrial_society",      "domain": DOMAIN_HISTORY_ANTHRO, "title": "Social change in industrial towns", "angle": "how rapid urban migration strained sanitation and labour conditions"},
    {"id": "ha_dendrochronology",        "domain": DOMAIN_HISTORY_ANTHRO, "title": "Dendrochronology as a dating method", "angle": "how overlapping tree-ring sequences build an absolute chronology"},
    {"id": "ha_radiocarbon",             "domain": DOMAIN_HISTORY_ANTHRO, "title": "Radiocarbon dating", "angle": "the assumptions behind the method and why calibration is required"},
    {"id": "ha_great_migrations",        "domain": DOMAIN_HISTORY_ANTHRO, "title": "Early human migration out of Africa", "angle": "how genetic and archaeological evidence dates the dispersal"},
    {"id": "ha_polynesian_voyaging",     "domain": DOMAIN_HISTORY_ANTHRO, "title": "Polynesian ocean voyaging", "angle": "how wayfinding without instruments settled the Pacific"},
    {"id": "ha_agricultural_origins",    "domain": DOMAIN_HISTORY_ANTHRO, "title": "The origins of agriculture", "angle": "why farming arose independently in several centres"},
    {"id": "ha_writing_systems",         "domain": DOMAIN_HISTORY_ANTHRO, "title": "The decipherment of ancient scripts", "angle": "how bilingual inscriptions enabled the reading of lost languages"},

    # ── Economics & sociology ─────────────────────────────────────────────────
    {"id": "es_loss_aversion",           "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Loss aversion in behavioural economics", "angle": "how asymmetric responses to gains and losses explain market anomalies"},
    {"id": "es_tragedy_commons",         "domain": DOMAIN_ECON_SOCIOLOGY, "title": "The tragedy of the commons", "angle": "why shared resources are over-exploited and how institutions mitigate it"},
    {"id": "es_comparative_advantage",   "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Comparative advantage", "angle": "why mutually beneficial trade arises even when one party is more efficient"},
    {"id": "es_information_asymmetry",    "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Information asymmetry in markets", "angle": "how the 'market for lemons' shows adverse selection eroding quality"},
    {"id": "es_globalisation_supply",    "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Globalisation and supply chains", "angle": "how integrated production networks distribute risk and vulnerability"},
    {"id": "es_urbanisation_growth",     "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Urbanisation and economic growth", "angle": "why agglomeration raises productivity yet strains infrastructure"},
    {"id": "es_urban_heat_island",       "domain": DOMAIN_ECON_SOCIOLOGY, "title": "The urban heat island effect", "angle": "how the built environment alters local climate and mitigation strategies"},
    {"id": "es_demographic_transition",  "domain": DOMAIN_ECON_SOCIOLOGY, "title": "The demographic transition", "angle": "how falling mortality and fertility reshape a population's age structure"},
    {"id": "es_inflation_theory",        "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Theories of inflation", "angle": "how demand-pull and cost-push explanations differ in policy implications"},
    {"id": "es_keynes_vs_monetarist",    "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Keynesian versus monetarist economics", "angle": "the dispute over whether fiscal or monetary policy stabilises output"},
    {"id": "es_central_banking",         "domain": DOMAIN_ECON_SOCIOLOGY, "title": "The role of central banks", "angle": "how independence and credibility shape the management of expectations"},
    {"id": "es_network_effects",         "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Network effects in technology markets", "angle": "how increasing returns can entrench a dominant standard"},
    {"id": "es_collective_behaviour",    "domain": DOMAIN_ECON_SOCIOLOGY, "title": "The psychology of crowds", "angle": "how individual judgement is reshaped within large groups"},
    {"id": "es_diffusion_innovation",    "domain": DOMAIN_ECON_SOCIOLOGY, "title": "The diffusion of innovations", "angle": "why adoption follows an S-curve across social categories"},
    {"id": "es_social_capital",          "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Social capital and trust", "angle": "how dense social ties affect economic cooperation and civic life"},
    {"id": "es_inequality_measures",     "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Measuring economic inequality", "angle": "what the Gini coefficient captures and what it conceals"},
    {"id": "es_public_goods",            "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Public goods and free riding", "angle": "why non-excludable goods are underprovided by markets"},
    {"id": "es_labour_migration",        "domain": DOMAIN_ECON_SOCIOLOGY, "title": "Economics of labour migration", "angle": "how remittances and brain drain pull in opposite directions"},
]


def _topics_for(exam_type: str = None, domain: str = None):
    """Return the filtered pool. Topics are shared across TOEFL/IELTS (both use
    the same academic register); `exam_type` is reserved for future per-exam
    weighting and currently does not narrow the pool. `domain` filters by area."""
    pool = EXAM_TOPIC_POOL
    if domain:
        pool = [t for t in pool if t["domain"] == domain]
    return pool


def pick_topics(n: int = 1, exclude_ids=None, domain: str = None, exam_type: str = None):
    """Pick `n` distinct random topics, excluding any ids the learner has already
    seen. Falls back to the full pool if exclusion would empty it (so generation
    never deadlocks once a heavy user has cycled the whole bank)."""
    exclude = set(exclude_ids or ())
    pool = _topics_for(exam_type, domain)
    fresh = [t for t in pool if t["id"] not in exclude]
    source = fresh if len(fresh) >= n else pool
    n = max(1, min(int(n), len(source)))
    return random.sample(source, n)


# ── Hard academic guardrail (shared by Reading + Listening) ───────────────────
ACADEMIC_RULES = (
    "You generate content for a HIGH-STAKES ACADEMIC EXAM (TOEFL iBT / IELTS Academic).\n"
    "ABSOLUTE CONTENT RULES — non-negotiable:\n"
    "1. STRICTLY ACADEMIC. The subject must be scientific, historical, anthropological, "
    "economic or sociological — the register of a university textbook or lecture.\n"
    "2. FORBIDDEN: everyday or conversational material of ANY kind — shopping, travel "
    "planning, daily routines, ordering food, hobbies, small talk, personal anecdotes, "
    "directions, weather chit-chat, social plans. That material belongs to a beginner "
    "course and must NEVER appear in the exam.\n"
    "3. LEXIS at C1–C2: precise, discipline-specific terminology; abstract nouns; "
    "low-frequency academic vocabulary. No simplified or childish wording.\n"
    "4. GRAMMAR of academic prose: frequent passive voice, nominalisation, complex "
    "subordination, and hedged claims (e.g. 'it has been argued that', 'the evidence "
    "suggests', 'this is generally attributed to', 'one interpretation holds that').\n"
    "5. Use cohesive, formal discourse markers (Furthermore, Consequently, Nevertheless, "
    "By contrast, In particular).\n"
    "6. Neutral, objective, third-person stance. Present competing interpretations where "
    "the topic is contested, rather than a single simple fact.\n"
)

# Question-type guidance mirrors the official rubrics the simulator scores against.
_READING_QTYPES = (
    "Question types MUST be drawn from the real exam set: factual detail, "
    "negative factual (EXCEPT/NOT), inference, rhetorical purpose ('Why does the "
    "author mention…'), vocabulary-in-context, and sentence simplification. "
    "Distractors must be plausible and same-register — never obviously wrong."
)
_LISTENING_QTYPES = (
    "Question types MUST be: main idea/gist, supporting detail, speaker function/"
    "purpose, speaker attitude/stance, and inference. Distractors must be plausible."
)


def _level_band(level: str) -> str:
    lv = (level or "C1").upper()
    if lv in ("A1", "A2", "B1"):
        # The exam floor is C1 regardless of the learner's course level — an
        # academic test does not get easier than C1.
        return "C1"
    return "C2" if lv == "C2" else "C1"


def build_exam_system_prompt(exam_type: str, section: str, level: str,
                             topic: dict, lang: str = "ru") -> str:
    """Return the System Prompt for Claude Sonnet that generates ONE exam variant
    as strict JSON. `section` is 'reading' | 'listening'. `topic` is a pool entry.
    Question stems/options are written in `lang` for Reading vocabulary glossing
    is NOT done — the passage and all options stay in English (real-exam fidelity);
    only optional UI hints may localise. We keep everything English here."""
    exam = "IELTS Academic" if str(exam_type).lower() == "ielts" else "TOEFL iBT"
    band = _level_band(level)
    domain_label = DOMAIN_LABEL.get(topic.get("domain", ""), "academic")
    title = topic.get("title", "an academic subject")
    angle = topic.get("angle", "")

    header = (
        f"You are a senior {exam} item-writer with twenty years of experience "
        f"authoring official test material.\n\n"
        f"{ACADEMIC_RULES}\n"
        f"TARGET DIFFICULTY: {band} (CEFR). EXAM: {exam}.\n"
        f"DOMAIN: {domain_label}.\n"
        f"TOPIC: \"{title}\" — focus specifically on: {angle}.\n"
        f"Write an ORIGINAL text on this exact focus; do not reproduce any "
        f"published passage. Vary structure and examples so the item is unique.\n\n"
    )

    if section == "listening":
        kind_line = (
            "Produce a LISTENING item that simulates an authentic university "
            "setting: either (a) a professor's lecture excerpt, or (b) an academic "
            "seminar discussion between two or three speakers (e.g. Professor / "
            "Student). Include natural lecture discourse signals ('Now, what's "
            "important to note here…', 'Let me turn to…', 'This brings us to…') "
            "WITHOUT lapsing into casual chit-chat. The intellectual content stays "
            "fully academic.\n"
        )
        body = (
            kind_line + _LISTENING_QTYPES + "\n\n"
            "Respond with ONLY valid JSON (no markdown, no commentary):\n"
            "{\n"
            '  "topic": "short academic title",\n'
            '  "domain": "' + topic.get("domain", "") + '",\n'
            '  "format": "lecture" | "discussion",\n'
            '  "transcript": "180-260 words of academic spoken English. For a '
            'discussion, prefix each turn with the speaker label and a newline.",\n'
            '  "questions": [\n'
            '    {"q": "question stem", "o": ["A","B","C","D"], "a": 0, '
            '"type": "gist|detail|function|attitude|inference", '
            '"explanation": "cite the part of the transcript that proves the key"}\n'
            "  ]\n"
            "}\n"
            "Generate EXACTLY 4 questions covering distinct types."
        )
    else:  # reading (default)
        body = (
            "Produce a READING item: a single dense academic passage.\n"
            + _READING_QTYPES + "\n\n"
            "Respond with ONLY valid JSON (no markdown, no commentary):\n"
            "{\n"
            '  "topic": "short academic title",\n'
            '  "domain": "' + topic.get("domain", "") + '",\n'
            '  "passage": "220-320 words of formal academic prose at ' + band + '. '
            'Use passive voice, hedging and discipline-specific terminology.",\n'
            '  "questions": [\n'
            '    {"q": "question stem", "o": ["A","B","C","D"], "a": 0, '
            '"type": "detail|negative|inference|purpose|vocabulary|simplify", '
            '"explanation": "cite the sentence in the passage that proves the key"}\n'
            "  ]\n"
            "}\n"
            "Generate EXACTLY 5 questions covering distinct types, including at "
            "least one inference and one vocabulary-in-context item."
        )
    return header + body

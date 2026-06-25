"""
exam_grader.py — AI controller for the premium exam simulator.

Grades the two open-ended sections of a TOEFL/IELTS mock with Claude Sonnet:
  • grade_writing()  — essay audit → score + error list + model answer
  • grade_speaking() — spoken-answer audit on an STT transcript →
                       fluency / pronunciation / grammar → section score

Both return a normalised dict on the official per-section scale
(TOEFL 0-30, IELTS band 0-9) and NEVER raise to the caller: on any failure
they return a calibrated fallback so an exam can always be finalised.

The calling route (server.py) is the place that enforces the subscription gate
and best-effort cost logging; this module only does the model I/O + scoring.
"""
import os
import json
import logging

import httpx

logger = logging.getLogger("exam_grader")

ANT_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Graded academic work uses Sonnet (matches handle_chat's exam path). Overridable.
GRADER_MODEL = os.getenv("EXAM_GRADER_MODEL", "claude-sonnet-4-6")

_SECTION_MAX = {"toefl": 30, "ielts": 9}


def _norm_type(exam_type: str) -> str:
    return "ielts" if str(exam_type or "").lower() == "ielts" else "toefl"


def section_max(exam_type: str) -> int:
    return _SECTION_MAX[_norm_type(exam_type)]


def _clamp(v, lo, hi):
    try:
        v = float(v)
    except Exception:
        return float(lo)
    return max(float(lo), min(float(hi), v))


def _round_score(exam_type: str, v: float):
    """IELTS → nearest 0.5 band; TOEFL → integer."""
    if _norm_type(exam_type) == "ielts":
        return round(v * 2) / 2.0
    return int(round(v))


def _extract_json(text: str) -> dict:
    """Pull the first balanced {...} object out of a model reply."""
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    blob = text[start:end + 1]
    try:
        return json.loads(blob)
    except Exception:
        cleaned = blob.replace("```json", "").replace("```", "")
        try:
            return json.loads(cleaned)
        except Exception:
            return {}


async def _complete(system: str, user: str, max_tokens: int = 1100) -> dict:
    """One Anthropic call → parsed JSON, or {} on any failure (never raises)."""
    if not ANT_KEY:
        logger.error("exam_grader: ANTHROPIC_API_KEY is not set")
        return {}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANT_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": GRADER_MODEL,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
        data = r.json()
        if "error" in data:
            logger.error("exam_grader API error: %s", data["error"])
            return {"_usage": {}, "_error": True}
        parsed = _extract_json(data.get("content", [{}])[0].get("text", ""))
        parsed["_usage"] = data.get("usage") or {}
        return parsed
    except Exception as e:
        logger.error("exam_grader call failed: %s", e)
        return {}


async def grade_writing(exam_type: str, prompt: str, essay: str,
                        min_words: int = 150) -> dict:
    """Audit an essay against the official Writing rubric.

    Returns: {section, exam_type, score, max_score, word_count,
              feedback, errors:[{wrong,fix,type}], model_answer, ok}
    """
    exam_type = _norm_type(exam_type)
    smax = section_max(exam_type)
    essay = (essay or "").strip()
    words = len(essay.split())
    band_hint = ("band 0-9 with one decimal, e.g. 6.5" if exam_type == "ielts"
                 else "integer 0-30")
    examiner = "IELTS Writing" if exam_type == "ielts" else "TOEFL iBT Writing"
    system = (
        f"You are a certified {examiner} examiner. Grade the candidate essay strictly "
        "against the official rubric: task response, coherence & cohesion, lexical "
        "resource, grammatical range & accuracy. Be honest and calibrated — do not "
        "inflate. Return ONLY a JSON object, no prose, with EXACTLY these keys:\n"
        '{\n'
        f'  "score": number ({band_hint}),\n'
        '  "feedback": "2-4 sentence overall assessment",\n'
        '  "errors": [{"wrong":"quoted text","fix":"correction","type":"grammar|lexis|cohesion|task"}],\n'
        '  "model_answer": "a clean, high-scoring rewrite of the essay (120-260 words)"\n'
        '}\n'
        "List up to 6 of the most important errors."
    )
    user = (f"EXAM: {exam_type.upper()} Writing\n"
            f"MIN WORDS: {min_words}\nWORD COUNT: {words}\n\n"
            f"TASK PROMPT:\n{prompt}\n\nCANDIDATE ESSAY:\n{essay}")
    res = await _complete(system, user, max_tokens=1300)
    ok = bool(res) and not res.get("_error") and "score" in res
    score = _clamp(res.get("score", 0), 0, smax)
    # Guard: an essay far below the word floor cannot earn a full-rubric score.
    if words < max(40, min_words // 3):
        score = min(score, smax * 0.3)
    return {
        "section": "writing",
        "exam_type": exam_type,
        "score": _round_score(exam_type, score),
        "max_score": smax,
        "word_count": words,
        "feedback": str(res.get("feedback") or "")[:1200],
        "errors": (res.get("errors") or [])[:6],
        "model_answer": str(res.get("model_answer") or "")[:3000],
        "usage": res.get("_usage") or {},
        "ok": ok,
    }


async def grade_speaking(exam_type: str, question: str, transcript: str) -> dict:
    """Audit a spoken answer (given as an STT transcript) against the Speaking rubric.

    Returns: {section, exam_type, score, max_score, word_count,
              fluency, pronunciation, grammar, feedback, ok}
    """
    exam_type = _norm_type(exam_type)
    smax = section_max(exam_type)
    transcript = (transcript or "").strip()
    words = len(transcript.split())
    band_hint = ("band 0-9 with one decimal" if exam_type == "ielts"
                 else "integer 0-30")
    examiner = "IELTS Speaking" if exam_type == "ielts" else "TOEFL iBT Speaking"
    system = (
        f"You are a certified {examiner} examiner. You receive a SPEECH-TO-TEXT "
        "transcript of the candidate's spoken answer, so ignore missing punctuation "
        "and capitalisation. Grade against the official rubric: fluency & coherence, "
        "pronunciation (infer from fillers, repetition, self-repairs, word choice), "
        "lexical resource, grammatical range & accuracy. Return ONLY a JSON object "
        "with EXACTLY these keys:\n"
        '{\n'
        f'  "score": number ({band_hint}),\n'
        '  "fluency": number (0-10),\n'
        '  "pronunciation": number (0-10),\n'
        '  "grammar": number (0-10),\n'
        '  "feedback": "2-4 sentence assessment with one concrete improvement tip"\n'
        '}'
    )
    user = (f"EXAM: {exam_type.upper()} Speaking\nWORD COUNT: {words}\n\n"
            f"QUESTION:\n{question}\n\nTRANSCRIPT:\n{transcript}")
    res = await _complete(system, user, max_tokens=700)
    ok = bool(res) and not res.get("_error") and "score" in res
    score = _clamp(res.get("score", 0), 0, smax)
    if words < 8:
        score = min(score, smax * 0.2)
    return {
        "section": "speaking",
        "exam_type": exam_type,
        "score": _round_score(exam_type, score),
        "max_score": smax,
        "word_count": words,
        "fluency": _clamp(res.get("fluency", 0), 0, 10),
        "pronunciation": _clamp(res.get("pronunciation", 0), 0, 10),
        "grammar": _clamp(res.get("grammar", 0), 0, 10),
        "feedback": str(res.get("feedback") or "")[:1000],
        "usage": res.get("_usage") or {},
        "ok": ok,
    }

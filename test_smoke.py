#!/usr/bin/env python3
"""
PolyGlotty smoke tests — a fast, dependency-light pre-push guard.

Run before every push:  python3 test_smoke.py

It catches the regressions that have actually bitten this project:
  1. Python syntax errors in any backend module (py_compile).
  2. Broken inline JS in the single-file WebApp (Node `vm` parse of each
     <script> block).
  3. Corrupt lesson data in GALAXY_COURSE — duplicate/missing ids, invalid
     level, missing bilingual title, or a quiz whose answer index points
     outside its options list.
  4. Limit-notice i18n — every free-limit message must render for every
     supported language (and fall back cleanly for an unknown one).

No third-party packages required: httpx is stubbed so ai_router imports
without its runtime dep, and Node is only needed for the JS/course checks
(they degrade to a SKIP with a clear note if Node is absent).

Exit code is 0 only when every non-skipped check passes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(ROOT, "webapp", "index.html")

# ANSI (falls back to plain if not a TTY)
_TTY = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s
def ok(s):    return _c("32", s)
def bad(s):   return _c("31", s)
def warn(s):  return _c("33", s)

FAILURES: list[str] = []
SKIPS: list[str] = []


def section(name: str):
    print("\n" + _c("1", f"── {name} " + "─" * max(0, 46 - len(name))))


def fail(msg: str):
    FAILURES.append(msg)
    print("  " + bad("FAIL ") + msg)


def passed(msg: str):
    print("  " + ok("ok   ") + msg)


def skip(msg: str):
    SKIPS.append(msg)
    print("  " + warn("skip ") + msg)


# ── 1. Python compiles ────────────────────────────────────────────────────────
def check_py_compile():
    section("Python compiles")
    import py_compile
    mods = [
        "server.py", "bot.py", "database.py", "ai_router.py", "ai_billing.py",
        "billing_config.py", "prompts.py", "exam_content.py", "exam_grader.py",
        "translations.py", "tts.py",
    ]
    for m in mods:
        path = os.path.join(ROOT, m)
        if not os.path.exists(path):
            skip(f"{m} (not found)")
            continue
        try:
            py_compile.compile(path, doraise=True)
            passed(m)
        except py_compile.PyCompileError as e:
            fail(f"{m}: {e}")


# ── 2. Inline JS parses ────────────────────────────────────────────────────────
_JS_PARSE = r"""
const vm=require("vm"),fs=require("fs");
const html=fs.readFileSync(process.argv[1],"utf8");
const re=/<script\b[^>]*>([\s\S]*?)<\/script>/gi;
let m,i=0,bad=0;
while((m=re.exec(html))){
  i++; const code=m[1]; if(!code.trim())continue;
  try{ new vm.Script(code); }
  catch(e){ bad++; console.log("BLOCK "+i+" "+e.message); }
}
console.log("TOTAL "+i+" BAD "+bad);
"""

def _have_node() -> bool:
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def check_js_parse():
    section("Inline WebApp JS parses")
    if not os.path.exists(HTML):
        fail(f"{HTML} not found")
        return
    if not _have_node():
        skip("Node not available — skipping JS parse")
        return
    out = subprocess.run(["node", "-e", _JS_PARSE, HTML], capture_output=True, text=True)
    lines = (out.stdout + out.stderr).strip().splitlines()
    total_line = next((l for l in lines if l.startswith("TOTAL")), "")
    for l in lines:
        if l.startswith("BLOCK"):
            fail("JS " + l)
    if total_line:
        parts = total_line.split()
        total, nbad = parts[1], parts[3]
        if nbad == "0":
            passed(f"{total} script blocks parse")
        else:
            fail(f"{nbad}/{total} script blocks failed to parse")
    else:
        fail("JS parse produced no result: " + (out.stderr or out.stdout)[:200])


# ── 3. GALAXY_COURSE integrity ─────────────────────────────────────────────────
_COURSE_EXTRACT = r"""
const fs=require("fs");
const html=fs.readFileSync(process.argv[1],"utf8");

// String-aware bracket matcher: given the index of an opening bracket, return
// the index of its matching close (ignoring brackets inside JS strings).
function matchFrom(str, openIdx){
  let depth=0,inStr=false,q="",esc=false;
  for(let i=openIdx;i<str.length;i++){
    const c=str[i];
    if(inStr){ if(esc){esc=false;continue;} if(c==="\\"){esc=true;continue;} if(c===q)inStr=false; continue; }
    if(c==="'"||c==='"'||c==="`"){inStr=true;q=c;continue;}
    if(c==="["||c==="{"||c==="(")depth++;
    else if(c==="]"||c==="}"||c===")"){depth--; if(depth===0)return i;}
  }
  return -1;
}

let all=[];
// (1) base literal:  const GALAXY_COURSE=[ ... ]
const marker="const GALAXY_COURSE=";
const s=html.indexOf(marker);
if(s<0){console.error("MARKER_NOT_FOUND");process.exit(2);}
const open=html.indexOf("[",s);
const end=matchFrom(html,open);
if(end<0){console.error("BASE_UNBALANCED");process.exit(3);}
try{ all=all.concat(eval(html.slice(open,end+1))); }
catch(e){ console.error("BASE_EVAL_FAIL "+e.message); process.exit(3); }

// (2) every  GALAXY_COURSE.push( ...lessons... )  block appended afterwards
let idx=0;
while((idx=html.indexOf("GALAXY_COURSE.push(", idx))>=0){
  const p=html.indexOf("(", idx);
  const pe=matchFrom(html,p);
  if(pe<0){console.error("PUSH_UNBALANCED@"+idx);process.exit(3);}
  try{ all=all.concat(eval("["+html.slice(p+1,pe)+"]")); }
  catch(e){ console.error("PUSH_EVAL_FAIL@"+idx+" "+e.message); process.exit(3); }
  idx=pe+1;
}
process.stdout.write(JSON.stringify(all));
"""

# CEFR levels + exam tracks (mirror COURSE_EXAM_LEVELS in index.html).
VALID_LEVELS = {"A0", "A1", "A2", "B1", "B2", "C1", "C2", "TOEFL", "IELTS", "CAE"}


def _valid_quiz(item, where: str):
    """Return list of error strings for a {q,o,a} quiz block."""
    errs = []
    if not isinstance(item, dict):
        return [f"{where}: not an object"]
    o = item.get("o")
    a = item.get("a")
    if not isinstance(o, list) or len(o) < 2:
        errs.append(f"{where}: 'o' must be a list of >=2 options")
    if not isinstance(a, int):
        errs.append(f"{where}: 'a' must be an int index")
    elif isinstance(o, list) and not (0 <= a < len(o)):
        errs.append(f"{where}: 'a'={a} out of range for {len(o)} options")
    return errs


def check_course():
    section("GALAXY_COURSE integrity")
    if not _have_node():
        skip("Node not available — skipping course validation")
        return
    out = subprocess.run(["node", "-e", _COURSE_EXTRACT, HTML], capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        fail("could not extract GALAXY_COURSE: " + (out.stderr or "no output")[:200])
        return
    try:
        course = json.loads(out.stdout)
    except json.JSONDecodeError as e:
        fail(f"GALAXY_COURSE did not serialise to JSON: {e}")
        return
    if not isinstance(course, list) or not course:
        fail("GALAXY_COURSE is empty or not a list")
        return

    seen = {}
    errs = []
    for idx, ls in enumerate(course):
        where = f"lesson[{idx}]"
        if not isinstance(ls, dict):
            errs.append(f"{where}: not an object")
            continue
        lid = ls.get("id")
        if not lid or not isinstance(lid, str):
            errs.append(f"{where}: missing/invalid id")
        else:
            where = f"'{lid}'"
            if lid in seen:
                errs.append(f"duplicate id '{lid}' (also at index {seen[lid]})")
            seen[lid] = idx
        lvl = str(ls.get("level", "")).upper()
        if lvl not in VALID_LEVELS:
            errs.append(f"{where}: invalid level '{ls.get('level')}'")
        title = ls.get("title")
        if not isinstance(title, dict) or not title.get("ru") or not title.get("en"):
            errs.append(f"{where}: title must have non-empty ru + en")
        # main quiz (present on standard lessons)
        if "o" in ls or "a" in ls:
            errs += _valid_quiz({"o": ls.get("o"), "a": ls.get("a")}, f"{where} main quiz")
        # practice sub-quizzes
        pr = ls.get("practice")
        if pr is not None:
            if not isinstance(pr, list):
                errs.append(f"{where}: 'practice' must be a list")
            else:
                for j, p in enumerate(pr):
                    errs += _valid_quiz(p, f"{where} practice[{j}]")

    if errs:
        for e in errs[:40]:
            fail(e)
        if len(errs) > 40:
            fail(f"... and {len(errs) - 40} more")
    else:
        passed(f"{len(course)} lessons: ids unique, levels valid, titles bilingual, quiz indices in range")


# ── 4. Limit-notice i18n ────────────────────────────────────────────────────────
SUPPORTED_LANGS = ["ru", "en", "es", "pt", "de", "fr", "uk", "tr", "zh", "ar"]


def check_limit_i18n():
    section("Limit-notice i18n")
    # Stub httpx so ai_router imports without its runtime dependency.
    if "httpx" not in sys.modules:
        sys.modules["httpx"] = types.ModuleType("httpx")
    try:
        import ai_router
    except Exception as e:
        fail(f"could not import ai_router: {type(e).__name__}: {e}")
        return

    funcs = []
    for name in ("gemini_free_limit_message", "ai_daily_limit_message"):
        fn = getattr(ai_router, name, None)
        if fn is None:
            fail(f"ai_router.{name} missing")
        else:
            funcs.append((name, fn))

    for name, fn in funcs:
        problems = []
        for lang in SUPPORTED_LANGS + ["zz"]:  # zz => fallback path
            try:
                s = fn(lang)
            except Exception as e:
                problems.append(f"{lang}: raised {type(e).__name__}")
                continue
            if not isinstance(s, str) or not s.strip():
                problems.append(f"{lang}: empty/non-str")
            elif "limit-mini" not in s:
                problems.append(f"{lang}: missing .limit-mini markup")
            elif "openPremium()" not in s:
                problems.append(f"{lang}: missing upgrade link")
        if problems:
            for p in problems[:12]:
                fail(f"{name} {p}")
        else:
            passed(f"{name}: renders for {len(SUPPORTED_LANGS)} langs + fallback")


def main():
    print(_c("1", "PolyGlotty smoke tests"))
    check_py_compile()
    check_js_parse()
    check_course()
    check_limit_i18n()

    print("\n" + "─" * 50)
    if SKIPS:
        print(warn(f"{len(SKIPS)} skipped"))
    if FAILURES:
        print(bad(f"FAILED — {len(FAILURES)} problem(s)"))
        sys.exit(1)
    print(ok("ALL CHECKS PASSED"))
    sys.exit(0)


if __name__ == "__main__":
    main()

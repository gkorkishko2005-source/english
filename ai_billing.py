"""
ai_billing.py — reserve → call → reconcile billing service for ALEX chat.

Flow for one AI request (see handle_chat integration):

    cfg   = await load_config()
    await ensure_allowance(uid, cfg)            # refill monthly pool if due
    model = determine_model(cfg, mode)          # chat→haiku, exam/grammar→sonnet
    ok, why = await can_spend(uid, cfg)         # funds + per-user + global gates
    if not ok: -> clear error, NO API call
    res = await reserve(uid, cfg)               # atomically hold max-reply credits
    if res is None: -> insufficient funds, NO API call
    ... call Anthropic (with prompt caching) ...
    await reconcile(uid, cfg, model, res, usage, cost_usd)  # refund the unused part

Invariants:
  * The owner never goes negative: we charge a *reserve* up front and only ever
    refund (reserve − real), never bill extra after the fact.
  * Money ops are atomic in the DB layer (guarded UPDATE … RETURNING).
  * Allowance is spent before prepaid credits; refunds restore allowance first.
"""
from __future__ import annotations

import math
import datetime
import logging

from billing_config import load_config  # noqa: F401 (re-export convenience)

logger = logging.getLogger(__name__)


def determine_model(cfg: dict, mode: str) -> str:
    """Pick the logical model for this request from the mode→model map."""
    m = (mode or "").strip().lower()
    return cfg.get("MODEL_BY_MODE", {}).get(m, "haiku")


def credits_for_tokens(cfg: dict, tokens: int) -> int:
    tpc = max(1, int(cfg.get("TOKENS_PER_CREDIT", 1000)))
    return max(1, math.ceil(max(0, int(tokens)) / tpc))


def flat_cost_enabled(cfg: dict) -> bool:
    """True when ALEX is billed at a strict flat price per reply."""
    return bool(cfg.get("BILLING_FLAT_COST"))


def reserve_amount(cfg: dict, voice: bool = False) -> int:
    """Credits to hold for one reply.

    FLAT mode (BILLING_FLAT_COST): exactly ALEX_MESSAGE_COST credits — the price
    is fixed regardless of token usage or voice, so the hold equals the charge
    and nothing is refunded on reconcile.

    METERED mode: worst-case credits = max INPUT (system+cache+history) + max
    OUTPUT tokens. This must be an upper bound on the real total so reconcile
    only ever refunds (never under-charges). Voice adds a flat surcharge."""
    if flat_cost_enabled(cfg):
        return max(1, int(cfg.get("ALEX_MESSAGE_COST", 6)))
    worst_tokens = int(cfg.get("MAX_INPUT_TOKENS", 8000)) + int(cfg.get("MAX_TOKENS_PER_REPLY", 1000))
    base = credits_for_tokens(cfg, worst_tokens)
    if voice:
        base += int(cfg.get("VOICE_SURCHARGE_CREDITS", 5))
    return base


async def can_spend(uid: int, cfg: dict, reserve: int) -> tuple[bool, str]:
    """Pre-call gates. Returns (ok, reason). On not-ok the caller must NOT hit
    the API. Reasons: insufficient_funds | daily_token_limit | global_budget."""
    from database import get_wallet, usage_today_tokens, usage_today_cost_global
    w = await get_wallet(uid)
    if (w["allowance"] + w["credits"]) < reserve:
        return False, "insufficient_funds"
    if await usage_today_tokens(uid) >= int(cfg.get("DAILY_TOKENS_PER_USER", 120000)):
        return False, "daily_token_limit"
    if await usage_today_cost_global() >= float(cfg.get("GLOBAL_DAILY_BUDGET_USD", 25.0)):
        return False, "global_budget"
    return True, ""


async def reserve(uid: int, cfg: dict, voice: bool = False) -> dict | None:
    """Hold the worst-case credits for this reply. Returns the reservation
    (with breakdown + 'reserved') or None if the user can't cover it."""
    from database import reserve_funds, ledger_add
    amt = reserve_amount(cfg, voice=voice)
    res = await reserve_funds(uid, amt)
    if res is None:
        return None
    res["reserved"] = amt
    try:
        await ledger_add(uid, "spend", -amt, meta="reserve")
    except Exception as e:
        logger.warning("ledger reserve write failed uid=%s: %s", uid, e)
    return res


async def reconcile(uid: int, cfg: dict, model: str, reservation: dict,
                    usage: dict, cost_usd: float) -> dict:
    """After the real reply: compute actual credits, refund the unused reserve
    (allowance first), and write the authoritative usage_log row."""
    from database import refund_funds, ledger_add, usage_log_add
    in_tok = int((usage or {}).get("input_tokens") or 0)
    out_tok = int((usage or {}).get("output_tokens") or 0)
    reserved = int(reservation.get("reserved") or 0)
    # FLAT mode: the reply costs exactly the reserved amount — no token-metered
    # refund. We still log real token usage for analytics / the global budget.
    if flat_cost_enabled(cfg):
        try:
            await usage_log_add(uid, model, in_tok, out_tok, float(cost_usd or 0.0), reserved)
        except Exception as e:
            logger.warning("usage_log write failed uid=%s: %s", uid, e)
        return {"real_credits": reserved, "refunded": 0,
                "input_tokens": in_tok, "output_tokens": out_tok}
    real = credits_for_tokens(cfg, in_tok + out_tok)
    if real > reserved:
        # Reserve was under-sized — raise MAX_INPUT_TOKENS. We still cap at the
        # reserved hold (never bill more than we reserved) but flag it loudly so
        # the owner can retune before it dents the margin.
        logger.warning("reserve under-sized uid=%s real=%s reserved=%s tokens=%s",
                       uid, real, reserved, in_tok + out_tok)
    real = min(real, reserved)  # we only ever reserved this much; never over-bill
    refund = max(0, reserved - real)
    if refund > 0:
        ref_allow = min(refund, int(reservation.get("allowance_taken") or 0))
        ref_cred = refund - ref_allow
        try:
            await refund_funds(uid, ref_allow, ref_cred)
            await ledger_add(uid, "refund", refund,
                             meta=f"reconcile real={real} reserved={reserved}")
        except Exception as e:
            logger.warning("refund failed uid=%s: %s", uid, e)
    try:
        await usage_log_add(uid, model, in_tok, out_tok, float(cost_usd or 0.0), real)
    except Exception as e:
        logger.warning("usage_log write failed uid=%s: %s", uid, e)
    return {"real_credits": real, "refunded": refund,
            "input_tokens": in_tok, "output_tokens": out_tok}


async def cancel(uid: int, reservation: dict, meta: str = "cancel"):
    """Fully refund a reservation when the request never produced a reply
    (API error / exception). Restores both buckets exactly as taken."""
    if not reservation:
        return
    from database import refund_funds, ledger_add
    a = int(reservation.get("allowance_taken") or 0)
    c = int(reservation.get("credits_taken") or 0)
    if a == 0 and c == 0:
        return
    try:
        await refund_funds(uid, a, c)
        await ledger_add(uid, "refund", a + c, meta=meta)
    except Exception as e:
        logger.warning("reservation cancel failed uid=%s: %s", uid, e)


async def topup(uid: int, credits: int, meta: str = "") -> int:
    """Add prepaid credits (e.g. after a Telegram Stars payment) + ledger entry.
    Returns the new credit balance."""
    from database import add_credits, ledger_add, get_credits
    credits = int(credits)
    if credits <= 0:
        return await get_credits(uid)
    await add_credits(uid, credits)
    try:
        await ledger_add(uid, "topup", credits, meta=meta or "stars")
    except Exception as e:
        logger.warning("ledger topup write failed uid=%s: %s", uid, e)
    return await get_credits(uid)


async def grant_allowance(uid: int, cfg: dict):
    """Set the monthly allowance pool and its next reset date. Called on a fresh
    subscription period."""
    from database import set_allowance, ledger_add
    amt = int(cfg.get("SUBSCRIPTION_ALLOWANCE", 0))
    days = int(cfg.get("ALLOWANCE_DAYS", 30))
    reset = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    await set_allowance(uid, amt, reset)
    try:
        await ledger_add(uid, "allowance", amt, meta="grant")
    except Exception as e:
        logger.warning("ledger allowance write failed uid=%s: %s", uid, e)


async def ensure_allowance(uid: int, cfg: dict):
    """Refill the monthly allowance if the period elapsed AND the subscription is
    still active. No-op for non-subscribers (they ride prepaid credits only)."""
    from database import get_user, check_platform
    u = await get_user(uid) or {}
    reset_at = u.get("allowance_reset_at")
    now = datetime.datetime.utcnow()
    due = False
    if reset_at is None:
        due = True
    else:
        try:
            if isinstance(reset_at, datetime.datetime):
                ra = reset_at.replace(tzinfo=None)
            else:
                s = str(reset_at).replace("Z", "").split("+")[0].strip()
                ra = datetime.datetime.fromisoformat(s)
            due = now >= ra
        except Exception:
            due = True
    if not due:
        return
    try:
        active = await check_platform(uid)
    except Exception:
        active = False
    if active:
        await grant_allowance(uid, cfg)

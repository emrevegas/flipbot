"""Parse bet amounts: numeric, all (full balance), half (50% balance)."""

from __future__ import annotations

from database import db
from modules.flip_utils import fmt_pts

_ALL = frozenset({"all", "max"})
_HALF = frozenset({"half", "1/2", "50%"})


def parse_bet_token(raw: str, balance: float) -> float | None:
    """Turn one token into a bet amount, or None if not a valid bet."""
    if raw is None:
        return None
    key = raw.strip().lower().replace(",", "").replace(" ", "")
    if key in _ALL:
        return float(balance)
    if key in _HALF:
        return float(balance) / 2.0
    try:
        val = float(raw.strip().replace(",", ""))
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


async def resolve_bet_amount(
    user_id: int | str,
    raw: str,
    *,
    ensure_user: bool = True,
) -> tuple[float | None, str | None]:
    """
    Resolve bet for prefix commands.
    Returns (bet, error_message). error_message is set when bet is None.
    """
    if ensure_user:
        await db.ensure_user(user_id, "?")
    user = await db.get_user(user_id)
    if not user:
        return None, "User not found."
    balance = float(user.get("balance", 0))
    bet = parse_bet_token(raw, balance)
    if bet is None:
        return None, "Invalid bet. Use a number, **all**, or **half**."
    if bet <= 0:
        return None, "Bet must be greater than 0."
    if bet > balance + 1e-9:
        return None, f"Insufficient balance. You have **{fmt_pts(balance)} pts**."
    return bet, None


def find_bet_in_tokens(
    tokens: list[str],
    balance: float,
    *,
    skip: set[str] | None = None,
) -> float | None:
    """First token that parses as a bet (skips mentions and skip set)."""
    skip_u = {s.upper() for s in (skip or set())}
    for tok in tokens:
        if tok.startswith("<@"):
            continue
        if tok.strip().upper() in skip_u:
            continue
        bet = parse_bet_token(tok, balance)
        if bet is not None:
            return bet
    return None

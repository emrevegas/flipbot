"""
Soft balance ceiling — users stay below caps without noticing rigging.

When a win would push balance over the effective cap, the round becomes a normal
loss (no trimmed/partial payouts). Near the cap, extra random losses keep play
feeling natural while the player still believes big wins are possible.

Caps (strictest / lowest wins):
  - Global cap (`/panel` → Tools → Global Balance Cap)
  - Per-user admin cap (`/user_panel`)
  - Welcome bonus users: auto cap (default 500 coins on registration)
  - Active promo with promo_max_withdrawal
  - Active deposit bonus with max_withdrawal
"""
from __future__ import annotations

import random
from typing import Optional

from modules.database import get_data, replace_data, get_user_data, set_user_data, check_permission
import modules.promo as promo_engine
import modules.bonus as bonus_engine

WELCOME_CAP_COINS = 500
BALANCE_CAP_SETTINGS_KEY = "server/balance_cap_settings"


def get_welcome_balance_cap() -> int:
    """Server default welcome registration balance ceiling (coins)."""
    settings = get_data("server/referral_settings") or {}
    try:
        cap = int(settings.get("welcome_bonus_balance_cap", WELCOME_CAP_COINS) or WELCOME_CAP_COINS)
    except (TypeError, ValueError):
        cap = WELCOME_CAP_COINS
    return max(1, cap)


def get_user_welcome_balance_cap(user_id) -> Optional[int]:
    wc = get_user_data(int(user_id), "welcome_cap") or {}
    if isinstance(wc, dict) and wc.get("capped"):
        try:
            stored = int(wc.get("ceiling", 0) or 0)
        except (TypeError, ValueError):
            stored = 0
        return stored if stored > 0 else get_welcome_balance_cap()
    return None


def get_admin_balance_cap(user_id) -> Optional[int]:
    data = get_user_data(int(user_id), "balance_cap") or {}
    if not isinstance(data, dict) or not data.get("enabled"):
        return None
    try:
        ceiling = int(data.get("ceiling", 0))
    except (TypeError, ValueError):
        return None
    return ceiling if ceiling > 0 else None


def set_admin_balance_cap(user_id, ceiling: int, *, enabled: bool = True) -> None:
    if ceiling <= 0:
        set_user_data(int(user_id), "balance_cap", {"enabled": False, "ceiling": 0})
        return
    set_user_data(int(user_id), "balance_cap", {
        "enabled": enabled,
        "ceiling": int(ceiling),
        "source": "admin",
    })


def get_balance_cap_settings() -> dict:
    data = get_data(BALANCE_CAP_SETTINGS_KEY) or {}
    return data if isinstance(data, dict) else {}


def get_global_balance_cap() -> Optional[int]:
    data = get_balance_cap_settings()
    if not data.get("global_enabled"):
        return None
    try:
        ceiling = int(data.get("global_ceiling", 0))
    except (TypeError, ValueError):
        return None
    return ceiling if ceiling > 0 else None


def set_global_balance_cap(ceiling: int, *, enabled: bool = True) -> None:
    ceiling = max(0, int(ceiling))
    replace_data(BALANCE_CAP_SETTINGS_KEY, {
        "global_enabled": bool(enabled) and ceiling > 0,
        "global_ceiling": ceiling,
    })


def get_balance_ceiling(user_id, mode: str = "real") -> Optional[int]:
    """Return max real balance target, or None."""
    if str(mode).lower() != "real":
        return None
    uid = int(user_id)
    if not check_permission(uid, "admin"):
        return None

    ceilings: list[int] = []

    global_cap = get_global_balance_cap()
    if global_cap:
        ceilings.append(global_cap)

    admin_cap = get_admin_balance_cap(uid)
    if admin_cap is not None:
        ceilings.append(admin_cap)

    welcome_cap = get_user_welcome_balance_cap(uid)
    if welcome_cap:
        ceilings.append(welcome_cap)

    pmx = promo_engine.get_promo_balance_ceiling(uid)
    if pmx is not None and pmx > 0:
        ceilings.append(int(pmx))

    ab = bonus_engine.get_active_bonus(uid)
    if ab:
        try:
            bmx = int(ab.get("max_withdrawal", 0) or 0)
        except (TypeError, ValueError):
            bmx = 0
        if bmx > 0:
            ceilings.append(bmx)

    if not ceilings:
        return None
    return min(ceilings)


def _cap_applies(user_id, mode: str) -> bool:
    return (
        str(mode).lower() == "real"
        and check_permission(int(user_id), "admin")
    )


def loss_bias_probability(
    user_id,
    mode: str,
    current_balance: int,
    *,
    prospective_balance: Optional[int] = None,
    bet: int = 0,
) -> float:
    """
    0.0 = no bias, up to ~0.97 near/above cap.
    Gradual curve — play still feels winnable far below the cap.
    """
    ceiling = get_balance_ceiling(user_id, mode)
    if ceiling is None or ceiling <= 0:
        return 0.0

    bal = int(current_balance)
    target = int(prospective_balance) if prospective_balance is not None else bal

    if target > ceiling:
        return random.uniform(0.94, 0.99)
    if target >= ceiling:
        return random.uniform(0.88, 0.96)

    ratio = bal / ceiling
    pressure = max(ratio, target / ceiling if ceiling else 0)

    if pressure < 0.40:
        return random.uniform(0.0, 0.05)
    if pressure < 0.60:
        return random.uniform(0.06, 0.18)
    if pressure < 0.78:
        return random.uniform(0.20, 0.38)
    if pressure < 0.90:
        return random.uniform(0.40, 0.62)
    if pressure < 0.97:
        return random.uniform(0.58, 0.78)
    return random.uniform(0.72, 0.90)


def should_suppress_win(
    user_id,
    mode: str,
    current_balance: int,
    *,
    prospective_balance: Optional[int] = None,
    bet: int = 0,
) -> bool:
    p = loss_bias_probability(
        user_id, mode, current_balance,
        prospective_balance=prospective_balance, bet=bet,
    )
    return p > 0 and random.random() < p


def get_game_rigged_chance(game_id: str) -> float:
    games = get_data("server/games") or {}
    entry = games.get(game_id, {}) if isinstance(games, dict) else {}
    if not isinstance(entry, dict):
        return 0.0
    try:
        return max(0.0, min(100.0, float(entry.get("rigged_chance", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def roll_rigged(game_id: str) -> bool:
    chance = get_game_rigged_chance(game_id)
    return chance > 0 and random.uniform(0, 100) < chance


def should_rig_outcome(
    user_id,
    mode: str,
    current_balance: int,
    bet: int,
    payout: int,
    *,
    game_id: str = "",
) -> bool:
    """
    Roll whether this round should be forced to lose before showing the outcome.
    Cap overflow => 100% (via get_cap_aware_rigged_chance); else server rigged_chance.
    """
    payout = max(0, int(payout))
    if str(mode).lower() == "real" and _cap_applies(user_id, mode):
        chance = get_cap_aware_rigged_chance(
            user_id, mode, game_id, int(current_balance), int(bet), payout,
        )
    else:
        chance = get_game_rigged_chance(game_id)
    return chance > 0 and random.uniform(0, 100) < chance


def _predetermined_meta(meta: Optional[dict]) -> dict:
    out = dict(meta) if isinstance(meta, dict) else {}
    out["predetermined"] = True
    return out


def projected_balance_after_payout(current_balance: int, payout: int) -> int:
    """Wallet balance after a win credit (bet already deducted for in-progress games)."""
    return int(current_balance) + max(0, int(payout))


def should_force_cap_loss(
    user_id,
    mode: str,
    current_balance: int,
    payout: int,
    *,
    game_id: str = "",
) -> bool:
    """
    True when crediting this payout would put balance above the effective cap.
    Always lose — used for 100% rigged / forced bomb in Mines etc.
    """
    if not _cap_applies(user_id, mode):
        return False
    payout = max(0, int(payout))
    if payout <= 0:
        return False
    ceiling = get_balance_ceiling(user_id, mode)
    if ceiling is None:
        return False
    return projected_balance_after_payout(current_balance, payout) > int(ceiling)


def get_cap_aware_rigged_chance(
    user_id,
    mode: str,
    game_id: str,
    current_balance: int,
    bet: int,
    payout: int,
) -> float:
    """Server rigged %, or 100 when this payout would exceed the balance cap."""
    if should_force_cap_loss(user_id, mode, current_balance, payout, game_id=game_id):
        return 100.0
    return get_game_rigged_chance(game_id)


def cap_side_bet_payout(
    user_id,
    mode: str,
    current_balance: int,
    side_bet: int,
    payout: int,
    *,
    game_id: str = "blackjack",
) -> int:
    """Cap an immediate side-bet win (stake already deducted with the main bet)."""
    return cap_game_payout(
        user_id, mode, current_balance, int(side_bet), int(payout), game_id=game_id,
    )


def cap_blackjack_settlement(
    user_id,
    mode: str,
    current_balance: int,
    round_stake: int,
    payout: int,
) -> int:
    """
    Hub blackjack main-round credit after all hand/insurance stakes are deducted.
    Returns 0 on cap block or error (never credits over the ceiling).
    """
    payout = max(0, int(payout))
    if payout <= 0:
        return 0
    try:
        return cap_game_payout(
            user_id, mode, int(current_balance), int(round_stake), payout, game_id="blackjack",
        )
    except Exception:
        return 0


def cap_game_payout(
    user_id,
    mode: str,
    current_balance: int,
    bet: int,
    payout: int,
    *,
    game_id: str = "",
) -> int:
    """
    Return payout to credit (0 = round should be treated as a normal loss).
    Never returns a trimmed win — all or nothing.
    """
    payout = max(0, int(payout))
    if payout <= 0 or not _cap_applies(user_id, mode):
        return payout

    ceiling = get_balance_ceiling(user_id, mode)
    if ceiling is None:
        return payout

    bal = int(current_balance)
    if projected_balance_after_payout(bal, payout) > int(ceiling):
        return 0

    if roll_rigged(game_id):
        return 0

    prospective = projected_balance_after_payout(bal, payout)
    if should_suppress_win(
        user_id, mode, bal,
        prospective_balance=prospective, bet=int(bet),
    ):
        return 0

    return payout


def rig_dice_result(game_result):
    """Force a natural-looking dice loss (house roll wins)."""
    from Games.base_game import GameResult
    import random as _rnd

    bet = int(game_result.bet)
    pr = int(game_result.meta.get("player_roll", 1))
    if pr < 6:
        hr = _rnd.randint(pr + 1, 6)
    else:
        pr = _rnd.randint(1, 5)
        hr = 6
    return GameResult(
        "lose", bet,
        meta=_predetermined_meta({"player_roll": pr, "house_roll": hr}),
    )


def rig_coinflip_result(game_result):
    from Games.base_game import GameResult

    bet = int(game_result.bet)
    pf = game_result.meta.get("player_flip", "Hot")
    hf = "Cold" if pf == "Hot" else "Hot"
    return GameResult(
        "lose", bet,
        multiplier=game_result.multiplier,
        meta=_predetermined_meta({"player_flip": pf, "house_flip": hf}),
    )


def rig_roulette_result(game_result):
    """Force loss: player 1–13, house 14–36 (house always higher)."""
    from Games.base_game import GameResult
    import random as _rnd

    bet = int(game_result.bet)
    ps = _rnd.randint(1, 13)
    hs = _rnd.randint(14, 36)
    return GameResult(
        "lose", bet,
        multiplier=game_result.multiplier,
        meta=_predetermined_meta({"player_spin": ps, "house_spin": hs}),
    )


def rig_case_open_results(
    items: list,
    pf_floats: list,
    count: int,
) -> tuple[list, int]:
    """Return low-value item pulls so the roll looks normal but pays below stake."""
    if not items:
        return [], 0
    ranked = sorted(items, key=lambda x: int(x.get("value", 0) or 0))
    pool = ranked[: max(1, (len(ranked) + 2) // 3)]
    results: list = []
    for i in range(count):
        fv = pf_floats[i % len(pf_floats)] if pf_floats else 0.0
        idx = int(fv * len(pool)) % len(pool)
        results.append(dict(pool[idx]))
    total = sum(int(r.get("value", 0) or 0) for r in results)
    return results, total


def rig_limbo_result(game_result, target_multiplier: float):
    from Games.base_game import GameResult
    import random as _rnd

    bet = int(game_result.bet)
    target = float(target_multiplier)
    forced_max = max(1.00, round(target - 0.01, 2))
    forced_val = round(_rnd.uniform(1.00, forced_max), 2)
    return GameResult(
        "lose", bet, multiplier=0.0,
        meta=_predetermined_meta({
            "result_value": forced_val,
            "target_multiplier": target,
        }),
        amount=0,
    )


def adjust_game_result(game_result, user_id, mode: str, game_id: str = ""):
    """
    No post-display mutation — each game must call should_rig_outcome + rig_* before UI.
    Kept for API compatibility; returns the result unchanged.
    """
    return game_result


def predetermine_loss_required(
    user_id,
    mode: str,
    current_balance: int,
    bet: int,
    prospective_payout: int,
    *,
    game_id: str = "",
) -> bool:
    """True when the round must be rigged to a natural loss before showing the outcome."""
    return should_rig_outcome(
        user_id, mode, current_balance, bet, prospective_payout, game_id=game_id,
    )


def revert_hilo_win_to_loss(state: dict) -> None:
    """Turn the latest HiLo win step into a bust (looks like a normal bad card)."""
    if state.get("last_result") != "win":
        return
    hist = state.get("history") or []
    if hist:
        last = hist[-1]
        try:
            mult = float(last.get("mult", 1.0) or 1.0)
        except (TypeError, ValueError):
            mult = 1.0
        if mult > 1.0 and state.get("multiplier", 1.0) > 1.0:
            state["multiplier"] = round(float(state["multiplier"]) / mult, 4)
        state["round"] = max(0, int(state.get("round", 0)) - 1)
        last["result"] = "lose"
        last["mult"] = 0.0
    state["phase"] = "done"
    state["last_result"] = "lose"


def apply_hilo_step_bias(state: dict, user_id, mode: str, current_balance: int) -> None:
    """After a winning HiLo guess, maybe end the round as a loss."""
    if state.get("last_result") != "win" or str(mode).lower() != "real":
        return
    if not _cap_applies(user_id, mode):
        return

    bet = int(state.get("bet", 0))
    prospective = int(current_balance) + int(bet * float(state.get("multiplier", 1.0)))

    ceiling = get_balance_ceiling(user_id, mode)
    if ceiling is not None and prospective > ceiling:
        revert_hilo_win_to_loss(state)
        return

    if roll_rigged("hilo"):
        revert_hilo_win_to_loss(state)
        return

    if should_suppress_win(
        user_id, mode, current_balance,
        prospective_balance=prospective, bet=bet,
    ):
        revert_hilo_win_to_loss(state)


def cap_hilo_cashout_payout(user_id, mode: str, current_balance: int, bet: int, multiplier: float) -> int:
    """Cashout amount to pay (0 = treat as bust, no balance added)."""
    payout = int(bet * float(multiplier))
    return cap_game_payout(user_id, mode, current_balance, bet, payout, game_id="hilo")

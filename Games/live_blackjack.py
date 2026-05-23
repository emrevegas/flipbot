"""
Multi-seat live blackjack round engine.

Reuses card math from Games/blackjack.py; separate game_id for stats/economy.
"""

from __future__ import annotations

import random
import time
from typing import Any

from Games.blackjack import (
    NUM_DECKS,
    RANKS,
    SUITS,
    _cur,
    can_double,
    can_insurance,
    can_split,
    do_double,
    do_insurance,
    do_split,
    evaluate,
    evaluate_side_bets,
    get_bj_emojis,
    hand_value,
    is_blackjack,
    _make_deck,
)
from Games.blackjack import hand_display as bj_hand_display
from modules.database import get_data
from modules.utils import format_balance

import modules.live_blackjack_tables as tables

PHASE_PLAYING = tables.PHASE_PLAYING
PHASE_SETTLING = tables.PHASE_SETTLING


def get_rigged_chance() -> float:
    games = get_data("server/games") or {}
    lb = games.get("live_blackjack", {}) if isinstance(games, dict) else {}
    try:
        v = float(lb.get("rigged_chance", 0) or 0)
    except (TypeError, ValueError):
        v = 0.0
    return max(0.0, min(100.0, v))


def _roll_rigged() -> bool:
    pct = get_rigged_chance()
    if pct <= 0:
        return False
    return random.random() * 100 < pct


def _seat_hand_state(seat: dict) -> dict:
    return {
        "cards": [],
        "bet": int(seat.get("bet") or 0),
        "side_pp": int(seat.get("side_pp") or 0),
        "side_21_3": int(seat.get("side_21_3") or 0),
        "status": "playing",
        "doubled": False,
        "from_split": False,
        "insurance_bet": 0,
        "seat_user_id": int(seat.get("user_id") or 0),
        "subhands": None,
    }


def _rig_initial_deal(state: dict) -> None:
    from cogs.games import _rig_initial_deal as solo_rig

    solo_rig(state)


def _rig_hit_card(state: dict) -> None:
    from cogs.games import _rig_hit_card as solo_rig

    solo_rig(state)


def _rig_dealer_beat(state: dict) -> None:
    from cogs.games import _rig_dealer_beat as solo_rig

    solo_rig(state)


def begin_round(table: dict) -> bool:
    """
    Deal to all seats with confirmed bets; start turn on first seat.
    Returns True if a round started, False if cancelled (no confirms).
    """
    tables.kick_two_seats_no_bet(table)

    active_seats = [
        i
        for i, s in enumerate(table["seats"])
        if s.get("user_id") and s.get("bet_confirmed") and int(s.get("bet") or 0) > 0
    ]
    if not active_seats:
        tables.abort_round_no_confirms(table)
        return False

    streak = tables._no_confirm_streak_map(table)
    for idx in active_seats:
        uid = table["seats"][idx].get("user_id")
        if uid:
            streak.pop(str(int(uid)), None)

    deck = _make_deck()
    dealer = [deck.pop(), deck.pop()]
    seat_states = []
    for idx in active_seats:
        seat = table["seats"][idx]
        hand = _seat_hand_state(seat)
        hand["cards"] = [deck.pop(), deck.pop()]
        seat_states.append({"seat_idx": idx, "hand": hand, "cur_sub": 0})

    round_state = {
        "deck": deck,
        "dealer": dealer,
        "seat_states": seat_states,
        "turn_idx": 0,
        "phase": "playing",
        "rigged": _roll_rigged(),
        "turn_deadline": int(time.time()) + tables.TURN_TIMEOUT_SECONDS,
        "side_results": {},
    }

    if round_state["rigged"]:
        pseudo = {
            "deck": deck,
            "dealer": dealer,
            "hands": [seat_states[0]["hand"]],
            "cur": 0,
            "phase": "playing",
        }
        _rig_initial_deal(pseudo)
        dealer = pseudo["dealer"]
        deck = pseudo["deck"]
        seat_states[0]["hand"] = pseudo["hands"][0]
        round_state["deck"] = deck
        round_state["dealer"] = dealer

    import modules.balance_cap as balance_cap
    from modules.player import Player

    for entry in seat_states:
        hand = entry["hand"]
        if len(hand.get("cards", [])) < 2:
            continue
        pseudo_full = {
            "deck": deck,
            "dealer": dealer,
            "hands": [hand],
            "cur": 0,
            "side_pp": hand["side_pp"],
            "side_21_3": hand["side_21_3"],
            "insurance_bet": 0,
            "phase": "playing",
        }
        sr = evaluate_side_bets(pseudo_full)
        round_state["side_results"][entry["seat_idx"]] = sr
        side_win = int(sr.get("pp_payout", 0)) + int(sr.get("t3_payout", 0))
        uid = int(hand.get("seat_user_id") or 0)
        if side_win and uid:
            pl = Player(uid)
            side_stake = int(hand.get("side_pp") or 0) + int(hand.get("side_21_3") or 0)
            try:
                if balance_cap.predetermine_loss_required(
                    uid,
                    "real",
                    pl.get_balance("real"),
                    side_stake or int(hand.get("bet") or 0),
                    side_win,
                    game_id="live_blackjack",
                ):
                    side_win = 0
                    sr["pp_payout"] = 0
                    sr["t3_payout"] = 0
                    round_state["side_results"][entry["seat_idx"]] = sr
            except Exception:
                pass
            if side_win:
                pl.add_balance("real", side_win)

    table["round"] = round_state
    table["dealer"] = dealer
    table["deck"] = deck
    table["phase"] = PHASE_PLAYING
    table["countdown_announce"] = None
    table["status_flash"] = None
    table["card_reveal"] = 1
    _sync_turn_after_deal(table)
    return True


def _sync_turn_after_deal(table: dict) -> None:
    """Skip natural-blackjack seats; finish round if every seat is already done."""
    rnd = table.get("round")
    if not rnd or rnd.get("phase") != "playing":
        return
    states = rnd.get("seat_states") or []
    while int(rnd.get("turn_idx", 0)) < len(states):
        hand = states[int(rnd["turn_idx"])]["hand"]
        if hand.get("status") == "playing" and is_blackjack(hand.get("cards", [])):
            hand["status"] = "stood"
        if hand.get("status") == "playing":
            rnd["turn_deadline"] = int(time.time()) + tables.TURN_TIMEOUT_SECONDS
            return
        rnd["turn_idx"] = int(rnd["turn_idx"]) + 1
    _finish_dealer(table)


def _current_turn(table: dict) -> tuple[int, dict] | None:
    rnd = table.get("round") or {}
    states = rnd.get("seat_states") or []
    if not states or rnd.get("phase") != "playing":
        return None
    ti = int(rnd.get("turn_idx", 0))
    if ti < 0 or ti >= len(states):
        return None
    hand = states[ti]["hand"]
    if hand.get("status") != "playing":
        return None
    return states[ti]["seat_idx"], hand


def _visible_cards(cards: list, table: dict) -> list:
    """During deal animation only show the first N cards per hand."""
    reveal = table.get("card_reveal")
    if reveal is None:
        return cards
    try:
        n = int(reveal)
    except (TypeError, ValueError):
        return cards
    return cards[: max(0, n)]


def _advance_seat_hand(state: dict) -> dict:
    """Move to next split hand on this seat without running dealer (live multi-seat)."""
    for i in range(state["cur"] + 1, len(state["hands"])):
        if state["hands"][i]["status"] == "playing":
            state["cur"] = i
            return state
    return state


def _live_hit(state: dict) -> dict:
    h = _cur(state)
    h["cards"].append(state["deck"].pop())
    val = hand_value(h["cards"])
    if val > 21:
        h["status"] = "busted"
        return _advance_seat_hand(state)
    if val == 21:
        h["status"] = "stood"
        return _advance_seat_hand(state)
    return state


def _live_stand(state: dict) -> dict:
    _cur(state)["status"] = "stood"
    return _advance_seat_hand(state)


def _live_split(state: dict, player=None, mode: str = "real") -> dict:
    h = _cur(state)
    idx = state["cur"]
    extra = h["bet"]
    if player:
        player.remove_balance(mode, extra)
    c1, c2 = h["cards"][0], h["cards"][1]
    n1 = state["deck"].pop()
    n2 = state["deck"].pop()
    state["hands"][idx] = {
        "cards": [c1, n1],
        "bet": h["bet"],
        "status": "playing",
        "doubled": False,
        "from_split": True,
        "side_pp": h.get("side_pp", 0),
        "side_21_3": h.get("side_21_3", 0),
        "insurance_bet": h.get("insurance_bet", 0),
        "seat_user_id": h.get("seat_user_id", 0),
        "subhands": None,
    }
    state["hands"].insert(
        idx + 1,
        {
            "cards": [c2, n2],
            "bet": extra,
            "status": "playing",
            "doubled": False,
            "from_split": True,
            "side_pp": 0,
            "side_21_3": 0,
            "insurance_bet": 0,
            "seat_user_id": h.get("seat_user_id", 0),
            "subhands": None,
        },
    )
    for hand in state["hands"][idx : idx + 2]:
        if hand["status"] == "playing" and hand_value(hand["cards"]) == 21:
            hand["status"] = "stood"
    if state["hands"][idx]["status"] != "playing":
        return _advance_seat_hand(state)
    return state


def _live_double(state: dict, player=None, mode: str = "real") -> dict:
    h = _cur(state)
    extra = h["bet"]
    if player:
        player.remove_balance(mode, extra)
    h["bet"] += extra
    h["doubled"] = True
    h["cards"].append(state["deck"].pop())
    val = hand_value(h["cards"])
    h["status"] = "busted" if val > 21 else "stood"
    return _advance_seat_hand(state)


def _build_play_state(table: dict, hand: dict) -> dict:
    rnd = table["round"] or {}
    return {
        "deck": rnd["deck"],
        "dealer": rnd["dealer"],
        "hands": [hand],
        "cur": 0,
        "side_pp": hand.get("side_pp", 0),
        "side_21_3": hand.get("side_21_3", 0),
        "insurance_bet": hand.get("insurance_bet", 0),
        "phase": "playing",
        "side_results": table["round"].get("side_results", {}).get(
            _current_turn(table)[0] if _current_turn(table) else 0, {}
        ),
        "rigged": rnd.get("rigged", False),
    }


def _advance_turn(table: dict) -> None:
    rnd = table["round"]
    states = rnd["seat_states"]
    ti = int(rnd["turn_idx"]) + 1
    while ti < len(states):
        if states[ti]["hand"].get("status") == "playing":
            rnd["turn_idx"] = ti
            rnd["turn_deadline"] = int(time.time()) + tables.TURN_TIMEOUT_SECONDS
            return
        ti += 1
    _finish_dealer(table)


def _finish_dealer(table: dict) -> None:
    """Compute final dealer hand; UI animation reveals it step by step."""
    rnd = table["round"]
    all_hands = [e["hand"] for e in rnd["seat_states"]]
    pseudo = {
        "deck": list(rnd["deck"]),
        "dealer": list(rnd["dealer"]),
        "hands": all_hands,
        "cur": 0,
        "phase": "playing",
    }
    if rnd.get("rigged"):
        _rig_dealer_beat(pseudo)
    from Games.blackjack import _play_dealer

    _play_dealer(pseudo)
    rnd["dealer_final"] = list(pseudo["dealer"])
    rnd["dealer_deck_final"] = list(pseudo["deck"])
    rnd["phase"] = "dealer_anim"
    rnd["turn_idx"] = -1
    table["dealer_show_count"] = min(2, len(rnd["dealer_final"]))
    table["dealer_hole_hidden"] = True


def _apply_balance_cap_rig_before_settle(table: dict) -> None:
    """Rig dealer to beat seats that would exceed balance cap — before UI settlement."""
    rnd = table.get("round") or {}
    if rnd.get("phase") != "done" and rnd.get("dealer_final"):
        pass  # caller sets done below
    dealer = list(rnd.get("dealer") or rnd.get("dealer_final") or [])
    deck = list(rnd.get("deck") or rnd.get("dealer_deck_final") or [])
    if not dealer:
        return

    try:
        import modules.balance_cap as balance_cap
        from modules.player import Player
        from cogs.games import _rig_dealer_beat
    except Exception:
        return

    needs_rig = False
    hands_for_rig = []
    for entry in rnd.get("seat_states") or []:
        hand = entry.get("hand") or {}
        uid = int(hand.get("seat_user_id") or 0)
        if not uid:
            continue
        main_bet = int(hand.get("bet") or 0)
        pseudo = {
            "deck": list(deck),
            "dealer": list(dealer),
            "hands": [hand],
            "cur": 0,
            "phase": "done",
            "insurance_bet": int(hand.get("insurance_bet") or 0),
            "side_results": rnd.get("side_results", {}).get(entry.get("seat_idx"), {}),
        }
        ev = evaluate(pseudo)
        payout = int(ev.get("total_return") or 0)
        if payout <= 0:
            continue
        pl = Player(uid)
        if balance_cap.predetermine_loss_required(
            uid, "real", pl.get_balance("real"), main_bet, payout, game_id="live_blackjack",
        ):
            needs_rig = True
            hands_for_rig.append(hand)

    if not needs_rig:
        return

    pseudo = {
        "deck": list(deck),
        "dealer": list(dealer),
        "hands": hands_for_rig or [e["hand"] for e in rnd.get("seat_states", [])],
        "cur": 0,
        "phase": "done",
    }
    _rig_dealer_beat(pseudo)
    rnd["dealer"] = pseudo["dealer"]
    rnd["deck"] = pseudo["deck"]
    table["dealer"] = pseudo["dealer"]
    if rnd.get("dealer_final") is not None:
        rnd["dealer_final"] = list(pseudo["dealer"])


def finalize_round_display(table: dict) -> list[dict]:
    """Apply final dealer cards, evaluate seats, store results for UI."""
    rnd = table.get("round") or {}
    if rnd.get("dealer_final"):
        rnd["dealer"] = rnd.pop("dealer_final")
        table["dealer"] = rnd["dealer"]
    if rnd.get("dealer_deck_final") is not None:
        rnd["deck"] = rnd.pop("dealer_deck_final")
    rnd["phase"] = "done"
    table["phase"] = PHASE_SETTLING
    table.pop("dealer_show_count", None)
    table.pop("dealer_hole_hidden", None)

    _apply_balance_cap_rig_before_settle(table)

    results = settle_round(table, {})
    table["round_results"] = results
    table["result_display_until"] = int(time.time()) + 8
    return results


def result_for_seat(table: dict, seat_idx: int) -> dict | None:
    for r in table.get("round_results") or []:
        if int(r.get("seat_idx", -1)) == seat_idx:
            return r
    return None


def format_result_badge(result: dict) -> str:
    ev = result.get("evaluate") or {}
    hands = ev.get("results") or []
    label = (hands[0].get("result") if hands else "lose") or "lose"
    net = int(result.get("net") or 0)
    names = {
        "win": "WIN",
        "blackjack": "BLACKJACK",
        "push": "PUSH",
        "lose": "LOSE",
        "bust": "BUST",
    }
    title = names.get(label, label.upper())
    if net > 0:
        return f"**🟢 {title}** +{format_balance(net, 'real')}"
    if net < 0:
        return f"**🔴 {title}** −{format_balance(abs(net), 'real')}"
    return f"**⚪ {title}** {format_balance(0, 'real')}"


def result_accent(result: dict | None) -> int:
    if not result:
        return 0x5865F2
    net = int(result.get("net") or 0)
    if net > 0:
        return 0x2ECC71
    if net < 0:
        return 0xE74C3C
    return 0x95A5A6


def apply_action(table: dict, user_id: int, action: str) -> tuple[bool, str]:
    uid = int(user_id)
    turn = _current_turn(table)
    if not turn:
        return False, "Not your turn."
    seat_idx, hand = turn
    seat = table["seats"][seat_idx]
    if int(seat.get("user_id") or 0) != uid:
        return False, "Not your turn."

    rnd = table["round"]
    state = _build_play_state(table, hand)

    if action == "hit":
        if rnd.get("rigged"):
            _rig_hit_card(state)
        _live_hit(state)
    elif action == "stand":
        _live_stand(state)
    elif action == "double":
        if not can_double(state):
            return False, "Cannot double."
        from modules.player import Player

        player = Player(uid)
        if player.get_balance("real") < hand["bet"]:
            return False, "Insufficient balance to double."
        if rnd.get("rigged"):
            _rig_hit_card(state)
        _live_double(state, player=player, mode="real")
    elif action == "split":
        if not can_split(state):
            return False, "Cannot split."
        from modules.player import Player

        player = Player(uid)
        _live_split(state, player=player, mode="real")
    elif action == "insurance":
        if not can_insurance(state):
            return False, "Insurance not available."
        ins_amt = hand["bet"] // 2
        from modules.player import Player

        player = Player(uid)
        if player.get_balance("real") < ins_amt:
            return False, "Insufficient balance for insurance."
        do_insurance(state, ins_amt, player=player, mode="real")
        hand["insurance_bet"] = ins_amt
    else:
        return False, "Unknown action."

    hand.update(state["hands"][state["cur"]])
    rnd["deck"] = state["deck"]
    rnd["dealer"] = state["dealer"]

    if hand["status"] != "playing":
        _advance_turn(table)
    else:
        rnd["turn_deadline"] = int(time.time()) + tables.TURN_TIMEOUT_SECONDS

    if rnd.get("phase") == "dealer_anim":
        return True, "dealer_anim"
    if rnd.get("phase") == "done":
        return True, "round_done"
    return True, ""


def auto_stand_if_timed_out(table: dict) -> bool:
    rnd = table.get("round")
    if not rnd or rnd.get("phase") != "playing":
        return False
    if int(time.time()) < int(rnd.get("turn_deadline") or 0):
        return False
    turn = _current_turn(table)
    if not turn:
        return False
    seat_idx, hand = turn
    uid = int(table["seats"][seat_idx].get("user_id") or 0)
    if not uid:
        _advance_turn(table)
        return True
    apply_action(table, uid, "stand")
    return True


def settle_round(table: dict, member_map: dict[int, Any]) -> list[dict]:
    """Return list of settlement dicts per seat; caller runs economy."""
    rnd = table.get("round") or {}
    results = []
    if rnd.get("phase") != "done":
        return results

    dealer = rnd["dealer"]
    for entry in rnd.get("seat_states", []):
        seat_idx = entry["seat_idx"]
        hand = entry["hand"]
        uid = int(hand.get("seat_user_id") or 0)
        pseudo = {
            "deck": rnd["deck"],
            "dealer": dealer,
            "hands": [hand],
            "cur": 0,
            "phase": "done",
            "insurance_bet": hand.get("insurance_bet", 0),
            "side_results": rnd.get("side_results", {}).get(seat_idx, {}),
        }
        ev = evaluate(pseudo)
        main_bet = int(hand.get("bet") or 0)
        side_pp = int(hand.get("side_pp") or 0)
        side_21_3 = int(hand.get("side_21_3") or 0)
        total_stake = main_bet + side_pp + side_21_3 + int(hand.get("insurance_bet") or 0)
        total_return = int(ev.get("total_return") or 0)
        net = total_return - total_stake
        results.append({
            "user_id": uid,
            "seat_idx": seat_idx,
            "bet": main_bet,
            "total_stake": total_stake,
            "total_return": total_return,
            "net": net,
            "evaluate": ev,
            "member": member_map.get(uid),
        })
    return results


def get_emoji_map(bot=None) -> dict:
    """Same emoji source as solo blackjack (guild lookup, then saved map)."""
    if bot is not None:
        try:
            from cogs.games import _get_bj_emoji_map

            em = _get_bj_emoji_map(bot)
            if em:
                return em
        except Exception:
            pass
    em = get_bj_emojis()
    if em:
        return em
    games = get_data("server/games") or {}
    bj = games.get("blackjack", {}) if isinstance(games, dict) else {}
    return bj.get("emojis", {}) if isinstance(bj.get("emojis"), dict) else {}


def _dealer_cards_for_display(table: dict) -> list:
    rnd = table.get("round") or {}
    if rnd.get("phase") == "dealer_anim":
        final = rnd.get("dealer_final") or rnd.get("dealer") or []
        n = int(table.get("dealer_show_count") or len(final))
        return list(final[: max(0, n)])
    dealer = table.get("dealer") or rnd.get("dealer") or []
    return _visible_cards(dealer, table)


def format_dealer_line(table: dict, emoji_map: dict, hide_hole: bool) -> str:
    rnd = table.get("round") or {}
    shown = _dealer_cards_for_display(table)
    if not shown:
        return "—"
    if rnd.get("phase") == "dealer_anim":
        hide = bool(table.get("dealer_hole_hidden")) and len(shown) >= 2
    else:
        hide = hide_hole and len(shown) >= 2
    line = bj_hand_display(shown, emoji_map, hide_second=hide)
    if hide:
        up = hand_value([shown[0]])
        return f"{line} (showing {up})"
    val = hand_value(shown)
    return f"{line} ({val})"


def format_seat_line(table: dict, seat_idx: int, emoji_map: dict) -> str:
    seat = table["seats"][seat_idx]
    uid = seat.get("user_id")
    if uid is None:
        return f"**Seat {seat_idx + 1}** — Empty"
    rnd = table.get("round")
    cards_txt = ""
    if rnd and rnd.get("phase") in ("playing", "done", "dealer_anim"):
        for entry in rnd.get("seat_states", []):
            if entry["seat_idx"] == seat_idx:
                h = entry["hand"]
                shown = _visible_cards(h["cards"], table)
                if not shown:
                    break
                cards_txt = bj_hand_display(shown, emoji_map)
                val = hand_value(shown)
                cards_txt = f"\n# {cards_txt}\n({val}) · {h.get('status', '')}"
                break
    res = result_for_seat(table, seat_idx)
    result_txt = f"\n{format_result_badge(res)}" if res else ""
    bet = int(seat.get("bet") or seat.get("pending_bet") or 0)
    pp = int(seat.get("side_pp") or seat.get("pending_side_pp") or 0)
    t3 = int(seat.get("side_21_3") or seat.get("pending_side_21_3") or 0)
    conf = "✅" if seat.get("bet_confirmed") else "⏳"
    extras = ""
    if pp:
        extras += f" · PP {format_balance(pp, 'real')}"
    if t3:
        extras += f" · 21+3 {format_balance(t3, 'real')}"
    return (
        f"**Seat {seat_idx + 1}** — <@{uid}> {conf} "
        f"bet {format_balance(bet, 'real')}{extras}{cards_txt}{result_txt}"
    )

"""
Blackjack — 6-deck shoe, dealer stands on soft 17.

Features:
  - Split (up to 3 times → 4 hands max); split Aces receive 1 card each
  - Double Down (any first 2 cards, costs extra bet)
  - Insurance (dealer shows Ace, pays 2:1)
  - Side Bets: Perfect Pairs, 21+3
  - Blackjack pays 3:2

Card emoji naming convention (guild emojis):
  {rank}{suit}   rank ∈ {A,2,3,4,5,6,7,8,9,0,J,Q,K}   suit ∈ {C,H,D,S}
  Examples: AC AH AD AS 2C 0H JD QS KH   (0 = 10)
  Card back: CB
"""

import random
from typing import Optional

# ─── Card constants ────────────────────────────────────────────────────────────

RANKS        = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '0', 'J', 'Q', 'K']
SUITS        = ['C', 'H', 'D', 'S']
SUIT_SYMBOLS = {'C': '♣', 'H': '♥', 'D': '♦', 'S': '♠'}
_RANK_DISPLAY = {'0': '10'}
NUM_DECKS    = 6

# ─── Deck & hand helpers ───────────────────────────────────────────────────────

def _make_deck() -> list[str]:
    deck = [r + s for r in RANKS for s in SUITS] * NUM_DECKS
    random.shuffle(deck)
    return deck


def _card_points(card: str) -> int:
    r = card[0]
    if r in ('J', 'Q', 'K', '0'):
        return 10
    if r == 'A':
        return 11
    return int(r)


def hand_value(cards: list[str]) -> int:
    total = sum(_card_points(c) for c in cards)
    aces  = sum(1 for c in cards if c[0] == 'A')
    while total > 21 and aces:
        total -= 10
        aces  -= 1
    return total


def is_blackjack(cards: list[str]) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


def _rank_label(rank: str) -> str:
    return _RANK_DISPLAY.get(rank, rank)


# ─── Display helpers ───────────────────────────────────────────────────────────

def card_display(card: str, emoji_map: dict) -> str:
    """Return emoji for card, fall back to text like `10♦`."""
    em = emoji_map.get(card)
    if em:
        return em
    r, s = card[0], card[1]
    return f"`{_rank_label(r)}{SUIT_SYMBOLS[s]}`"


def hand_display(cards: list[str], emoji_map: dict,
                 hide_second: bool = False) -> str:
    parts = []
    for i, card in enumerate(cards):
        if i == 1 and hide_second:
            parts.append(emoji_map.get("CB") or "🎴")
        else:
            parts.append(card_display(card, emoji_map))
    return "  ".join(parts)


# ─── Side bet evaluations ──────────────────────────────────────────────────────

_RED_SUITS = {'H', 'D'}


def eval_perfect_pairs(c1: str, c2: str) -> tuple[Optional[str], int]:
    """Returns (label, multiplier) or (None, 0)."""
    if c1[0] != c2[0]:
        return None, 0
    if c1[1] == c2[1]:
        return "perfect_pair", 25
    if (c1[1] in _RED_SUITS) == (c2[1] in _RED_SUITS):
        return "colored_pair", 10
    return "mixed_pair", 5


def eval_21plus3(c1: str, c2: str, dealer_up: str) -> tuple[Optional[str], int]:
    """Returns (label, multiplier) or (None, 0)."""
    cards = [c1, c2, dealer_up]
    ranks = [c[0] for c in cards]
    suits = [c[1] for c in cards]
    idxs  = sorted(RANKS.index(r) for r in ranks)
    same_rank   = len(set(ranks)) == 1
    same_suit   = len(set(suits)) == 1
    is_straight = len(set(idxs)) == 3 and idxs[2] - idxs[0] == 2
    if same_rank and same_suit:
        return "suited_three_of_a_kind", 100
    if same_rank:
        return "three_of_a_kind", 30
    if same_suit and is_straight:
        return "straight_flush", 40
    if same_suit:
        return "flush", 5
    if is_straight:
        return "straight", 10
    return None, 0


# ─── Game state ────────────────────────────────────────────────────────────────

def new_state(bet: int, side_pp: int = 0, side_21_3: int = 0) -> dict:
    """Create and deal a fresh game state. Returns serialisable dict."""
    deck   = _make_deck()
    p_hand = [deck.pop(), deck.pop()]
    d_hand = [deck.pop(), deck.pop()]
    return {
        "deck":          deck,
        "dealer":        d_hand,
        "hands": [{
            "cards":      p_hand,
            "bet":        bet,
            "status":     "playing",   # playing | stood | busted
            "doubled":    False,
            "from_split": False,
        }],
        "cur":           0,            # index of current hand
        "side_pp":       side_pp,
        "side_21_3":     side_21_3,
        "insurance_bet": 0,
        "phase":         "playing",    # playing | done
        "side_results":  None,         # filled after deal
    }


def _cur(state: dict) -> dict:
    return state["hands"][state["cur"]]


# ─── Action guards ─────────────────────────────────────────────────────────────

def can_split(state: dict) -> bool:
    if state["phase"] != "playing":
        return False
    h = _cur(state)
    if h["status"] != "playing" or len(h["cards"]) != 2:
        return False
    if len(state["hands"]) >= 4:
        return False
    # Same rank character only (Q+10 can't split; Q+Q can)
    return h["cards"][0][0] == h["cards"][1][0]


def can_double(state: dict) -> bool:
    if state["phase"] != "playing":
        return False
    h = _cur(state)
    return h["status"] == "playing" and len(h["cards"]) == 2


def can_insurance(state: dict) -> bool:
    return (
        state["phase"] == "playing"
        and state["dealer"][0][0] == "A"
        and state.get("insurance_bet", 0) == 0
        and len(state["hands"]) == 1
        and len(state["hands"][0]["cards"]) == 2
    )


# ─── State transitions ─────────────────────────────────────────────────────────

def _advance(state: dict) -> dict:
    """Move to next playing hand; if none, let dealer play."""
    for i in range(state["cur"] + 1, len(state["hands"])):
        if state["hands"][i]["status"] == "playing":
            state["cur"] = i
            return state
    # All hands done
    _play_dealer(state)
    state["phase"] = "done"
    return state


def do_hit(state: dict) -> dict:
    h = _cur(state)
    h["cards"].append(state["deck"].pop())
    val = hand_value(h["cards"])
    if val > 21:
        h["status"] = "busted"
        state = _advance(state)
    elif val == 21:
        h["status"] = "stood"
        state = _advance(state)
    return state


def do_stand(state: dict) -> dict:
    _cur(state)["status"] = "stood"
    return _advance(state)


def do_double(state: dict, player=None, mode: str = "real") -> dict:
    h = _cur(state)
    extra = h["bet"]
    if player:
        player.remove_balance(mode, extra)
    h["bet"]     += extra
    h["doubled"]  = True
    h["cards"].append(state["deck"].pop())
    val = hand_value(h["cards"])
    h["status"] = "busted" if val > 21 else "stood"
    return _advance(state)


def do_split(state: dict, player=None, mode: str = "real") -> dict:
    h   = _cur(state)
    idx = state["cur"]
    extra = h["bet"]
    if player:
        player.remove_balance(mode, extra)
    c1, c2 = h["cards"][0], h["cards"][1]
    n1 = state["deck"].pop()
    n2 = state["deck"].pop()
    state["hands"][idx] = {
        "cards": [c1, n1], "bet": h["bet"],
        "status": "playing", "doubled": False, "from_split": True,
    }
    state["hands"].insert(idx + 1, {
        "cards": [c2, n2], "bet": extra,
        "status": "playing", "doubled": False, "from_split": True,
    })
    # Auto-stand any new hand that already hit 21 (e.g. A+10 after ace split)
    for hand in state["hands"][idx: idx + 2]:
        if hand["status"] == "playing" and hand_value(hand["cards"]) == 21:
            hand["status"] = "stood"
    # If the first split hand is already done, advance to the next
    if state["hands"][idx]["status"] != "playing":
        state = _advance(state)
    return state


def do_insurance(state: dict, amount: int, player=None, mode: str = "real") -> dict:
    if player:
        player.remove_balance(mode, amount)
    state["insurance_bet"] = amount
    return state


def _play_dealer(state: dict) -> None:
    """Dealer draws to 17+, stands on soft 17."""
    d = state["dealer"]
    while hand_value(d) < 17:
        d.append(state["deck"].pop())


# ─── Evaluation ───────────────────────────────────────────────────────────────

def evaluate_side_bets(state: dict) -> dict:
    """Evaluate PP and 21+3 bets immediately after deal."""
    cards = state["hands"][0]["cards"]
    if len(cards) < 2:
        return {}
    c1, c2     = cards[0], cards[1]
    dealer_up  = state["dealer"][0]
    pp_lbl, pp_mult = eval_perfect_pairs(c1, c2)
    t3_lbl, t3_mult = eval_21plus3(c1, c2, dealer_up)
    pp_bet = state.get("side_pp", 0)
    t3_bet = state.get("side_21_3", 0)
    return {
        "pp_label":   pp_lbl,
        "pp_mult":    pp_mult,
        "pp_payout":  int(pp_bet * pp_mult) if pp_bet and pp_mult else 0,
        "pp_bet":     pp_bet,
        "t3_label":   t3_lbl,
        "t3_mult":    t3_mult,
        "t3_payout":  int(t3_bet * t3_mult) if t3_bet and t3_mult else 0,
        "t3_bet":     t3_bet,
    }


def evaluate(state: dict) -> dict:
    """Final evaluation after dealer plays. Returns payouts per hand + totals."""
    d_cards = state["dealer"]
    d_val   = hand_value(d_cards)
    d_bj    = is_blackjack(d_cards)
    results = []
    total_ret = 0

    for h in state["hands"]:
        cards = h["cards"]
        val   = hand_value(cards)
        bet   = h["bet"]
        h_bj  = is_blackjack(cards) and not h["from_split"]
        if h["status"] == "busted":
            result, payout = "bust", 0
        elif h_bj and not d_bj:
            result, payout = "blackjack", bet + int(bet * 1.5)
        elif d_bj and not h_bj:
            result, payout = "lose", 0
        elif val > d_val or d_val > 21:
            result, payout = "win", bet * 2
        elif val == d_val:
            result, payout = "push", bet
        else:
            result, payout = "lose", 0
        results.append({
            "cards": cards, "bet": bet, "value": val,
            "result": result, "payout": payout,
        })
        total_ret += payout

    ins_bet = state.get("insurance_bet", 0)
    if ins_bet and d_bj:
        total_ret += ins_bet * 3   # 2:1 + original back

    # Side bets are credited at deal time (hub blackjack); do not add again here.

    return {
        "results":          results,
        "dealer_value":     d_val,
        "dealer_blackjack": d_bj,
        "total_return":     total_ret,
    }


# ─── Emoji map helper ──────────────────────────────────────────────────────────

def get_bj_emojis() -> dict:
    """Return saved card emoji map from DB (keys: AC, 8H, 0D, CB, …)."""
    try:
        from modules.database import get_data
        gd     = get_data("server/games") or {}
        bj     = gd.get("blackjack", {}) if isinstance(gd, dict) else {}
        emojis = bj.get("emojis", {})
        return emojis if isinstance(emojis, dict) else {}
    except Exception:
        return {}

"""
HiLo — Provably Fair kart tahmin oyunu.

Kart sistemi Blackjack ile aynıdır (A,2,...,K × C,H,D,S).
House edge: %10

Nasıl oynanır:
  - Bir kart gösterilir.
  - Oyuncu 📈 Higher (bir sonraki kart daha büyük) veya
           📉 Lower  (bir sonraki kart daha küçük) tahminde bulunur.
  - Doğru tahmin → çarpan artar, oyun devam eder.
  - Yanlış tahmin → bahis kaybedilir.
  - Aynı değer (push) → çarpan değişmez, oyun devam eder.
    !! İstisna: Mevcut kart As veya 2 ise aynı değer = KAYIP.
  - As (en yüksek kart): sadece Lower ve Same seçenekleri aktif.
  - 2  (en düşük kart) : sadece Higher ve Same seçenekleri aktif.
  - İstediği zaman Cash Out → birikmiş çarpan × bahis kazanılır.

Kart değerleri: A=14 (en yüksek), 2-9=face, 0/T=10, J=11, Q=12, K=13
"""

import random

RANKS      = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '0', 'J', 'Q', 'K']
SUITS      = ['C', 'H', 'D', 'S']
HOUSE_EDGE = 0.10  # %10

RANK_VALUE: dict[str, int] = {
    'A': 14, '2': 2, '3': 3, '4': 4, '5': 5,
    '6': 6, '7': 7, '8': 8, '9': 9, '0': 10,
    'J': 11, 'Q': 12, 'K': 13,
}


def make_hilo_deck(floats: list) -> list:
    """PF float listesinden deterministik karıştırılmış 52 kartlık deste üret."""
    deck = [r + s for r in RANKS for s in SUITS]
    seed = (
        int(floats[0] * 2**32)
        ^ int(floats[1] * 2**32)
        ^ int(floats[2] * 2**32)
    )
    rng = random.Random(seed)
    rng.shuffle(deck)
    return deck


def card_value(card: str) -> int:
    """Kartın sıralama değerini döndür (A=14 en yüksek, 2 en düşük)."""
    return RANK_VALUE[card[0]]


def calc_hilo_odds(current_card: str, remaining: list) -> dict:
    """
    Mevcut kart ve henüz çekilmemiş kartlar için Higher/Lower çarpan ve yüzdelerini hesapla.

    remaining: mevcut kartın arkasındaki kartlar (mevcut kart dahil değil).
    """
    total = len(remaining)
    if total == 0:
        return {
            "higher_mult":  0.0, "lower_mult":  0.0,
            "higher_pct":   0.0, "lower_pct":   0.0,
            "higher_count": 0,   "lower_count": 0,
            "same_count":   0,   "total":       0,
        }

    cv            = card_value(current_card)
    higher_count  = sum(1 for c in remaining if card_value(c) > cv)
    lower_count   = sum(1 for c in remaining if card_value(c) < cv)
    same_count    = total - higher_count - lower_count
    higher_pct    = higher_count / total
    lower_pct     = lower_count  / total
    higher_mult   = round((1.0 - HOUSE_EDGE) / higher_pct, 2) if higher_pct > 0 else 0.0
    lower_mult    = round((1.0 - HOUSE_EDGE) / lower_pct,  2) if lower_pct  > 0 else 0.0

    return {
        "higher_mult":  higher_mult,
        "lower_mult":   lower_mult,
        "higher_pct":   round(higher_pct * 100, 1),
        "lower_pct":    round(lower_pct  * 100, 1),
        "higher_count": higher_count,
        "lower_count":  lower_count,
        "same_count":   same_count,
        "total":        total,
    }


def new_hilo_state(bet: int, floats: list, game_uid: str = "") -> dict:
    """Yeni HiLo oyun durumu oluştur."""
    deck = make_hilo_deck(floats)
    return {
        "deck":        deck,
        "card_idx":    0,          # mevcut kartın deste indeksi
        "multiplier":  1.0,        # birikmiş çarpan
        "bet":         bet,
        "round":       0,          # tamamlanan kazanma turu sayısı
        "phase":       "playing",  # "playing" | "done"
        "last_choice": None,       # son hamle: "higher" | "lower"
        "last_result": None,       # son sonuç: "win" | "lose" | "push" | "cashout"
        "history":     [],         # son turların özeti
        "game_uid":    game_uid,
    }


def hilo_guess(state: dict, choice: str) -> dict:
    """
    Higher veya Lower tahmini işle. state'i in-place günceller ve döndürür.
    choice: "higher" | "lower"
    """
    deck        = state["deck"]
    current_idx = state["card_idx"]
    current     = deck[current_idx]

    # Deste bitti → zorla cashout
    if current_idx + 1 >= len(deck):
        state["phase"]       = "done"
        state["last_result"] = "cashout"
        return state

    nxt      = deck[current_idx + 1]
    cur_val  = card_value(current)
    nxt_val  = card_value(nxt)

    # Tahmin yapmadan önce kalan destenin oranlarını hesapla
    remaining = deck[current_idx + 1:]
    odds      = calc_hilo_odds(current, remaining)

    if   nxt_val > cur_val: actual = "higher"
    elif nxt_val < cur_val: actual = "lower"
    else:                   actual = "same"

    hist_entry = {
        "card":   current,
        "next":   nxt,
        "choice": choice,
        "actual": actual,
        "mult":   1.0,
        "result": "push",
    }

    # As (14) ve 2'de: aynı değer artık doğru yönde seçim yapıldıysa kazandırır
    _extreme = card_value(current) in (14, 2)

    if actual == "same" and _extreme:
        # Ace → "Same or Higher" bet: choice=="higher" wins on same rank
        # 2   → "Same or Lower"  bet: choice=="lower"  wins on same rank
        _correct_dir = (card_value(current) == 14 and choice == "higher") or \
                       (card_value(current) == 2  and choice == "lower")
        if _correct_dir:
            # WIN on same rank at extreme card
            same_count = odds["same_count"]
            _total     = odds["total"]
            same_pct   = same_count / _total if _total > 0 else 0
            same_mult  = round((1.0 - HOUSE_EDGE) / same_pct, 2) if same_pct > 0 else 0.0
            state["multiplier"]  = round(state["multiplier"] * same_mult, 4)
            state["round"]      += 1
            state["card_idx"]    = current_idx + 1
            state["last_choice"] = choice
            state["last_result"] = "win"
            hist_entry["result"] = "win"
            hist_entry["mult"]   = same_mult
        else:
            # LOSE — wrong direction (e.g., chose "lower" on Ace and got same rank)
            state["phase"]       = "done"
            state["last_choice"] = choice
            state["last_result"] = "lose"
            hist_entry["result"] = "lose"
            hist_entry["mult"]   = 0.0

    elif actual == "same":
        # Normal push — çarpan değişmez, sonraki karta geç
        state["card_idx"]    = current_idx + 1
        state["last_choice"] = choice
        state["last_result"] = "push"

    elif actual == choice:
        # Kazandı
        mult                 = odds[f"{choice}_mult"]
        state["multiplier"]  = round(state["multiplier"] * mult, 4)
        state["round"]      += 1
        state["card_idx"]    = current_idx + 1
        state["last_choice"] = choice
        state["last_result"] = "win"
        hist_entry["result"] = "win"
        hist_entry["mult"]   = mult

    else:
        # Kaybetti
        state["phase"]       = "done"
        state["last_choice"] = choice
        state["last_result"] = "lose"
        hist_entry["result"] = "lose"
        hist_entry["mult"]   = 0.0

    state["history"].append(hist_entry)
    if len(state["history"]) > 10:
        state["history"] = state["history"][-10:]

    return state

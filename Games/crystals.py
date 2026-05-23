"""
Crystals oyunu — Provably Fair 5 kristal açılışı
5 kristal teker teker açılır; eşleşme kombinasyonuna göre çarpan uygulanır.
Game logic only — Discord API yok.
"""
import hashlib
import hmac as _hmac
from collections import Counter
from .base_game import BaseGame, GameResult


class CrystalsGame(BaseGame):
    CRYSTAL_TYPES = ["blue", "white", "black", "purple", "yellow", "green", "red", "aqua"]
    COUNT = 5

    # ── Varsayılan ödeme tablosu ──────────────────────────────────────────────
    DEFAULT_MULTIPLIERS: dict = {
        "quintuple": 20.0,   # 5 aynı renk
        "quadruple":  4.80,  # 4 aynı renk
        "full_house": 3.84,  # 3 + 2
        "triple":     2.88,  # 3 aynı renk
        "two_pair":   1.92,  # 2 + 2
        "one_pair":   0.10,  # 2 aynı (teselli ödülü)
        "no_match":   0.0,   # Eşleşme yok
    }

    # ── Combo görünen adları ──────────────────────────────────────────────────
    COMBO_LABELS: dict = {
        "quintuple":  "🌟 QUINTUPLE!",
        "quadruple":  "🔥 QUADRUPLE!",
        "full_house": "💫 FULL HOUSE!",
        "triple":     "✨ TRIPLE!",
        "two_pair":   "⭐ TWO PAIR!",
        "one_pair":   "💠 ONE PAIR",
        "no_match":   "❌ No Match",
    }

    def __init__(self):
        super().__init__(name="Crystals", emoji="💎", game_id="crystals")

    # ── Provably Fair kristal üretimi ─────────────────────────────────────────

    @staticmethod
    def generate_crystals(server_seed: str, client_seed: str, nonce: int, count: int = 5) -> list:
        """
        HMAC-SHA256 tabanlı Provably Fair kristal üretimi.

        Adımlar:
          1. 2 round HMAC-SHA256 ile 16 float üretilir (5 için yeterli).
          2. Her float, CRYSTAL_TYPES dizisindeki bir indekse eşlenir.

        Döndürür: count uzunlukta kristal tipi listesi (str).
        """
        floats: list = []
        for extra in range(2):
            msg = f"{client_seed}:{nonce}:{extra}".encode()
            digest = _hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
            floats.extend(
                int.from_bytes(digest[i * 4:(i + 1) * 4], "big") / (2 ** 32)
                for i in range(8)
            )

        types = CrystalsGame.CRYSTAL_TYPES
        return [types[int(floats[i] * len(types))] for i in range(count)]

    @staticmethod
    def generate_no_match_crystals(server_seed: str, client_seed: str, nonce: int, count: int = 5) -> list:
        """PF kristaller — 5 farklı renk (no_match), adil görünür kayıp."""
        floats: list = []
        for extra in range(2):
            msg = f"{client_seed}:{nonce}:{extra}".encode()
            digest = _hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
            floats.extend(
                int.from_bytes(digest[i * 4:(i + 1) * 4], "big") / (2 ** 32)
                for i in range(8)
            )
        types = CrystalsGame.CRYSTAL_TYPES
        order = list(range(len(types)))
        for i in range(len(types) - 1, 0, -1):
            j = int(floats[len(types) - 1 - i] * (i + 1))
            order[i], order[j] = order[j], order[i]
        picked = [types[order[i]] for i in range(min(count, len(types)))]
        while len(picked) < count:
            picked.append(types[len(picked) % len(types)])
        return picked[:count]

    # ── Kombinasyon analizi ───────────────────────────────────────────────────

    @staticmethod
    def get_combo(crystals: list) -> str:
        """5 kristale bakarak kombinasyon tipini döndürür."""
        counts = Counter(crystals)
        freqs = sorted(counts.values(), reverse=True)

        if freqs[0] == 5:
            return "quintuple"
        if freqs[0] == 4:
            return "quadruple"
        if freqs[0] == 3 and len(freqs) > 1 and freqs[1] == 2:
            return "full_house"
        if freqs[0] == 3:
            return "triple"
        if freqs[0] == 2 and len(freqs) > 1 and freqs[1] == 2:
            return "two_pair"
        if freqs[0] == 2:
            return "one_pair"
        return "no_match"

    @staticmethod
    def get_multiplier(combo: str, multipliers: dict | None = None) -> float:
        """Kombinasyona göre çarpanı döndürür."""
        mults = multipliers if multipliers is not None else CrystalsGame.DEFAULT_MULTIPLIERS
        return float(mults.get(combo, 0.0))

    # ── BaseGame stubs ─────────────────────────────────────────────────────────

    def play_round(self, bet: int, **kwargs) -> GameResult:
        """Rastgele oyun (test için). Gerçek akış cogs/games.py içinde."""
        import random
        crystals = [random.choice(self.CRYSTAL_TYPES) for _ in range(self.COUNT)]
        combo = self.get_combo(crystals)
        mult = self.get_multiplier(combo)
        if mult > 1.0:
            result = "win"
        elif mult == 1.0:
            result = "tie"
        else:
            result = "lose"
        return GameResult(
            result=result,
            bet=bet,
            multiplier=mult,
            meta={"crystals": crystals, "combo": combo},
            amount=int(bet * mult),
        )

    async def play(self, interaction, message_id, player, bet, mode, **kwargs):
        """No-op: tam interaktif akış cogs/games.py içinde yönetilir."""
        pass

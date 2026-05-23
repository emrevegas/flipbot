"""
Towers oyunu — Provably Fair 10 katlı kule tırmanışı
Her katta 1 bomba saklanmış; güvenli kolonu seç ve yukarı çık.
Game logic only — no Discord API here.
"""
import hashlib
import hmac as _hmac
from .base_game import BaseGame, GameResult


class TowersGame(BaseGame):
    FLOORS = 10

    # Moda göre kolon (sütun) sayısı — her katta 1 bomba
    COLS = {
        "easy":   4,   # 3 safe  / 1 bomb
        "normal": 3,   # 2 safe  / 1 bomb
        "hard":   2,   # 1 safe  / 1 bomb
    }

    # Çarpan tablosu: her indexi kat 1..10'a karşılık gelir (0-indexed = kat 1)
    MULTIPLIERS: dict = {
        "easy":   [1.23, 1.65, 2.20, 2.90, 4.00, 5.02,  7.3, 9.40,  12.50,  17.05],
        "normal": [1.44, 2.16, 3.24, 4.86, 7.29, 10.93, 16.40,  24.60,  36.91,  55.36],
        "hard":   [1.90, 3.80, 7.60, 15.30, 30.60, 61.44, 122.88, 245.76, 491.52, 983.04],
    }

    def __init__(self):
        super().__init__(name="Towers", emoji="🗼", game_id="towers")

    # ── Provably Fair board generation ────────────────────────────────────────

    @staticmethod
    def generate_floors(server_seed: str, client_seed: str, nonce: int, tower_mode: str) -> list:
        """
        Her kat için bombanın bulunduğu kolon indeksini (0-indexed) üretir.

        Adımlar:
          1. 2 round HMAC-SHA256 → 16 float (10 kat için yeterli).
          2. Her floatı [0, cols) aralığına map'le.

        Döndürür: 10 elemanlı liste — her eleman o katın bomba kolon indeksi.
        """
        cols = TowersGame.COLS.get(tower_mode, 3)
        floats: list[float] = []
        for extra in range(2):
            msg = f"{client_seed}:{nonce}:{extra}".encode()
            digest = _hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
            floats.extend(
                int.from_bytes(digest[i * 4:(i + 1) * 4], "big") / (2 ** 32)
                for i in range(8)
            )
        return [int(floats[i] * cols) for i in range(TowersGame.FLOORS)]

    # ── BaseGame stubs ─────────────────────────────────────────────────────────

    def play_round(self, bet: int, **kwargs) -> GameResult:
        raise NotImplementedError("Towers is interactive; use the Discord UI flow.")

    async def play(self, interaction, message_id, player, bet, mode, **kwargs):
        """No-op: tam interaktif akış cogs/games.py içinde yönetilir."""
        pass

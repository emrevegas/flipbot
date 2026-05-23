"""
Mines oyunu — Provably Fair 5×4 grid (20 hücre)
Game logic only — no Discord API here.
"""
import math
import hashlib
import hmac as _hmac
from .base_game import BaseGame, GameResult


class MinesGame(BaseGame):
    ROWS = 4
    COLS = 5
    TOTAL = ROWS * COLS  # 20 cells

    def __init__(self):
        super().__init__(name="Mines", emoji="💣", game_id="mines")

    # ── Multiplier calculation ─────────────────────────────────────────────────

    @staticmethod
    def nCr(n: int, r: int) -> int:
        f = math.factorial
        return f(n) // f(r) // f(n - r)

    @staticmethod
    def calc_multiplier(mine_count: int, diamonds: int, house_edge: float = 0.15) -> float:
        """
        Cashout multiplier for `diamonds` safely revealed cells.
        Based on: (1 - house_edge) × C(20, diamonds) / C(20 - mines, diamonds)
        """
        n = MinesGame.TOTAL  # 20
        if diamonds <= 0:
            return 1.0
        safe = n - mine_count
        if diamonds > safe or safe <= 0:
            return 1.0
        return round(
            (1 - house_edge) * MinesGame.nCr(n, diamonds) / MinesGame.nCr(safe, diamonds),
            4,
        )

    # ── Board generation (Provably Fair) ──────────────────────────────────────

    @staticmethod
    def generate_board(server_seed: str, client_seed: str, nonce: int, mine_count: int) -> list:
        """
        Deterministically places mines on a 5×4 grid using extended HMAC-SHA256.

        Steps:
          1. Generate 32 floats from 4 rounds of HMAC-SHA256(server_seed, "{client_seed}:{nonce}:{i}")
          2. Apply Fisher-Yates shuffle on cell indices [0..19]
          3. First `mine_count` shuffled indices become mines

        Returns a 2-D list where 1 = mine, 0 = safe cell.
        """
        total = MinesGame.TOTAL  # 20
        # 4 HMAC rounds × 8 floats/round = 32 floats (Fisher-Yates needs ≤ 19)
        floats: list[float] = []
        for extra in range(4):
            msg = f"{client_seed}:{nonce}:{extra}".encode()
            digest = _hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
            floats.extend(
                int.from_bytes(digest[i * 4:(i + 1) * 4], "big") / (2 ** 32)
                for i in range(8)
            )

        # Fisher-Yates partial shuffle
        cells = list(range(total))
        for i in range(total - 1, 0, -1):
            j = int(floats[total - 1 - i] * (i + 1))
            cells[i], cells[j] = cells[j], cells[i]

        mine_positions = set(cells[:mine_count])
        board = []
        for r in range(MinesGame.ROWS):
            board.append([
                1 if (r * MinesGame.COLS + c) in mine_positions else 0
                for c in range(MinesGame.COLS)
            ])
        return board

    # ── BaseGame stubs ─────────────────────────────────────────────────────────

    def play_round(self, bet: int, **kwargs) -> GameResult:
        raise NotImplementedError("Mines is interactive; use the Discord UI flow.")

    async def play(self, interaction, message_id, player, bet, mode, mine_count: int = 3):
        """No-op: the full interactive flow is handled in cogs/games.py."""
        pass

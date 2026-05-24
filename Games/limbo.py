"""
Limbo oyunu — Provably Fair
House edge: %8
Kazanma olasılığı: 92 / target_multiplier
Kazanç: bet * target_multiplier
"""
import random
from .base_game import BaseGame, GameResult


HOUSE_EDGE = 0.10  # %8


class LimboGame(BaseGame):
    """Limbo — kullanıcı hedef çarpan seçer, sonuç >= target ise kazanır."""

    def __init__(self):
        super().__init__(name="Limbo", emoji="🚀", multiplier=1.0, game_id="limbo")

    @staticmethod
    def win_chance(target_multiplier: float) -> float:
        """Verilen çarpan için kazanma olasılığı (0-1)."""
        return (1.0 - HOUSE_EDGE) / target_multiplier

    @staticmethod
    def roll_result_value() -> float:
        """Random multiplier: (1 - house_edge) / U(0,1], min 1.00x (Stake-style limbo)."""
        f = max(random.random(), 0.000001)
        return max(1.00, round((1.0 - HOUSE_EDGE) / f, 2))

    @staticmethod
    def generate_result(server_seed: str, client_seed: str, nonce: int) -> float:
        """
        Provably fair sonuç üret.
        PF float listesinin ilk elemanından [1.00, +∞) aralığında bir sonuç türetir.
        Formül: result = (1 - HOUSE_EDGE) / float  → minimum 1.00x
        """
        from modules.provably_fair import pf_floats
        floats = pf_floats(server_seed, client_seed, nonce)
        f = floats[0]
        if f == 0:
            f = 0.000001
        raw = (1.0 - HOUSE_EDGE) / f
        # Alt sınır 1.00x
        return max(1.00, round(raw, 2))

    def play_round(self, bet: int, target_multiplier: float, floats: list = None) -> GameResult:
        """Bir tur oyna."""
        if floats:
            f = floats[0]
            if f == 0:
                f = 0.000001
            raw = (1.0 - HOUSE_EDGE) / f
            result_value = max(1.00, round(raw, 2))
        else:
            result_value = self.roll_result_value()

        result = "win" if result_value >= target_multiplier else "lose"
        payout = int(bet * target_multiplier) if result == "win" else 0

        return GameResult(
            result=result,
            bet=bet,
            multiplier=target_multiplier if result == "win" else 0.0,
            meta={"result_value": result_value, "target_multiplier": target_multiplier},
            amount=payout,
        )

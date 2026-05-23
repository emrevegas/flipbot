"""
Games package - Tüm oyunlar
"""
from .base_game import BaseGame, GameResult
from .roulette import RouletteGame
from .dice import DiceGame
from .coinflip import CoinFlipGame
from .mines import MinesGame
from .crystals import CrystalsGame
from .towers import TowersGame
from .limbo import LimboGame
from .slot import SlotGame
from .blackjack import (
    get_bj_emojis, new_state as bj_new_state, hand_value as bj_hand_value,
    hand_display as bj_hand_display, card_display as bj_card_display,
    can_split as bj_can_split, can_double as bj_can_double,
    can_insurance as bj_can_insurance, is_blackjack as bj_is_blackjack,
    do_hit, do_stand, do_double, do_split, do_insurance,
    evaluate as bj_evaluate, evaluate_side_bets, eval_perfect_pairs, eval_21plus3,
    RANKS as BJ_RANKS, SUITS as BJ_SUITS,
)
from .hilo import (
    calc_hilo_odds, new_hilo_state, hilo_guess,
    HOUSE_EDGE as HILO_HOUSE_EDGE,
)

__all__ = [
    'BaseGame', 'GameResult',
    'RouletteGame', 'DiceGame', 'CoinFlipGame', 'MinesGame',
    'CrystalsGame', 'TowersGame', 'LimboGame', 'SlotGame',
    'get_bj_emojis', 'bj_new_state', 'bj_hand_value', 'bj_hand_display',
    'bj_card_display', 'bj_can_split', 'bj_can_double', 'bj_can_insurance',
    'bj_is_blackjack', 'do_hit', 'do_stand', 'do_double', 'do_split',
    'do_insurance', 'bj_evaluate', 'evaluate_side_bets',
    'eval_perfect_pairs', 'eval_21plus3', 'BJ_RANKS', 'BJ_SUITS',
    'calc_hilo_odds', 'new_hilo_state', 'hilo_guess', 'HILO_HOUSE_EDGE',
]

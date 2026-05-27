import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import time
import random
from modules.database import (
    get_data,
    set_data,
    replace_data,
    check_permission,
    is_super_admin,
    get_server_data,
    set_server_data,
    get_user_data,
    get_all_registered_user_ids,
)
from modules.translator import t
from modules.utils import *
import modules.bonus as bonus_engine


def _build_admin_panel_embed(interaction: discord.Interaction) -> discord.Embed:
    """Build the rich admin panel home embed (user language)."""
    guild = interaction.guild
    uid = str(interaction.user.id)
    guild_id = str(guild.id)
    server_data = get_server_data(guild_id)
    root_data = get_server_data()

    reg_channel_id = server_data.get("registration_channel")
    private_cat_id = server_data.get("private_category_id")
    withdraw_channel_id = server_data.get("withdraw_channel")
    deposit_cat_id = server_data.get("deposit_category")
    cashier_role_id = server_data.get("cashier_role")
    min_withdrawal = server_data.get("min_withdrawal", 100)
    min_bet = format_balance(root_data.get("minBet", 20), "real")
    max_bet = format_balance(root_data.get("maxBet", 50000), "real")

    def ch(v):
        return f"<#{v}>" if v else "`—`"

    def ro(v):
        return f"<@&{v}>" if v else "`—`"

    pf_log_ch_id = server_data.get("pf_log_channel")
    deposit_log_ch_id = (get_data("server/deposit_settings") or {}).get("channel_id")
    games_data = get_data("server/games") or {}
    enabled_games = sum(1 for g in games_data.values() if isinstance(g, dict) and g.get("enabled"))
    bonus_templates = bonus_engine.get_bonus_templates() or {}
    active_bonuses = sum(1 for b in bonus_templates.values() if b.get("enabled"))

    embed = discord.Embed(
        title=t("admin_panel.home_title", user_id=uid),
        description=t(
            "admin_panel.home_description",
            user_id=uid,
            guild=guild.name,
            members=guild.member_count or "?",
            user=interaction.user.mention,
        ),
        color=0x2B2D31,
    )
    embed.add_field(
        name=t("admin_panel.hubs.home_channels", user_id=uid),
        value=(
            t("admin_panel.hubs.channels.summary", user_id=uid) + "\n\n"
            + t(
                "admin_panel.hubs.channels.line_registration",
                user_id=uid,
                reg=ch(reg_channel_id),
            )
            + " · "
            + t(
                "admin_panel.hubs.channels.line_private",
                user_id=uid,
                room=ch(private_cat_id),
            )
        ),
        inline=True,
    )
    embed.add_field(
        name=t("admin_panel.hubs.home_payments", user_id=uid),
        value=(
            t("admin_panel.hubs.payments.summary", user_id=uid)
            + "\n\n"
            + t("admin_panel.hubs.payments.line_deposit", user_id=uid, dep=ch(deposit_cat_id))
            + "\n"
            + t("admin_panel.hubs.payments.line_withdraw", user_id=uid, wd=ch(withdraw_channel_id))
            + "\n"
            + t(
                "admin_panel.hubs.payments.line_min_wd",
                user_id=uid,
                amount=format_balance(min_withdrawal, "real"),
            )
        ),
        inline=True,
    )
    embed.add_field(
        name=t("admin_panel.hubs.home_games", user_id=uid),
        value=(
            t("admin_panel.hubs.games.summary", user_id=uid)
            + "\n\n"
            + t(
                "admin_panel.hubs.games.line_active",
                user_id=uid,
                count=enabled_games,
                min=min_bet,
                max=max_bet,
            )
            + "\n"
            + t(
                "admin_panel.hubs.games.line_log",
                user_id=uid,
                log=ch(server_data.get("game_log_channel") or pf_log_ch_id),
            )
        ),
        inline=True,
    )
    embed.add_field(
        name=t("admin_panel.hubs.home_rewards", user_id=uid),
        value=(
            t("admin_panel.hubs.rewards.summary", user_id=uid)
            + "\n\n"
            + t(
                "admin_panel.hubs.rewards.line_bonus",
                user_id=uid,
                active=active_bonuses,
                configs=len(get_data("server/giveaway_configs") or {}),
            )
        ),
        inline=True,
    )
    embed.add_field(
        name=t("admin_panel.hubs.home_tools", user_id=uid),
        value=t("admin_panel.hubs.tools.summary", user_id=uid),
        inline=True,
    )
    embed.add_field(
        name=t("admin_panel.hubs.home_quick", user_id=uid),
        value=t(
            "admin_panel.hubs.home_quick_line",
            user_id=uid,
            dep_log=ch(deposit_log_ch_id),
            cashier=ro(cashier_role_id),
        ),
        inline=False,
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(
        text=t("admin_panel.home_footer", user_id=uid, admin=interaction.user.name)
    )
    return embed


def _ensure_mines_game_entry(games_data: dict) -> dict:
    """Ensure `mines` exists in server/games with required management fields."""
    if not isinstance(games_data, dict):
        games_data = {}

    mines = games_data.get("mines")
    if not isinstance(mines, dict):
        mines = {}

    mines.setdefault("name", "Mines")
    mines.setdefault("emoji", "💣")
    mines.setdefault("enabled", True)
    mines.setdefault("description", "Navigate a minefield — more gems = bigger multiplier!")
    mines.setdefault("min_bet", 10)
    mines.setdefault("max_bet", 10000)
    mines.setdefault("house_edge", 15.0)
    mines.setdefault("rigged_chance", 5.0)
    mines.setdefault("category", "table_games")
    mines.setdefault("created_at", int(time.time()))
    mines.setdefault("last_modified", int(time.time()))

    emojis = mines.get("emojis")
    if not isinstance(emojis, dict):
        emojis = {}
    emojis.setdefault("hidden", "❓")
    emojis.setdefault("gem", "💎")
    emojis.setdefault("mine", "💣")
    mines["emojis"] = emojis

    games_data["mines"] = mines
    return games_data


def _ensure_crystals_game_entry(games_data: dict) -> dict:
    """Ensure `crystals` exists in server/games with required management fields."""
    if not isinstance(games_data, dict):
        games_data = {}

    crystals = games_data.get("crystals")
    if not isinstance(crystals, dict):
        crystals = {}

    crystals.setdefault("name", "Crystals")
    crystals.setdefault("emoji", "💎")
    crystals.setdefault("enabled", True)
    crystals.setdefault("description", "Reveal 5 crystals — match them to multiply your bet!")
    crystals.setdefault("min_bet", 10)
    crystals.setdefault("max_bet", 10000)
    crystals.setdefault("house_edge", 5.0)
    crystals.setdefault("rigged_chance", 5.0)
    crystals.setdefault("category", "special_games")
    crystals.setdefault("created_at", int(time.time()))
    crystals.setdefault("last_modified", int(time.time()))

    emojis = crystals.get("emojis")
    if not isinstance(emojis, dict):
        emojis = {}
    emojis.setdefault("game", "💎")
    emojis.setdefault("hidden", "🔮")
    crystal_types = emojis.get("crystals")
    if not isinstance(crystal_types, dict):
        crystal_types = {}
    for k, v in {
        "blue": "🔵", "white": "⚪", "black": "⚫", "purple": "🟣",
        "yellow": "🟡", "green": "🟢", "red": "🔴", "aqua": "💧",
    }.items():
        crystal_types.setdefault(k, v)
    emojis["crystals"] = crystal_types
    crystals["emojis"] = emojis

    mults = crystals.get("multipliers")
    if not isinstance(mults, dict):
        mults = {}
    for k, v in {
        "quintuple": 20.0, "quadruple": 4.80, "full_house": 3.84,
        "triple": 2.88, "two_pair": 1.92, "one_pair": 0.10, "no_match": 0.0,
    }.items():
        mults.setdefault(k, v)
    crystals["multipliers"] = mults

    games_data["crystals"] = crystals
    return games_data


def _ensure_towers_game_entry(games_data: dict) -> dict:
    """Ensure `towers` exists in server/games with required management fields."""
    if not isinstance(games_data, dict):
        games_data = {}

    towers = games_data.get("towers")
    if not isinstance(towers, dict):
        towers = {}

    towers.setdefault("name", "Towers")
    towers.setdefault("emoji", "🗼")
    towers.setdefault("enabled", True)
    towers.setdefault("description", "Climb the 10-floor tower! Pick the safe column on each floor.")
    towers.setdefault("min_bet", 10)
    towers.setdefault("max_bet", 10000)
    towers.setdefault("house_edge", 0.0)
    towers.setdefault("rigged_chance", 15.0)
    towers.setdefault("category", "special_games")
    towers.setdefault("created_at", int(time.time()))
    towers.setdefault("last_modified", int(time.time()))

    emojis = towers.get("emojis")
    if not isinstance(emojis, dict):
        emojis = {}
    emojis.setdefault("game",   "🗼")
    emojis.setdefault("hidden", "🔮")
    emojis.setdefault("gem",    "💎")
    emojis.setdefault("bomb",   "💣")
    towers["emojis"] = emojis

    games_data["towers"] = towers
    return games_data


def _ensure_roulette_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    roulette = games_data.get("roulette")
    if not isinstance(roulette, dict):
        roulette = {}
    roulette.setdefault("name", "Roulette")
    roulette.setdefault("emoji", "🎰")
    roulette.setdefault("enabled", True)
    roulette.setdefault("description", "Bet on red, black or a number on the roulette wheel!")
    roulette.setdefault("min_bet", 10)
    roulette.setdefault("max_bet", 10000)
    roulette.setdefault("house_edge", 2.7)
    roulette.setdefault("rigged_chance", 0.0)
    roulette.setdefault("category", "table_games")
    roulette.setdefault("created_at", int(time.time()))
    roulette.setdefault("last_modified", int(time.time()))
    games_data["roulette"] = roulette
    return games_data


def _ensure_dice_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    dice = games_data.get("dice")
    if not isinstance(dice, dict):
        dice = {}
    dice.setdefault("name", "Dice")
    dice.setdefault("emoji", "🎲")
    dice.setdefault("enabled", True)
    dice.setdefault("description", "Roll the dice — predict over or under your target number!")
    dice.setdefault("min_bet", 10)
    dice.setdefault("max_bet", 10000)
    dice.setdefault("house_edge", 1.0)
    dice.setdefault("rigged_chance", 0.0)
    dice.setdefault("category", "table_games")
    dice.setdefault("created_at", int(time.time()))
    dice.setdefault("last_modified", int(time.time()))
    games_data["dice"] = dice
    return games_data


def _ensure_coinflip_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    coinflip = games_data.get("coinflip")
    if not isinstance(coinflip, dict):
        coinflip = {}
    coinflip.setdefault("name", "Coin Flip")
    coinflip.setdefault("emoji", "🪙")
    coinflip.setdefault("enabled", True)
    coinflip.setdefault("description", "Heads or tails — 50/50 shot to double your bet!")
    coinflip.setdefault("min_bet", 10)
    coinflip.setdefault("max_bet", 10000)
    coinflip.setdefault("house_edge", 1.0)
    coinflip.setdefault("rigged_chance", 0.0)
    coinflip.setdefault("hot_emoji", "🔥")
    coinflip.setdefault("cold_emoji", "❄️")
    coinflip.setdefault("category", "table_games")
    coinflip.setdefault("created_at", int(time.time()))
    coinflip.setdefault("last_modified", int(time.time()))
    games_data["coinflip"] = coinflip
    return games_data


def _ensure_limbo_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    limbo = games_data.get("limbo")
    if not isinstance(limbo, dict):
        limbo = {}
    limbo.setdefault("name", "Limbo")
    limbo.setdefault("emoji", "🚀")
    limbo.setdefault("enabled", True)
    limbo.setdefault("description", "Set a multiplier target — crash before it and you win!")
    limbo.setdefault("min_bet", 10)
    limbo.setdefault("max_bet", 10000)
    limbo.setdefault("house_edge", 1.0)
    limbo.setdefault("rigged_chance", 0.0)
    limbo.setdefault("category", "table_games")
    limbo.setdefault("created_at", int(time.time()))
    limbo.setdefault("last_modified", int(time.time()))
    games_data["limbo"] = limbo
    return games_data


def _ensure_slide_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    slide = games_data.get("slide")
    if not isinstance(slide, dict):
        slide = {}
    slide.setdefault("name", "Slide")
    slide.setdefault("emoji", "🎢")
    slide.setdefault("enabled", True)
    slide.setdefault("description", "Multiplier strip — pointer picks your payout.")
    slide.setdefault("min_bet", 10)
    slide.setdefault("max_bet", 10000)
    slide.setdefault("house_edge", 2.75)
    slide.setdefault("rigged_chance", 0.0)
    slide.setdefault("category", "table_games")
    slide.setdefault("created_at", int(time.time()))
    slide.setdefault("last_modified", int(time.time()))
    games_data["slide"] = slide
    return games_data


def _ensure_market_predict_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    mp = games_data.get("market_predict")
    if not isinstance(mp, dict):
        mp = {}
    mp.setdefault("name", "Market Predict")
    mp.setdefault("emoji", "📈")
    mp.setdefault("enabled", True)
    mp.setdefault(
        "description",
        "Predict UP/DOWN — center line breaks. If rigged, animation flips against your bet."
    )
    mp.setdefault("min_bet", 10)
    mp.setdefault("max_bet", 10000)
    mp.setdefault("house_edge", 2.0)  # 2% => 1.96x net on win (gross pays 2x)
    mp.setdefault("rigged_chance", 0.0)
    mp.setdefault("category", "table_games")
    mp.setdefault("created_at", int(time.time()))
    mp.setdefault("last_modified", int(time.time()))
    games_data["market_predict"] = mp
    return games_data


def _ensure_jackpot_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    jp = games_data.get("jackpot")
    if not isinstance(jp, dict):
        jp = {}
    jp.setdefault("name", "Jackpot")
    jp.setdefault("emoji", "🎰")
    jp.setdefault("enabled", True)
    jp.setdefault("description", "Multiplayer pool — bet share = win chance. 2% fee on pool.")
    jp.setdefault("min_bet", 10)
    jp.setdefault("max_bet", 100000)
    jp.setdefault("house_edge", 2.0)
    jp.setdefault("rigged_chance", 0.0)
    jp.setdefault("category", "special_games")
    jp.setdefault("created_at", int(time.time()))
    jp.setdefault("last_modified", int(time.time()))
    games_data["jackpot"] = jp
    return games_data


def _ensure_htw_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    htw = games_data.get("htw")
    if not isinstance(htw, dict):
        htw = {}
    htw.setdefault("name", "HTW")
    htw.setdefault("emoji", "🎡")
    htw.setdefault("enabled", True)
    htw.setdefault("description", "Head-to-head wheel — higher spin wins.")
    htw.setdefault("min_bet", 10)
    htw.setdefault("max_bet", 10000)
    htw.setdefault("house_edge", 5.0)
    htw.setdefault("rigged_chance", 0.0)
    htw.setdefault("category", "table_games")
    htw.setdefault("created_at", int(time.time()))
    htw.setdefault("last_modified", int(time.time()))
    games_data["htw"] = htw
    return games_data


def _ensure_chicken_road_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    cr = games_data.get("chicken_road")
    if not isinstance(cr, dict):
        cr = {}
    cr.setdefault("name", "Chicken Road")
    cr.setdefault("emoji", "🐔")
    cr.setdefault("enabled", True)
    cr.setdefault("description", "Cross lanes before the car hits.")
    cr.setdefault("min_bet", 10)
    cr.setdefault("max_bet", 10000)
    cr.setdefault("house_edge", 5.0)
    cr.setdefault("rigged_chance", 0.0)
    cr.setdefault("category", "special_games")
    cr.setdefault("created_at", int(time.time()))
    cr.setdefault("last_modified", int(time.time()))
    games_data["chicken_road"] = cr
    return games_data


def _repair_game_entry(game_id: str, info) -> dict:
    """Fill missing emoji/name so game management UI never KeyErrors."""
    if not isinstance(info, dict):
        info = {}
    defaults = {
        "name": game_id.replace("_", " ").title(),
        "emoji": "🎮",
        "enabled": False,
        "description": "",
        "min_bet": 10,
        "max_bet": 10000,
        "house_edge": 2.0,
        "rigged_chance": 0.0,
        "category": "other",
    }
    for key, val in defaults.items():
        info.setdefault(key, val)
    return info


def _merge_game_aliases(games_data: dict) -> dict:
    """slots → slot duplicate cleanup."""
    if not isinstance(games_data, dict):
        return {}
    if "slots" in games_data:
        if "slot" not in games_data or not isinstance(games_data.get("slot"), dict):
            games_data["slot"] = games_data.pop("slots")
        else:
            games_data.pop("slots", None)
    return games_data


def _ensure_slot_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    slot = games_data.get("slot")
    if not isinstance(slot, dict):
        slot = {}
    slot.setdefault("name", "Slot Machine")
    slot.setdefault("emoji", "🎰")
    slot.setdefault("enabled", True)
    slot.setdefault("description", "3×5 slot machine with 30 paylines!")
    slot.setdefault("min_bet", 10)
    slot.setdefault("max_bet", 10000)
    slot.setdefault("house_edge", 13.0)
    slot.setdefault("rigged_chance", 0.0)
    slot.setdefault("category", "special_games")
    slot.setdefault("created_at", int(time.time()))
    slot.setdefault("last_modified", int(time.time()))
    games_data["slot"] = slot
    return games_data


def _ensure_case_battle_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    battle = games_data.get("case_battle")
    if not isinstance(battle, dict):
        battle = {}
    battle.setdefault("name", "Case Battle")
    battle.setdefault("emoji", "⚔️")
    battle.setdefault("enabled", True)
    battle.setdefault("description", "Open the same case vs an opponent — highest item value wins!")
    battle.setdefault("min_bet", 10)
    battle.setdefault("max_bet", 10000)
    battle.setdefault("house_edge", 5.0)
    battle.setdefault("category", "special_games")
    battle.setdefault("created_at", int(time.time()))
    battle.setdefault("last_modified", int(time.time()))
    games_data["case_battle"] = battle
    return games_data


def _ensure_case_opening_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    case_opening = games_data.get("case_opening")
    if not isinstance(case_opening, dict):
        case_opening = {}
    case_opening.setdefault("name", "Case Opening")
    case_opening.setdefault("emoji", "📦")
    case_opening.setdefault("enabled", True)
    case_opening.setdefault("description", "Open mystery cases and win valuable items!")
    case_opening.setdefault("min_bet", 10)
    case_opening.setdefault("max_bet", 10000)
    case_opening.setdefault("house_edge", 5.0)
    case_opening.setdefault("rigged_chance", 0.0)
    case_opening.setdefault("category", "special_games")
    case_opening.setdefault("created_at", int(time.time()))
    case_opening.setdefault("last_modified", int(time.time()))
    games_data["case_opening"] = case_opening
    return games_data


def _ensure_live_blackjack_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    lb = games_data.get("live_blackjack")
    if not isinstance(lb, dict):
        lb = {}
    lb.setdefault("name", "Live Blackjack")
    lb.setdefault("emoji", "🃏")
    lb.setdefault("enabled", True)
    lb.setdefault("min_bet", 10)
    lb.setdefault("max_bet", 10000)
    lb.setdefault("rigged_chance", 0.0)
    lb.setdefault("category", "special_games")
    games_data["live_blackjack"] = lb
    return games_data


def _ensure_blackjack_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    bj = games_data.get("blackjack")
    if not isinstance(bj, dict):
        bj = {}
    bj.setdefault("name", "Blackjack")
    bj.setdefault("emoji", "🃏")
    bj.setdefault("enabled", True)
    bj.setdefault("description", "6-deck blackjack: Split, Double Down, Insurance & Side Bets!")
    bj.setdefault("min_bet", 10)
    bj.setdefault("max_bet", 10000)
    bj.setdefault("house_edge", 0.5)
    bj.setdefault("rigged_chance", 0.0)
    bj.setdefault("category", "special_games")
    bj.setdefault("emojis", {})
    bj.setdefault("created_at", int(time.time()))
    bj.setdefault("last_modified", int(time.time()))
    games_data["blackjack"] = bj
    return games_data


def _ensure_hilo_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    hilo = games_data.get("hilo")
    if not isinstance(hilo, dict):
        hilo = {}
    hilo.setdefault("name", "HiLo")
    hilo.setdefault("emoji", "🎴")
    hilo.setdefault("enabled", True)
    hilo.setdefault("description", "Guess Higher or Lower on each card — multiply your bet with every correct pick!")
    hilo.setdefault("min_bet", 10)
    hilo.setdefault("max_bet", 10000)
    hilo.setdefault("house_edge", 3.0)
    hilo.setdefault("rigged_chance", 0.0)
    hilo.setdefault("category", "special_games")
    hilo.setdefault("created_at", int(time.time()))
    hilo.setdefault("last_modified", int(time.time()))
    games_data["hilo"] = hilo
    return games_data


def _ensure_all_game_entries(games_data: dict) -> dict:
    """Tüm oyunların server/games'de kaydının olduğundan emin ol."""
    games_data = _merge_game_aliases(games_data if isinstance(games_data, dict) else {})
    games_data = _ensure_mines_game_entry(games_data)
    games_data = _ensure_crystals_game_entry(games_data)
    games_data = _ensure_towers_game_entry(games_data)
    games_data = _ensure_roulette_game_entry(games_data)
    games_data = _ensure_dice_game_entry(games_data)
    games_data = _ensure_coinflip_game_entry(games_data)
    games_data = _ensure_htw_game_entry(games_data)
    games_data = _ensure_limbo_game_entry(games_data)
    games_data = _ensure_slide_game_entry(games_data)
    games_data = _ensure_market_predict_game_entry(games_data)
    games_data = _ensure_jackpot_game_entry(games_data)
    games_data = _ensure_slot_game_entry(games_data)
    games_data = _ensure_case_opening_game_entry(games_data)
    games_data = _ensure_case_battle_game_entry(games_data)
    games_data = _ensure_blackjack_game_entry(games_data)
    games_data = _ensure_live_blackjack_game_entry(games_data)
    games_data = _ensure_hilo_game_entry(games_data)
    games_data = _ensure_chicken_road_game_entry(games_data)
    for gid in list(games_data.keys()):
        if isinstance(games_data[gid], dict):
            games_data[gid] = _repair_game_entry(gid, games_data[gid])
        else:
            del games_data[gid]
    return games_data


async def _persist_games_panel(games_data: dict) -> dict:
    """Normalize panel JSON and mirror into SQLite for prefix game commands."""
    games_data = _ensure_all_game_entries(games_data)
    set_data("server/games", games_data)
    from database import db as flip_db
    await flip_db.sync_panel_games_to_sqlite(games_data)
    return games_data


class AdminPanelSelect(discord.ui.Select):
    """Ana menü — 5 bölüm hub'ı."""

    def __init__(self, user_id: int):
        uid = str(user_id)
        options = [
            discord.SelectOption(
                label=t("admin_panel.hubs.channels.menu_label", user_id=uid),
                description=t("admin_panel.hubs.channels.menu_desc", user_id=uid),
                emoji="📡",
                value="hub_channels",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.payments.menu_label", user_id=uid),
                description=t("admin_panel.hubs.payments.menu_desc", user_id=uid),
                emoji="💰",
                value="hub_payments",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.games.menu_label", user_id=uid),
                description=t("admin_panel.hubs.games.menu_desc", user_id=uid),
                emoji="🎮",
                value="hub_games",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.rewards.menu_label", user_id=uid),
                description=t("admin_panel.hubs.rewards.menu_desc", user_id=uid),
                emoji="🎁",
                value="hub_rewards",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.tools.menu_label", user_id=uid),
                description=t("admin_panel.hubs.tools.menu_desc", user_id=uid),
                emoji="🤖",
                value="hub_tools",
            ),
        ]
        super().__init__(
            placeholder=t("admin_panel.hubs.main_placeholder", user_id=uid),
            options=options,
            custom_id="admin_panel:main_select",
        )

    async def callback(self, interaction: discord.Interaction):
        from modules.admin_panel_nav import (
            HUB_CHANNELS,
            HUB_GAMES,
            HUB_PAYMENTS,
            HUB_REWARDS,
            HUB_TOOLS,
            go_hub,
        )

        hub_map = {
            "hub_channels": HUB_CHANNELS,
            "hub_payments": HUB_PAYMENTS,
            "hub_games": HUB_GAMES,
            "hub_rewards": HUB_REWARDS,
            "hub_tools": HUB_TOOLS,
        }
        await go_hub(interaction, hub_map[self.values[0]], user_id=interaction.user.id)


class AdminPanelView(discord.ui.View):
    """Ana admin panel view"""
    
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.add_item(AdminPanelSelect(user_id))

    @discord.ui.button(label="🔄 Restart Bot", style=discord.ButtonStyle.danger, row=1, custom_id="admin_panel:restart")
    async def restart_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Restart the bot process"""
        uid = str(interaction.user.id)
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("admin_panel.restart_no_perm", user_id=uid), ephemeral=True
            )
        await interaction.response.send_message(
            embed=discord.Embed(
                title=t("admin_panel.restart_title", user_id=uid),
                description=t("admin_panel.restart_desc", user_id=uid),
                color=discord.Color.orange(),
            ),
            ephemeral=True,
        )
        import os, sys
        os.execv(sys.executable, [sys.executable] + sys.argv)


# ── Admin panel hubs (grouped navigation) ─────────────────────────────────────

async def _admin_open_route(interaction: discord.Interaction, route: str) -> None:
    """Open a specific settings screen by route id."""
    uid = str(interaction.user.id)
    ns = t("admin_panel.not_set", user_id=uid)
    guild_id = str(interaction.guild.id)
    server_data = get_server_data(guild_id)

    if route == "maintenance":
        from cogs.maintenance import MaintenanceSettingsView, _build_maintenance_embed
        lang = get_user_lang(interaction.user.id)
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", user_id=uid), ephemeral=True
            )
        await interaction.response.edit_message(
            embed=_build_maintenance_embed(lang),
            view=MaintenanceSettingsView(lang),
        )
        return

    if route == "registration":
        reg_channel = server_data.get("registration_channel")
        member_role_id = server_data.get("member_role")
        embed = discord.Embed(
            title=t("admin_panel.routes.registration_title", user_id=uid),
            description=t("admin_panel.routes.registration_desc", user_id=uid),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name=t("admin_panel.routes.reg_channel", user_id=uid),
            value=f"<#{reg_channel}>" if reg_channel else ns,
            inline=False,
        )
        embed.add_field(
            name=t("admin_panel.routes.member_role", user_id=uid),
            value=f"<@&{member_role_id}>" if member_role_id else ns,
            inline=False,
        )
        await interaction.response.edit_message(
            embed=embed, view=RegistrationSettingsView(interaction.user.id)
        )
        return

    if route == "private_rooms":
        private_cat = server_data.get("private_category_id")
        play_cat = server_data.get("play_category_id")
        play_chs = server_data.get("play_channel_ids") or []
        play_lines = []
        for i, ch_id in enumerate(play_chs[:5]):
            play_lines.append(f"**play-{i + 1}:** <#{ch_id}>" if ch_id else f"**play-{i + 1}:** {ns}")
        if not play_lines:
            play_lines = [t("admin_panel.routes.play_channels_none", user_id=uid)]
        embed = discord.Embed(
            title=t("admin_panel.routes.private_title", user_id=uid),
            description=t("admin_panel.routes.private_desc", user_id=uid),
            color=discord.Color.purple(),
        )
        embed.add_field(
            name=t("admin_panel.routes.private_category", user_id=uid),
            value=f"<#{private_cat}>" if private_cat else ns,
            inline=False,
        )
        embed.add_field(
            name=t("admin_panel.routes.play_category", user_id=uid),
            value=f"<#{play_cat}>" if play_cat else ns,
            inline=False,
        )
        embed.add_field(
            name=t("admin_panel.routes.play_channels", user_id=uid),
            value="\n".join(play_lines),
            inline=False,
        )
        await interaction.response.edit_message(
            embed=embed, view=PrivateRoomSettingsView(interaction.user.id)
        )
        return

    if route == "deposit":
        embed, view = _build_deposit_settings_embed_and_view(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)
        return

    if route == "withdraw":
        withdraw_channel = server_data.get("withdraw_channel")
        min_withdrawal = server_data.get("min_withdrawal", 100)
        withdraw_mode = server_data.get("withdraw_mode", "log")
        multiplier = server_data.get("withdraw_min_multiplier", 0) or 0
        withdraw_log_channel = server_data.get("withdraw_log_channel")
        embed = discord.Embed(
            title=t("admin_panel.routes.withdraw_title", user_id=uid),
            description=t("admin_panel.routes.withdraw_desc", user_id=uid),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name=t("admin_panel.routes.withdraw_channel", user_id=uid),
            value=f"<#{withdraw_channel}>" if withdraw_channel else ns,
            inline=False,
        )
        embed.add_field(
            name=t("admin_panel.routes.min_withdraw", user_id=uid),
            value=format_balance(min_withdrawal, "real"),
            inline=True,
        )
        embed.add_field(
            name=t("admin_panel.routes.deposit_multiplier", user_id=uid),
            value=f"{multiplier}x"
            if multiplier
            else t("admin_panel.routes.multiplier_off", user_id=uid),
            inline=True,
        )
        embed.add_field(
            name=t("admin_panel.routes.withdraw_log", user_id=uid),
            value=f"<#{withdraw_log_channel}>" if withdraw_log_channel else ns,
            inline=False,
        )
        embed.add_field(
            name=t("admin_panel.routes.withdraw_mode", user_id=uid),
            value=t("admin_panel.routes.mode_ticket", user_id=uid)
            if withdraw_mode == "ticket"
            else t("admin_panel.routes.mode_log", user_id=uid),
            inline=False,
        )
        await interaction.response.edit_message(
            embed=embed, view=WithdrawSettingsView(interaction.user.id)
        )
        return

    if route == "self_roles":
        from cogs.self_roles import SelfRolesAdminView, build_admin_embed
        from modules.self_roles_store import get_config

        cfg = get_config(interaction.guild.id)
        await interaction.response.edit_message(
            embed=build_admin_embed(interaction.guild, cfg),
            view=SelfRolesAdminView.for_guild(interaction.guild, interaction.user.id),
        )
        return

    if route == "tickets":
        tickets_data = get_data("server/tickets") or {}
        ticket_settings = get_data("server/ticket_settings") or {}
        open_tickets = (
            len([t for t in tickets_data.get(guild_id, {}).values() if t.get("status") == "open"])
            if guild_id in tickets_data
            else 0
        )
        embed = discord.Embed(
            title=t("admin_panel.routes.tickets_title", user_id=uid),
            description=t("admin_panel.routes.tickets_desc", user_id=uid),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name=t("admin_panel.routes.open_tickets", user_id=uid),
            value=str(open_tickets),
            inline=True,
        )
        embed.add_field(
            name=t("admin_panel.routes.ticket_category", user_id=uid),
            value=f"<#{ticket_settings.get('category_id')}>"
            if ticket_settings.get("category_id")
            else ns,
            inline=True,
        )
        await interaction.response.edit_message(
            embed=embed, view=TicketSystemView(interaction.user.id)
        )
        return

    if route == "game_log":
        log_ch = server_data.get("game_log_channel") or server_data.get("pf_log_channel")
        embed = discord.Embed(
            title=t("admin_panel.routes.game_log_title", user_id=uid),
            description=t("admin_panel.routes.game_log_desc", user_id=uid),
            color=0xF5A623,
        )
        embed.add_field(
            name=t("admin_panel.routes.channel", user_id=uid),
            value=f"<#{log_ch}>" if log_ch else ns,
            inline=False,
        )
        embed.add_field(
            name=t("admin_panel.routes.game_log_example", user_id=uid),
            value=t("admin_panel.routes.game_log_sample", user_id=uid),
            inline=False,
        )
        await interaction.response.edit_message(
            embed=embed, view=PFLogSettingsView(interaction.user.id)
        )
        return

    if route == "finance_stats":
        embed = _build_finance_stats_embed(guild_id)
        await interaction.response.edit_message(embed=embed, view=FinanceStatsView())
        return

    if route == "live_stats":
        ls_ch_id = server_data.get("live_stats_channel")
        ls_msg_id = server_data.get("live_stats_message_id")
        embed = discord.Embed(
            title=t("admin_panel.routes.live_stats_title", user_id=uid),
            description=t("admin_panel.routes.live_stats_desc", user_id=uid),
            color=0x3498DB,
        )
        embed.add_field(
            name=t("admin_panel.routes.channel", user_id=uid),
            value=f"<#{ls_ch_id}>" if ls_ch_id else ns,
            inline=True,
        )
        embed.add_field(
            name=t("admin_panel.routes.message_id", user_id=uid),
            value=f"`{ls_msg_id}`" if ls_msg_id else "—",
            inline=True,
        )
        await interaction.response.edit_message(
            embed=embed, view=LiveStatsSettingsView(interaction.user.id)
        )
        return

    if route == "game_management":
        await _persist_games_panel(get_data("server/games") or {})
        await interaction.response.edit_message(
            embed=_build_game_list_management_embed(interaction.user.id),
            view=GameListManagementView(interaction.user.id),
        )
        return

    if route == "exchange_rates":
        await interaction.response.edit_message(
            embed=_build_exchange_rates_embed(), view=ExchangeRatesManagementView()
        )
        return

    if route == "bonus_settings":
        embed, view = _build_bonus_list_embed_and_view()
        await interaction.response.edit_message(embed=embed, view=view)
        return

    if route == "giveaway_settings":
        from cogs.giveaway import GiveawayListView, _giveaway_list_embed
        await interaction.response.edit_message(embed=_giveaway_list_embed(), view=GiveawayListView())
        return

    if route == "promo_codes":
        await interaction.response.edit_message(
            embed=_build_promo_list_embed(), view=PromoManagementView()
        )
        return

    if route == "community_cases":
        from cogs.cases import _get_db, _admin_community_cases_embed, AdminCommunityCasesView
        await interaction.response.edit_message(
            embed=_admin_community_cases_embed(_get_db()),
            view=AdminCommunityCasesView(interaction.user.id),
        )
        return

    if route == "race_management":
        from cogs.races import _build_race_panel_embed, RacePanelView
        await interaction.response.edit_message(
            embed=_build_race_panel_embed(),
            view=RacePanelView(interaction.user.id),
        )
        return

    if route == "crypto_deposits":
        from cogs.crypto_deposit import _build_admin_embed, CryptoAdminView
        await interaction.response.edit_message(
            embed=_build_admin_embed(), view=CryptoAdminView()
        )
        return

    if route == "global_balance_cap":
        await interaction.response.edit_message(
            embed=_build_global_balance_cap_embed(interaction.user.id),
            view=GlobalBalanceCapView(interaction.user.id),
        )
        return

    if route == "live_blackjack_setup":
        await interaction.response.edit_message(
            embed=_build_live_blackjack_setup_embed(uid),
            view=LiveBlackjackSettingsView(interaction.user.id, return_to="games_hub"),
        )
        return

    if route == "bot_settings":
        embed = discord.Embed(
            title=f"🤖 {t('admin_panel.bot_settings', user_id=uid)}",
            description=t("admin_panel.bot_settings_desc", user_id=uid),
            color=0x9B59B6,
        )
        await interaction.response.edit_message(
            embed=embed, view=BotSettingsView(interaction.user.id)
        )
        return

    if route == "broadcast_dm":
        await interaction.response.edit_message(
            embed=_build_broadcast_dm_preview_embed("📢 Broadcast", ""),
            view=BroadcastDMView(),
        )
        return


class _HubSelect(discord.ui.Select):
    def __init__(self, hub: str, user_id: int):
        from modules.admin_panel_nav import hub_select_options

        self.hub = hub
        options, placeholder = hub_select_options(hub, user_id)
        super().__init__(
            placeholder=placeholder,
            options=options,
            custom_id=f"admin_hub:{hub}",
        )

    async def callback(self, interaction: discord.Interaction):
        v = self.values[0]
        if v == "back_home":
            from modules.admin_panel_nav import go_home
            return await go_home(interaction, user_id=interaction.user.id)
        await _admin_open_route(interaction, v)


class ChannelsHubView(discord.ui.View):
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.add_item(_HubSelect("channels", user_id))


class PaymentsHubView(discord.ui.View):
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.add_item(_HubSelect("payments", user_id))


class GamesHubView(discord.ui.View):
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.add_item(_HubSelect("games", user_id))


class RewardsHubView(discord.ui.View):
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.add_item(_HubSelect("rewards", user_id))


class ToolsHubView(discord.ui.View):
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.add_item(_HubSelect("tools", user_id))


class ChannelSelectMenu(discord.ui.ChannelSelect):
    """Kanal seçim menüsü"""
    
    def __init__(self, user_id: int = 0):
        self.user_id = user_id
        super().__init__(
            placeholder=t("admin_panel.select_channel", user_id=str(user_id)),
            channel_types=[discord.ChannelType.text],
            custom_id="admin_panel:channel_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Kanal seçildiğinde"""
        channel_obj = self.values[0]
        channel = interaction.guild.get_channel(channel_obj.id)
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        
        # Eski kayıt menüsünü sil
        old_channel_id = server_data.get("registration_channel")
        old_message_id = server_data.get("registration_message_id")
        
        if old_channel_id and old_message_id:
            try:
                old_channel = interaction.guild.get_channel(old_channel_id)
                if old_channel:
                    old_message = await old_channel.fetch_message(old_message_id)
                    await old_message.delete()
            except:
                pass  # Mesaj zaten silinmiş veya bulunamadı
        
        # Yeni kayıt menüsünü gönder
        from cogs.registration import RegistrationView
        
        embed = discord.Embed(
            title=t("registration.menu_title", user_id=str(interaction.user.id)),
            description=t("registration.menu_description", user_id=str(interaction.user.id)),
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=interaction.client.user.display_avatar.url)
        embed.set_footer(text=f"Vegas Casino • {interaction.guild.name}")
        
        view = RegistrationView()
        new_message = await channel.send(embed=embed, view=view)
        
        # Yeni kanal ve mesaj ID'sini kaydet
        server_data["registration_channel"] = channel.id
        server_data["registration_message_id"] = new_message.id
        set_server_data(guild_id, server_data)
        
        embed_response = discord.Embed(
            title="✅ Success",
            description=t("admin_panel.registration_channel_set", user_id=str(interaction.user.id), channel=channel.mention),
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed_response, view=RegistrationSettingsView(interaction.user.id))


class CategorySelectMenu(discord.ui.ChannelSelect):
    """Kategori seçim menüsü"""
    
    def __init__(self, user_id: int = 0):
        self.user_id = user_id
        super().__init__(
            placeholder=t("admin_panel.select_category", user_id=str(user_id)),
            channel_types=[discord.ChannelType.category],
            custom_id="admin_panel:category_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Kategori seçildiğinde"""
        category = self.values[0]
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data["private_category_id"] = category.id
        set_server_data(guild_id, server_data)
        
        embed = discord.Embed(
            title="✅ Success",
            description=t("admin_panel.private_category_set", user_id=str(interaction.user.id), category=category.mention),
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=PrivateRoomSettingsView(interaction.user.id))


class PrivateRoomChannelSelect(discord.ui.ChannelSelect):
    """Özel oda menüsü için kanal seçimi"""
    
    def __init__(self, user_id: int = 0):
        self.user_id = user_id
        super().__init__(
            placeholder=t("admin_panel.select_channel", user_id=str(user_id)),
            channel_types=[discord.ChannelType.text],
            custom_id="admin_panel:private_room_channel_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Kanal seçildiğinde özel oda menüsünü gönder"""
        selected_channel = self.values[0]
        
        # Gerçek kanal objesini al
        channel = interaction.guild.get_channel(selected_channel.id)
        if not channel:
            embed_error = discord.Embed(
                title="❌ Error",
                description="Kanal bulunamadı!",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed_error, view=PrivateRoomSettingsView(interaction.user.id))
            return
        
        # Özel oda menüsünü oluştur
        embed = discord.Embed(
            title=t("private_rooms.menu_title", user_id=str(interaction.user.id)),
            description=t("private_rooms.menu_description", user_id=str(interaction.user.id)),
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
        embed.set_footer(text="Vegas Casino | Özel Oda Sistemi", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        
        # View ekle (private_rooms cog'undan)
        from cogs.private_rooms import PrivateRoomMenuView
        view = PrivateRoomMenuView()
        
        try:
            await channel.send(embed=embed, view=view)
            
            embed_response = discord.Embed(
                title="✅ Success",
                description=t("admin_panel.private_room_menu_sent", user_id=str(interaction.user.id)).format(channel=channel.mention),
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed_response, view=PrivateRoomSettingsView(interaction.user.id))
        except Exception as e:
            embed_error = discord.Embed(
                title="❌ Error",
                description=f"Menü gönderilemedi: {str(e)}",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed_error, view=PrivateRoomSettingsView(interaction.user.id))


class ServerSettingsSelect(discord.ui.Select):
    """Server settings select menu"""
    
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Maintenance Mode",
                description="Enable or disable bot maintenance mode",
                emoji="🔧",
                value="maintenance",
            ),
            discord.SelectOption(
                label="Registration Settings",
                description="Configure registration channel and menu",
                emoji="📝",
                value="registration"
            ),
            discord.SelectOption(
                label="Private Room Settings",
                description="Configure private room category and menu",
                emoji="🏠",
                value="private_rooms"
            ),
            discord.SelectOption(
                label="Deposit Settings",
                description="Configure deposit world, bot and channel",
                emoji="💳",
                value="deposit"
            ),
            discord.SelectOption(
                label="Withdraw Settings",
                description="Configure withdrawal channel and minimum amount",
                emoji="🏦",
                value="withdraw"
            ),
            discord.SelectOption(
                label="Ticket System",
                description="Configure support ticket system",
                emoji="🎫",
                value="tickets"
            ),
            discord.SelectOption(
                label="Provably Fair Log Channel",
                description="Set channel for Provably Fair game logs",
                emoji="🔍",
                value="pf_log"
            ),
            discord.SelectOption(
                label="Finance Stats",
                description="View total deposits, withdrawals and profit/loss",
                emoji="📊",
                value="finance_stats"
            ),
            discord.SelectOption(
                label="Live Stats Channel",
                description="Set channel for the auto-updating live statistics embed",
                emoji="📡",
                value="live_stats"
            ),
            discord.SelectOption(
                label="⬅️ Back to Main Menu",
                description="Return to admin panel main menu",
                emoji="⬅️",
                value="back"
            )
        ]
        
        super().__init__(
            placeholder="Select a category to configure...",
            options=options,
            custom_id="server_settings:main_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle category selection"""
        if self.values[0] == "maintenance":
            from cogs.maintenance import MaintenanceSettingsView, _build_maintenance_embed

            lang = get_user_lang(interaction.user.id)
            if check_permission(interaction.user.id, "admin"):
                return await interaction.response.send_message(
                    t("errors.no_permission", user_id=str(interaction.user.id)), ephemeral=True
                )
            embed = _build_maintenance_embed(lang)
            await interaction.response.edit_message(embed=embed, view=MaintenanceSettingsView(lang))
            return

        if self.values[0] == "registration":
            view = RegistrationSettingsView(interaction.user.id)
            server_data = get_server_data(str(interaction.guild.id))
            reg_channel = server_data.get('registration_channel')
            member_role_id = server_data.get('member_role')

            embed = discord.Embed(
                title="📝 Registration Settings",
                description="Configure user registration settings",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="📢 Current Registration Channel",
                value=f"<#{reg_channel}>" if reg_channel else "Not Set",
                inline=False
            )
            embed.add_field(
                name="🎭 Member Role (on register)",
                value=f"<@&{member_role_id}>" if member_role_id else "Not Set",
                inline=False
            )
            await interaction.response.edit_message(embed=embed, view=view)
        
        elif self.values[0] == "private_rooms":
            view = PrivateRoomSettingsView(interaction.user.id)
            server_data = get_server_data(str(interaction.guild.id))
            private_cat = server_data.get('private_category_id')
            
            embed = discord.Embed(
                title="🏠 Private Room Settings",
                description="Configure private room system",
                color=discord.Color.purple()
            )
            embed.add_field(
                name="📁 Current Private Category",
                value=f"<#{private_cat}>" if private_cat else "Not Set",
                inline=False
            )
            await interaction.response.edit_message(embed=embed, view=view)
        
        elif self.values[0] == "deposit":
            embed, view = _build_deposit_settings_embed_and_view(interaction.guild)
            await interaction.response.edit_message(embed=embed, view=view)

        elif self.values[0] == "withdraw":
            guild_id = str(interaction.guild.id)
            server_data = get_server_data(guild_id)
            withdraw_channel = server_data.get("withdraw_channel")
            min_withdrawal = server_data.get("min_withdrawal", 100)
            withdraw_mode = server_data.get("withdraw_mode", "log")
            multiplier = server_data.get("withdraw_min_multiplier", 0) or 0
            withdraw_log_channel = server_data.get("withdraw_log_channel")

            embed = discord.Embed(
                title="🏦 Withdraw Settings",
                description="Configure the withdrawal channel and minimum withdrawal amount.",
                color=discord.Color.orange()
            )
            embed.add_field(
                name="📢 Withdraw Channel",
                value=f"<#{withdraw_channel}>" if withdraw_channel else "Not Set",
                inline=False
            )
            embed.add_field(
                name="📉 Fixed Min Withdrawal",
                value=format_balance(min_withdrawal, "real"),
                inline=True
            )
            embed.add_field(
                name="✖️ Deposit Multiplier",
                value=f"{multiplier}x last deposit" if multiplier else "❌ Disabled (using fixed min)",
                inline=True
            )
            embed.add_field(
                name="📢 Withdraw Log Channel",
                value=f"<#{withdraw_log_channel}>" if withdraw_log_channel else "Not Set",
                inline=False
            )
            embed.add_field(
                name="🔄 Withdraw Mode",
                value="🎫 Ticket (private ticket channel per request)" if withdraw_mode == "ticket" else "📋 Log (send to withdraw channel)",
                inline=False
            )
            await interaction.response.edit_message(embed=embed, view=WithdrawSettingsView(interaction.user.id))

        elif self.values[0] == "tickets":
            tickets_data    = get_data("server/tickets") or {}
            ticket_settings = get_data("server/ticket_settings") or {}
            guild_id = str(interaction.guild.id)
            open_tickets = len([t for t in tickets_data.get(guild_id, {}).values() if t.get("status") == "open"]) if guild_id in tickets_data else 0
            view = TicketSystemView(interaction.user.id)
            
            embed = discord.Embed(
                title="🎫 Ticket System",
                description="Manage support ticket system configuration",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="📊 Statistics",
                value=f"Open Tickets: **{open_tickets}**",
                inline=False
            )
            embed.add_field(
                name="📁 Ticket Category",
                value=f"<#{ticket_settings.get('category_id')}>" if ticket_settings.get('category_id') else "Not Set",
                inline=False
            )
            await interaction.response.edit_message(embed=embed, view=view)
        
        elif self.values[0] == "pf_log":
            view = PFLogSettingsView(interaction.user.id)
            guild_id = str(interaction.guild.id)
            server_data = get_server_data(guild_id)
            log_ch = server_data.get("game_log_channel") or server_data.get("pf_log_channel")

            embed = discord.Embed(
                title="📢 Game Log Channel",
                description="Channel for short English game result lines (all games).",
                color=0xf5a623,
            )
            embed.add_field(
                name="📢 Current Channel",
                value=f"<#{log_ch}>" if log_ch else "Not Set",
                inline=False,
            )
            embed.add_field(
                name="ℹ️ Format",
                value=(
                    "One embed per finished round — **description only**:\n"
                    "`@Player **win** +$100 at **Roulette`**\n"
                    "`@Player **lose** -$50 at **Dice`**\n"
                    "`@Player **tie** $0 at **Coin Flip`**"
                ),
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=view)

        elif self.values[0] == "finance_stats":
            embed = _build_finance_stats_embed(str(interaction.guild.id))
            await interaction.response.edit_message(embed=embed, view=FinanceStatsView())

        elif self.values[0] == "live_stats":
            guild_id   = str(interaction.guild.id)
            server_data = get_server_data(guild_id)
            ls_ch_id   = server_data.get("live_stats_channel")
            ls_msg_id  = server_data.get("live_stats_message_id")
            embed = discord.Embed(
                title="📡 Live Stats Channel",
                description=(
                    "Set a channel where the bot will post and auto-update\n"
                    "a live statistics embed every **60 seconds**."
                ),
                color=0x3498db,
            )
            embed.add_field(
                name="📢 Current Channel",
                value=f"<#{ls_ch_id}>" if ls_ch_id else "`Not Set`",
                inline=True,
            )
            embed.add_field(
                name="🗒️ Message ID",
                value=f"`{ls_msg_id}`" if ls_msg_id else "`—`",
                inline=True,
            )
            await interaction.response.edit_message(embed=embed, view=LiveStatsSettingsView(interaction.user.id))

        elif self.values[0] == "back":
            from modules.admin_panel_nav import go_home

            await go_home(interaction, user_id=interaction.user.id)


class PFLogChannelSelect(discord.ui.ChannelSelect):
    """Provably Fair log kanalı seçim menüsü"""

    def __init__(self):
        super().__init__(
            placeholder="Select game log channel…",
            channel_types=[discord.ChannelType.text],
            custom_id="admin_panel:pf_log_channel_select",
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0].resolve() or self.values[0]
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data["game_log_channel"] = channel.id
        server_data["pf_log_channel"] = channel.id
        set_server_data(guild_id, server_data)

        embed = discord.Embed(
            title="✅ Game Log Channel Set",
            description=(
                f"Short game results (win / tie / lose) will be posted to {channel.mention}.\n"
                f"Example: `@Player **win** +$100 at **Roulette**`"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=PFLogSettingsView(interaction.user.id))


class PFLogSettingsView(discord.ui.View):
    """PF log channel settings view"""

    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="📢 Set Game Log Channel", style=discord.ButtonStyle.primary, row=0)
    async def set_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(timeout=300)
        view.add_item(PFLogChannelSelect())
        view.add_item(BackToServerSettingsButton("games", interaction.user.id))
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="❌ Clear Channel", style=discord.ButtonStyle.danger, row=0)
    async def clear_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data.pop("game_log_channel", None)
        server_data.pop("pf_log_channel", None)
        set_server_data(guild_id, server_data)
        embed = discord.Embed(
            title="✅ Game Log Channel Cleared",
            description="Game logs will no longer be posted until a channel is set again.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=PFLogSettingsView(interaction.user.id))

    @discord.ui.button(label="⬅️ Oyunlar", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_GAMES, go_hub

        await go_hub(interaction, HUB_GAMES, user_id=interaction.user.id)


# ── Live Stats Settings ────────────────────────────────────────────────────────

class LiveStatsChannelSelect(discord.ui.ChannelSelect):
    """Channel picker for the live stats auto-updating embed."""

    def __init__(self):
        super().__init__(
            placeholder="Select the channel for live stats...",
            channel_types=[discord.ChannelType.text],
            custom_id="admin_panel:live_stats_channel_select",
        )

    async def callback(self, interaction: discord.Interaction):
        channel     = self.values[0].resolve() or self.values[0]
        guild_id    = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data["live_stats_channel"] = channel.id
        # Reset saved message ID so the cog posts a fresh message
        server_data.pop("live_stats_message_id", None)
        set_server_data(guild_id, server_data)

        embed = discord.Embed(
            title="✅ Live Stats Channel Set",
            description=(
                f"Live stats will be posted and updated every 60 s in {channel.mention}.\n"
                "The embed will appear within 60 seconds."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=LiveStatsSettingsView(interaction.user.id))


class LiveStatsSettingsView(discord.ui.View):
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="📡 Set Channel", style=discord.ButtonStyle.primary, row=0)
    async def set_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(timeout=300)
        view.add_item(LiveStatsChannelSelect())
        view.add_item(BackToServerSettingsButton("games", interaction.user.id))
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="❌ Clear Channel", style=discord.ButtonStyle.danger, row=0)
    async def clear_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id    = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data.pop("live_stats_channel", None)
        server_data.pop("live_stats_message_id", None)
        set_server_data(guild_id, server_data)
        embed = discord.Embed(
            title="✅ Live Stats Channel Cleared",
            description="The live stats embed will no longer be updated.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=LiveStatsSettingsView(interaction.user.id))

    @discord.ui.button(label="⬅️ Oyunlar", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_GAMES, go_hub

        await go_hub(interaction, HUB_GAMES, user_id=interaction.user.id)


class MemberRoleSelect(discord.ui.RoleSelect):
    """Register olunca verilecek rol seçimi"""

    def __init__(self):
        super().__init__(
            placeholder="Select a role to assign on registration...",
            custom_id="admin_panel:member_role_select"
        )

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data["member_role"] = role.id
        set_server_data(guild_id, server_data)

        embed = discord.Embed(
            title="✅ Member Role Set",
            description=f"Users will now receive {role.mention} upon registration.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=RegistrationSettingsView(interaction.user.id))


class UnregisteredRoleSelect(discord.ui.RoleSelect):
    """Register olmamış kullanıcılara ait rol - kayıt olunca kaldırılır"""

    def __init__(self):
        super().__init__(
            placeholder="Select the role to remove on registration...",
            custom_id="admin_panel:unregistered_role_select"
        )

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data["unregistered_role"] = role.id
        set_server_data(guild_id, server_data)

        embed = discord.Embed(
            title="✅ Unregistered Role Set",
            description=f"{role.mention} will be removed from users upon registration.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=RegistrationSettingsView(interaction.user.id))


class RegistrationSettingsView(discord.ui.View):
    """Registration settings management view"""
    
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id
    
    @discord.ui.button(label="📝 Set Registration Channel", style=discord.ButtonStyle.primary, row=0)
    async def set_registration_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Kayıt kanalı ayarla"""
        view = discord.ui.View(timeout=300)
        view.add_item(ChannelSelectMenu(interaction.user.id))
        view.add_item(BackToServerSettingsButton("channels", interaction.user.id))
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="🎭 Set Member Role", style=discord.ButtonStyle.success, row=0)
    async def set_member_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Üyelik rolü ayarla"""
        view = discord.ui.View(timeout=300)
        view.add_item(MemberRoleSelect())
        view.add_item(BackToServerSettingsButton("channels", interaction.user.id))
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="🚫 Set Unregistered Role", style=discord.ButtonStyle.primary, row=1)
    async def set_unregistered_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Kayıt olunca kaldırılacak rolü ayarla"""
        view = discord.ui.View(timeout=300)
        view.add_item(UnregisteredRoleSelect())
        view.add_item(BackToServerSettingsButton("channels", interaction.user.id))
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="❌ Clear Member Role", style=discord.ButtonStyle.danger, row=2)
    async def clear_member_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Üyelik rolünü kaldır"""
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data.pop("member_role", None)
        set_server_data(guild_id, server_data)
        embed = discord.Embed(
            title="✅ Member Role Cleared",
            description="No role will be assigned on registration.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=RegistrationSettingsView(interaction.user.id))

    @discord.ui.button(label="🗑️ Clear Unregistered Role", style=discord.ButtonStyle.danger, row=2)
    async def clear_unregistered_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Kayıt olunca kaldırılacak rolü temizle"""
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data.pop("unregistered_role", None)
        set_server_data(guild_id, server_data)
        embed = discord.Embed(
            title="✅ Unregistered Role Cleared",
            description="No role will be removed on registration.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=RegistrationSettingsView(interaction.user.id))

    @discord.ui.button(label="⬅️ Sunucu & Kanallar", style=discord.ButtonStyle.secondary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_CHANNELS, go_hub

        await go_hub(interaction, HUB_CHANNELS, user_id=interaction.user.id)


def _build_live_blackjack_setup_embed(user_id: int | str) -> discord.Embed:
    from modules.live_blackjack_tables import get_settings as lb_settings, list_tables

    uid = str(user_id)
    ns = t("admin_panel.not_set", user_id=uid)
    settings = lb_settings()
    cat_id = settings.get("category_id")
    mains = settings.get("main_table_channels") or []
    main_lines = []
    for i, ch_id in enumerate(mains[:2]):
        if ch_id:
            main_lines.append(f"**Table {i + 1}:** <#{ch_id}>")
        else:
            main_lines.append(f"**Table {i + 1}:** {ns}")
    if not main_lines:
        main_lines = [t("admin_panel.routes.live_bj_no_mains", user_id=uid)]

    overflow = sum(
        1 for tbl in list_tables() if isinstance(tbl, dict) and not tbl.get("is_main")
    )

    embed = discord.Embed(
        title=t("admin_panel.routes.live_bj_title", user_id=uid),
        description=t("admin_panel.routes.live_bj_desc", user_id=uid),
        color=0x1ABC9C,
    )
    embed.add_field(
        name=t("admin_panel.routes.live_bj_category", user_id=uid),
        value=f"<#{cat_id}>" if cat_id else ns,
        inline=False,
    )
    embed.add_field(
        name=t("admin_panel.routes.live_bj_main_tables", user_id=uid),
        value="\n".join(main_lines),
        inline=False,
    )
    embed.add_field(
        name=t("admin_panel.routes.live_bj_overflow", user_id=uid),
        value=str(overflow),
        inline=True,
    )
    embed.set_footer(text=t("admin_panel.routes.live_bj_footer", user_id=uid))
    return embed


class LiveBlackjackCategorySelect(discord.ui.ChannelSelect):
    def __init__(self, user_id: int = 0):
        self.panel_user_id = user_id
        super().__init__(
            placeholder=t("admin_panel.routes.live_bj_cat_placeholder", user_id=str(user_id)),
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
            custom_id="admin_panel:live_bj_category",
        )

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", user_id=str(interaction.user.id)),
                ephemeral=True,
            )
        category = self.values[0]
        from modules.live_blackjack_tables import get_settings, save_settings

        settings = get_settings()
        settings["category_id"] = category.id
        save_settings(settings)
        embed = _build_live_blackjack_setup_embed(interaction.user.id)
        embed.add_field(
            name="✅",
            value=t(
                "admin_panel.routes.live_bj_cat_set",
                user_id=str(interaction.user.id),
                category=category.mention,
            ),
            inline=False,
        )
        await interaction.response.edit_message(
            embed=embed,
            view=LiveBlackjackSettingsView(self.panel_user_id),
        )


class LiveBlackjackInitTablesButton(discord.ui.Button):
    def __init__(self, user_id: int = 0):
        self.panel_user_id = user_id
        super().__init__(
            label=t("admin_panel.routes.live_bj_btn_create", user_id=str(user_id)),
            style=discord.ButtonStyle.success,
            emoji="🃏",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", user_id=str(interaction.user.id)),
                ephemeral=True,
            )
        if not interaction.guild:
            return await interaction.response.send_message("Guild only.", ephemeral=True)
        from modules.live_blackjack_tables import create_main_tables

        ok, err, mentions = await create_main_tables(interaction.guild, interaction.client)
        if not ok:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌", description=err, color=discord.Color.red()),
                ephemeral=True,
            )
        embed = _build_live_blackjack_setup_embed(interaction.user.id)
        embed.description = t(
            "admin_panel.routes.live_bj_created",
            user_id=str(interaction.user.id),
            channels=", ".join(mentions),
        )
        await interaction.response.edit_message(
            embed=embed,
            view=LiveBlackjackSettingsView(self.panel_user_id),
        )


class LiveBlackjackRefreshTablesButton(discord.ui.Button):
    def __init__(self, user_id: int = 0):
        self.panel_user_id = user_id
        super().__init__(
            label=t("admin_panel.routes.live_bj_btn_refresh", user_id=str(user_id)),
            style=discord.ButtonStyle.secondary,
            emoji="🔄",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", user_id=str(interaction.user.id)),
                ephemeral=True,
            )
        from cogs.live_blackjack import refresh_table_message
        from modules.live_blackjack_tables import list_tables

        n = 0
        for table in list_tables(interaction.guild.id if interaction.guild else None):
            await refresh_table_message(interaction.client, table)
            n += 1
        await interaction.response.send_message(
            t("admin_panel.routes.live_bj_refreshed", user_id=str(interaction.user.id), count=n),
            ephemeral=True,
        )


class LiveBlackjackSettingsView(discord.ui.View):
    def __init__(self, user_id: int = 0, *, return_to: str = "game_list"):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.return_to = return_to
        uid = str(user_id)
        cat_btn = discord.ui.Button(
            label=t("admin_panel.routes.live_bj_btn_category", user_id=uid),
            style=discord.ButtonStyle.primary,
            emoji="📁",
            row=0,
        )
        cat_btn.callback = self._set_category
        self.add_item(cat_btn)
        self.add_item(LiveBlackjackInitTablesButton(user_id))
        self.add_item(LiveBlackjackRefreshTablesButton(user_id))
        if return_to == "games_hub":
            self.add_item(BackToServerSettingsButton("games", user_id))
        else:
            self.add_item(BackToGameListButton(user_id))

    async def _set_category(self, interaction: discord.Interaction):
        view = discord.ui.View(timeout=300)
        view.add_item(LiveBlackjackCategorySelect(interaction.user.id))
        if self.return_to == "games_hub":
            view.add_item(BackToServerSettingsButton("games", interaction.user.id))
        else:
            view.add_item(BackToGameListButton(interaction.user.id))
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=t("admin_panel.routes.live_bj_cat_pick_title", user_id=str(interaction.user.id)),
                description=t("admin_panel.routes.live_bj_cat_pick_desc", user_id=str(interaction.user.id)),
                color=0x1ABC9C,
            ),
            view=view,
        )


class SetupPlayChannelsButton(discord.ui.Button):
    def __init__(self, user_id: int = 0):
        super().__init__(
            label=t("admin_panel.routes.play_setup_btn", user_id=str(user_id)),
            style=discord.ButtonStyle.success,
            emoji="🎮",
            row=1,
        )
        self.panel_user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", user_id=str(interaction.user.id)),
                ephemeral=True,
            )
        if not interaction.guild:
            return await interaction.response.send_message("Guild only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        from modules.private_room_hub import setup_play_channels

        ok, err, mentions = await setup_play_channels(interaction.guild, interaction.client)
        if not ok:
            return await interaction.followup.send(
                embed=discord.Embed(title="❌", description=err, color=discord.Color.red()),
                ephemeral=True,
            )
        desc = t(
            "admin_panel.routes.play_setup_done",
            user_id=str(interaction.user.id),
            channels=", ".join(mentions),
        )
        await interaction.followup.send(
            embed=discord.Embed(title="✅", description=desc, color=discord.Color.green()),
            ephemeral=True,
        )


class RefreshPlayHubMenusButton(discord.ui.Button):
    def __init__(self, user_id: int = 0):
        super().__init__(
            label=t("admin_panel.routes.play_refresh_btn", user_id=str(user_id)),
            style=discord.ButtonStyle.secondary,
            emoji="🔄",
            row=1,
        )
        self.panel_user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", user_id=str(interaction.user.id)),
                ephemeral=True,
            )
        if not interaction.guild:
            return await interaction.response.send_message("Guild only.", ephemeral=True)
        from modules.private_room_hub import refresh_play_hub_menus

        n = await refresh_play_hub_menus(interaction.guild, interaction.client)
        await interaction.response.send_message(
            t("admin_panel.routes.play_refreshed", user_id=str(interaction.user.id), count=n),
            ephemeral=True,
        )


class PrivateRoomSettingsView(discord.ui.View):
    """Private room settings management view"""
    
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(SetupPlayChannelsButton(user_id))
        self.add_item(RefreshPlayHubMenusButton(user_id))
    
    @discord.ui.button(label="🏠 Set Private Category", style=discord.ButtonStyle.primary, row=0)
    async def set_private_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Özel oda kategorisi ayarla"""
        view = discord.ui.View(timeout=300)
        view.add_item(CategorySelectMenu(interaction.user.id))
        view.add_item(BackToServerSettingsButton("channels", interaction.user.id))
        
        await interaction.response.edit_message(view=view)
    
    @discord.ui.button(label="📨 Send Private Room Menu", style=discord.ButtonStyle.success, row=0)
    async def send_private_room_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Özel oda menüsünü gönder"""
        view = discord.ui.View(timeout=300)
        view.add_item(PrivateRoomChannelSelect(interaction.user.id))
        view.add_item(BackToServerSettingsButton("channels", interaction.user.id))
        
        await interaction.response.edit_message(view=view)
    
    @discord.ui.button(label="⬅️ Sunucu & Kanallar", style=discord.ButtonStyle.secondary, row=2)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_CHANNELS, go_hub

        await go_hub(interaction, HUB_CHANNELS, user_id=interaction.user.id)


class ServerSettingsView(ChannelsHubView):
    """Geriye dönük uyumluluk — Sunucu & Kanallar hub'ı."""


class BetAmountModal(discord.ui.Modal):
    """Bahis miktarı modal"""
    
    def __init__(self, bet_type: str, user_id: int = 0):
        self.bet_type = bet_type  # "min" or "max"
        self.user_id = user_id
        uid = str(user_id)
        title = t("admin_panel.min_bet_modal" if bet_type == "min" else "admin_panel.max_bet_modal", user_id=uid)
        super().__init__(title=title, timeout=300)
        
        self.amount_input = discord.ui.TextInput(
            label=t("admin_panel.bet_amount_label", user_id=uid),
            placeholder=t("admin_panel.bet_amount_placeholder", user_id=uid),
            required=True,
            max_length=10,
            style=discord.TextStyle.short
        )
        self.add_item(self.amount_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        """Modal gönderildiğinde"""
        try:
            amount = int(self.amount_input.value)
            if amount < 1:
                raise ValueError("Amount must be positive")

            if self.bet_type == "min":
                set_data("server/server", {"minBet": amount})
                message = t("admin_panel.min_bet_set", user_id=str(interaction.user.id), amount=format_balance(amount, "real"))
            else:
                set_data("server/server", {"maxBet": amount})
                message = t("admin_panel.max_bet_set", user_id=str(interaction.user.id), amount=format_balance(amount, "real"))

            embed = discord.Embed(
                title="✅ Success",
                description=message,
                color=discord.Color.green()
            )
            root_data = get_data("server/server") or {}
            min_bet = format_balance(root_data.get("minBet", 20), "real")
            max_bet = format_balance(root_data.get("maxBet", 50000), "real")
            embed.add_field(name="💰 Min Bet", value=min_bet, inline=True)
            embed.add_field(name="💎 Max Bet", value=max_bet, inline=True)
            await interaction.response.edit_message(
                embed=_build_game_list_management_embed(interaction.user.id),
                view=GameListManagementView(interaction.user.id),
            )
            
        except ValueError:
            embed = discord.Embed(
                title="❌ Error",
                description=t("admin_panel.invalid_amount", user_id=str(interaction.user.id)),
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class MinesRiggedModal(discord.ui.Modal):
    """Mines rigged_chance ayarını değiştiren tek-alanlı modal."""

    def __init__(self, current_info: dict):
        super().__init__(title="Mines — Rigged Chance", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}

        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — Safe→Bomb",
            placeholder="5.0",
            default=str(current_info.get("rigged_chance", 5.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="Rigged chance must be a number between 0 and 100.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        games_data = _ensure_mines_game_entry(get_data("server/games") or {})
        mines = games_data.get("mines", {})
        mines["rigged_chance"] = round(rigged, 4)
        mines["last_modified"] = int(time.time())
        games_data["mines"] = mines
        set_data("server/games", games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Mines Rigged Chance Güncellendi",
                description=f"🎲 Rigged Chance: **{rigged}%**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class CrystalsRiggedModal(discord.ui.Modal):
    """Crystals rigged_chance — force no-match / cap loss before reveal."""

    def __init__(self, current_info: dict):
        super().__init__(title="Crystals — Rigged Chance", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — cap / no-match",
            placeholder="5.0",
            default=str(current_info.get("rigged_chance", 5.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="Rigged chance must be between 0 and 100.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        games_data = _ensure_crystals_game_entry(get_data("server/games") or {})
        c = games_data["crystals"]
        c["rigged_chance"] = round(rigged, 4)
        c["last_modified"] = int(time.time())
        set_data("server/games", games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Crystals Rigged Updated",
                description=f"🎲 Rigged Chance: **{rigged}%**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class MinesSettingsModal(discord.ui.Modal):
    """Mines-specific settings modal (house edge + custom emojis)."""

    def __init__(self, current_info: dict):
        super().__init__(title="Mines Settings", timeout=300)

        current_emojis = current_info.get("emojis", {}) if isinstance(current_info, dict) else {}

        self.house_edge_input = discord.ui.TextInput(
            label="House Edge (%)",
            placeholder="Example: 15.0",
            default=str(current_info.get("house_edge", 15.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.game_emoji_input = discord.ui.TextInput(
            label="Game Emoji (dropdown/title)",
            placeholder="Example: 💣 or <:mine:1234567890>",
            default=str(current_info.get("emoji", "💣")),
            required=True,
            max_length=80,
            style=discord.TextStyle.short,
        )
        self.hidden_emoji_input = discord.ui.TextInput(
            label="Hidden Cell Emoji",
            placeholder="Example: ❓",
            default=str(current_emojis.get("hidden", "❓")),
            required=True,
            max_length=80,
            style=discord.TextStyle.short,
        )
        self.gem_emoji_input = discord.ui.TextInput(
            label="Gem Emoji",
            placeholder="Example: 💎",
            default=str(current_emojis.get("gem", "💎")),
            required=True,
            max_length=80,
            style=discord.TextStyle.short,
        )
        self.mine_emoji_input = discord.ui.TextInput(
            label="Mine Emoji",
            placeholder="Example: 💣",
            default=str(current_emojis.get("mine", "💣")),
            required=True,
            max_length=80,
            style=discord.TextStyle.short,
        )

        self.add_item(self.house_edge_input)
        self.add_item(self.game_emoji_input)
        self.add_item(self.hidden_emoji_input)
        self.add_item(self.gem_emoji_input)
        self.add_item(self.mine_emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            house_edge = float(self.house_edge_input.value.replace(",", "."))
            if house_edge < 0 or house_edge >= 100:
                raise ValueError("House edge must be between 0 and 99.99")

            game_emoji = self.game_emoji_input.value.strip()
            hidden_emoji = self.hidden_emoji_input.value.strip()
            gem_emoji = self.gem_emoji_input.value.strip()
            mine_emoji = self.mine_emoji_input.value.strip()

            if not all([game_emoji, hidden_emoji, gem_emoji, mine_emoji]):
                raise ValueError("Emoji fields cannot be empty")

            games_data = get_data("server/games") or {}
            games_data = _ensure_mines_game_entry(games_data)

            mines = games_data.get("mines", {})
            mines["house_edge"] = round(house_edge, 4)
            mines["emoji"] = game_emoji
            mines["emojis"] = {
                "hidden": hidden_emoji,
                "gem": gem_emoji,
                "mine": mine_emoji,
            }
            mines["last_modified"] = int(time.time())
            games_data["mines"] = mines

            set_data("server/games", games_data)

            embed = discord.Embed(
                title="✅ Mines Settings Updated",
                description=(
                    f"House Edge: **{house_edge}%**\n"
                    f"Game Emoji: {game_emoji}\n"
                    f"Hidden: {hidden_emoji} | Gem: {gem_emoji} | Mine: {mine_emoji}"
                ),
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid Input",
                description="House edge must be a number between 0 and 99.99 and emoji fields cannot be empty.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class CrystalsSettingsModal(discord.ui.Modal):
    """Crystals ana ayarları: house edge, emoji, pair çarpanları."""

    def __init__(self, current_info: dict):
        super().__init__(title="Crystals Settings", timeout=300)
        emojis = current_info.get("emojis", {}) if isinstance(current_info, dict) else {}
        mults  = current_info.get("multipliers", {}) if isinstance(current_info, dict) else {}

        self.he_input = discord.ui.TextInput(
            label="House Edge (%)",
            placeholder="Example: 5.0",
            default=str(current_info.get("house_edge", 5.0)),
            required=True, max_length=8, style=discord.TextStyle.short,
        )
        self.game_emoji_input = discord.ui.TextInput(
            label="Game Emoji (title & dropdown)",
            placeholder="Example: 💎 or <:crystal:12345>",
            default=str(emojis.get("game", "💎")),
            required=True, max_length=80, style=discord.TextStyle.short,
        )
        self.hidden_input = discord.ui.TextInput(
            label="Hidden Crystal Emoji (unrevealed)",
            placeholder="Example: 🔮",
            default=str(emojis.get("hidden", "🔮")),
            required=True, max_length=80, style=discord.TextStyle.short,
        )
        self.one_pair_input = discord.ui.TextInput(
            label="One Pair Multiplier",
            placeholder="Example: 0.10",
            default=str(mults.get("one_pair", 0.10)),
            required=True, max_length=10, style=discord.TextStyle.short,
        )
        self.two_pair_input = discord.ui.TextInput(
            label="Two Pair Multiplier",
            placeholder="Example: 1.92",
            default=str(mults.get("two_pair", 1.92)),
            required=True, max_length=10, style=discord.TextStyle.short,
        )
        for item in (
            self.he_input, self.game_emoji_input, self.hidden_input,
            self.one_pair_input, self.two_pair_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            house_edge = float(self.he_input.value.replace(",", "."))
            one_pair   = float(self.one_pair_input.value.replace(",", "."))
            two_pair   = float(self.two_pair_input.value.replace(",", "."))
            game_emoji = self.game_emoji_input.value.strip()
            hidden     = self.hidden_input.value.strip()
            if house_edge < 0 or house_edge >= 100:
                raise ValueError("house_edge out of range")
            if not game_emoji or not hidden:
                raise ValueError("emoji cannot be empty")

            games_data = _ensure_crystals_game_entry(get_data("server/games") or {})
            c = games_data["crystals"]
            c["house_edge"] = round(house_edge, 4)
            c["last_modified"] = int(time.time())
            if not isinstance(c.get("emojis"), dict):
                c["emojis"] = {}
            c["emojis"]["game"]   = game_emoji
            c["emojis"]["hidden"] = hidden
            if not isinstance(c.get("multipliers"), dict):
                c["multipliers"] = {}
            c["multipliers"]["one_pair"] = round(one_pair, 4)
            c["multipliers"]["two_pair"] = round(two_pair, 4)
            set_data("server/games", games_data)

            embed = discord.Embed(
                title="✅ Crystals Settings Updated",
                description=(
                    f"House Edge: **{house_edge}%**\n"
                    f"Game Emoji: {game_emoji} │ Hidden: {hidden}\n"
                    f"One Pair: **{one_pair}x** │ Two Pair: **{two_pair}x**"
                ),
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except ValueError:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="House edge: 0–99.99 | Multipliers: numbers | Emojis: not empty.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )


class CrystalsPayoutsModal(discord.ui.Modal):
    """Crystals üst ödeme tablosu: triple → quintuple."""

    def __init__(self, current_info: dict):
        super().__init__(title="Crystals Payouts", timeout=300)
        mults = current_info.get("multipliers", {}) if isinstance(current_info, dict) else {}

        self.triple_input = discord.ui.TextInput(
            label="Triple Multiplier (3 of a kind)",
            placeholder="Example: 2.88",
            default=str(mults.get("triple", 2.88)),
            required=True, max_length=10, style=discord.TextStyle.short,
        )
        self.fh_input = discord.ui.TextInput(
            label="Full House Multiplier (3 + 2)",
            placeholder="Example: 3.84",
            default=str(mults.get("full_house", 3.84)),
            required=True, max_length=10, style=discord.TextStyle.short,
        )
        self.quad_input = discord.ui.TextInput(
            label="Quadruple Multiplier (4 of a kind)",
            placeholder="Example: 4.80",
            default=str(mults.get("quadruple", 4.80)),
            required=True, max_length=10, style=discord.TextStyle.short,
        )
        self.quint_input = discord.ui.TextInput(
            label="Quintuple Multiplier (5 of a kind)",
            placeholder="Example: 20.0",
            default=str(mults.get("quintuple", 20.0)),
            required=True, max_length=10, style=discord.TextStyle.short,
        )
        for item in (self.triple_input, self.fh_input, self.quad_input, self.quint_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            triple = float(self.triple_input.value.replace(",", "."))
            fh     = float(self.fh_input.value.replace(",", "."))
            quad   = float(self.quad_input.value.replace(",", "."))
            quint  = float(self.quint_input.value.replace(",", "."))
            if any(v < 0 for v in (triple, fh, quad, quint)):
                raise ValueError("negative multiplier")

            games_data = _ensure_crystals_game_entry(get_data("server/games") or {})
            c = games_data["crystals"]
            if not isinstance(c.get("multipliers"), dict):
                c["multipliers"] = {}
            c["multipliers"].update({
                "triple": round(triple, 4),
                "full_house": round(fh, 4),
                "quadruple": round(quad, 4),
                "quintuple": round(quint, 4),
            })
            c["last_modified"] = int(time.time())
            set_data("server/games", games_data)

            embed = discord.Embed(
                title="✅ Crystals Payouts Updated",
                description=(
                    f"Triple: **{triple}x** │ Full House: **{fh}x**\n"
                    f"Quadruple: **{quad}x** │ Quintuple: **{quint}x**"
                ),
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except ValueError:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="All multiplier fields must be non-negative numbers.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )


class BackButton(discord.ui.Button):
    """Geri butonu - Ana menüye dön"""
    
    def __init__(self):
        super().__init__(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=2)
    
    async def callback(self, interaction: discord.Interaction):
        from modules.admin_panel_nav import go_home

        await go_home(interaction, user_id=interaction.user.id)


class BackToServerSettingsButton(discord.ui.Button):
    """Hub veya ana menüye dön."""

    def __init__(self, hub: str = "channels", user_id: int = 0):
        from modules.admin_panel_nav import back_hub_label

        self.hub = hub
        self.user_id = user_id
        super().__init__(
            label=back_hub_label(hub, user_id),
            style=discord.ButtonStyle.secondary,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        from modules.admin_panel_nav import go_hub

        await go_hub(interaction, self.hub, user_id=interaction.user.id)


# ─────────────────────────────────────────────────────────────────────────────
# Exchange Rates Management
# ─────────────────────────────────────────────────────────────────────────────

def _parse_emoji_for_select(emoji_str: str):
    """Parse an emoji string for use in discord.SelectOption."""
    if not emoji_str:
        return None
    s = emoji_str.strip()
    if s.startswith("<") and ":" in s and s.endswith(">"):
        try:
            return discord.PartialEmoji.from_str(s)
        except Exception:
            return None
    return s or None


def _build_exchange_rates_embed() -> discord.Embed:
    """Build the exchange rates admin management embed."""
    rates_data = get_data("server/exchange_rates") or {}
    coin_usd_rate = rates_data.get("coin_usd_rate", 0.10)
    custom_rates  = rates_data.get("custom_rates", [])

    embed = discord.Embed(
        title="💱 Exchange Rates",
        description=(
            "Kullanıcılara private room'da gösterilen kur tablosunu yönetin.\n"
            "Temel USD kurundan başka özel para birimleri ekleyebilirsiniz."
        ),
        color=0xf5a623,
    )

    embed.add_field(
        name="💵 Temel USD Kuru",
        value=f"**1 Coin = ${coin_usd_rate:.4g}**",
        inline=False,
    )

    if custom_rates:
        lines = ""
        for r in custom_rates:
            emoji   = r.get("emoji", "🪙")
            name    = r.get("name", "Unknown")
            amount  = r.get("amount", 0)
            lines  += f"{emoji} **{name}** — 1 Coin = **{amount} {name}**\n"
        embed.add_field(
            name=f"🪙 Özel Kurlar  ·  {len(custom_rates)} adet",
            value=lines,
            inline=False,
        )
    else:
        embed.add_field(
            name="🪙 Özel Kurlar",
            value="*Henüz özel kur eklenmedi.*",
            inline=False,
        )

    embed.set_footer(text="Vegas Casino | Exchange Rates Management")
    return embed


class SetBaseRateModal(discord.ui.Modal, title="Set Base USD Rate"):
    rate_input = discord.ui.TextInput(
        label="1 Coin = $? (USD)",
        placeholder="Example: 0.10",
        required=True,
        max_length=12,
        style=discord.TextStyle.short,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rate = float(self.rate_input.value.replace(",", "."))
            if rate <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Geçersiz Değer",
                    description="Lütfen 0'dan büyük bir sayı girin (örn: `0.10`).",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

        rates_data = get_data("server/exchange_rates") or {}
        rates_data["coin_usd_rate"] = round(rate, 6)
        set_data("server/exchange_rates", rates_data)

        embed = discord.Embed(
            title="✅ Temel Kur Güncellendi",
            description=f"**1 Coin = `${rate:.6g}` USD**",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Vegas Casino | Exchange Rates")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AddExchangeRateModal(discord.ui.Modal, title="Add Exchange Rate"):
    name_input = discord.ui.TextInput(
        label="Para Birimi Adı",
        placeholder="Örn: Byte Coin",
        required=True,
        max_length=50,
        style=discord.TextStyle.short,
    )
    emoji_input = discord.ui.TextInput(
        label="Emoji",
        placeholder="Örn: 🪙  veya  <:bytecoin:123456789>",
        required=True,
        max_length=100,
        style=discord.TextStyle.short,
    )
    amount_input = discord.ui.TextInput(
        label="Miktar  (1 Coin = kaç adet?)",
        placeholder="Örn: 450",
        required=True,
        max_length=20,
        style=discord.TextStyle.short,
    )

    async def on_submit(self, interaction: discord.Interaction):
        import os as _os
        try:
            name   = self.name_input.value.strip()
            emoji  = self.emoji_input.value.strip()
            amount = float(self.amount_input.value.strip().replace(",", "."))
            if amount <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Geçersiz Değer",
                    description="Miktar 0'dan büyük bir sayı olmalıdır.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

        rates_data = get_data("server/exchange_rates") or {}
        if "custom_rates" not in rates_data or not isinstance(rates_data["custom_rates"], list):
            rates_data["custom_rates"] = []

        rate_id = _os.urandom(4).hex()
        rates_data["custom_rates"].append({
            "id":       rate_id,
            "name":     name,
            "emoji":    emoji,
            "amount":   amount,
            "added_at": int(time.time()),
        })
        set_data("server/exchange_rates", rates_data)

        embed = discord.Embed(
            title="✅ Kur Eklendi",
            description=f"{emoji} **{name}**\n1 Coin = **{amount} {name}**",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Vegas Casino | Exchange Rates")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class EditExchangeRateModal(discord.ui.Modal, title="Edit Exchange Rate"):
    def __init__(self, rate: dict):
        super().__init__()
        self.rate_id = rate["id"]
        self.name_input = discord.ui.TextInput(
            label="Para Birimi Adı",
            default=rate.get("name", ""),
            required=True,
            max_length=50,
            style=discord.TextStyle.short,
        )
        self.emoji_input = discord.ui.TextInput(
            label="Emoji",
            default=rate.get("emoji", ""),
            required=True,
            max_length=100,
            style=discord.TextStyle.short,
        )
        self.amount_input = discord.ui.TextInput(
            label="Miktar  (1 Coin = kaç adet?)",
            default=str(rate.get("amount", "")),
            required=True,
            max_length=20,
            style=discord.TextStyle.short,
        )
        for item in (self.name_input, self.emoji_input, self.amount_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            name   = self.name_input.value.strip()
            emoji  = self.emoji_input.value.strip()
            amount = float(self.amount_input.value.strip().replace(",", "."))
            if amount <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Geçersiz Değer",
                    description="Miktar 0'dan büyük bir sayı olmalıdır.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

        rates_data  = get_data("server/exchange_rates") or {}
        custom_rates = rates_data.get("custom_rates", [])
        updated = False
        for r in custom_rates:
            if r.get("id") == self.rate_id:
                r.update({"name": name, "emoji": emoji, "amount": amount})
                r.pop("unit", None)
                updated = True
                break

        if not updated:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Hata", description="Kur bulunamadı.", color=discord.Color.red()),
                ephemeral=True,
            )

        rates_data["custom_rates"] = custom_rates
        set_data("server/exchange_rates", rates_data)

        embed = discord.Embed(
            title="✅ Kur Güncellendi",
            description=f"{emoji} **{name}**\n1 Coin = **{amount} {name}**",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Vegas Casino | Exchange Rates")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ManageRateSelect(discord.ui.Select):
    """Select to pick a custom rate for edit or delete."""

    def __init__(self, action: str):
        self.action = action  # "edit" | "delete"
        rates_data   = get_data("server/exchange_rates") or {}
        custom_rates = rates_data.get("custom_rates", [])

        options = []
        for r in custom_rates[:25]:
            emoji_parsed = _parse_emoji_for_select(r.get("emoji", ""))
            amount = r.get("amount", "?")
            name   = r.get("name",   "Unknown")
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    description=f"1 Coin = {amount} {name}"[:100],
                    emoji=emoji_parsed,
                    value=r["id"],
                )
            )

        if not options:
            options.append(discord.SelectOption(label="Kur bulunamadı", value="_none_"))

        super().__init__(
            placeholder=f"Düzenlenecek/silinecek kuru seç ({action})...",
            options=options,
            custom_id=f"exchange_rates:{action}_select",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "_none_":
            return await interaction.response.send_message("❌ Kur bulunamadı.", ephemeral=True)

        rate_id      = self.values[0]
        rates_data   = get_data("server/exchange_rates") or {}
        custom_rates = rates_data.get("custom_rates", [])
        rate         = next((r for r in custom_rates if r.get("id") == rate_id), None)

        if not rate:
            return await interaction.response.send_message("❌ Kur bulunamadı.", ephemeral=True)

        if self.action == "edit":
            await interaction.response.send_modal(EditExchangeRateModal(rate))

        elif self.action == "delete":
            rates_data["custom_rates"] = [r for r in custom_rates if r.get("id") != rate_id]
            set_data("server/exchange_rates", rates_data)
            emoji = rate.get("emoji", "")
            name  = rate.get("name", "Unknown")
            embed = discord.Embed(
                title="🗑️ Kur Silindi",
                description=f"{emoji} **{name}** başarıyla kaldırıldı.",
                color=discord.Color.orange(),
            )
            embed.set_footer(text="Vegas Casino | Exchange Rates")
            await interaction.response.send_message(embed=embed, ephemeral=True)


class ExchangeRatesManagementView(discord.ui.View):
    """Admin panel: Exchange Rates management view."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="💵 Temel Kuru Ayarla", style=discord.ButtonStyle.primary, row=0)
    async def set_base_rate(self, interaction: discord.Interaction, button: discord.ui.Button):
        rates_data   = get_data("server/exchange_rates") or {}
        current_rate = rates_data.get("coin_usd_rate", 0.10)
        modal = SetBaseRateModal()
        modal.rate_input.default = str(current_rate)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="➕ Kur Ekle", style=discord.ButtonStyle.success, row=0)
    async def add_rate(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddExchangeRateModal())

    @discord.ui.button(label="✏️ Kur Düzenle", style=discord.ButtonStyle.secondary, row=1)
    async def edit_rate(self, interaction: discord.Interaction, button: discord.ui.Button):
        rates_data   = get_data("server/exchange_rates") or {}
        custom_rates = rates_data.get("custom_rates", [])
        if not custom_rates:
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Kur Yok",
                    description="Düzenlenecek özel kur bulunmuyor.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        view = discord.ui.View(timeout=120)
        view.add_item(ManageRateSelect("edit"))
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✏️ Kur Düzenle",
                description="Aşağıdan düzenlemek istediğin kuru seç:",
                color=discord.Color.blue(),
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="🗑️ Kur Sil", style=discord.ButtonStyle.danger, row=1)
    async def remove_rate(self, interaction: discord.Interaction, button: discord.ui.Button):
        rates_data   = get_data("server/exchange_rates") or {}
        custom_rates = rates_data.get("custom_rates", [])
        if not custom_rates:
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Kur Yok",
                    description="Silinecek özel kur bulunmuyor.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        view = discord.ui.View(timeout=120)
        view.add_item(ManageRateSelect("delete"))
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🗑️ Kur Sil",
                description="Aşağıdan silmek istediğin kuru seç:",
                color=discord.Color.red(),
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="🔄 Yenile", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = _build_exchange_rates_embed()
        await interaction.response.edit_message(embed=embed, view=ExchangeRatesManagementView())

    @discord.ui.button(label="⬅️ Ödeme & Finans", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_PAYMENTS, go_hub

        await go_hub(interaction, HUB_PAYMENTS, user_id=interaction.user.id)


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast DM
# ─────────────────────────────────────────────────────────────────────────────

def _build_broadcast_dm_preview_embed(title: str, description: str) -> discord.Embed:
    """Build the DM builder/preview embed for the admin panel."""
    embed = discord.Embed(
        title="📨 DM Yayını — Düzenleyici",
        color=0x5865F2,
    )
    embed.add_field(name="📌 Başlık", value=title or "*(boş)*", inline=False)
    embed.add_field(name="📝 Açıklama", value=(description[:512] + "…") if len(description) > 512 else (description or "*(boş)*"), inline=False)
    embed.set_footer(text="Vegas Casino | Broadcast DM Builder")
    return embed


class _DMEditModal(discord.ui.Modal):
    """Modal for editing DM embed title or description."""

    def __init__(self, field: str, view: "BroadcastDMView"):
        self._field = field
        self._broadcast_view = view
        if field == "title":
            super().__init__(title="Başlık Değiştir")
            self.text_input = discord.ui.TextInput(
                label="Yeni Başlık",
                placeholder="Örn: 🎉 Önemli Duyuru",
                default=view.embed_title[:4000] if view.embed_title else "",
                required=True,
                max_length=256,
                style=discord.TextStyle.short,
            )
        else:
            super().__init__(title="Açıklama Değiştir")
            self.text_input = discord.ui.TextInput(
                label="Yeni Açıklama",
                placeholder="DM olarak gönderilecek mesaj...",
                default=view.embed_description[:4000] if view.embed_description else "",
                required=True,
                max_length=4000,
                style=discord.TextStyle.paragraph,
            )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        if self._field == "title":
            self._broadcast_view.embed_title = self.text_input.value
        else:
            self._broadcast_view.embed_description = self.text_input.value
        embed = _build_broadcast_dm_preview_embed(
            self._broadcast_view.embed_title,
            self._broadcast_view.embed_description,
        )
        await interaction.response.edit_message(embed=embed, view=self._broadcast_view)


class BroadcastDMView(discord.ui.View):
    """Admin panel — DM broadcast builder view."""

    def __init__(self, embed_title: str = "📢 Duyuru", embed_description: str = "Açıklama girilmedi."):
        super().__init__(timeout=300)
        self.embed_title = embed_title
        self.embed_description = embed_description

    @discord.ui.button(label="✏️ Başlık Değiştir", style=discord.ButtonStyle.secondary, row=0)
    async def edit_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_DMEditModal("title", self))

    @discord.ui.button(label="📝 Açıklama Değiştir", style=discord.ButtonStyle.secondary, row=0)
    async def edit_description(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_DMEditModal("description", self))

    @discord.ui.button(label="👁️ Önizle", style=discord.ButtonStyle.primary, row=1)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        preview_embed = discord.Embed(
            title=self.embed_title,
            description=self.embed_description,
            color=discord.Color.blue(),
        )
        preview_embed.set_footer(text="Vegas Casino")
        await interaction.response.send_message(embed=preview_embed, ephemeral=True)

    @discord.ui.button(label="📨 Herkese Gönder", style=discord.ButtonStyle.danger, row=1)
    async def send_to_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)

        dm_embed = discord.Embed(
            title=self.embed_title,
            description=self.embed_description,
            color=discord.Color.blue(),
        )
        dm_embed.set_footer(text="Vegas Casino")

        success = 0
        failed = 0
        for member in interaction.guild.members:
            if member.bot:
                continue
            try:
                await member.send(embed=dm_embed)
                success += 1
            except Exception:
                failed += 1

        result_embed = discord.Embed(
            title="📨 DM Yayını Tamamlandı",
            description=f"✅ Başarılı: **{success}**\n❌ Başarısız (DM kapalı vb.): **{failed}**",
            color=discord.Color.green(),
        )
        result_embed.set_footer(text="Vegas Casino | Broadcast DM")
        await interaction.followup.send(embed=result_embed, ephemeral=True)

    @discord.ui.button(label="⬅️ Bot & Araçlar", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_TOOLS, go_hub

        await go_hub(interaction, HUB_TOOLS, user_id=interaction.user.id)


class AdminPanel(commands.Cog):
    """Admin panel cog"""
    
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="panel", description="Open admin panel (Admin only)")
    async def panel(self, interaction: discord.Interaction):
        """Admin paneli aç"""
        if check_permission(interaction.user.id, "admin"):
            embed = discord.Embed(
                title="❌ Permission Denied",
                description=t("errors.no_permission", user_id=str(interaction.user.id)),
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        view = AdminPanelView(interaction.user.id)
        embed = _build_admin_panel_embed(interaction)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


    @commands.command()
    async def send_guild_id(self, ctx):
        """Sunucu ID'sini gönderir (sadece bot sahibi)"""
        
        
        await ctx.send(f"Sunucu ID'si: {ctx.guild.id}")
    
    @app_commands.command(name="reset_balances", description="[Owner] Reset ALL users' balance to 0")
    @app_commands.describe(mode="Which balance to reset: real, demo, or both")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Real balance only",  value="real"),
        app_commands.Choice(name="Demo balance only",  value="demo"),
        app_commands.Choice(name="Both real and demo", value="both"),
    ])
    async def reset_balances(self, interaction: discord.Interaction, mode: str = "real"):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message("❌ Owner only.", ephemeral=True)

        from modules.database import _get_conn
        conn = _get_conn()
        rows = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
        count = rows["cnt"] if rows else "?"

        embed = discord.Embed(
            title="⚠️ Reset All Balances",
            description=(
                f"This will set **{mode}** balance to **0** for all `{count}` registered users.\n\n"
                "**This action cannot be undone!**"
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, view=_ConfirmResetBalancesView(mode), ephemeral=True)

    @app_commands.command(name="reset_user", description="[Admin] Bir kullanıcının istatistiklerini, geçmişini ve deposit/withdraw kayıtlarını sıfırla")
    @app_commands.describe(user="Sıfırlanacak kullanıcı")
    async def reset_user(self, interaction: discord.Interaction, user: discord.Member):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message("❌ Admin yetkisi gerekli.", ephemeral=True)

        embed = discord.Embed(
            title="⚠️ Kullanıcı Verisi Sıfırla",
            description=(
                f"**{user.mention}** (`{user.id}`) için aşağıdaki veriler **kalıcı olarak silinecek:**\n\n"
                "• 📊 Tüm istatistikler (wager, kazanç, kayıp, deposit…)\n"
                "• 🎮 Oyun geçmişi\n"
                "• 📥 Deposit geçmişi\n"
                "• 📤 Withdraw geçmişi\n\n"
                "**Bu işlem geri alınamaz!**"
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Vegas Casino | Admin Panel")
        await interaction.response.send_message(
            embed=embed,
            view=_ConfirmResetUserView(user.id, str(user)),
            ephemeral=True,
        )

    @app_commands.command(
        name="clear_all_promos",
        description="[Admin] Tüm kullanıcıların aktif promo verisini sıfırla (kod şablonları kalır)",
    )
    async def clear_all_promos(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message("❌ Admin yetkisi gerekli.", ephemeral=True)
        embed = discord.Embed(
            title="⚠️ Tüm Promo Verilerini Sıfırla",
            description=(
                "Tüm kullanıcıların **active_promo** kaydı silinir (wagering, completed, buglu state).\n\n"
                "**Promo kod şablonları** (`server/promo_codes`) ve **used_by** listeleri **değişmez**.\n\n"
                "Eski promo bug'ından etkilenen oyuncular için bir kez çalıştırın.\n\n"
                "**Bu işlem geri alınamaz!**"
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=_ConfirmClearAllPromosView(),
            ephemeral=True,
        )

    @app_commands.command(
        name="reset_platform",
        description="[Owner] Sıfırla: items/cases, oyun setup ve admin yetkileri hariç tüm veri",
    )
    async def reset_platform(self, interaction: discord.Interaction):
        if not is_super_admin(interaction.user.id):
            if check_permission(interaction.user.id, "admin"):
                return await interaction.response.send_message(
                    "❌ Sadece super admin veya bot sahibi kullanabilir.",
                    ephemeral=True,
                )
        embed = discord.Embed(
            title="⚠️ Platform Sıfırlama",
            description=(
                "**Korunacak:**\n"
                "• `/items` ve `/cases` — item & kasa kataloğu\n"
                "• `server/games` — oyun ayarları (min/max, rig %, house edge)\n"
                "• `server/admins` — kullanıcı yetkileri\n\n"
                "**Silinecek / sıfırlanacak:**\n"
                "• Tüm bakiyeler, istatistikler, geçmişler\n"
                "• Rakeback birikimi (accumulated) ve claim geçmişi\n"
                "• Promo state, referral, ticket kayıtları, oyun oturumları\n"
                "• Diğer tüm `server/*` runtime verisi\n\n"
                "**Geri alınamaz!** Onay için **RESET** yazın."
            ),
            color=discord.Color.dark_red(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=_ConfirmPlatformResetView(),
            ephemeral=True,
        )

    @app_commands.command(
        name="ticket_panel",
        description="[Admin] Kategorili destek ticket panelini bir kanala gönder",
    )
    @app_commands.describe(channel="Panelin gönderileceği metin kanalı")
    async def ticket_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description=t("errors.no_permission", user_id=str(interaction.user.id)),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        from modules.ticket_system import TicketPanelView, build_ticket_panel_embed

        embed = build_ticket_panel_embed()
        await channel.send(embed=embed, view=TicketPanelView())
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Ticket Paneli Gönderildi",
                description=f"Panel {channel.mention} kanalına yerleştirildi.\n\n"
                "Ticket kategorisi: **Panel → Ticket System** ile ayarlayın.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="promodos", description="Promo DOS")
    async def promodos(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description=t("errors.no_permission", user_id=str(interaction.user.id)),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        state = _get_promodos_state()
        active = bool(state.get("active", False))
        embed = _build_promodos_embed(active)
        await interaction.response.send_message(
            embed=embed,
            view=PromoDosView(active),
            ephemeral=True,
        )

    @commands.command(name="resetgames", aliases=["cleargames", "resetgame"])
    async def resetgames(self, ctx: commands.Context, target: str = ""):
        """
        [Admin] Takılı oyun oturumlarını temizle.
        `.resetgames` — herkes | `.resetgames @user` — tek kullanıcı | `.resetgames 123456789`
        """
        if check_permission(ctx.author.id, "admin"):
            return await ctx.send(
                embed=discord.Embed(
                    description="❌ Admin yetkisi gerekli.",
                    color=discord.Color.red(),
                )
            )

        from cogs.games import clear_stuck_game_sessions

        user_id: int | None = None
        raw = (target or "").strip()
        if raw:
            if ctx.message.mentions:
                user_id = ctx.message.mentions[0].id
            else:
                try:
                    user_id = int(raw.lstrip("<@!>").rstrip(">"))
                except ValueError:
                    return await ctx.send(
                        embed=discord.Embed(
                            description="❌ Kullanım: `.resetgames` | `.resetgames @user` | `.resetgames <id>`",
                            color=discord.Color.red(),
                        )
                    )

        result = await clear_stuck_game_sessions(user_id, refund=True)
        count = result["count"]
        refunded = result["refunded_total"]

        if count == 0:
            desc = (
                f"**{target or 'Tüm kullanıcılar'}** için aktif oyun oturumu yok."
                if user_id
                else "Aktif oyun oturumu yok (cache temizlendi)."
            )
        elif user_id:
            desc = (
                f"<@{user_id}> için **{count}** oturum kapatıldı."
                + (f" İade: **{fmt_pts(refunded)} pts**." if refunded > 0 else "")
            )
        else:
            desc = (
                f"**{count}** takılı oturum kapatıldı."
                + (f" Toplam iade: **{fmt_pts(refunded)} pts**." if refunded > 0 else "")
            )

        await ctx.send(
            embed=discord.Embed(
                title="✅ Oyun oturumları sıfırlandı",
                description=desc,
                color=discord.Color.green(),
            )
        )

    @app_commands.command(
        name="resetgames",
        description="[Admin] Takılı oyun oturumlarını temizle (active game hatası)",
    )
    @app_commands.describe(user="Boş bırak = tüm kullanıcılar")
    async def resetgames_slash(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                "❌ Admin yetkisi gerekli.", ephemeral=True,
            )

        from cogs.games import clear_stuck_game_sessions

        await interaction.response.defer(ephemeral=True)
        result = await clear_stuck_game_sessions(
            user.id if user else None,
            refund=True,
        )
        count = result["count"]
        refunded = result["refunded_total"]
        if user:
            if count == 0:
                desc = f"{user.mention} için aktif oturum yok (cache temizlendi)."
            else:
                desc = f"{user.mention} — **{count}** oturum kapatıldı."
                if refunded > 0:
                    desc += f" İade: **{fmt_pts(refunded)} pts**."
        else:
            desc = f"**{count}** oturum kapatıldı."
            if refunded > 0:
                desc += f" Toplam iade: **{fmt_pts(refunded)} pts**."
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Oyun oturumları sıfırlandı",
                description=desc,
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    @commands.command(name="savemojis")
    async def save_emojis(self, ctx):
        """Sunucudaki tüm emojileri emojis.json dosyasına kaydeder"""
        
        # Yetki kontrolü
        if check_permission(ctx.author.id, "admin"):
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You need admin permission to use this command!",
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)
        
        # Emojileri topla
        emojis_dict = get_data("server/emojis")
        guild = ctx.guild
        
        # Sunucudaki tüm emojileri kaydet
        for emoji in guild.emojis:
            emoji_str = str(emoji)
            emojis_dict[emoji.name] = emoji_str
        
        # Kaydet
        set_data("server/emojis", emojis_dict)
        
        embed = discord.Embed(
            title="✅ Emojis Saved",
            description=f"Successfully saved {len(guild.emojis)} emojis to emojis.json!",
            color=discord.Color.green()
        )
        embed.add_field(
            name="📊 Summary",
            value=f"**Total Emojis:** {len(emojis_dict)}\n"
                  f"**Animated:** {len([e for e in guild.emojis if e.animated])}\n"
                  f"**Static:** {len([e for e in guild.emojis if not e.animated])}",
            inline=False
        )
        
        await ctx.send(embed=embed)


def _build_game_list_management_embed(user_id: int | str) -> discord.Embed:
    """Game list with per-game min/max and global bet limits."""
    uid = str(user_id)
    games_data = _ensure_all_game_entries(get_data("server/games") or {})
    root = get_data("server/server") or {}
    embed = discord.Embed(
        title=t("game_management.game_list_title", user_id=uid),
        description=t("game_management.game_list_description", user_id=uid),
        color=discord.Color.blue(),
    )
    embed.add_field(
        name=t("game_management.global_limits", user_id=uid),
        value=(
            f"**{t('game_management.global_min', user_id=uid)}:** "
            f"{format_balance(root.get('minBet', 20), 'real')}\n"
            f"**{t('game_management.global_max', user_id=uid)}:** "
            f"{format_balance(root.get('maxBet', 50000), 'real')}"
        ),
        inline=False,
    )
    for game_id, game_info in games_data.items():
        if not isinstance(game_info, dict):
            continue
        status = (
            t("game_management.enabled", user_id=uid)
            if game_info.get("enabled")
            else t("game_management.disabled", user_id=uid)
        )
        embed.add_field(
            name=f"{game_info.get('emoji', '🎮')} {game_info.get('name', game_id)}",
            value=(
                f"**Status:** {status}\n"
                f"**Min:** {format_balance(game_info.get('min_bet', 0), 'real')}\n"
                f"**Max:** {format_balance(game_info.get('max_bet', 0), 'real')}"
            ),
            inline=True,
        )
    return embed


class BackToGameListButton(discord.ui.Button):
    def __init__(self, user_id: int = 0):
        super().__init__(
            label=t("game_management.back_to_list", user_id=str(user_id)),
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.panel_user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        await _persist_games_panel(get_data("server/games") or {})
        await interaction.response.edit_message(
            embed=_build_game_list_management_embed(interaction.user.id),
            view=GameListManagementView(interaction.user.id),
        )


class GameListManagementView(discord.ui.View):
    """Game list, select, global min/max, and statistics on one screen."""

    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(GameListSelect())

    @discord.ui.button(label="💰 Set Min Bet", style=discord.ButtonStyle.secondary, row=1)
    async def set_min_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BetAmountModal("min", interaction.user.id))

    @discord.ui.button(label="💎 Set Max Bet", style=discord.ButtonStyle.secondary, row=1)
    async def set_max_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BetAmountModal("max", interaction.user.id))

    @discord.ui.button(label="📊 Statistics", style=discord.ButtonStyle.success, row=1)
    async def view_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = _build_game_stats_overview_embed()
        view = GameStatsOverviewView(interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="⬅️ Oyunlar", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_GAMES, go_hub

        await go_hub(interaction, HUB_GAMES, user_id=interaction.user.id)


def _build_game_stats_overview_embed() -> discord.Embed:
    """Build overview embed for server/game_stats."""
    game_stats = get_data("server/game_stats") or {}
    games_data = get_data("server/games") or {}

    popularity = sorted(
        game_stats.items(),
        key=lambda item: item[1].get("all_time", {}).get("total_plays", 0),
        reverse=True
    )
    rank_map = {game_id: index + 1 for index, (game_id, _) in enumerate(popularity)}

    embed = discord.Embed(
        title="📊 Game Statistics Overview",
        description="Overall game performance statistics",
        color=discord.Color.green()
    )

    if not game_stats:
        embed.add_field(name="No Data", value="No game statistics recorded yet.", inline=False)
        return embed

    for game_id, stats in game_stats.items():
        game_info = games_data.get(game_id, {})
        all_time = stats.get("all_time", {})
        rank_value = rank_map.get(game_id, 0)

        embed.add_field(
            name=f"{game_info.get('emoji', '🎮')} {game_info.get('name', game_id)}",
            value=(
                f"**Plays:** {all_time.get('total_plays', 0)}\n"
                f"**Wagered:** {format_balance(all_time.get('total_wagered', 0), 'real')}\n"
                f"**Profit:** {format_balance(all_time.get('total_profit', 0), 'real')}\n"
                f"**Rank:** #{rank_value}"
            ),
            inline=True
        )

    return embed


class GameStatsOverviewView(discord.ui.View):
    """Overview statistics panel with reset action."""

    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="🗑️ Reset Overview Stats", style=discord.ButtonStyle.danger, row=0)
    async def reset_overview_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                "❌ You don't have permission to reset stats.",
                ephemeral=True,
            )

        replace_data("server/game_stats", {})
        embed = _build_game_stats_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send("✅ Overview statistics reset successfully.", ephemeral=True)

    @discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _persist_games_panel(get_data("server/games") or {})
        await interaction.response.edit_message(
            embed=_build_game_list_management_embed(interaction.user.id),
            view=GameListManagementView(interaction.user.id),
        )


class GameListSelect(discord.ui.Select):
    """Oyun seçim menüsü"""
    
    def __init__(self):
        games_data = _ensure_all_game_entries(get_data("server/games") or {})
        set_data("server/games", games_data)

        options = []
        for game_id, game_info in sorted(games_data.items(), key=lambda x: x[1].get("name", x[0]) if isinstance(x[1], dict) else x[0]):
            if not isinstance(game_info, dict):
                continue
            status_emoji = "✅" if game_info.get("enabled") else "❌"
            label = str(game_info.get("name", game_id))[:100]
            options.append(
                discord.SelectOption(
                    label=label,
                    description=f"{status_emoji} | Category: {game_info.get('category', 'N/A')}"[:100],
                    emoji=game_info.get("emoji") or "🎮",
                    value=game_id,
                )
            )
        if not options:
            options = [
                discord.SelectOption(label="No games", value="_none", description="—"),
            ]
        
        super().__init__(
            placeholder="Select a game to manage...",
            options=options,
            custom_id="game_management:game_select",
            row=0
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Oyun seçildiğinde detay göster"""
        game_id = self.values[0]
        if game_id == "_none":
            return await interaction.response.defer()
        games_data = await _persist_games_panel(get_data("server/games") or {})
        game_stats = get_data("server/game_stats") or {}

        game_info = games_data.get(game_id) or _repair_game_entry(game_id, {})
        stats = game_stats.get(game_id, {}).get("all_time", {})

        status = t("game_management.enabled", user_id=str(interaction.user.id)) if game_info.get("enabled") else t("game_management.disabled", user_id=str(interaction.user.id))

        embed = discord.Embed(
            title=t("game_management.game_detail_title", user_id=str(interaction.user.id)).format(
                emoji=game_info.get("emoji", "🎮"),
                name=game_info.get("name", game_id),
            ),
            description=game_info.get("description", "No description"),
            color=discord.Color.green() if game_info.get("enabled") else discord.Color.red()
        )
        
        # Oyun bilgileri
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name=t("game_management.category", user_id=str(interaction.user.id)), value=game_info.get("category", "N/A"), inline=True)
        embed.add_field(name=t("game_management.house_edge", user_id=str(interaction.user.id)), value=f"{game_info.get('house_edge', 0)}%", inline=True)
        
        embed.add_field(name="Min Bet", value=format_balance(game_info.get("min_bet", 0), "real"), inline=True)
        embed.add_field(name="Max Bet", value=format_balance(game_info.get("max_bet", 0), "real"), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        
        # İstatistikler
        embed.add_field(name=t("game_management.total_plays", user_id=str(interaction.user.id)), value=str(stats.get("total_plays", 0)), inline=True)
        embed.add_field(name=t("game_management.total_profit", user_id=str(interaction.user.id)), value=format_balance(stats.get("total_profit", 0), "real"), inline=True)
        embed.add_field(name=t("game_management.popularity_rank", user_id=str(interaction.user.id)), value=f"#{stats.get('popularity_rank', 0)}", inline=True)
        
        view = GameDetailView(game_id)
        await interaction.response.edit_message(embed=embed, view=view)


# ─── Crystals Emoji Setup Wizard ──────────────────────────────────────────────

_CRYSTAL_KEYS = ["blue", "white", "black", "purple", "yellow", "green", "red", "aqua", "platform", "bosluk"]

_CRYSTAL_NAMES = {
    "blue":     "Mavi",
    "white":    "Beyaz",
    "black":    "Siyah",
    "purple":   "Mor",
    "yellow":   "Sarı",
    "green":    "Yeşil",
    "red":      "Kırmızı",
    "aqua":     "Aqua",
    "platform": "Platform (Ayraç)",
    "bosluk":   "Boşluk",
}

_MINES_EMOJI_KEYS = ["game", "hidden", "gem", "mine"]
_MINES_EMOJI_NAMES = {
    "game":   "Oyun (dropdown/başlık)",
    "hidden": "Gizli Hücre",
    "gem":    "Gem (elmas)",
    "mine":   "Mayın",
}


# ─── Bot Guild Picker for Emoji Setup Wizards ─────────────────────────────────

class _BotGuildSelect(discord.ui.Select):
    """Select a guild from the bot's guilds to load emojis for setup."""

    def __init__(self, guilds: list, game_type: str):
        self.game_type = game_type
        options = [
            discord.SelectOption(
                label=g.name[:100],
                description=f"{len(g.emojis)} özel emoji",
                value=str(g.id),
            )
            for g in guilds[:25]
        ]
        super().__init__(
            placeholder="🌐 Emoji kaynağı olarak bir sunucu seç...",
            options=options,
            custom_id=f"bot_guild_pick:{game_type}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        import os as _os
        guild = interaction.client.get_guild(int(self.values[0]))
        if not guild:
            return await interaction.response.send_message("❌ Sunucu bulunamadı.", ephemeral=True)

        guild_emojis = list(guild.emojis)

        if self.game_type == "mines":
            if not guild_emojis:
                games_data = _ensure_mines_game_entry(get_data("server/games") or {})
                set_data("server/games", games_data)
                await interaction.response.send_modal(MinesSettingsModal(games_data.get("mines", {})))
                return
            embed = discord.Embed(
                title="💣 Mines Emoji Setup",
                description=f"**{guild.name}** sunucusundan değiştirmek istediğin emojileri seç.",
                color=discord.Color.orange(),
            )
            embed.set_footer(text="Vegas Casino | Mines Setup")
            await interaction.response.edit_message(embed=embed, view=MinesEmojiSetupView(guild_emojis))

        elif self.game_type == "crystals":
            if not guild_emojis:
                return await interaction.response.edit_message(
                    embed=discord.Embed(
                        title="❌ Emoji Yok",
                        description="Seçilen sunucuda özel emoji bulunamadı.",
                        color=discord.Color.red(),
                    ),
                    view=None,
                )
            embed = discord.Embed(
                title="💎 Crystals Emoji Setup",
                description=f"**{guild.name}** sunucusundan değiştirmek istediğin emojileri seç.",
                color=discord.Color.purple(),
            )
            embed.set_footer(text="Vegas Casino | Crystals Setup")
            await interaction.response.edit_message(embed=embed, view=CrystalsPickView(guild_emojis))

        elif self.game_type == "towers":
            if not guild_emojis:
                games_data = _ensure_towers_game_entry(get_data("server/games") or {})
                set_data("server/games", games_data)
                await interaction.response.send_modal(TowersSettingsModal(games_data.get("towers", {})))
                return
            embed = discord.Embed(
                title="🗼 Towers Emoji Setup",
                description=f"**{guild.name}** sunucusundan değiştirmek istediğin emojileri seç.",
                color=discord.Color.blue(),
            )
            embed.set_footer(text="Vegas Casino | Towers Setup")
            await interaction.response.edit_message(embed=embed, view=TowersEmojiSetupView(guild_emojis))

        elif self.game_type == "slot":
            if not guild_emojis:
                games_data = _ensure_slot_game_entry(get_data("server/games") or {})
                set_data("server/games", games_data)
                await interaction.response.send_modal(SlotSettingsModal(games_data.get("slot", {})))
                return
            embed = discord.Embed(
                title="🎰 Slot Emoji Setup",
                description=f"**{guild.name}** sunucusundan değiştirmek istediğin emojileri seç.",
                color=discord.Color.gold(),
            )
            embed.set_footer(text="Vegas Casino | Slot Setup")
            await interaction.response.edit_message(embed=embed, view=SlotEmojiSetupView(guild_emojis))

class _BotGuildPickView(discord.ui.View):
    """Ephemeral view shown before emoji setup wizards to pick source guild."""

    def __init__(self, guilds: list, game_type: str):
        super().__init__(timeout=120)
        self.add_item(_BotGuildSelect(guilds, game_type))


# ─── Guild Emoji Select ───────────────────────────────────────────────────────

class _GuildEmojiSelect(discord.ui.Select):
    """Sunucu emojilerinden bir sayfayı gösteren select menü."""

    def __init__(self, emojis: list, row: int, page: int, total_pages: int, label_name: str):
        import os as _os
        options = [
            discord.SelectOption(label=e.name[:100], value=str(e.id), emoji=e)
            for e in emojis
        ]
        if total_pages == 1:
            placeholder = f"🔍 {label_name} için emoji seç"
        else:
            placeholder = f"🔍 {label_name} — Sayfa {page}/{total_pages}"
        super().__init__(
            placeholder=placeholder,
            options=options,
            min_values=0,
            max_values=1,
            custom_id=f"guild_emoji_sel:{row}:{_os.urandom(4).hex()}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class _EmojiPickView(discord.ui.View):
    """Tek bir emoji tipi için guild emoji sayfalarını sunar, İleri butonu ile ilerler."""

    def __init__(self, keys: list, key_names: dict, step_idx: int,
                 guild_emojis: list, temp_emojis: dict,
                 save_fn, title_prefix: str):
        super().__init__(timeout=180)
        self.keys = keys
        self.key_names = key_names
        self.step_idx = step_idx
        self.guild_emojis = guild_emojis
        self.temp_emojis = dict(temp_emojis)
        self.save_fn = save_fn
        self.title_prefix = title_prefix

        current_key = keys[step_idx]
        current_name = key_names[current_key]
        total_steps = len(keys)

        chunks = [guild_emojis[i:i+25] for i in range(0, len(guild_emojis), 25)][:4]
        total_pages = len(chunks)

        self._selects: list = []
        for idx, chunk in enumerate(chunks):
            sel = _GuildEmojiSelect(
                chunk, row=idx, page=idx + 1,
                total_pages=total_pages, label_name=current_name,
            )
            self._selects.append(sel)
            self.add_item(sel)

        is_last = (step_idx == len(keys) - 1)
        btn = discord.ui.Button(
            label="✅ Tamamla" if is_last else f"İleri → ({step_idx + 1}/{total_steps})",
            style=discord.ButtonStyle.success if is_last else discord.ButtonStyle.primary,
            row=4,
        )
        btn.callback = self._advance
        self.add_item(btn)

    async def _advance(self, interaction: discord.Interaction):
        chosen = []
        for sel in self._selects:
            chosen.extend(sel.values)

        if len(chosen) == 0:
            return await interaction.response.send_message(
                "❌ Bir emoji seçmelisin.", ephemeral=True
            )
        if len(chosen) > 1:
            return await interaction.response.send_message(
                "❌ Sadece 1 emoji seçebilirsin — farklı sayfalardan birden fazla seçildi.",
                ephemeral=True,
            )

        emoji_id = int(chosen[0])
        emoji_obj = discord.utils.get(self.guild_emojis, id=emoji_id)
        emoji_str = str(emoji_obj) if emoji_obj else f"<:{emoji_id}>"

        current_key = self.keys[self.step_idx]
        self.temp_emojis[current_key] = emoji_str

        if self.step_idx < len(self.keys) - 1:
            next_idx = self.step_idx + 1
            next_key = self.keys[next_idx]
            next_name = self.key_names[next_key]
            total = len(self.keys)
            next_view = _EmojiPickView(
                self.keys, self.key_names, next_idx,
                self.guild_emojis, self.temp_emojis,
                self.save_fn, self.title_prefix,
            )
            embed = discord.Embed(
                title=f"{self.title_prefix} — Adım {next_idx + 1}/{total}",
                description=f"**{next_name}** için bir emoji seç.",
                color=discord.Color.purple(),
            )
            embed.set_footer(text="Vegas Casino | Emoji Setup")
            await interaction.response.edit_message(embed=embed, view=next_view)
        else:
            result_embed = await self.save_fn(self.temp_emojis)
            await interaction.response.edit_message(embed=result_embed, view=None)


def _make_crystals_save_fn():
    async def _save(temp_emojis: dict) -> discord.Embed:
        games_data = _ensure_crystals_game_entry(get_data("server/games") or {})
        crystals_data = games_data.get("crystals", {})
        if not isinstance(crystals_data.get("emojis"), dict):
            crystals_data["emojis"] = {}
        if not isinstance(crystals_data["emojis"].get("crystals"), dict):
            crystals_data["emojis"]["crystals"] = {}
        saved = dict(temp_emojis)
        platform_emoji = saved.pop("platform", None)
        bosluk_emoji   = saved.pop("bosluk",   None)
        crystals_data["emojis"]["crystals"].update(saved)
        if platform_emoji:
            crystals_data["emojis"]["platform"] = platform_emoji
        if bosluk_emoji:
            crystals_data["emojis"]["bosluk"] = bosluk_emoji
        games_data["crystals"] = crystals_data
        set_data("server/games", games_data)
        lines = "\n".join(
            f"{e}  **{_CRYSTAL_NAMES.get(k, k.title())}**"
            for k, e in temp_emojis.items()
        )
        return discord.Embed(
            title="✅ Setup Completed!",
            description=f"Kristal emojileri kaydedildi:\n\n{lines}",
            color=discord.Color.green(),
        ).set_footer(text="Vegas Casino | Crystals Setup")
    return _save


def _make_mines_save_fn():
    async def _save(temp_emojis: dict) -> discord.Embed:
        games_data = _ensure_mines_game_entry(get_data("server/games") or {})
        mines = games_data.get("mines", {})
        if not isinstance(mines.get("emojis"), dict):
            mines["emojis"] = {}
        saved = dict(temp_emojis)
        game_emoji = saved.pop("game", None)
        if game_emoji:
            mines["emoji"] = game_emoji
        mines["emojis"].update(saved)
        games_data["mines"] = mines
        set_data("server/games", games_data)
        display = {}
        if game_emoji:
            display["game"] = game_emoji
        display.update(saved)
        lines = "\n".join(
            f"{e}  **{_MINES_EMOJI_NAMES.get(k, k.title())}**"
            for k, e in display.items()
        )
        return discord.Embed(
            title="✅ Mines Emoji Güncellendi!",
            description=f"Kaydedilen emojiler:\n\n{lines}",
            color=discord.Color.green(),
        ).set_footer(text="Vegas Casino | Mines Setup")
    return _save


# ─── Towers Emoji Setup Wizard ────────────────────────────────────────────────

_TOWERS_EMOJI_KEYS = ["game", "hidden", "gem", "bomb"]
_TOWERS_EMOJI_NAMES = {
    "game":   "Oyun (dropdown/başlık)",
    "hidden": "Gizli Hücre",
    "gem":    "Gem (güvenli kolon)",
    "bomb":   "Bomba",
}


def _make_towers_save_fn():
    async def _save(temp_emojis: dict) -> discord.Embed:
        games_data = _ensure_towers_game_entry(get_data("server/games") or {})
        towers = games_data.get("towers", {})
        if not isinstance(towers.get("emojis"), dict):
            towers["emojis"] = {}
        saved = dict(temp_emojis)
        game_emoji = saved.pop("game", None)
        if game_emoji:
            towers["emoji"] = game_emoji
            towers["emojis"]["game"] = game_emoji
        towers["emojis"].update(saved)
        games_data["towers"] = towers
        set_data("server/games", games_data)
        display = {}
        if game_emoji:
            display["game"] = game_emoji
        display.update(saved)
        lines = "\n".join(
            f"{e}  **{_TOWERS_EMOJI_NAMES.get(k, k.title())}**"
            for k, e in display.items()
        )
        return discord.Embed(
            title="✅ Towers Emoji Güncellendi!",
            description=f"Kaydedilen emojiler:\n\n{lines}",
            color=discord.Color.green(),
        ).set_footer(text="Vegas Casino | Towers Setup")
    return _save


class _TowersPickSelect(discord.ui.Select):
    """Hangi towers emojilerini değiştirmek istediğini seçtiren multi-select."""

    def __init__(self):
        options = [
            discord.SelectOption(label=_TOWERS_EMOJI_NAMES[k], value=k)
            for k in _TOWERS_EMOJI_KEYS
        ]
        super().__init__(
            placeholder="🔧 Değiştirmek istediğin emojileri seç",
            options=options,
            min_values=1,
            max_values=len(options),
            custom_id="towers_pick_which",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class TowersEmojiSetupView(discord.ui.View):
    """Towers için hangi emojileri değiştireceğini seçtir."""

    def __init__(self, guild_emojis: list):
        super().__init__(timeout=120)
        self.guild_emojis = guild_emojis
        self.sel = _TowersPickSelect()
        self.add_item(self.sel)

        btn = discord.ui.Button(label="▶️ Başla", style=discord.ButtonStyle.primary, row=1)
        btn.callback = self._start
        self.add_item(btn)

    async def _start(self, interaction: discord.Interaction):
        chosen_keys = self.sel.values
        if not chosen_keys:
            return await interaction.response.send_message("❌ En az bir emoji seçmelisin.", ephemeral=True)

        ordered = [k for k in _TOWERS_EMOJI_KEYS if k in chosen_keys]
        total = len(ordered)
        first_name = _TOWERS_EMOJI_NAMES[ordered[0]]
        view = _EmojiPickView(
            ordered, _TOWERS_EMOJI_NAMES, 0,
            self.guild_emojis, {},
            _make_towers_save_fn(), "🗼 Towers Emoji Setup",
        )
        embed = discord.Embed(
            title=f"🗼 Towers Emoji Setup — Adım 1/{total}",
            description=f"**{first_name}** için bir emoji seç.",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Vegas Casino | Towers Setup")
        await interaction.response.edit_message(embed=embed, view=view)


class _TowersSetupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🗼 Towers Setup", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        guilds = list(interaction.client.guilds)
        if not guilds:
            games_data = _ensure_towers_game_entry(get_data("server/games") or {})
            set_data("server/games", games_data)
            return await interaction.response.send_modal(TowersSettingsModal(games_data.get("towers", {})))
        embed = discord.Embed(
            title="🗼 Towers Emoji Setup — Sunucu Seç",
            description="Emojilerin yükleneceği sunucuyu seçin.",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Vegas Casino | Towers Setup")
        await interaction.response.send_message(
            embed=embed,
            view=_BotGuildPickView(guilds, "towers"),
            ephemeral=True,
        )


class _TowersRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Towers Rigged %", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_towers_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        await interaction.response.send_modal(TowersRiggedModal(games_data.get("towers", {})))


class TowersRiggedModal(discord.ui.Modal):
    """Towers rigged_chance ayarını değiştiren tek-alanlı modal."""

    def __init__(self, current_info: dict):
        super().__init__(title="Towers — Rigged Chance", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}

        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — Gem→Bomb",
            placeholder="15.0",
            default=str(current_info.get("rigged_chance", 15.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="Rigged chance must be a number between 0 and 100.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        games_data = _ensure_towers_game_entry(get_data("server/games") or {})
        towers = games_data.get("towers", {})
        towers["rigged_chance"] = round(rigged, 4)
        towers["last_modified"] = int(time.time())
        games_data["towers"] = towers
        set_data("server/games", games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Towers Rigged Chance Güncellendi",
                description=f"🎲 Rigged Chance: **{rigged}%**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class TowersSettingsModal(discord.ui.Modal):
    """Towers settings modal (emoji fallback — no guild emojis)."""

    def __init__(self, current_info: dict):
        super().__init__(title="Towers Settings", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        current_emojis = current_info.get("emojis", {}) if isinstance(current_info, dict) else {}

        self.game_emoji_input = discord.ui.TextInput(
            label="Game Emoji (dropdown/title)",
            placeholder="🗼",
            default=str(current_info.get("emoji", "🗼")),
            required=True, max_length=80, style=discord.TextStyle.short,
        )
        self.hidden_emoji_input = discord.ui.TextInput(
            label="Hidden Cell Emoji",
            placeholder="🔮",
            default=str(current_emojis.get("hidden", "🔮")),
            required=True, max_length=80, style=discord.TextStyle.short,
        )
        self.gem_emoji_input = discord.ui.TextInput(
            label="Gem Emoji (safe column)",
            placeholder="💎",
            default=str(current_emojis.get("gem", "💎")),
            required=True, max_length=80, style=discord.TextStyle.short,
        )
        self.bomb_emoji_input = discord.ui.TextInput(
            label="Bomb Emoji",
            placeholder="💣",
            default=str(current_emojis.get("bomb", "💣")),
            required=True, max_length=80, style=discord.TextStyle.short,
        )
        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — Gem→Bomb",
            placeholder="15.0",
            default=str(current_info.get("rigged_chance", 15.0)),
            required=True, max_length=8, style=discord.TextStyle.short,
        )
        self.add_item(self.game_emoji_input)
        self.add_item(self.hidden_emoji_input)
        self.add_item(self.gem_emoji_input)
        self.add_item(self.bomb_emoji_input)
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        games_data = _ensure_towers_game_entry(get_data("server/games") or {})
        towers = games_data.get("towers", {})
        if not isinstance(towers.get("emojis"), dict):
            towers["emojis"] = {}
        game_e  = self.game_emoji_input.value.strip()
        hidden_e = self.hidden_emoji_input.value.strip()
        gem_e   = self.gem_emoji_input.value.strip()
        bomb_e  = self.bomb_emoji_input.value.strip()
        towers["emoji"] = game_e
        towers["emojis"]["game"]   = game_e
        towers["emojis"]["hidden"] = hidden_e
        towers["emojis"]["gem"]    = gem_e
        towers["emojis"]["bomb"]   = bomb_e
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            rigged = max(0.0, min(100.0, rigged))
        except (TypeError, ValueError):
            rigged = 15.0
        towers["rigged_chance"] = round(rigged, 4)
        towers["last_modified"] = int(time.time())
        games_data["towers"] = towers
        set_data("server/games", games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Towers Emojileri Kaydedildi!",
                description=(
                    f"{game_e} Oyun  |  {hidden_e} Gizli  |  "
                    f"{gem_e} Gem  |  {bomb_e} Bomba\n"
                    f"🎲 Rigged Chance: **{rigged}%**"
                ),
                color=discord.Color.green(),
            ).set_footer(text="Vegas Casino | Towers Setup"),
            ephemeral=True,
        )


class _CrystalsPickSelect(discord.ui.Select):
    """Hangi kristal emojilerini değiştirmek istediğini seçtiren multi-select."""

    def __init__(self):
        options = [
            discord.SelectOption(label=_CRYSTAL_NAMES[k], value=k)
            for k in _CRYSTAL_KEYS
        ]
        super().__init__(
            placeholder="🔧 Değiştirmek istediğin emojileri seç",
            options=options,
            min_values=1,
            max_values=len(options),
            custom_id="crystals_pick_which",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class CrystalsPickView(discord.ui.View):
    """İlk adım: hangi emojileri değiştireceğini seçtir."""

    def __init__(self, guild_emojis: list):
        super().__init__(timeout=120)
        self.guild_emojis = guild_emojis
        self.sel = _CrystalsPickSelect()
        self.add_item(self.sel)

        btn = discord.ui.Button(label="▶️ Başla", style=discord.ButtonStyle.primary, row=1)
        btn.callback = self._start
        self.add_item(btn)

    async def _start(self, interaction: discord.Interaction):
        chosen_keys = self.sel.values
        if not chosen_keys:
            return await interaction.response.send_message("❌ En az bir emoji seçmelisin.", ephemeral=True)

        # preserve original order
        ordered = [k for k in _CRYSTAL_KEYS if k in chosen_keys]
        total = len(ordered)
        first_name = _CRYSTAL_NAMES[ordered[0]]
        view = _EmojiPickView(
            ordered, _CRYSTAL_NAMES, 0,
            self.guild_emojis, {},
            _make_crystals_save_fn(), "💎 Crystals Setup",
        )
        embed = discord.Embed(
            title=f"💎 Crystals Setup — Adım 1/{total}",
            description=f"**{first_name}** için bir emoji seç.",
            color=discord.Color.purple(),
        )
        embed.set_footer(text="Vegas Casino | Crystals Setup")
        await interaction.response.edit_message(embed=embed, view=view)


class _MinesPickSelect(discord.ui.Select):
    """Hangi mines emojilerini değiştirmek istediğini seçtiren multi-select."""

    def __init__(self):
        options = [
            discord.SelectOption(label=_MINES_EMOJI_NAMES[k], value=k)
            for k in _MINES_EMOJI_KEYS
        ]
        super().__init__(
            placeholder="🔧 Değiştirmek istediğin emojileri seç",
            options=options,
            min_values=1,
            max_values=len(options),
            custom_id="mines_pick_which",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class MinesEmojiSetupView(discord.ui.View):
    """İlk adım: hangi mines emojilerini değiştireceğini seçtir."""

    def __init__(self, guild_emojis: list):
        super().__init__(timeout=120)
        self.guild_emojis = guild_emojis
        self.sel = _MinesPickSelect()
        self.add_item(self.sel)

        btn = discord.ui.Button(label="▶️ Başla", style=discord.ButtonStyle.primary, row=1)
        btn.callback = self._start
        self.add_item(btn)

    async def _start(self, interaction: discord.Interaction):
        chosen_keys = self.sel.values
        if not chosen_keys:
            return await interaction.response.send_message("❌ En az bir emoji seçmelisin.", ephemeral=True)

        ordered = [k for k in _MINES_EMOJI_KEYS if k in chosen_keys]
        total = len(ordered)
        first_name = _MINES_EMOJI_NAMES[ordered[0]]
        view = _EmojiPickView(
            ordered, _MINES_EMOJI_NAMES, 0,
            self.guild_emojis, {},
            _make_mines_save_fn(), "💣 Mines Emoji Setup",
        )
        embed = discord.Embed(
            title=f"💣 Mines Emoji Setup — Adım 1/{total}",
            description=f"**{first_name}** için bir emoji seç.",
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Vegas Casino | Mines Setup")
        await interaction.response.edit_message(embed=embed, view=view)


# ─── Slot Emoji Setup Wizard ──────────────────────────────────────────────────

_SLOT_EMOJI_KEYS = ["game", "spin", "cherry", "lemon", "orange", "grapes", "bell", "star", "diamond", "seven"]
_SLOT_EMOJI_NAMES = {
    "game":    "Oyun (dropdown/başlık)",
    "spin":    "Spin (dönen hücre)",
    "cherry":  "Kiraz 🍒",
    "lemon":   "Limon 🍋",
    "orange":  "Portakal 🍊",
    "grapes":  "Üzüm 🍇",
    "bell":    "Çan 🔔",
    "star":    "Yıldız ⭐",
    "diamond": "Elmas 💎",
    "seven":   "Yedi 7️⃣",
}


def _make_slot_save_fn():
    async def _save(temp_emojis: dict) -> discord.Embed:
        games_data = _ensure_slot_game_entry(get_data("server/games") or {})
        slot = games_data.get("slot", {})
        if not isinstance(slot.get("emojis"), dict):
            slot["emojis"] = {}
        saved = dict(temp_emojis)
        game_emoji = saved.pop("game", None)
        if game_emoji:
            slot["emoji"] = game_emoji
            slot["emojis"]["game"] = game_emoji
        slot["emojis"].update(saved)
        slot["last_modified"] = int(time.time())
        games_data["slot"] = slot
        set_data("server/games", games_data)
        display = {}
        if game_emoji:
            display["game"] = game_emoji
        display.update(saved)
        lines = "\n".join(
            f"{e}  **{_SLOT_EMOJI_NAMES.get(k, k.title())}**"
            for k, e in display.items()
        )
        return discord.Embed(
            title="✅ Slot Emoji Güncellendi!",
            description=f"Kaydedilen emojiler:\n\n{lines}",
            color=discord.Color.green(),
        ).set_footer(text="Vegas Casino | Slot Setup")
    return _save


class _SlotPickSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=_SLOT_EMOJI_NAMES[k], value=k)
            for k in _SLOT_EMOJI_KEYS
        ]
        super().__init__(
            placeholder="🔧 Değiştirmek istediğin emojileri seç",
            options=options,
            min_values=1,
            max_values=len(options),
            custom_id="slot_pick_which",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class SlotEmojiSetupView(discord.ui.View):
    def __init__(self, guild_emojis: list):
        super().__init__(timeout=120)
        self.guild_emojis = guild_emojis
        self.sel = _SlotPickSelect()
        self.add_item(self.sel)
        btn = discord.ui.Button(label="▶️ Başla", style=discord.ButtonStyle.primary, row=1)
        btn.callback = self._start
        self.add_item(btn)

    async def _start(self, interaction: discord.Interaction):
        chosen_keys = self.sel.values
        if not chosen_keys:
            return await interaction.response.send_message("❌ En az bir emoji seçmelisin.", ephemeral=True)
        ordered = [k for k in _SLOT_EMOJI_KEYS if k in chosen_keys]
        total = len(ordered)
        first_name = _SLOT_EMOJI_NAMES[ordered[0]]
        view = _EmojiPickView(
            ordered, _SLOT_EMOJI_NAMES, 0,
            self.guild_emojis, {},
            _make_slot_save_fn(), "🎰 Slot Emoji Setup",
        )
        embed = discord.Embed(
            title=f"🎰 Slot Emoji Setup — Adım 1/{total}",
            description=f"**{first_name}** için bir emoji seç.",
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Vegas Casino | Slot Setup")
        await interaction.response.edit_message(embed=embed, view=view)


class _SlotSetupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎰 Slot Setup", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        guilds = list(interaction.client.guilds)
        if not guilds:
            games_data = _ensure_slot_game_entry(get_data("server/games") or {})
            set_data("server/games", games_data)
            return await interaction.response.send_modal(SlotSettingsModal(games_data.get("slot", {})))
        embed = discord.Embed(
            title="🎰 Slot Emoji Setup — Sunucu Seç",
            description="Emojilerin yükleneceği sunucuyu seçin.",
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Vegas Casino | Slot Setup")
        await interaction.response.send_message(
            embed=embed,
            view=_BotGuildPickView(guilds, "slot"),
            ephemeral=True,
        )


class _LiveBlackjackSetupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🃏 Table Setup", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", user_id=str(interaction.user.id)),
                ephemeral=True,
            )
        await interaction.response.edit_message(
            embed=_build_live_blackjack_setup_embed(interaction.user.id),
            view=LiveBlackjackSettingsView(interaction.user.id, return_to="game_list"),
        )


class _LiveBlackjackRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Live BJ Rigged %", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_live_blackjack_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        await interaction.response.send_modal(
            LiveBlackjackRiggedModal(games_data.get("live_blackjack", {}))
        )


class LiveBlackjackRiggedModal(discord.ui.Modal):
    def __init__(self, current_info: dict):
        super().__init__(title="Live Blackjack — Rigged %", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%)",
            default=str(current_info.get("rigged_chance", 0.0)),
            required=True,
            max_length=8,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message("Invalid % (0–100).", ephemeral=True)
        games_data = _ensure_live_blackjack_game_entry(get_data("server/games") or {})
        games_data["live_blackjack"]["rigged_chance"] = rigged
        set_data("server/games", games_data)
        await interaction.response.send_message(
            f"✅ Live Blackjack rigged chance: **{rigged}%**",
            ephemeral=True,
        )


class _BlackjackRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Blackjack Rigged %", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_blackjack_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        await interaction.response.send_modal(BlackjackRiggedModal(games_data.get("blackjack", {})))


class BlackjackRiggedModal(discord.ui.Modal):
    """Blackjack rigged_chance ayarını değiştiren modal."""

    def __init__(self, current_info: dict):
        super().__init__(title="Blackjack — Rigged Chance", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}

        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — force player loss",
            placeholder="0.0",
            default=str(current_info.get("rigged_chance", 0.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="Rigged chance must be a number between 0 and 100.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        games_data = _ensure_blackjack_game_entry(get_data("server/games") or {})
        bj = games_data.get("blackjack", {})
        bj["rigged_chance"] = round(rigged, 4)
        bj["last_modified"] = int(time.time())
        games_data["blackjack"] = bj
        set_data("server/games", games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Blackjack Rigged Chance Güncellendi",
                description=f"🎲 Rigged Chance: **{rigged}%**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class SlotSettingsModal(discord.ui.Modal):
    """Slot emoji ayarları modal (fallback — özel emoji yok)."""

    def __init__(self, current_info: dict):
        super().__init__(title="Slot Emoji Settings", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        emojis = current_info.get("emojis", {}) if isinstance(current_info, dict) else {}
        if not isinstance(emojis, dict):
            emojis = {}

        defaults = {
            "game": "🎰", "spin": "🔮", "cherry": "🍒", "lemon": "🍋",
            "orange": "🍊", "grapes": "🍇", "bell": "🔔", "star": "⭐",
            "diamond": "💎", "seven": "7️⃣",
        }
        self._inputs = {}
        # Modal max 5 items — show most important ones
        keys_shown = ["game", "spin", "cherry", "lemon", "seven"]
        for k in keys_shown:
            inp = discord.ui.TextInput(
                label=_SLOT_EMOJI_NAMES[k],
                placeholder=defaults[k],
                default=str(emojis.get(k, "") or current_info.get("emoji", "") if k == "game" else emojis.get(k, "")),
                required=False, max_length=80, style=discord.TextStyle.short,
            )
            self._inputs[k] = inp
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        games_data = _ensure_slot_game_entry(get_data("server/games") or {})
        slot = games_data.get("slot", {})
        if not isinstance(slot.get("emojis"), dict):
            slot["emojis"] = {}
        for k, inp in self._inputs.items():
            val = inp.value.strip()
            if val:
                if k == "game":
                    slot["emoji"] = val
                    slot["emojis"]["game"] = val
                else:
                    slot["emojis"][k] = val
        slot["last_modified"] = int(time.time())
        games_data["slot"] = slot
        set_data("server/games", games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Slot Emojileri Kaydedildi!",
                color=discord.Color.green(),
            ).set_footer(text="Vegas Casino | Slot Setup"),
            ephemeral=True,
        )


# ── Roulette rigged ───────────────────────────────────────────────────────────

class _RouletteRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Roulette Rigged %", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_roulette_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        await interaction.response.send_modal(RouletteRiggedModal(games_data.get("roulette", {})))


class RouletteRiggedModal(discord.ui.Modal):
    """Roulette rigged_chance ayarını değiştiren modal."""

    def __init__(self, current_info: dict):
        super().__init__(title="Roulette — Rigged Chance", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — force house win",
            placeholder="0.0",
            default=str(current_info.get("rigged_chance", 0.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="Rigged chance must be a number between 0 and 100.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        games_data = _ensure_roulette_game_entry(get_data("server/games") or {})
        roulette = games_data.get("roulette", {})
        roulette["rigged_chance"] = round(rigged, 4)
        roulette["last_modified"] = int(time.time())
        games_data["roulette"] = roulette
        set_data("server/games", games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Roulette Rigged Chance Güncellendi",
                description=f"🎲 Rigged Chance: **{rigged}%**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


# ── Dice rigged ───────────────────────────────────────────────────────────────

class _DiceRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Dice Rigged %", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_dice_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        await interaction.response.send_modal(DiceRiggedModal(games_data.get("dice", {})))


class DiceRiggedModal(discord.ui.Modal):
    """Dice rigged_chance ayarını değiştiren modal."""

    def __init__(self, current_info: dict):
        super().__init__(title="Dice — Rigged Chance", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — force house win",
            placeholder="0.0",
            default=str(current_info.get("rigged_chance", 0.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="Rigged chance must be a number between 0 and 100.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        games_data = _ensure_dice_game_entry(get_data("server/games") or {})
        dice = games_data.get("dice", {})
        dice["rigged_chance"] = round(rigged, 4)
        dice["last_modified"] = int(time.time())
        games_data["dice"] = dice
        set_data("server/games", games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Dice Rigged Chance Güncellendi",
                description=f"🎲 Rigged Chance: **{rigged}%**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


# ── Coinflip rigged ───────────────────────────────────────────────────────────

class _CoinflipHotColdSetupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🔥 Hot/Cold Setup", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_coinflip_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        cf = games_data.get("coinflip", {})
        await interaction.response.send_modal(CoinflipHotColdModal(cf))


class CoinflipHotColdModal(discord.ui.Modal, title="Coin Flip — Hot & Cold Emojis"):
    def __init__(self, current_info: dict):
        super().__init__(timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        self.hot_in = discord.ui.TextInput(
            label="Hot emoji",
            placeholder="🔥 or <:custom_hot:id>",
            default=str(current_info.get("hot_emoji", "🔥")),
            max_length=80,
        )
        self.cold_in = discord.ui.TextInput(
            label="Cold emoji",
            placeholder="❄️ or <:custom_cold:id>",
            default=str(current_info.get("cold_emoji", "❄️")),
            max_length=80,
        )
        self.add_item(self.hot_in)
        self.add_item(self.cold_in)

    async def on_submit(self, interaction: discord.Interaction):
        from modules.coinflip_flow import save_coinflip_emojis

        hot = self.hot_in.value.strip() or "🔥"
        cold = self.cold_in.value.strip() or "❄️"
        save_coinflip_emojis(hot, cold)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Hot/Cold emojis updated",
                description=f"**Hot:** {hot}\n**Cold:** {cold}",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class _CoinflipRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Coinflip Rigged %", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_coinflip_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        await interaction.response.send_modal(CoinflipRiggedModal(games_data.get("coinflip", {})))


class CoinflipRiggedModal(discord.ui.Modal):
    """Coinflip rigged_chance ayarını değiştiren modal."""

    def __init__(self, current_info: dict):
        super().__init__(title="Coinflip — Rigged Chance", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — force house win",
            placeholder="0.0",
            default=str(current_info.get("rigged_chance", 0.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="Rigged chance must be a number between 0 and 100.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        games_data = _ensure_coinflip_game_entry(get_data("server/games") or {})
        coinflip = games_data.get("coinflip", {})
        coinflip["rigged_chance"] = round(rigged, 4)
        coinflip["last_modified"] = int(time.time())
        games_data["coinflip"] = coinflip
        set_data("server/games", games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Coinflip Rigged Chance Güncellendi",
                description=f"🎲 Rigged Chance: **{rigged}%**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class _JackpotChannelButton(discord.ui.Button):
    """Dedicated Jackpot room — chat is auto-deleted; bets via .jp / .jackpot."""

    def __init__(self):
        super().__init__(
            label="🎰 Jackpot Room",
            style=discord.ButtonStyle.secondary,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        from modules.jackpot_store import get_settings

        settings = get_settings()
        ch_id = settings.get("channel_id")
        embed = discord.Embed(
            title="🎰 Jackpot Room",
            description=(
                "Players join with `.jp <bet>` or `.jackpot <bet>`.\n"
                "Win chance = bet ÷ pool. **2%** house fee on the winner payout.\n"
                "User messages are deleted (bot + admin messages stay).\n"
                "`.canceljp` refunds your bet before the spin starts.\n\n"
                f"**Channel:** {'<#' + str(ch_id) + '>' if ch_id else '❌ Not set'}"
            ),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, view=_JackpotChannelView(), ephemeral=True)


class _JackpotChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Select Jackpot channel…",
        min_values=1,
        max_values=1,
        row=0,
    )
    async def set_channel(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect
    ):
        from modules.jackpot_store import get_settings, save_settings

        channel = select.values[0]
        settings = get_settings()
        settings["channel_id"] = channel.id
        save_settings(settings)
        from modules.jackpot_flow import bootstrap_jackpot_room

        posted = " Lobby menu posted." if channel else ""
        try:
            await bootstrap_jackpot_room(interaction.client)
        except Exception:
            posted = " (Could not post lobby — check bot permissions in that channel.)"
        await interaction.response.send_message(
            f"✅ Jackpot room set to {channel.mention}.{posted}",
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh Menu", style=discord.ButtonStyle.primary, row=2)
    async def refresh_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.jackpot_flow import bootstrap_jackpot_room

        try:
            await bootstrap_jackpot_room(interaction.client)
            await interaction.response.send_message("✅ Jackpot lobby refreshed.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(
                f"❌ Refresh failed: {exc}",
                ephemeral=True,
            )

    @discord.ui.button(label="Clear Channel", style=discord.ButtonStyle.danger, row=2)
    async def clear_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.jackpot_store import get_settings, save_settings

        settings = get_settings()
        settings.pop("channel_id", None)
        save_settings(settings)
        await interaction.response.send_message("✅ Jackpot channel cleared.", ephemeral=True)


class _CaseBattleLogChannelButton(discord.ui.Button):
    """Case Battle log channel — PvP only; bot battles stay in private room."""

    def __init__(self):
        super().__init__(
            label="📢 Battle Log Channel",
            style=discord.ButtonStyle.secondary,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        from modules.case_battle import get_case_battle_settings

        settings = get_case_battle_settings()
        ch_id = settings.get("log_channel_id")
        role_id = settings.get("ping_role_id")
        embed = discord.Embed(
            title="⚔️ Case Battle Channel",
            description=(
                "Battles vs **Bot** play in the private room only.\n"
                "When **Bot mode is OFF**, open lobbies are posted to this channel.\n"
                "The **ping role** is mentioned on each new lobby (not @everyone).\n\n"
                f"**Channel:** {'<#' + str(ch_id) + '>' if ch_id else '❌ Not set'}\n"
                f"**Ping role:** {('<@&' + str(role_id) + '>') if role_id else '❌ Not set'}"
            ),
            color=discord.Color.purple(),
        )
        view = _CaseBattleLogChannelView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class _CaseBattleLogChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Select battle channel…",
        min_values=1,
        max_values=1,
        row=0,
    )
    async def set_channel(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect
    ):
        from modules.case_battle import get_case_battle_settings, save_case_battle_settings

        channel = select.values[0]
        settings = get_case_battle_settings()
        settings["log_channel_id"] = channel.id
        save_case_battle_settings(settings)
        await interaction.response.send_message(
            f"✅ Case Battle channel set to {channel.mention}",
            ephemeral=True,
        )

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Select role to ping on new lobbies…",
        min_values=1,
        max_values=1,
        row=1,
    )
    async def set_ping_role(
        self, interaction: discord.Interaction, select: discord.ui.RoleSelect
    ):
        from modules.case_battle import get_case_battle_settings, save_case_battle_settings

        role = select.values[0]
        settings = get_case_battle_settings()
        settings["ping_role_id"] = role.id
        save_case_battle_settings(settings)
        await interaction.response.send_message(
            f"✅ Lobby ping role set to {role.mention}",
            ephemeral=True,
        )

    @discord.ui.button(label="Clear Channel", style=discord.ButtonStyle.danger, row=2)
    async def clear_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.case_battle import get_case_battle_settings, save_case_battle_settings

        settings = get_case_battle_settings()
        settings.pop("log_channel_id", None)
        save_case_battle_settings(settings)
        await interaction.response.send_message("✅ Case Battle channel cleared.", ephemeral=True)

    @discord.ui.button(label="Clear Ping Role", style=discord.ButtonStyle.secondary, row=2)
    async def clear_ping_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.case_battle import get_case_battle_settings, save_case_battle_settings

        settings = get_case_battle_settings()
        settings.pop("ping_role_id", None)
        save_case_battle_settings(settings)
        await interaction.response.send_message("✅ Lobby ping role cleared.", ephemeral=True)


class GameDetailView(discord.ui.View):
    """Oyun detay view - Toggle ve istatistik butonları"""
    
    def __init__(self, game_id: str):
        super().__init__(timeout=300)
        self.game_id = game_id

        # Oyuna özel butonları dinamik ekle (row=1)
        if game_id == "mines":
            self.add_item(_MinesSettingsButton())
            self.add_item(_MinesRiggedButton())
        elif game_id == "crystals":
            self.add_item(_CrystalsSetupButton())
            self.add_item(_CrystalsRiggedButton())
        elif game_id == "towers":
            self.add_item(_TowersSetupButton())
            self.add_item(_TowersRiggedButton())
        elif game_id == "slot":
            self.add_item(_SlotSetupButton())
        elif game_id == "limbo":
            self.add_item(_LimboRiggedButton())
        elif game_id == "slide":
            self.add_item(_SlideRiggedButton())
        elif game_id == "market_predict":
            self.add_item(_MarketPredictRiggedButton())
        elif game_id == "blackjack":
            self.add_item(_BlackjackRiggedButton())
        elif game_id == "live_blackjack":
            self.add_item(_LiveBlackjackSetupButton())
            self.add_item(_LiveBlackjackRiggedButton())
        elif game_id == "roulette":
            self.add_item(_RouletteRiggedButton())
        elif game_id == "dice":
            self.add_item(_DiceRiggedButton())
        elif game_id == "coinflip":
            self.add_item(_CoinflipHotColdSetupButton())
            self.add_item(_CoinflipRiggedButton())
        elif game_id == "case_battle":
            self.add_item(_CaseBattleLogChannelButton())
        elif game_id == "jackpot":
            self.add_item(_JackpotChannelButton())
        # Tüm oyunlar için house edge butonu (row=1)
        self.add_item(_HouseEdgeButton(game_id))
    
    @discord.ui.button(label="Toggle Status", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def toggle_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Oyun durumunu değiştir"""
        games_data = _ensure_all_game_entries(get_data("server/games") or {})
        game_info = games_data.get(self.game_id) or _repair_game_entry(self.game_id, {})

        game_info["enabled"] = not game_info.get("enabled", False)
        games_data[self.game_id] = game_info
        await _persist_games_panel(games_data)

        status_text = t("game_management.status_enabled", user_id=str(interaction.user.id)) if game_info["enabled"] else t("game_management.status_disabled", user_id=str(interaction.user.id))
        
        embed = discord.Embed(
            title="✅ Status Updated",
            description=t("game_management.game_toggled", user_id=str(interaction.user.id)).format(
                name=game_info["name"],
                status=status_text
            ),
            color=discord.Color.green() if game_info["enabled"] else discord.Color.red()
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="📊 Detailed Stats", style=discord.ButtonStyle.success, row=0)
    async def detailed_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Detaylı istatistikler"""
        game_stats = get_data("server/game_stats") or {}
        game_record = game_stats.get(self.game_id, {})
        all_time = game_record.get("all_time", {})
        by_user = game_record.get("by_user", {})
        games_data = get_data("server/games") or {}
        game_info = games_data.get(self.game_id, {})
        game_name = game_info.get("name", self.game_id.title())

        embed = discord.Embed(
            title="📊 Detailed Game Statistics",
            description=f"Statistics for {game_name}",
            color=discord.Color.teal()
        )
        embed.add_field(name="Total Plays", value=str(all_time.get("total_plays", 0)), inline=True)
        embed.add_field(name="Total Wagered", value=format_balance(all_time.get("total_wagered", 0), 'real'), inline=True)
        embed.add_field(name="Total Profit", value=format_balance(all_time.get("total_profit", 0), 'real'), inline=True)

        if by_user:
            top_users = sorted(by_user.items(), key=lambda item: item[1].get("profit", 0), reverse=True)[:5]
            top_lines = [f"<@{user_id}> — {stats.get('plays', 0)} plays, {format_balance(stats.get('profit', 0), 'real')}" for user_id, stats in top_users]
            embed.add_field(name="Top Players", value="\n".join(top_lines), inline=False)
        else:
            embed.add_field(name="Top Players", value="No player statistics available yet.", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="⬅️ Back to List", style=discord.ButtonStyle.secondary, row=2)
    async def back_to_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _persist_games_panel(get_data("server/games") or {})
        await interaction.response.edit_message(
            embed=_build_game_list_management_embed(interaction.user.id),
            view=GameListManagementView(interaction.user.id),
        )


class _HouseEdgeButton(discord.ui.Button):
    def __init__(self, game_id: str):
        self.game_id = game_id
        super().__init__(label="📈 House Edge", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = get_data("server/games") or {}
        game_info = games_data.get(self.game_id, {})
        await interaction.response.send_modal(_HouseEdgeModal(self.game_id, game_info))


class _HouseEdgeModal(discord.ui.Modal, title="House Edge"):
    def __init__(self, game_id: str, game_info: dict):
        super().__init__()
        self.game_id = game_id
        self.he_input = discord.ui.TextInput(
            label="House Edge (%)",
            placeholder="0.0 – 99.9",
            default=str(game_info.get("house_edge", 1.0)),
            max_length=6,
        )
        self.add_item(self.he_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            house_edge = float(self.he_input.value.replace(",", "."))
            if house_edge < 0 or house_edge >= 100:
                raise ValueError("out of range")
        except ValueError:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Invalid value", description="Enter a number between 0 and 99.9.", color=discord.Color.red()),
                ephemeral=True
            )
        games_data = get_data("server/games") or {}
        game_info = games_data.get(self.game_id, {})
        game_info["house_edge"] = round(house_edge, 4)
        game_info["last_modified"] = int(time.time())
        games_data[self.game_id] = game_info
        await _persist_games_panel(games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ House Edge Updated",
                description=f"**{game_info.get('name', self.game_id)}** house edge: **{house_edge}%**",
                color=discord.Color.green()
            ),
            ephemeral=True
        )


class _MinesSettingsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="💣 Mines Settings", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        guilds = list(interaction.client.guilds)
        if not guilds:
            games_data = _ensure_mines_game_entry(get_data("server/games") or {})
            set_data("server/games", games_data)
            return await interaction.response.send_modal(MinesSettingsModal(games_data.get("mines", {})))
        embed = discord.Embed(
            title="💣 Mines Emoji Setup — Sunucu Seç",
            description="Emojilerin yükleneceği sunucuyu seçin.",
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Vegas Casino | Mines Setup")
        await interaction.response.send_message(
            embed=embed,
            view=_BotGuildPickView(guilds, "mines"),
            ephemeral=True,
        )


class LimboRiggedModal(discord.ui.Modal):
    """Limbo rigged_chance ayarını değiştiren modal."""

    def __init__(self, current_info: dict):
        super().__init__(title="Limbo — Rigged Chance", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — force lose",
            placeholder="0.0",
            default=str(current_info.get("rigged_chance", 0.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="Rigged chance must be a number between 0 and 100.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        games_data = _ensure_limbo_game_entry(get_data("server/games") or {})
        limbo = games_data.get("limbo", {})
        limbo["rigged_chance"] = round(rigged, 4)
        limbo["last_modified"] = int(time.time())
        games_data["limbo"] = limbo
        set_data("server/games", games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Limbo Rigged Chance Güncellendi",
                description=f"🎲 Rigged Chance: **{rigged}%**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class _LimboRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Limbo Rigged %", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_limbo_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        await interaction.response.send_modal(LimboRiggedModal(games_data.get("limbo", {})))


class MarketPredictRiggedModal(discord.ui.Modal):
    """Market Predict rigged_chance ayarını değiştiren modal."""

    def __init__(self, current_info: dict):
        super().__init__(title="Market Predict — Rigged Chance", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — force opposite direction",
            placeholder="0.0",
            default=str(current_info.get("rigged_chance", 0.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="Rigged chance must be a number between 0 and 100.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

        games_data = _ensure_market_predict_game_entry(get_data("server/games") or {})
        mp = games_data.get("market_predict", {})
        mp["rigged_chance"] = round(rigged, 4)
        mp["last_modified"] = int(time.time())
        games_data["market_predict"] = mp
        await _persist_games_panel(games_data)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Market Predict Rigged Chance Güncellendi",
                description=f"📈 Rigged Chance: **{rigged}%**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class _MarketPredictRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📈 Market Predict Rigged %", style=discord.ButtonStyle.danger, row=1
        )

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_market_predict_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        await interaction.response.send_modal(
            MarketPredictRiggedModal(games_data.get("market_predict", {}))
        )


class SlideRiggedModal(discord.ui.Modal):
    """Slide rigged_chance setting."""

    def __init__(self, current_info: dict):
        super().__init__(title="Slide — Rigged Chance", timeout=300)
        if not isinstance(current_info, dict):
            current_info = {}
        self.rigged_chance_input = discord.ui.TextInput(
            label="Rigged Chance (%) — force low multiplier",
            placeholder="0.0",
            default=str(current_info.get("rigged_chance", 0.0)),
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
        )
        self.add_item(self.rigged_chance_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rigged = float(self.rigged_chance_input.value.replace(",", "."))
            if rigged < 0 or rigged > 100:
                raise ValueError
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid Input",
                    description="Rigged chance must be a number between 0 and 100.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        games_data = _ensure_slide_game_entry(get_data("server/games") or {})
        slide = games_data.get("slide", {})
        slide["rigged_chance"] = round(rigged, 4)
        slide["last_modified"] = int(time.time())
        games_data["slide"] = slide
        await _persist_games_panel(games_data)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Slide Rigged Chance Updated",
                description=f"🎲 Rigged Chance: **{rigged}%**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class _SlideRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Slide Rigged %", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_slide_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        await interaction.response.send_modal(SlideRiggedModal(games_data.get("slide", {})))


class _MinesRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Mines Rigged %", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_mines_game_entry(get_data("server/games") or {})
        set_data("server/games", games_data)
        await interaction.response.send_modal(MinesRiggedModal(games_data.get("mines", {})))


class _CrystalsRiggedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎲 Rigged %", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        games_data = _ensure_crystals_game_entry(get_data("server/games") or {})
        await interaction.response.send_modal(CrystalsRiggedModal(games_data.get("crystals", {})))


class _CrystalsSetupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="💎 Crystals Setup", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        guilds = list(interaction.client.guilds)
        if not guilds:
            return await interaction.response.send_message(
                "❌ Kullanılabilir sunucu bulunamadı.", ephemeral=True
            )
        embed = discord.Embed(
            title="💎 Crystals Emoji Setup — Sunucu Seç",
            description="Emojilerin yükleneceği sunucuyu seçin.",
            color=discord.Color.purple(),
        )
        embed.set_footer(text="Vegas Casino | Crystals Setup")
        await interaction.response.send_message(
            embed=embed,
            view=_BotGuildPickView(guilds, "crystals"),
            ephemeral=True,
        )


class ReferralSettingsSelect(discord.ui.Select):
    """Referral settings select menu"""
    
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Manage Users",
                description="View and manage users with referral codes",
                emoji="👥",
                value="manage_users"
            ),
            discord.SelectOption(
                label="Change Welcome Bonus",
                description="Set the welcome bonus for new referrals",
                emoji="💰",
                value="change_welcome_bonus"
            ),
            discord.SelectOption(
                label="Change Min Withdrawal",
                description="Set minimum withdrawal amount",
                emoji="📤",
                value="change_min_withdrawal"
            ),
            discord.SelectOption(
                label="⬅️ Back to Admin Panel",
                description="Return to main admin panel",
                emoji="⬅️",
                value="back_to_admin"
            )
        ]
        
        super().__init__(
            placeholder="Select an option...",
            options=options,
            custom_id="referral_settings:main_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "manage_users":
            referrals_data = get_data("server/referrals")
            if not referrals_data:
                embed = discord.Embed(
                    title="❌ No Users",
                    description="No users with referral codes found!",
                    color=discord.Color.red()
                )
                await interaction.response.edit_message(embed=embed, view=ReferralSettingsView())
                return
            
            view = ReferralUserListView(page=0)
            embed = view.create_embed()
            await interaction.response.edit_message(embed=embed, view=view)
        
        elif self.values[0] == "change_welcome_bonus":
            modal = WelcomeBonusModal()
            await interaction.response.send_modal(modal)
        
        elif self.values[0] == "change_min_withdrawal":
            modal = MinWithdrawalModal()
            await interaction.response.send_modal(modal)
        
        elif self.values[0] == "back_to_admin":
            from modules.admin_panel_nav import go_home

            await go_home(interaction, user_id=interaction.user.id)


class ReferralSettingsView(discord.ui.View):
    """Main referral settings view"""
    
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(ReferralSettingsSelect())


class WelcomeBonusModal(discord.ui.Modal, title="Change Welcome Bonus"):
    """Modal to change welcome bonus amount"""
    
    bonus_input = discord.ui.TextInput(
        label="Welcome Bonus Amount",
        placeholder="Enter amount (e.g., 100)",
        required=True,
        max_length=10
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.bonus_input.value)
            if amount < 0:
                raise ValueError("Amount must be positive")
            
            settings = get_data("server/referral_settings")
            settings["welcome_bonus"] = amount
            set_data("server/referral_settings", settings)
            
            embed = discord.Embed(
                title="✅ Success",
                description=f"Welcome bonus set to **{format_balance(amount)}**",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except ValueError:
            embed = discord.Embed(
                title="❌ Error",
                description="Please enter a valid positive number!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class MinWithdrawalModal(discord.ui.Modal, title="Change Minimum Withdrawal"):
    """Modal to change minimum withdrawal amount"""
    
    amount_input = discord.ui.TextInput(
        label="Minimum Withdrawal Amount",
        placeholder="Enter amount (e.g., 10)",
        required=True,
        max_length=10
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount_input.value)
            if amount < 0:
                raise ValueError("Amount must be positive")
            
            settings = get_data("server/referral_settings")
            settings["min_withdrawal"] = amount
            set_data("server/referral_settings", settings)
            
            embed = discord.Embed(
                title="✅ Success",
                description=f"Minimum withdrawal set to **{format_balance(amount)}**",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except ValueError:
            embed = discord.Embed(
                title="❌ Error",
                description="Please enter a valid positive number!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class ReferralUserListView(discord.ui.View):
    """View for listing referral users with pagination"""
    
    USERS_PER_PAGE = 25
    
    def __init__(self, page: int = 0):
        super().__init__(timeout=300)
        self.page = page
        self.setup_select_menus()
    
    def setup_select_menus(self):
        """Setup select menus based on current page"""
        referrals_data = get_data("server/referrals")
        user_list = list(referrals_data.items())
        total_pages = (len(user_list) + self.USERS_PER_PAGE - 1) // self.USERS_PER_PAGE
        
        start_idx = self.page * self.USERS_PER_PAGE
        end_idx = min(start_idx + self.USERS_PER_PAGE, len(user_list))
        page_users = user_list[start_idx:end_idx]
        
        # Add user select menu
        if page_users:
            self.add_item(ReferralUserSelect(page_users))
        
        # Add pagination select menu if needed
        if total_pages > 1:
            self.add_item(PaginationSelect(self.page, total_pages))
        
        # Add back button
        self.add_item(BackToReferralSettingsSelect())
    
    def create_embed(self):
        """Create embed for current page"""
        referrals_data = get_data("server/referrals")
        user_list = list(referrals_data.items())
        total_pages = (len(user_list) + self.USERS_PER_PAGE - 1) // self.USERS_PER_PAGE
        
        start_idx = self.page * self.USERS_PER_PAGE
        end_idx = min(start_idx + self.USERS_PER_PAGE, len(user_list))
        page_users = user_list[start_idx:end_idx]
        
        embed = discord.Embed(
            title="👥 Referral Users Management",
            description=f"Manage users with referral codes\n**Page {self.page + 1}/{total_pages}** • **Total Users: {len(user_list)}**",
            color=discord.Color.gold()
        )
        
        for user_id, data in page_users:
            embed.add_field(
                name=f"📌 {data['code']}",
                value=f"User: <@{user_id}>\n"
                      f"Commission: **{data['commission_rate']}%**\n"
                      f"Total Earned: **{format_balance(data['total_earned'])}**\n"
                      f"Referred: **{len(data['referred_users'])}** users",
                inline=True
            )
        
        return embed


class ReferralUserSelect(discord.ui.Select):
    """Select menu for choosing a referral user"""
    
    def __init__(self, users: list):
        options = []
        for user_id, data in users:
            options.append(discord.SelectOption(
                label=f"{data['code']} - {data['commission_rate']}%",
                description=f"Earned: {data['total_earned']} • {len(data['referred_users'])} refs",
                value=user_id,
                emoji="👤"
            ))
        
        super().__init__(
            placeholder="Select a user to manage...",
            options=options,
            custom_id="referral_users:user_select",
            row=0
        )
    
    async def callback(self, interaction: discord.Interaction):
        user_id = self.values[0]
        view = UserManagementView(user_id)
        embed = view.create_embed()
        await interaction.response.edit_message(embed=embed, view=view)


class PaginationSelect(discord.ui.Select):
    """Pagination select menu"""
    
    def __init__(self, current_page: int, total_pages: int):
        self.current_page = current_page
        self.total_pages = total_pages
        
        options = []
        
        # Show 5 pages at a time
        start_page = max(0, current_page - 2)
        end_page = min(total_pages, start_page + 5)
        
        for i in range(start_page, end_page):
            emoji = "📍" if i == current_page else "📄"
            options.append(discord.SelectOption(
                label=f"Page {i + 1}",
                value=str(i),
                emoji=emoji,
                default=(i == current_page)
            ))
        
        super().__init__(
            placeholder=f"Navigate pages ({current_page + 1}/{total_pages})",
            options=options,
            custom_id="referral_users:pagination",
            row=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        page = int(self.values[0])
        view = ReferralUserListView(page=page)
        embed = view.create_embed()
        await interaction.response.edit_message(embed=embed, view=view)


class BackToReferralSettingsSelect(discord.ui.Select):
    """Back button as select menu"""
    
    def __init__(self):
        options = [
            discord.SelectOption(
                label="⬅️ Back to Referral Settings",
                value="back",
                emoji="⬅️"
            )
        ]
        
        super().__init__(
            placeholder="Go back...",
            options=options,
            custom_id="referral_users:back",
            row=2
        )
    
    async def callback(self, interaction: discord.Interaction):
        view = ReferralSettingsView()
        referral_settings = get_data("server/referral_settings")
        referrals_data = get_data("server/referrals")
        total_users = len(referrals_data)
        total_earnings = sum(data.get("total_earned", 0) for data in referrals_data.values())
        
        embed = discord.Embed(
            title="🎁 Referral Settings",
            description="Manage referral system settings and users",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="📊 System Statistics",
            value=f"Total Users: **{total_users}**\n"
                  f"Total Earnings: **{format_balance(total_earnings)}**\n"
                  f"Welcome Bonus: **{format_balance(referral_settings.get('welcome_bonus', 100))}**\n"
                  f"Min Withdrawal: **{format_balance(referral_settings.get('min_withdrawal', 10))}**",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=view)


class UserManagementView(discord.ui.View):
    """View for managing individual user"""
    
    def __init__(self, user_id: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(UserActionSelect(user_id))
        self.add_item(BackToUserListSelect())
    
    def create_embed(self):
        """Create embed for user management"""
        referrals_data = get_data("server/referrals")
        user_data = referrals_data.get(self.user_id, {})
        
        embed = discord.Embed(
            title=f"👤 Managing User: {user_data.get('code', 'Unknown')}",
            description=f"<@{self.user_id}>",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="💼 Financial Info",
            value=f"Commission Rate: **{user_data.get('commission_rate', 0)}%**\n"
                  f"Total Earned: **{format_balance(user_data.get('total_earned', 0))}**\n"
                  f"Available Balance: **{format_balance(user_data.get('available_balance', 0))}**\n"
                  f"Today Earned: **{format_balance(user_data.get('today_earned', 0))}**",
            inline=False
        )
        
        embed.add_field(
            name="👥 Referral Info",
            value=f"Total Referred: **{len(user_data.get('referred_users', []))}** users\n"
                  f"Referral Code: **{user_data.get('code', 'N/A')}**",
            inline=False
        )
        
        # Show top 5 referred users
        if user_data.get('referral_earnings'):
            earnings_list = sorted(
                user_data['referral_earnings'].items(),
                key=lambda x: x[1].get('total_earned', 0),
                reverse=True
            )[:5]
            
            if earnings_list:
                referred_text = ""
                for ref_id, ref_data in earnings_list:
                    referred_text += f"• <@{ref_id}>: {format_balance(ref_data.get('total_earned', 0))}\n"
                
                embed.add_field(
                    name="🏆 Top Referrals",
                    value=referred_text,
                    inline=False
                )
        
        return embed


class UserActionSelect(discord.ui.Select):
    """Select menu for user actions"""
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        
        options = [
            discord.SelectOption(
                label="Edit Commission Rate",
                description="Change the user's commission percentage",
                emoji="📊",
                value="edit_commission"
            ),
            discord.SelectOption(
                label="View All Referrals",
                description="See all users referred by this user",
                emoji="👥",
                value="view_referrals"
            ),
            discord.SelectOption(
                label="Reset Earnings",
                description="Reset available balance to 0",
                emoji="🔄",
                value="reset_earnings"
            )
        ]
        
        super().__init__(
            placeholder="Select an action...",
            options=options,
            custom_id="user_management:action_select",
            row=0
        )
    
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "edit_commission":
            modal = EditCommissionModal(self.user_id)
            await interaction.response.send_modal(modal)
        
        elif self.values[0] == "view_referrals":
            referrals_data = get_data("server/referrals")
            user_data = referrals_data.get(self.user_id, {})
            referred_users = user_data.get("referred_users", [])
            
            if not referred_users:
                embed = discord.Embed(
                    title="❌ No Referrals",
                    description="This user hasn't referred anyone yet!",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            # Show all referrals with earnings
            referral_earnings = user_data.get("referral_earnings", {})
            import time
            
            description = f"Total: **{len(referred_users)}** users\n\n"
            
            for ref_id in referred_users[:25]:  # Show first 25
                earnings = referral_earnings.get(ref_id, {}).get("total_earned", 0)
                joined_at = referral_earnings.get(ref_id, {}).get("joined_at", 0)
                joined_date = time.strftime("%Y-%m-%d", time.localtime(joined_at)) if joined_at else "Unknown"
                
                description += f"**<@{ref_id}>**\n"
                description += f"└ Earned: {format_balance(earnings)} • Joined: {joined_date}\n\n"
            
            if len(referred_users) > 25:
                description += f"\n*Showing 25 of {len(referred_users)} referrals*"
            
            embed = discord.Embed(
                title=f"👥 Referrals of {user_data.get('code', 'Unknown')}",
                description=description,
                color=discord.Color.blue()
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        elif self.values[0] == "reset_earnings":
            referrals_data = get_data("server/referrals")
            if self.user_id in referrals_data:
                referrals_data[self.user_id]["available_balance"] = 0
                referrals_data[self.user_id]["today_earned"] = 0
                set_data("referrals", referrals_data)
                
                embed = discord.Embed(
                    title="✅ Success",
                    description=f"Reset earnings for <@{self.user_id}>",
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)


class BackToUserListSelect(discord.ui.Select):
    """Back to user list button as select menu"""
    
    def __init__(self):
        options = [
            discord.SelectOption(
                label="⬅️ Back to User List",
                value="back",
                emoji="⬅️"
            )
        ]
        
        super().__init__(
            placeholder="Go back...",
            options=options,
            custom_id="user_management:back",
            row=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        view = ReferralUserListView(page=0)
        embed = view.create_embed()
        await interaction.response.edit_message(embed=embed, view=view)


class EditCommissionModal(discord.ui.Modal, title="Edit Commission Rate"):
    """Modal to edit user's commission rate"""
    
    def __init__(self, user_id: str):
        super().__init__()
        self.user_id = user_id
    
    commission_input = discord.ui.TextInput(
        label="Commission Rate (%)",
        placeholder="Enter percentage (e.g., 5 for 5%)",
        required=True,
        max_length=5
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            rate = float(self.commission_input.value)
            if rate < 0 or rate > 100:
                raise ValueError("Rate must be between 0 and 100")
            
            referrals_data = get_data("server/referrals")
            if self.user_id in referrals_data:
                referrals_data[self.user_id]["commission_rate"] = rate
                set_data("referrals", referrals_data)
                
                embed = discord.Embed(
                    title="✅ Success",
                    description=f"Commission rate for <@{self.user_id}> set to **{rate}%**",
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                raise ValueError("User not found")
        except ValueError as e:
            embed = discord.Embed(
                title="❌ Error",
                description=f"Invalid input: {str(e)}",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class DepositSettingsView(discord.ui.View):
    """Deposit settings — setup select + payment-method multi-select + add button."""

    def __init__(self):
        super().__init__(timeout=300)
        from modules.ingame_deposit import ensure_ingame_payment_method
        ensure_ingame_payment_method()
        methods = get_data("server/payment_methods") or {}
        self.add_item(_DepositSetupSelect())
        if methods:
            self.add_item(_PaymentMethodsSelect(methods))
        self.add_item(_ConfigureIngameDepositButton())
        self.add_item(_AddPaymentMethodButton())
        self.add_item(BackToServerSettingsButton("payments"))


class _DepositSetupSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Set Deposit Category", value="category", emoji="📁",
                                 description="Channel category for deposit tickets"),
            discord.SelectOption(label="Set Cashier Role", value="cashier_role", emoji="👤",
                                 description="Role that can view deposit tickets"),
            discord.SelectOption(label="Set Minimum Deposit", value="min_deposit", emoji="📉",
                                 description="Minimum amount required to open a deposit ticket"),
            discord.SelectOption(label="Set Global Cashier Limit", value="global_limit", emoji="🔒",
                                 description="Max deposit amount any cashier can process"),
            discord.SelectOption(label="Set Deposit Log Channel", value="log_channel", emoji="📢",
                                 description="Channel to log deposit approvals and rejections"),
            discord.SelectOption(label="Configure In-Game Deposit", value="ingame_config", emoji="🎮",
                                 description="World, bot, webhook channel, DL→coin rate"),
        ]
        super().__init__(placeholder="⚙️ Configure settings...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val == "ingame_config":
            from modules.ingame_deposit import get_ingame_config, is_ingame_configured
            cfg = get_ingame_config()
            embed = discord.Embed(
                title=t("admin_panel.ingame_config_title", user_id=str(interaction.user.id)),
                description=_format_ingame_config_description(cfg, is_ingame_configured(cfg)),
                color=discord.Color.green(),
            )
            view = _IngameDepositConfigView()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        if val == "category":
            categories = [
                c for c in interaction.guild.categories
                if c.permissions_for(interaction.guild.me).manage_channels
            ]
            if not categories:
                return await interaction.response.send_message("❌ No accessible categories.", ephemeral=True)
            embed = discord.Embed(
                title="📁 Select Deposit Category",
                description="Choose the category where deposit ticket channels will be created.",
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(embed=embed, view=DepositCategorySelectView(categories), ephemeral=True)

        elif val == "cashier_role":
            roles = [r for r in interaction.guild.roles if not r.is_default() and r < interaction.guild.me.top_role]
            if not roles:
                return await interaction.response.send_message("❌ No available roles.", ephemeral=True)
            embed = discord.Embed(
                title="👤 Select Cashier Role",
                description="Choose the role that can view and handle deposit tickets.",
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(embed=embed, view=CashierRoleSelectView(roles), ephemeral=True)

        elif val == "min_deposit":
            await interaction.response.send_modal(MinDepositAmountModal())

        elif val == "global_limit":
            await interaction.response.send_modal(CashierDepositLimitModal(None))

        elif val == "log_channel":
            embed = discord.Embed(
                title="📢 Select Deposit Log Channel",
                description="Choose the channel where deposit approvals and rejections will be logged.",
                color=discord.Color.blue(),
            )
            view = discord.ui.View(timeout=300)
            view.add_item(DepositChannelSelect())
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class _PaymentMethodsSelect(discord.ui.Select):
    """Multi-select for toggling payment methods. Selected = enabled, unselected = disabled."""

    def __init__(self, methods: dict):
        options = [
            discord.SelectOption(
                label=info.get("name", k),
                value=k,
                emoji=info.get("emoji") or None,
                default=bool(info.get("enabled")),
            )
            for k, info in methods.items()
        ]
        super().__init__(
            placeholder="💳 Toggle payment methods (selected = enabled)...",
            options=options,
            min_values=0,
            max_values=max(1, len(options)),
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        methods = get_data("server/payment_methods") or {}
        enabled_keys = set(self.values)
        for k in methods:
            methods[k]["enabled"] = k in enabled_keys
        set_data("server/payment_methods", methods)
        embed, view = _build_deposit_settings_embed_and_view(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)


def _format_ingame_config_description(cfg: dict, configured: bool) -> str:
    world = cfg.get("world") or "—"
    bot = cfg.get("bot_name") or "—"
    ch = cfg.get("webhook_channel_id")
    from modules.ingame_deposit import format_dl_coin_rate

    rate = format_dl_coin_rate(float(cfg.get("dl_to_coin_rate", 0) or 0))
    status = "✅ Ready" if configured else "⚠️ Incomplete"
    ch_txt = f"<#{ch}>" if ch else "—"
    return (
        f"**Status:** {status}\n"
        f"**World:** `{world}`\n"
        f"**Bot:** `{bot}`\n"
        f"**Webhook channel:** {ch_txt}\n"
        f"**Coins per 1 DL:** `{rate}`"
    )


class _IngameDepositConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(_IngameWebhookChannelSelect())
        self.add_item(_IngameEditDetailsButton())


class _IngameEditDetailsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="World / Bot / Rate", style=discord.ButtonStyle.secondary, emoji="✏️")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(IngameDepositConfigModal())


class _ConfigureIngameDepositButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="In-Game Deposit", style=discord.ButtonStyle.primary, emoji="🎮", row=4)

    async def callback(self, interaction: discord.Interaction):
        from modules.ingame_deposit import get_ingame_config, is_ingame_configured
        cfg = get_ingame_config()
        embed = discord.Embed(
            title=t("admin_panel.ingame_config_title", user_id=str(interaction.user.id)),
            description=_format_ingame_config_description(cfg, is_ingame_configured(cfg)),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, view=_IngameDepositConfigView(), ephemeral=True)


class IngameDepositConfigModal(discord.ui.Modal, title="🎮 In-Game Deposit Settings"):
    world_input = discord.ui.TextInput(label="World name", placeholder="e.g. VEGAS", max_length=32)
    bot_input = discord.ui.TextInput(label="Bot name", placeholder="e.g. VegasBot", max_length=32)
    rate_input = discord.ui.TextInput(
        label="Bot coins per 1 DL",
        placeholder="e.g. 0.70 or 100",
        max_length=12,
    )

    def __init__(self):
        super().__init__()
        from modules.ingame_deposit import get_ingame_config
        cfg = get_ingame_config()
        if cfg.get("world"):
            self.world_input.default = str(cfg["world"])
        if cfg.get("bot_name"):
            self.bot_input.default = str(cfg["bot_name"])
        if cfg.get("dl_to_coin_rate"):
            from modules.ingame_deposit import format_dl_coin_rate

            self.rate_input.default = format_dl_coin_rate(cfg["dl_to_coin_rate"])

    async def on_submit(self, interaction: discord.Interaction):
        from modules.ingame_deposit import (
            INGAME_METHOD_KEY,
            ensure_ingame_payment_method,
            is_ingame_configured,
            parse_decimal_input,
        )
        try:
            rate = parse_decimal_input(self.rate_input.value)
            if rate <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            return await interaction.response.send_message(
                "❌ Invalid DL→coin rate. Use e.g. `0.70` or `0,70` for 0.7 coins per 1 DL.",
                ephemeral=True,
            )

        methods = get_data("server/payment_methods") or {}
        ensure_ingame_payment_method()
        entry = methods.get(INGAME_METHOD_KEY, {})
        entry["world"] = self.world_input.value.strip()
        entry["bot_name"] = self.bot_input.value.strip()
        entry["dl_to_coin_rate"] = rate
        entry["type"] = "ingame"
        methods[INGAME_METHOD_KEY] = entry
        set_data("server/payment_methods", methods)

        embed = discord.Embed(
            title="✅ In-Game Deposit Updated",
            description=_format_ingame_config_description(entry, is_ingame_configured(entry)),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class _IngameWebhookChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Select webhook / log channel for in-game deposits",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        from modules.ingame_deposit import INGAME_METHOD_KEY, ensure_ingame_payment_method, is_ingame_configured
        channel = self.values[0]
        methods = get_data("server/payment_methods") or {}
        ensure_ingame_payment_method()
        entry = methods.get(INGAME_METHOD_KEY, {})
        entry["webhook_channel_id"] = channel.id
        entry["type"] = "ingame"
        methods[INGAME_METHOD_KEY] = entry
        set_data("server/payment_methods", methods)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Webhook Channel Set",
                description=(
                    f"In-game deposit logs will be read from {channel.mention}.\n\n"
                    + _format_ingame_config_description(entry, is_ingame_configured(entry))
                ),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class _AddPaymentMethodButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Add Payment Method", style=discord.ButtonStyle.success, emoji="➕", row=3)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_AddPaymentMethodModal())


class _AddPaymentMethodModal(discord.ui.Modal, title="➕ Add Payment Method"):
    name_input = discord.ui.TextInput(label="Method name", placeholder="e.g. Crypto", max_length=32)
    key_input = discord.ui.TextInput(label="Internal key (no spaces)", placeholder="e.g. crypto", max_length=32)
    emoji_input = discord.ui.TextInput(label="Emoji (optional)", placeholder="e.g. 🪙", max_length=45, required=False)
    desc_input = discord.ui.TextInput(label="Description (optional)", placeholder="Short description", max_length=100, required=False)
    ticket_input = discord.ui.TextInput(
        label="Ticket mode? (yes / no)",
        placeholder="yes = withdraw opens a ticket channel (like Gold)",
        max_length=3,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        key = self.key_input.value.strip().lower().replace(" ", "_")
        methods = get_data("server/payment_methods") or {}
        if key in methods:
            return await interaction.response.send_message(f"❌ Key `{key}` already exists.", ephemeral=True)
        ticket_mode = self.ticket_input.value.strip().lower() in ("yes", "y", "evet", "true", "1")
        methods[key] = {
            "name": self.name_input.value.strip(),
            "enabled": False,
            "emoji": self.emoji_input.value.strip() or "💸",
            "description": self.desc_input.value.strip(),
            "ticket": ticket_mode,
        }
        set_data("server/payment_methods", methods)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Payment Method Added",
                description=f"**{methods[key]['name']}** added (disabled by default). Enable it from the payment methods select.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


def _build_deposit_settings_embed_and_view(guild: discord.Guild):
    """Build the deposit settings embed + view."""
    from modules.ingame_deposit import ensure_ingame_payment_method, get_ingame_config, is_ingame_configured
    ensure_ingame_payment_method()
    methods = get_data("server/payment_methods") or {}
    ingame_cfg = get_ingame_config()
    server_data = get_server_data(str(guild.id))
    deposit_cat = server_data.get("deposit_category")
    cashier_role = server_data.get("cashier_role")
    deposit_settings = get_data("server/deposit_settings") or {}
    min_deposit = deposit_settings.get("min_deposit", 0)
    global_limit = deposit_settings.get("cashier_deposit_limit", 0)
    log_channel_id = deposit_settings.get("channel_id")

    embed = discord.Embed(
        title="💳 Deposit Settings",
        color=discord.Color.green(),
    )

    # Configuration block
    config_lines = [
        f"📁 **Deposit Category:** {'<#' + str(deposit_cat) + '>' if deposit_cat else '❌ Not set'}",
        f"👤 **Cashier Role:** {'<@&' + str(cashier_role) + '>' if cashier_role else '❌ Not set'}",
        f"📉 **Minimum Deposit:** {format_balance(min_deposit, 'real') if min_deposit else '❌ None'}",
        f"🔒 **Global Cashier Limit:** {format_balance(global_limit, 'real') if global_limit else '❌ None'}",
        f"📢 **Deposit Log Channel:** {'<#' + str(log_channel_id) + '>' if log_channel_id else '❌ Not set'}",
    ]
    embed.add_field(name="⚙️ Configuration", value="\n".join(config_lines), inline=False)

    # Payment methods block
    if methods:
        method_lines = [
            f"{'✅' if info.get('enabled') else '❌'} {info.get('emoji', '')} **{info.get('name', k)}**"
            for k, info in methods.items()
        ]
        embed.add_field(name="💳 Payment Methods", value="\n".join(method_lines), inline=False)
    else:
        embed.add_field(name="💳 Payment Methods", value="No payment methods configured.", inline=False)

    ingame_ch = ingame_cfg.get("webhook_channel_id")
    embed.add_field(
        name="🎮 In-Game Deposit",
        value=_format_ingame_config_description(ingame_cfg, is_ingame_configured(ingame_cfg)),
        inline=False,
    )
    if ingame_ch:
        embed.set_footer(text="Enable “In-Game Funds” in payment methods • Webhook channel must receive bot logs")
    else:
        embed.set_footer(text="Use the selects below to configure • Selected methods = enabled")

    return embed, DepositSettingsView()




class WithdrawSettingsView(discord.ui.View):
    """Withdraw settings management view"""

    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="📢 Set Withdraw Channel", style=discord.ButtonStyle.primary, row=0)
    async def set_withdraw_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(timeout=300)
        view.add_item(WithdrawChannelSelect())
        view.add_item(BackToServerSettingsButton("payments", interaction.user.id))
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="📉 Set Min Withdrawal", style=discord.ButtonStyle.secondary, row=0)
    async def set_min_withdrawal(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = MinWithdrawAmountModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="✖️ Set Deposit Multiplier", style=discord.ButtonStyle.secondary, row=0)
    async def set_multiplier(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = WithdrawMultiplierModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🔄 Toggle Mode", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        current_mode = server_data.get("withdraw_mode", "log")
        server_data["withdraw_mode"] = "ticket" if current_mode == "log" else "log"
        set_server_data(guild_id, server_data)
        new_mode = server_data["withdraw_mode"]
        withdraw_channel = server_data.get("withdraw_channel")
        min_withdrawal = server_data.get("min_withdrawal", 100)
        withdraw_log_channel = server_data.get("withdraw_log_channel")
        embed = discord.Embed(
            title="🏦 Withdraw Settings",
            description="Configure the withdrawal channel and minimum withdrawal amount.",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="📢 Withdraw Channel",
            value=f"<#{withdraw_channel}>" if withdraw_channel else "Not Set",
            inline=False
        )
        embed.add_field(
            name="📉 Fixed Min Withdrawal",
            value=format_balance(min_withdrawal, "real"),
            inline=True
        )
        multiplier = server_data.get("withdraw_min_multiplier", 0) or 0
        embed.add_field(
            name="✖️ Deposit Multiplier",
            value=f"{multiplier}x last deposit" if multiplier else "❌ Disabled (using fixed min)",
            inline=True
        )
        embed.add_field(
            name="📢 Withdraw Log Channel",
            value=f"<#{withdraw_log_channel}>" if withdraw_log_channel else "Not Set",
            inline=False
        )
        embed.add_field(
            name="🔄 Withdraw Mode",
            value="🎫 Ticket (private ticket channel per request)" if new_mode == "ticket" else "📋 Log (send to withdraw channel)",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=WithdrawSettingsView(interaction.user.id))

    @discord.ui.button(label="📢 Set Log Channel", style=discord.ButtonStyle.primary, row=1)
    async def set_log_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(timeout=300)
        view.add_item(WithdrawLogChannelSelect())
        view.add_item(BackToServerSettingsButton("payments", interaction.user.id))
        await interaction.response.edit_message(view=view)

    @discord.ui.button(label="⬅️ Ödeme & Finans", style=discord.ButtonStyle.secondary, row=2)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_PAYMENTS, go_hub

        await go_hub(interaction, HUB_PAYMENTS, user_id=interaction.user.id)


class WithdrawChannelSelect(discord.ui.ChannelSelect):
    """Channel select for withdrawal requests"""

    def __init__(self):
        super().__init__(
            placeholder="Select withdraw requests channel",
            channel_types=[discord.ChannelType.text],
            custom_id="withdraw_settings:channel_select"
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data["withdraw_channel"] = channel.id
        set_server_data(guild_id, server_data)

        guild_id2 = str(interaction.guild.id)
        sd2 = get_server_data(guild_id2)
        mode2 = sd2.get("withdraw_mode", "log")
        embed = discord.Embed(
            title="🏦 Withdraw Settings",
            description="Configure the withdrawal channel and minimum withdrawal amount.",
            color=discord.Color.orange()
        )
        mul2 = sd2.get("withdraw_min_multiplier", 0) or 0
        wlc2 = sd2.get("withdraw_log_channel")
        embed.add_field(name="📢 Withdraw Channel", value=channel.mention, inline=False)
        embed.add_field(name="📉 Fixed Min Withdrawal", value=format_balance(sd2.get("min_withdrawal", 100), "real"), inline=True)
        embed.add_field(name="✖️ Deposit Multiplier", value=f"{mul2}x last deposit" if mul2 else "❌ Disabled (using fixed min)", inline=True)
        embed.add_field(name="📢 Withdraw Log Channel", value=f"<#{wlc2}>" if wlc2 else "Not Set", inline=False)
        embed.add_field(
            name="🔄 Withdraw Mode",
            value="🎫 Ticket (private ticket channel per request)" if mode2 == "ticket" else "📋 Log (send to withdraw channel)",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=WithdrawSettingsView(interaction.user.id))


class WithdrawLogChannelSelect(discord.ui.ChannelSelect):
    """Channel select for withdrawal log messages"""

    def __init__(self):
        super().__init__(
            placeholder="Select withdraw log channel",
            channel_types=[discord.ChannelType.text],
            custom_id="withdraw_settings:log_channel_select"
        )

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data["withdraw_log_channel"] = channel.id
        set_server_data(guild_id, server_data)
        withdraw_channel = server_data.get("withdraw_channel")
        min_withdrawal = server_data.get("min_withdrawal", 100)
        withdraw_mode = server_data.get("withdraw_mode", "log")
        multiplier = server_data.get("withdraw_min_multiplier", 0) or 0
        embed = discord.Embed(
            title="🏦 Withdraw Settings",
            description="Configure the withdrawal channel and minimum withdrawal amount.",
            color=discord.Color.orange()
        )
        embed.add_field(name="📢 Withdraw Channel", value=f"<#{withdraw_channel}>" if withdraw_channel else "Not Set", inline=False)
        embed.add_field(name="📉 Fixed Min Withdrawal", value=format_balance(min_withdrawal, "real"), inline=True)
        embed.add_field(name="✖️ Deposit Multiplier", value=f"{multiplier}x last deposit" if multiplier else "❌ Disabled (using fixed min)", inline=True)
        embed.add_field(name="📢 Withdraw Log Channel", value=channel.mention, inline=False)
        embed.add_field(
            name="🔄 Withdraw Mode",
            value="🎫 Ticket (private ticket channel per request)" if withdraw_mode == "ticket" else "📋 Log (send to withdraw channel)",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=WithdrawSettingsView(interaction.user.id))


class MinWithdrawAmountModal(discord.ui.Modal, title="Set Minimum Withdrawal"):
    """Modal to set minimum withdrawal amount"""

    amount_input = discord.ui.TextInput(
        label="Minimum Withdrawal Amount",
        placeholder="Enter minimum amount (e.g., 100)",
        required=True,
        max_length=10
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount_input.value)
            if amount < 1:
                raise ValueError()
        except (ValueError, TypeError):
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Error", description="Please enter a valid positive number!", color=discord.Color.red()),
                ephemeral=True
            )

        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data["min_withdrawal"] = amount
        set_server_data(guild_id, server_data)

        withdraw_channel = server_data.get("withdraw_channel")
        withdraw_mode = server_data.get("withdraw_mode", "log")
        multiplier = server_data.get("withdraw_min_multiplier", 0) or 0
        withdraw_log_channel = server_data.get("withdraw_log_channel")
        embed = discord.Embed(
            title="🏦 Withdraw Settings",
            description="Configure the withdrawal channel and minimum withdrawal amount.",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="📢 Withdraw Channel",
            value=f"<#{withdraw_channel}>" if withdraw_channel else "Not Set",
            inline=False
        )
        embed.add_field(
            name="📉 Fixed Min Withdrawal",
            value=format_balance(amount, "real"),
            inline=True
        )
        embed.add_field(
            name="✖️ Deposit Multiplier",
            value=f"{multiplier}x last deposit" if multiplier else "❌ Disabled (using fixed min)",
            inline=True
        )
        embed.add_field(
            name="📢 Withdraw Log Channel",
            value=f"<#{withdraw_log_channel}>" if withdraw_log_channel else "Not Set",
            inline=False
        )
        embed.add_field(
            name="🔄 Withdraw Mode",
            value="🎫 Ticket (private ticket channel per request)" if withdraw_mode == "ticket" else "📋 Log (send to withdraw channel)",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=WithdrawSettingsView(interaction.user.id))


class WithdrawMultiplierModal(discord.ui.Modal, title="✖️ Set Deposit Multiplier"):
    """Set the minimum withdrawal as a multiplier of the user's last deposit."""

    multiplier_input = discord.ui.TextInput(
        label="Multiplier (0 = disabled, e.g. 2 = 2x)",
        placeholder="Enter a number like 1.5 or 2. Set 0 to disable.",
        required=True,
        max_length=6
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = float(self.multiplier_input.value.replace(",", ".").strip())
            if value < 0:
                raise ValueError()
        except (ValueError, TypeError):
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Error", description="Please enter a valid non-negative number!", color=discord.Color.red()),
                ephemeral=True
            )

        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        if value == 0:
            server_data.pop("withdraw_min_multiplier", None)
        else:
            server_data["withdraw_min_multiplier"] = value
        set_server_data(guild_id, server_data)

        withdraw_channel = server_data.get("withdraw_channel")
        min_withdrawal = server_data.get("min_withdrawal", 100)
        withdraw_mode = server_data.get("withdraw_mode", "log")
        embed = discord.Embed(
            title="🏦 Withdraw Settings",
            description="Configure the withdrawal channel and minimum withdrawal amount.",
            color=discord.Color.orange()
        )
        embed.add_field(name="📢 Withdraw Channel", value=f"<#{withdraw_channel}>" if withdraw_channel else "Not Set", inline=False)
        embed.add_field(name="📉 Fixed Min Withdrawal", value=format_balance(min_withdrawal, "real"), inline=True)
        embed.add_field(
            name="✖️ Deposit Multiplier",
            value=f"{value}x last deposit" if value > 0 else "❌ Disabled (using fixed min)",
            inline=True
        )
        embed.add_field(
            name="� Withdraw Log Channel",
            value=f"<#{server_data.get('withdraw_log_channel')}>" if server_data.get("withdraw_log_channel") else "Not Set",
            inline=False
        )
        embed.add_field(
            name="�🔄 Withdraw Mode",
            value="🎫 Ticket (private ticket channel per request)" if withdraw_mode == "ticket" else "📋 Log (send to withdraw channel)",
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=WithdrawSettingsView(interaction.user.id))


# ─────────────────────────────────────────────────────────────────────────────
# Finance Stats
# ─────────────────────────────────────────────────────────────────────────────

def _build_finance_stats_embed(guild_id: str) -> discord.Embed:
    """Build the finance statistics embed including manual adjustments."""
    all_user_ids = get_all_registered_user_ids()
    total_deposits = 0.0
    total_withdrawals = 0.0
    deposit_count = 0
    withdraw_count = 0
    for uid in all_user_ids:
        dep_history = get_user_data(int(uid), "deposit_history") or {}
        for dep in dep_history.values():
            if dep.get("status") in ("approved", "completed"):
                confirmed = dep.get("confirmed_amount") or dep.get("amount") or 0
                total_deposits += float(confirmed)
                deposit_count += 1
        wdr_history = get_user_data(int(uid), "withdraw_history") or {}
        for wdr in wdr_history.values():
            if wdr.get("status") == "approved":
                total_withdrawals += float(wdr.get("amount", 0))
                withdraw_count += 1

    server_data = get_server_data(guild_id)
    adj = server_data.get("finance_manual_adjustments", {})
    dep_adj = float(adj.get("deposit", 0))
    wdr_adj = float(adj.get("withdraw", 0))

    total_deposits += dep_adj
    total_withdrawals += wdr_adj

    net = total_deposits - total_withdrawals
    color = discord.Color.green() if net >= 0 else discord.Color.red()
    embed = discord.Embed(title="📊 Finance Statistics", color=color)
    embed.add_field(
        name="💰 Total Deposits",
        value=f"{format_balance(total_deposits, 'real')} ({deposit_count} txn)"
              + (f"\n*Adjustment: {'+' if dep_adj >= 0 else ''}{format_balance(dep_adj, 'real')}*" if dep_adj != 0 else ""),
        inline=False
    )
    embed.add_field(
        name="💸 Total Withdrawals",
        value=f"{format_balance(total_withdrawals, 'real')} ({withdraw_count} txn)"
              + (f"\n*Adjustment: {'+' if wdr_adj >= 0 else ''}{format_balance(wdr_adj, 'real')}*" if wdr_adj != 0 else ""),
        inline=False
    )
    embed.add_field(
        name="📈 Net Profit" if net >= 0 else "📉 Net Loss",
        value=format_balance(abs(net), "real"),
        inline=False
    )
    return embed


class FinanceStatsView(discord.ui.View):
    """Buttons shown on the Finance Stats panel."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="✏️ Update", style=discord.ButtonStyle.primary, row=0)
    async def update_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = FinanceUpdateView()
        embed = discord.Embed(
            title="✏️ Manual Adjustment",
            description="Select what you want to adjust:",
            color=discord.Color.blurple()
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="🔄 Reset Adjustments", style=discord.ButtonStyle.danger, row=0)
    async def reset_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        server_data.pop("finance_manual_adjustments", None)
        set_server_data(guild_id, server_data)
        embed = _build_finance_stats_embed(guild_id)
        await interaction.response.edit_message(embed=embed, view=FinanceStatsView())

    @discord.ui.button(label="⬅️ Ödeme & Finans", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_PAYMENTS, go_hub

        await go_hub(interaction, HUB_PAYMENTS, user_id=interaction.user.id)


class FinanceUpdateSelect(discord.ui.Select):
    """Select to choose deposit/withdraw and add/remove."""

    def __init__(self):
        options = [
            discord.SelectOption(label="➕ Add to Deposits",      value="deposit_add",      emoji="💰"),
            discord.SelectOption(label="➖ Remove from Deposits",  value="deposit_remove",   emoji="💰"),
            discord.SelectOption(label="➕ Add to Withdrawals",    value="withdraw_add",     emoji="💸"),
            discord.SelectOption(label="➖ Remove from Withdrawals", value="withdraw_remove", emoji="💸"),
        ]
        super().__init__(placeholder="Select adjustment type...", options=options)

    async def callback(self, interaction: discord.Interaction):
        modal = FinanceAdjustModal(self.values[0])
        await interaction.response.send_modal(modal)


class FinanceUpdateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(FinanceUpdateSelect())

    @discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = _build_finance_stats_embed(str(interaction.guild.id))
        await interaction.response.edit_message(embed=embed, view=FinanceStatsView())


class FinanceAdjustModal(discord.ui.Modal, title="Manual Finance Adjustment"):
    amount_input = discord.ui.TextInput(
        label="Amount",
        placeholder="e.g. 5000",
        required=True,
        max_length=15
    )

    def __init__(self, adjustment_type: str):
        super().__init__()
        self.adjustment_type = adjustment_type

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.amount_input.value.replace(",", ".").replace(" ", "").strip()
        try:
            amount = float(raw)
            if amount <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Error", description="Please enter a valid positive number.", color=discord.Color.red()),
                ephemeral=True
            )

        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        adj = server_data.get("finance_manual_adjustments", {})

        if self.adjustment_type == "deposit_add":
            adj["deposit"] = float(adj.get("deposit", 0)) + amount
        elif self.adjustment_type == "deposit_remove":
            adj["deposit"] = float(adj.get("deposit", 0)) - amount
        elif self.adjustment_type == "withdraw_add":
            adj["withdraw"] = float(adj.get("withdraw", 0)) + amount
        elif self.adjustment_type == "withdraw_remove":
            adj["withdraw"] = float(adj.get("withdraw", 0)) - amount

        server_data["finance_manual_adjustments"] = adj
        set_server_data(guild_id, server_data)

        embed = _build_finance_stats_embed(guild_id)
        await interaction.response.edit_message(embed=embed, view=FinanceStatsView())


class PaymentMethodsSelect(discord.ui.Select):
    def __init__(self):
        methods = get_data("server/payment_methods") or {}
        options = []
        for key, info in methods.items():
            label = info.get("name")
            status = "Enabled" if info.get("enabled") else "Disabled"
            options.append(discord.SelectOption(label=f"{label} ({status})", value=key))
        super().__init__(placeholder="Select a payment method to toggle...", options=options, custom_id="payment_methods:select")

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        methods = get_data("server/payment_methods") or {}
        if selected in methods:
            methods[selected]["enabled"] = not methods[selected].get("enabled", False)
            set_data("server/payment_methods", methods)
            status = "enabled" if methods[selected]["enabled"] else "disabled"
            embed = discord.Embed(title="✅ Success", description=f"{methods[selected].get('name')} is now {status}.", color=discord.Color.green())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            # refresh view
            view = PaymentMethodsView()
            embed_main = discord.Embed(title="💸 Payment Methods", description="Enable or disable payment methods available for deposits", color=discord.Color.gold())
            for key, info in methods.items():
                status = "✅ Enabled" if info.get("enabled") else "❌ Disabled"
                embed_main.add_field(name=info.get("name"), value=status, inline=False)
            try:
                if interaction.message:
                    await interaction.message.edit(embed=embed_main, view=view)
            except Exception:
                pass


class PaymentMethodsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(PaymentMethodsSelect())
        self.add_item(BackToServerSettingsButton("payments"))





class DepositChannelSelect(discord.ui.ChannelSelect):
    """Channel select for deposit notifications"""
    
    def __init__(self):
        super().__init__(
            placeholder="Select deposit notification channel",
            channel_types=[discord.ChannelType.text],
            custom_id="deposit_settings:channel_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        settings = get_data("server/deposit_settings") or {}
        settings["channel_id"] = channel.id
        set_data("server/deposit_settings", settings)
        
        embed = discord.Embed(
            title="✅ Success",
            description=f"Deposit channel set to: {channel.mention}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TicketSystemView(discord.ui.View):
    """Ticket system settings view"""
    
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id
    
    @discord.ui.button(label="📁 Set Ticket Category", style=discord.ButtonStyle.primary, row=0)
    async def set_ticket_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ticket kategorisi ayarla"""
        view = discord.ui.View(timeout=300)
        view.add_item(TicketCategorySelect())
        view.add_item(BackToServerSettingsButton("channels", interaction.user.id))
        
        embed = discord.Embed(
            title="📁 Set Ticket Category",
            description="Select a category where ticket channels will be created.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="⬅️ Sunucu & Kanallar", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_CHANNELS, go_hub

        await go_hub(interaction, HUB_CHANNELS, user_id=interaction.user.id)


class TicketCategorySelect(discord.ui.ChannelSelect):
    """Ticket category select menu"""
    
    def __init__(self):
        super().__init__(
            placeholder="Select a category for tickets",
            channel_types=[discord.ChannelType.category],
            custom_id="ticket_settings:category_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        settings = get_data("server/ticket_settings") or {}
        settings["category_id"] = category.id
        set_data("server/ticket_settings", settings)
        
        embed = discord.Embed(
            title="✅ Success",
            description=f"Ticket category set to: {category.mention}",
            color=discord.Color.green()
        )
        
        # Geri dön
        view = TicketSystemView(interaction.user.id)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Ana mesajı güncelle
        ticket_settings = get_data("server/ticket_settings") or {}
        tickets_data = get_data("server/tickets") or {}
        guild_id = str(interaction.guild.id)
        open_tickets = len([t for t in tickets_data.get(guild_id, {}).values() if t.get("status") == "open"]) if guild_id in tickets_data else 0
        
        main_embed = discord.Embed(
            title="🎫 Ticket System",
            description="Manage support ticket system configuration",
            color=discord.Color.blue()
        )
        main_embed.add_field(
            name="📊 Statistics",
            value=f"Open Tickets: **{open_tickets}**",
            inline=False
        )
        main_embed.add_field(
            name="📁 Ticket Category",
            value=f"{category.mention}",
            inline=False
        )
        try:
            if interaction.message:
                await interaction.message.edit(embed=main_embed, view=view)
        except Exception:
            pass


class SetDepositCategoryButton(discord.ui.Button):
    """Button to set deposit category"""
    
    def __init__(self):
        super().__init__(
            label="Set Deposit Category",
            style=discord.ButtonStyle.primary,
            emoji="📁",
            row=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Open category selection"""
        categories = [cat for cat in interaction.guild.categories if cat.permissions_for(interaction.guild.me).manage_channels]
        
        if not categories:
            embed = discord.Embed(
                title="❌ Error",
                description="No categories available or bot lacks permissions.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        view = DepositCategorySelectView(categories)
        embed = discord.Embed(
            title="📁 Select Deposit Category",
            description="Choose a category for deposit tickets, or select 'Create New' to create one.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class DepositCategorySelectView(discord.ui.View):
    """View for selecting deposit category"""
    
    def __init__(self, categories):
        super().__init__(timeout=300)
        self.add_item(DepositCategorySelect(categories))


class DepositCategorySelect(discord.ui.Select):
    """Select menu for deposit category"""
    
    def __init__(self, categories):
        options = [
            discord.SelectOption(
                label=cat.name,
                description=f"ID: {cat.id}",
                value=str(cat.id)
            )
            for cat in categories[:24]  # Discord limit
        ]
        options.append(
            discord.SelectOption(
                label="➕ Create New Category",
                description="Create a new category for deposits",
                value="create_new"
            )
        )
        
        super().__init__(
            placeholder="Select deposit category...",
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle category selection"""
        selected = self.values[0]
        
        if selected == "create_new":
            # Create new category
            try:
                category = await interaction.guild.create_category(
                    name="💳 Deposit Tickets",
                    reason="Deposit ticket system setup"
                )
                
                # Set permissions for cashier role
                server_data = get_server_data(str(interaction.guild.id))
                cashier_role_id = server_data.get("cashier_role")
                if cashier_role_id:
                    cashier_role = interaction.guild.get_role(int(cashier_role_id))
                    if cashier_role:
                        await category.set_permissions(
                            cashier_role,
                            read_messages=True,
                            send_messages=True,
                            manage_messages=True
                        )
                        await category.set_permissions(
                            interaction.guild.default_role,
                            read_messages=False
                        )
                
                # Save to server data
                server_data["deposit_category"] = category.id
                set_server_data(str(interaction.guild.id), server_data)
                
                embed = discord.Embed(
                    title="✅ Success",
                    description=f"Created new deposit category: {category.mention}",
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(embed=embed, view=None)
                
            except Exception as e:
                embed = discord.Embed(
                    title="❌ Error",
                    description=f"Failed to create category: {str(e)}",
                    color=discord.Color.red()
                )
                await interaction.response.edit_message(embed=embed, view=None)
        
        else:
            # Use existing category
            category_id = int(selected)
            category = interaction.guild.get_channel(category_id)
            
            if not category:
                embed = discord.Embed(
                    title="❌ Error",
                    description="Category not found.",
                    color=discord.Color.red()
                )
                await interaction.response.edit_message(embed=embed, view=None)
                return
            
            # Save to server data
            server_data = get_server_data(str(interaction.guild.id))
            server_data["deposit_category"] = category_id
            set_server_data(str(interaction.guild.id), server_data)
            
            embed = discord.Embed(
                title="✅ Success",
                description=f"Set deposit category to: {category.mention}",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)


class SetCashierRoleButton(discord.ui.Button):
    """Button to set cashier role"""
    
    def __init__(self):
        super().__init__(
            label="Set Cashier Role",
            style=discord.ButtonStyle.primary,
            emoji="👤",
            row=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Open role selection"""
        roles = [role for role in interaction.guild.roles if not role.is_default() and role < interaction.guild.me.top_role]
        
        if not roles:
            embed = discord.Embed(
                title="❌ Error",
                description="No roles available.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        view = CashierRoleSelectView(roles)
        embed = discord.Embed(
            title="👤 Select Cashier Role",
            description="Choose the role that can view deposit ticket channels.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class CashierRoleSelectView(discord.ui.View):
    """View for selecting cashier role"""
    
    def __init__(self, roles):
        super().__init__(timeout=300)
        self.add_item(CashierRoleSelect(roles))


class CashierRoleSelect(discord.ui.Select):
    """Select menu for cashier role"""
    
    def __init__(self, roles):
        options = [
            discord.SelectOption(
                label=role.name,
                description=f"ID: {role.id}",
                value=str(role.id)
            )
            for role in roles[:24]  # Discord limit
        ]
        
        super().__init__(
            placeholder="Select cashier role...",
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle role selection"""
        selected = self.values[0]
        role_id = int(selected)
        role = interaction.guild.get_role(role_id)
        
        if not role:
            embed = discord.Embed(
                title="❌ Error",
                description="Role not found.",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=None)
            return
        
        # Save to server data
        server_data = get_server_data(str(interaction.guild.id))
        server_data["cashier_role"] = role_id
        set_server_data(str(interaction.guild.id), server_data)
        
        embed = discord.Embed(
            title="✅ Success",
            description=f"Set cashier role to: {role.mention}",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=None)


class SetCashierDepositLimitButton(discord.ui.Button):
    """Button to set the maximum deposit amount a cashier (non-admin) can process."""

    def __init__(self):
        super().__init__(
            label="Set Cashier Deposit Limit",
            style=discord.ButtonStyle.secondary,
            emoji="🔒",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        server_data = get_server_data(str(interaction.guild.id))
        cashier_role_id = server_data.get("cashier_role")
        cashier_members = []
        if cashier_role_id:
            role = interaction.guild.get_role(int(cashier_role_id))
            if role:
                cashier_members = [m for m in role.members if not m.bot]

        deposit_settings = get_data("server/deposit_settings") or {}
        global_limit = deposit_settings.get("cashier_deposit_limit", 0)
        user_limits  = deposit_settings.get("cashier_user_limits", {})

        from modules.utils import format_balance
        embed = discord.Embed(
            title="🔒 Cashier Deposit Limits",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="🌐 Global Limit",
            value=format_balance(global_limit, "real") if global_limit else "No limit",
            inline=False,
        )
        if cashier_members:
            lines = []
            for m in cashier_members[:15]:
                ul = user_limits.get(str(m.id))
                lines.append(f"{m.mention} — {format_balance(ul, 'real') if ul else 'global'}")
            embed.add_field(name="👤 Cashier Members", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="👤 Cashier Members", value="No cashier role set or no members.", inline=False)

        view = _CashierLimitView(cashier_members)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class _CashierMemberSelect(discord.ui.Select):
    def __init__(self, members: list):
        options = [
            discord.SelectOption(
                label=m.display_name[:100],
                description=f"ID: {m.id}",
                value=str(m.id),
            )
            for m in members[:25]
        ]
        super().__init__(
            placeholder="Select a cashier to set individual limit...",
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_user_id = int(self.values[0])
        await interaction.response.defer()


class _CashierLimitView(discord.ui.View):
    def __init__(self, members: list):
        super().__init__(timeout=120)
        self.selected_user_id: Optional[int] = None
        if members:
            self.add_item(_CashierMemberSelect(members))

    @discord.ui.button(label="Set limit for selected cashier", style=discord.ButtonStyle.primary, row=1)
    async def set_per_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_user_id:
            return await interaction.response.send_message(
                "❌ Select a cashier from the list first.", ephemeral=True
            )
        await interaction.response.send_modal(CashierDepositLimitModal(self.selected_user_id))

    @discord.ui.button(label="Set global cashier limit", style=discord.ButtonStyle.secondary, row=1)
    async def set_global(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CashierDepositLimitModal(None))


class CashierDepositLimitModal(discord.ui.Modal, title="🔒 Cashier Deposit Limit"):
    amount_input = discord.ui.TextInput(
        label="Max deposit amount (0 = no limit)",
        placeholder="e.g. 5000",
        required=True,
        max_length=15,
    )

    def __init__(self, user_id: Optional[int] = None):
        super().__init__()
        self.target_user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.amount_input.value.strip().replace(",", "").replace(".", "")
        try:
            limit = float(raw)
            if limit < 0:
                raise ValueError()
        except ValueError:
            return await interaction.response.send_message(
                "❌ Invalid amount. Enter a number ≥ 0 (0 removes the limit).",
                ephemeral=True,
            )

        deposit_settings = get_data("server/deposit_settings") or {}
        from modules.utils import format_balance

        if self.target_user_id:
            user_limits = deposit_settings.get("cashier_user_limits", {})
            if limit == 0:
                user_limits.pop(str(self.target_user_id), None)
                desc = f"Per-user limit **removed** for <@{self.target_user_id}>."
            else:
                user_limits[str(self.target_user_id)] = limit
                desc = (
                    f"Limit for <@{self.target_user_id}> set to **{format_balance(limit, 'real')}**.\n"
                    "This overrides the global limit for this cashier."
                )
            deposit_settings["cashier_user_limits"] = user_limits
        else:
            if limit == 0:
                deposit_settings.pop("cashier_deposit_limit", None)
                desc = "Global cashier limit **removed** \u2014 cashiers can process any amount."
            else:
                deposit_settings["cashier_deposit_limit"] = limit
                desc = (
                    f"Global cashier limit set to **{format_balance(limit, 'real')}**.\n"
                    "Cashiers without a personal limit will be blocked above this amount."
                )

        set_data("server/deposit_settings", deposit_settings)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Cashier Deposit Limit Updated",
                description=desc,
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class MinDepositAmountModal(discord.ui.Modal, title="📉 Minimum Deposit"):
    amount_input = discord.ui.TextInput(
        label="Minimum deposit amount (0 = disabled)",
        placeholder="e.g. 100",
        required=True,
        max_length=15,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.amount_input.value.strip().replace(",", "").replace(".", "")
        try:
            minimum = float(raw)
            if minimum < 0:
                raise ValueError()
        except ValueError:
            return await interaction.response.send_message(
                "❌ Invalid amount. Enter a number ≥ 0.",
                ephemeral=True,
            )

        deposit_settings = get_data("server/deposit_settings") or {}
        if minimum == 0:
            deposit_settings.pop("min_deposit", None)
            desc = "Minimum deposit requirement removed."
        else:
            deposit_settings["min_deposit"] = minimum
            desc = f"Minimum deposit set to **{format_balance(minimum, 'real')}**."

        set_data("server/deposit_settings", deposit_settings)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Minimum Deposit Updated",
                description=desc,
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


def _build_global_balance_cap_embed(user_id: int) -> discord.Embed:
    from modules.balance_cap import get_global_balance_cap, get_balance_cap_settings

    uid = str(user_id)
    cap = get_global_balance_cap()
    settings = get_balance_cap_settings()
    enabled = bool(settings.get("global_enabled"))

    if cap:
        status = t("admin_panel.hubs.global_cap.status_on", user_id=uid, cap=format_balance(cap, "real"))
    else:
        status = t("admin_panel.hubs.global_cap.status_off", user_id=uid)

    return discord.Embed(
        title=t("admin_panel.hubs.global_cap.title", user_id=uid),
        description=t("admin_panel.hubs.global_cap.description", user_id=uid) + "\n\n" + status,
        color=0x9B59B6 if enabled else 0x95A5A6,
    )


class _SetGlobalBalanceCapModal(discord.ui.Modal, title="Global Balance Cap"):
    ceiling_input = discord.ui.TextInput(
        label="Max balance for all users (coins, 0 = off)",
        placeholder="e.g. 1000",
        max_length=12,
        required=True,
    )

    def __init__(self, user_id: int, current: int = 0):
        super().__init__()
        self.user_id = user_id
        if current > 0:
            self.ceiling_input.default = str(current)

    async def on_submit(self, interaction: discord.Interaction):
        from modules.balance_cap import set_global_balance_cap

        raw = self.ceiling_input.value.strip().replace(",", "")
        try:
            ceiling = int(raw)
        except ValueError:
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Invalid",
                    description="Enter a whole number.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
        set_global_balance_cap(ceiling)
        await interaction.response.edit_message(
            embed=_build_global_balance_cap_embed(self.user_id),
            view=GlobalBalanceCapView(self.user_id),
        )


class GlobalBalanceCapView(discord.ui.View):
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="Set / Update", style=discord.ButtonStyle.primary, emoji="✏️", row=0)
    async def set_cap(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.balance_cap import get_global_balance_cap

        current = get_global_balance_cap() or 0
        await interaction.response.send_modal(_SetGlobalBalanceCapModal(interaction.user.id, current))

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.secondary, emoji="🗑️", row=0)
    async def disable_cap(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.balance_cap import set_global_balance_cap

        set_global_balance_cap(0, enabled=False)
        await interaction.response.edit_message(
            embed=_build_global_balance_cap_embed(interaction.user.id),
            view=GlobalBalanceCapView(interaction.user.id),
        )

    @discord.ui.button(label="⬅️ Bot & Tools", style=discord.ButtonStyle.secondary, row=1)
    async def back_hub(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_TOOLS, go_hub

        await go_hub(interaction, HUB_TOOLS, user_id=interaction.user.id)


class BotSettingsView(discord.ui.View):
    """Bot settings view with feature selection"""

    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(BotSettingsSelect())

    @discord.ui.button(label="⬅️ Bot & Araçlar", style=discord.ButtonStyle.secondary, row=1)
    async def back_hub(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_TOOLS, go_hub

        await go_hub(interaction, HUB_TOOLS, user_id=interaction.user.id)


class BotSettingsSelect(discord.ui.Select):
    """Select menu for bot settings features"""

    def __init__(self):
        options = [
            discord.SelectOption(
                label="💰 Balance Emoji",
                description="Set the emoji used for displaying balances",
                emoji="💰",
                value="balance_emoji"
            ),
            discord.SelectOption(
                label="💎 Demo Balance Emoji",
                description="Set the emoji used for displaying demo balances",
                emoji="💎",
                value="demo_balance_emoji"
            ),
            discord.SelectOption(
                label="💸 Rakeback Settings",
                description="Configure rakeback tiers, roles and withdrawal limits",
                emoji="💸",
                value="rakeback_settings"
            ),
            discord.SelectOption(
                label="🎁 Referral Registration Bonus",
                description="Set bonus coins given when registering with a referral code",
                emoji="🎁",
                value="referral_reg_bonus"
            )
        ]

        super().__init__(
            placeholder="Select a setting to configure...",
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle feature selection"""
        if self.values[0] == "balance_emoji":
            view = BalanceEmojiView(interaction, "balance")
            embed = discord.Embed(
                title="💰 Balance Emoji Settings",
                description="Select the emoji to use for displaying balances throughout the bot.",
                color=discord.Color.gold()
            )
            await interaction.response.edit_message(embed=embed, view=view)
        elif self.values[0] == "demo_balance_emoji":
            view = BalanceEmojiView(interaction, "demo")
            embed = discord.Embed(
                title="💎 Demo Balance Emoji Settings",
                description="Select the emoji to use for displaying demo balances throughout the bot.",
                color=discord.Color.blue()
            )
            await interaction.response.edit_message(embed=embed, view=view)
        elif self.values[0] == "rakeback_settings":
            view = RakebackSettingsView()
            embed = _build_rakeback_embed()
            await interaction.response.edit_message(embed=embed, view=view)
        elif self.values[0] == "referral_reg_bonus":
            await interaction.response.send_modal(ReferralRegBonusModal())


class BalanceEmojiView(discord.ui.View):
    """View for balance emoji selection"""

    def __init__(self, interaction=None, emoji_type="balance"):
        super().__init__(timeout=300)
        self.interaction = interaction
        self.emoji_type = emoji_type  # "balance" or "demo"

        # Get current coin emoji based on type
        server_data = get_data("server/server") or {}
        if emoji_type == "demo":
            current_emoji = server_data.get("demo_coin_emoji", "<:mor_elmas:1183873215467110572>")
        else:
            current_emoji = server_data.get("coin_emoji", "<:wl:1087846393722449990>")

        # Get all emojis from guild
        if interaction and interaction.guild:
            emoji_list = [(emoji.name, str(emoji)) for emoji in interaction.guild.emojis]
        else:
            # Fallback to server data if no interaction
            emojis_data = get_data("server/emojis") or {}
            emoji_list = list(emojis_data.items())

        # Create select menus (max 4, 25 options each)
        for i in range(min(4, (len(emoji_list) + 24) // 25)):
            start_idx = i * 25
            end_idx = min((i + 1) * 25, len(emoji_list))
            emoji_chunk = emoji_list[start_idx:end_idx]

            select = CoinEmojiSelect(emoji_chunk, current_emoji, i + 1, emoji_type)
            self.add_item(select)

    @discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=4)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Back to bot settings"""
        view = BotSettingsView(interaction.user.id)
        embed = discord.Embed(
            title="🤖 Bot Settings",
            description="Configure bot-wide settings",
            color=discord.Color.purple()
        )
        await interaction.response.edit_message(embed=embed, view=view)


class CoinEmojiSelect(discord.ui.Select):
    """Select menu for coin emoji"""

    def __init__(self, emoji_chunk, current_emoji, page_num, emoji_type="balance"):
        self.emoji_chunk = emoji_chunk
        self.current_emoji = current_emoji
        self.emoji_type = emoji_type

        options = []
        for emoji_name, emoji_value in emoji_chunk:
            # Check if this emoji is currently selected
            is_default = emoji_value == current_emoji

            # Parse emoji for SelectOption
            emoji_param = None
            try:
                if emoji_value.startswith('<') and emoji_value.endswith('>'):
                    # Custom emoji: extract ID
                    # Format: <a:name:id> or <:name:id>
                    parts = emoji_value.strip('<>').split(':')
                    if len(parts) >= 3:
                        emoji_param = parts[2]  # The ID part
                else:
                    # Unicode emoji: use as string
                    emoji_param = emoji_value
            except:
                # Fallback: don't use emoji
                emoji_param = None

            options.append(
                discord.SelectOption(
                    label=f"{emoji_name}",
                    description=f"Current: {'Yes' if is_default else 'No'}",
                    value=emoji_value,
                    default=is_default,
                    emoji=emoji_value
                )
            )

        super().__init__(
            placeholder=f"Select coin emoji (Page {page_num})",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle emoji selection"""
        selected_emoji = self.values[0]

        # Save to server data based on emoji type
        server_data = get_data("server/server") or {}
        if self.emoji_type == "demo":
            server_data["demo_coin_emoji"] = selected_emoji
            embed_title = "✅ Demo Balance Emoji Updated"
            embed_desc = f"New demo balance emoji: {selected_emoji}"
        else:
            server_data["coin_emoji"] = selected_emoji
            embed_title = "✅ Balance Emoji Updated"
            embed_desc = f"New balance emoji: {selected_emoji}"
        
        set_data("server/server", server_data)

        embed = discord.Embed(
            title=embed_title,
            description=embed_desc,
            color=discord.Color.green()
        )

        # Update the view to show new selection
        new_view = BalanceEmojiView(interaction, self.emoji_type)
        await interaction.response.edit_message(embed=embed, view=new_view)


# ─────────────────────────────────────────────────────────────────────────────
# Rakeback Settings
# ─────────────────────────────────────────────────────────────────────────────

def _build_rakeback_embed() -> discord.Embed:
    """Build the Rakeback Settings overview embed."""
    settings = get_data("server/rakeback_settings") or {}
    tiers = settings.get("tiers", [])
    min_withdrawal = settings.get("min_withdrawal", 100)

    embed = discord.Embed(
        title="💸 Rakeback Settings",
        description="Configure rakeback tiers linked to Discord roles.\n"
                    "Players earn a % of every real bet back as rakeback.",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="📤 Minimum Withdrawal",
        value=format_balance(min_withdrawal, "real"),
        inline=False
    )
    if tiers:
        tier_lines = []
        for i, tier in enumerate(tiers):
            tier_lines.append(
                f"**{i+1}.** <@&{tier['role_id']}> — **{tier['percentage']}%** "
                f"(min wagered: {format_balance(tier['min_wagered'], 'real')})"
            )
        embed.add_field(name="🏅 Tiers", value="\n".join(tier_lines), inline=False)
    else:
        embed.add_field(name="🏅 Tiers", value="No tiers configured yet.", inline=False)
    return embed


class RakebackSettingsView(discord.ui.View):
    """Main view for Rakeback Settings."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="➕ Add Tier", style=discord.ButtonStyle.success, row=0)
    async def add_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddRakebackTierModal())

    @discord.ui.button(label="🗑 Remove Tier", style=discord.ButtonStyle.danger, row=0)
    async def remove_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = get_data("server/rakeback_settings") or {}
        tiers = settings.get("tiers", [])
        if not tiers:
            await interaction.response.send_message(
                embed=create_error_embed("No tiers to remove."), ephemeral=True
            )
            return
        view = RemoveRakebackTierView(tiers)
        embed = discord.Embed(
            title="🗑 Remove Rakeback Tier",
            description="Select a tier to delete.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="📤 Set Min Withdrawal", style=discord.ButtonStyle.primary, row=1)
    async def set_min_withdrawal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RakebackMinWithdrawalModal())

    @discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = BotSettingsView(interaction.user.id)
        embed = discord.Embed(
            title="🤖 Bot Settings",
            description="Configure bot-wide settings",
            color=discord.Color.purple()
        )
        await interaction.response.edit_message(embed=embed, view=view)


class AddRakebackTierModal(discord.ui.Modal, title="Add Rakeback Tier"):
    """Modal with RoleSelect (via Label) + two TextInputs."""

    percentage_input = discord.ui.TextInput(
        label="Rakeback Percentage (%)",
        placeholder="e.g. 2.5",
        required=True,
        max_length=6,
        style=discord.TextStyle.short
    )

    min_wagered_input = discord.ui.TextInput(
        label="Min Total Wagered to qualify",
        placeholder="e.g. 10000  (0 = no requirement)",
        required=True,
        max_length=12,
        style=discord.TextStyle.short
    )

    def __init__(self):
        super().__init__()
        self.role_select = discord.ui.RoleSelect(
            placeholder="Select the role for this tier...",
            min_values=1,
            max_values=1,
        )
        self.role_label = discord.ui.Label(
            text="Select the rakeback role",
            component=self.role_select
        )
        self.add_item(self.role_label)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.role_select.values:
            await interaction.response.send_message(
                embed=create_error_embed("Please select a role!"), ephemeral=True
            )
            return

        role = self.role_select.values[0]

        try:
            percentage = float(self.percentage_input.value)
            min_wagered = int(self.min_wagered_input.value)
            if percentage <= 0 or percentage > 100:
                raise ValueError("Percentage must be between 0 and 100")
            if min_wagered < 0:
                raise ValueError("Min wagered cannot be negative")
        except ValueError as exc:
            await interaction.response.send_message(
                embed=create_error_embed(f"Invalid input: {exc}"), ephemeral=True
            )
            return

        settings = get_data("server/rakeback_settings") or {}
        tiers = settings.get("tiers", [])
        tiers = [t for t in tiers if str(t.get("role_id")) != str(role.id)]
        tiers.append({
            "role_id": str(role.id),
            "role_name": role.name,
            "percentage": percentage,
            "min_wagered": min_wagered
        })
        settings["tiers"] = tiers
        set_data("server/rakeback_settings", settings)

        embed = _build_rakeback_embed()
        embed.description = (
            f"✅ Tier added: <@&{role.id}> → **{percentage}%** "
            f"(min wagered: {format_balance(min_wagered, 'real')})\n\n"
            + (embed.description or "")
        )
        await interaction.response.edit_message(embed=embed, view=RakebackSettingsView())


class RemoveRakebackTierView(discord.ui.View):
    """View for tier removal — select menu + back button."""

    def __init__(self, tiers: list):
        super().__init__(timeout=300)
        self.add_item(RemoveRakebackTierSelect(tiers))

    @discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = _build_rakeback_embed()
        await interaction.response.edit_message(embed=embed, view=RakebackSettingsView())


class RemoveRakebackTierSelect(discord.ui.Select):
    """Select to remove a rakeback tier."""

    def __init__(self, tiers: list):
        options = [
            discord.SelectOption(
                label=f"{tier['role_name']} — {tier['percentage']}%",
                description=f"Min wagered: {tier['min_wagered']}",
                value=str(tier["role_id"])
            )
            for tier in tiers
        ]
        super().__init__(placeholder="Select tier to remove...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        role_id = self.values[0]
        settings = get_data("server/rakeback_settings") or {}
        tiers = settings.get("tiers", [])
        settings["tiers"] = [t for t in tiers if str(t.get("role_id")) != role_id]
        set_data("server/rakeback_settings", settings)

        embed = _build_rakeback_embed()
        await interaction.response.edit_message(embed=embed, view=RakebackSettingsView())


class ReferralRegBonusModal(discord.ui.Modal, title="Referral Registration Bonus"):
    """Modal to set the bonus given to users who register with a referral code."""

    amount_input = discord.ui.TextInput(
        label="Bonus Amount (0 = disabled)",
        placeholder="e.g. 500  (enter 0 to disable)",
        required=True,
        max_length=12,
        style=discord.TextStyle.short,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount_input.value.replace(",", "").replace(".", "").strip())
            if amount < 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                embed=create_error_embed("Please enter a valid non-negative number."),
                ephemeral=True,
            )

        settings = get_data("server/referral_settings") or {}
        settings["welcome_bonus"] = amount
        set_data("server/referral_settings", settings)

        if amount > 0:
            msg = f"Welcome bonus set to **{format_balance(amount, 'real')}**. All new users will receive this on registration."
        else:
            msg = "Welcome bonus **disabled**. No bonus will be given on registration."

        await interaction.response.send_message(
            embed=create_success_embed("✅ Welcome Bonus Updated", msg),
            ephemeral=True,
        )


class RakebackMinWithdrawalModal(discord.ui.Modal, title="Set Minimum Withdrawal"):
    """Modal to change the minimum rakeback withdrawal amount."""

    amount_input = discord.ui.TextInput(
        label="Minimum Withdrawal Amount",
        placeholder="e.g. 100",
        required=True,
        max_length=10,
        style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount_input.value)
            if amount < 0:
                raise ValueError("Amount must be non-negative")
        except ValueError as exc:
            await interaction.response.send_message(
                embed=create_error_embed(f"Invalid input: {exc}"), ephemeral=True
            )
            return

        settings = get_data("server/rakeback_settings") or {}
        settings["min_withdrawal"] = amount
        set_data("server/rakeback_settings", settings)

        embed = _build_rakeback_embed()
        await interaction.response.edit_message(embed=embed, view=RakebackSettingsView())


# ─────────────────────────────────────────────────────────────────────────────
# Bonus Settings Panel
# ─────────────────────────────────────────────────────────────────────────────

def _build_bonus_list_embed_and_view():
    bonus_engine.auto_close_expired_bonuses()
    templates = bonus_engine.get_bonus_templates()

    embed = discord.Embed(
        title="🎁 Deposit Bonus Management",
        color=0x2b2d31,
    )

    if not templates:
        embed.description = (
            "No deposit bonuses configured yet.\n\n"
            "Use **➕ Add Bonus** to create your first bonus template.\n\n"
            "**Bonus Types:**\n"
            "> 🔒 **Fixed** — player must grow their balance to a target (e.g. 2×) to unlock a boosted withdrawal cap (e.g. 4×)\n"
            "> 💰 **Percentage** — player gets N% added on deposit; must wager a total before withdrawing"
        )
    else:
        lines = []
        for bid, tmpl in list(templates.items())[:10]:
            btype = tmpl.get("type", "fixed")
            enabled = tmpl.get("enabled", True)
            dot = "🟢" if enabled else "🔴"
            name = tmpl.get("name", bid)
            desc = tmpl.get("description", "")

            if btype == "fixed":
                wt = tmpl.get("wager_target_multiplier", 2)
                mw = tmpl.get("max_withdrawal_multiplier", 4)
                detail = f"Grow balance to **{wt}× deposit** → withdraw up to **{mw}× deposit**"
            else:
                pct = tmpl.get("percentage", 0)
                wm = tmpl.get("wager_multiplier", 1)
                mw_abs = tmpl.get("max_withdrawal")
                detail = f"**+{pct}%** bonus · wager **{wm}×** · cap: **{format_balance(mw_abs, 'real') if mw_abs else '∞'}**"

            closes_at = tmpl.get("closes_at")
            import time as _t
            if closes_at:
                remaining_s = int(closes_at - _t.time())
                if remaining_s <= 0:
                    closes_str = "⏰ Kapandı"
                else:
                    h, m = divmod(remaining_s // 60, 60)
                    closes_str = f"⏰ {h}s {m}dk sonra kapanır"
            else:
                closes_str = ""
            max_users = int(tmpl.get("max_users", 0))
            used = len(tmpl.get("activated_users", []))
            users_str = f"👥 {used}/{max_users}" if max_users > 0 else (f"👥 {used} kullanıcı" if used else "")
            forfeit_val = tmpl.get("min_balance_forfeit", 0)
            forfeit = f"⚠️ forfeit ≤ {format_balance(forfeit_val, 'real')}" if forfeit_val else ""

            # Requirements
            req_level = int(tmpl.get("req_min_level", 0))
            req_wager = int(tmpl.get("req_min_wagered", 0))
            req_parts = []
            if req_level > 0:
                req_parts.append(f"Lvl≥{req_level}")
            if req_wager > 0:
                req_parts.append(f"Wager≥{format_balance(req_wager, 'real')}")
            req_str = f"🔒 {', '.join(req_parts)}" if req_parts else ""

            meta = "  ·  ".join(filter(None, [closes_str, users_str, forfeit, req_str]))

            lines.append(
                f"{dot} **{name}** `[{btype}]`\n"
                f"┣ {detail}\n"
                + (f"┣ {meta}\n" if meta else "")
                + (f"┗ _{desc}_" if desc else "┗ *(no description)*")
            )

        embed.description = "\n\n".join(lines)
        embed.set_footer(text=f"Vegas Casino · {len(templates)} bonus template(s) configured")

    return embed, BonusListView()


class BonusListView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Add Bonus", style=discord.ButtonStyle.success, emoji="➕", row=0)
    async def add_bonus(self, interaction: discord.Interaction, button: discord.ui.Button):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        embed = discord.Embed(
            title="🎁 Bonus Tipi Seçin",
            description=(
                "**🔒 Fixed (Büyüme Bonusu)**\n"
                "Oyuncu yatırdığı miktarı belirli bir kata ulaştırınca\n"
                "artırılmış çekim limiti kazanır.\n"
                "*Örnek: 25k yatır → 50k yap → 100k çek*\n\n"
                "**💰 Percentage (Yüzde Bonusu)**\n"
                "Yatırılan miktara ek chip eklenir, belirli bir\n"
                "çevrim tamamlanınca çekim açılır.\n"
                "*Örnek: %20 bonus + 5× çevrim*"
            ),
            color=0x2b2d31,
        )
        view = BonusTypeSelectView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Delete Bonus", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def delete_bonus(self, interaction: discord.Interaction, button: discord.ui.Button):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        templates = bonus_engine.get_bonus_templates()
        if not templates:
            return await interaction.response.send_message("No bonuses to delete.", ephemeral=True)
        view = discord.ui.View(timeout=120)
        view.add_item(BonusDeleteSelect(templates))
        await interaction.response.send_message("Select a bonus to delete:", view=view, ephemeral=True)

    @discord.ui.button(label="Toggle Enable", style=discord.ButtonStyle.secondary, emoji="🔄", row=0)
    async def toggle_bonus(self, interaction: discord.Interaction, button: discord.ui.Button):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        templates = bonus_engine.get_bonus_templates()
        if not templates:
            return await interaction.response.send_message("No bonuses to toggle.", ephemeral=True)
        view = discord.ui.View(timeout=120)
        view.add_item(BonusToggleSelect(templates))
        await interaction.response.send_message("Select a bonus to toggle:", view=view, ephemeral=True)

    @discord.ui.button(label="🔓 Reactivate", style=discord.ButtonStyle.secondary, emoji="🔓", row=1)
    async def reactivate_bonus(self, interaction: discord.Interaction, button: discord.ui.Button):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        templates = bonus_engine.get_bonus_templates()
        now = int(time.time())
        inactive = {k: v for k, v in templates.items() if not v.get("enabled", True) or (v.get("closes_at") and now > v["closes_at"])}
        if not inactive:
            return await interaction.response.send_message("No inactive bonuses to reactivate.", ephemeral=True)
        view = discord.ui.View(timeout=120)
        view.add_item(BonusReactivateSelect(inactive))
        await interaction.response.send_message("Select a bonus to reactivate with original duration:", view=view, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="🔙", row=1)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_REWARDS, go_hub

        await go_hub(interaction, HUB_REWARDS, user_id=interaction.user.id)


class BonusDeleteSelect(discord.ui.Select):
    def __init__(self, templates: dict):
        options = [
            discord.SelectOption(label=v.get("name", k)[:100], value=k)
            for k, v in list(templates.items())[:25]
        ]
        super().__init__(placeholder="Select bonus to delete", options=options)

    async def callback(self, interaction: discord.Interaction):
        bid = self.values[0]
        bonus_engine.delete_bonus_template(bid)
        embed, view = _build_bonus_list_embed_and_view()
        await interaction.response.edit_message(embed=embed, view=view)
        await interaction.followup.send("✅ Bonus deleted.", ephemeral=True)


class BonusToggleSelect(discord.ui.Select):
    def __init__(self, templates: dict):
        options = [
            discord.SelectOption(
                label=v.get("name", k)[:100],
                description="✅ Enabled" if v.get("enabled", True) else "❌ Disabled",
                value=k,
            )
            for k, v in list(templates.items())[:25]
        ]
        super().__init__(placeholder="Select bonus to toggle", options=options)

    async def callback(self, interaction: discord.Interaction):
        bid = self.values[0]
        new_state = bonus_engine.toggle_bonus_template(bid)
        label = "enabled" if new_state else "disabled"
        embed, view = _build_bonus_list_embed_and_view()
        await interaction.response.edit_message(embed=embed, view=view)
        await interaction.followup.send(f"✅ Bonus {label}.", ephemeral=True)


class BonusReactivateSelect(discord.ui.Select):
    def __init__(self, templates: dict):
        options = []
        for k, v in list(templates.items())[:25]:
            expire_h = int(v.get("expire_hours", 0))
            desc = f"disabled · expire_hours: {expire_h}h" if expire_h else "disabled · no expiry"
            options.append(discord.SelectOption(label=v.get("name", k)[:100], description=desc[:100], value=k))
        if not options:
            options.append(discord.SelectOption(label="No inactive bonuses", value="_none_"))
        super().__init__(placeholder="Select bonus to reactivate", options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "_none_":
            return await interaction.response.send_message("No inactive bonuses.", ephemeral=True)
        bid = self.values[0]
        ok, err = bonus_engine.reactivate_bonus_template(bid)
        if not ok:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)
        embed, view = _build_bonus_list_embed_and_view()
        await interaction.response.edit_message(embed=embed, view=view)
        await interaction.followup.send("✅ Bonus reactivated with original duration.", ephemeral=True)


class BonusTypeSelectView(discord.ui.View):
    """Ephemeral view shown after clicking 'Add Bonus' — lets admin pick the type."""
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        placeholder="🎯 Bonus tipini seçin...",
        options=[
            discord.SelectOption(
                label="🔒 Fixed — Büyüme Bonusu",
                description="Bakiyeni hedefe ulaştır → artırılmış çekim limiti kazan",
                value="fixed",
                emoji="🔒",
            ),
            discord.SelectOption(
                label="💰 Percentage — Yüzde Bonusu",
                description="Yatırıma ek chip al, çevrim tamamla, çek",
                value="percentage",
                emoji="💰",
            ),
        ],
    )
    async def type_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        btype = select.values[0]
        if btype == "fixed":
            await interaction.response.send_modal(AddFixedBonusModal())
        else:
            await interaction.response.send_modal(AddPercentageBonusModal())


class AddFixedBonusModal(discord.ui.Modal, title="🔒 Add Fixed Bonus"):
    name_input = discord.ui.TextInput(
        label="Bonus Name",
        placeholder="e.g. Welcome Bonus",
        required=True, max_length=80,
    )
    multipliers_input = discord.ui.TextInput(
        label="Balance Target × , Max Withdrawal ×",
        placeholder="2,4  →  grow 2×, withdraw up to 4×  |  2.5,5",
        required=True, max_length=20,
    )
    limits_input = discord.ui.TextInput(
        label="Expire (hrs), Max Users, Min Forfeit",
        placeholder="48,100,500  →  closes in 48hrs, 100 users, ≤500 forfeit",
        required=False, max_length=40,
    )
    description_input = discord.ui.TextInput(
        label="Description (shown to users)",
        placeholder="e.g. Grow your balance 2×, earn up to 4× withdrawal!",
        required=False, max_length=120,
        style=discord.TextStyle.paragraph,
    )

    async def on_submit(self, interaction: discord.Interaction):
        parts = [p.strip() for p in self.multipliers_input.value.split(",")]
        try:
            wager_target_mult = float(parts[0])
            max_withdrawal_mult = float(parts[1]) if len(parts) > 1 and parts[1] else wager_target_mult * 2
        except (ValueError, IndexError):
            return await interaction.response.send_message(
                "❌ Invalid format. Example: `2,4` (2× target, 4× withdrawal)", ephemeral=True
            )

        expire_hours, max_users, forfeit = _parse_limits(self.limits_input.value)

        bonus_engine.create_bonus_template(
            name=self.name_input.value.strip(),
            btype="fixed",
            wager_target_multiplier=wager_target_mult,
            max_withdrawal_multiplier=max_withdrawal_mult,
            expire_hours=expire_hours,
            max_users=max_users,
            min_balance_forfeit=forfeit,
            description=self.description_input.value.strip(),
        )
        embed, view = _build_bonus_list_embed_and_view()
        await interaction.response.edit_message(embed=embed, view=view)
        await interaction.followup.send(
            f"✅ **Fixed bonus** created! ({wager_target_mult}× target → {max_withdrawal_mult}× withdrawal)",
            ephemeral=True,
        )


class AddPercentageBonusModal(discord.ui.Modal, title="💰 Add Percentage Bonus"):
    name_input = discord.ui.TextInput(
        label="Bonus Name",
        placeholder="e.g. 20% Reload Bonus",
        required=True, max_length=80,
    )
    pct_wager_input = discord.ui.TextInput(
        label="% , Wager Multiplier , Max Withdrawal (comma)",
        placeholder="20,5  or  20,5,1000000  →  20% bonus + 5× wager",
        required=True, max_length=40,
    )
    limits_input = discord.ui.TextInput(
        label="Expire (hrs), Max Users, Min Forfeit",
        placeholder="48,100,500  →  closes in 48hrs, 100 users, ≤500 forfeit",
        required=False, max_length=40,
    )
    description_input = discord.ui.TextInput(
        label="Description (shown to users)",
        placeholder="e.g. Get 20% extra chips + unlock withdrawal after 5× wager!",
        required=False, max_length=120,
        style=discord.TextStyle.paragraph,
    )

    async def on_submit(self, interaction: discord.Interaction):
        parts = [p.strip() for p in self.pct_wager_input.value.split(",")]
        try:
            pct = float(parts[0])
            wager_mult = float(parts[1])
            max_withdrawal = int(parts[2]) if len(parts) > 2 and parts[2] else None
        except (ValueError, IndexError):
            return await interaction.response.send_message(
                "❌ Invalid format. Example: `20,5` or `20,5,1000000`", ephemeral=True
            )

        expire_hours, max_users, forfeit = _parse_limits(self.limits_input.value)

        bonus_engine.create_bonus_template(
            name=self.name_input.value.strip(),
            btype="percentage",
            percentage=pct,
            wager_multiplier=wager_mult,
            max_withdrawal=max_withdrawal,
            expire_hours=expire_hours,
            max_users=max_users,
            min_balance_forfeit=forfeit,
            description=self.description_input.value.strip(),
        )
        embed, view = _build_bonus_list_embed_and_view()
        await interaction.response.edit_message(embed=embed, view=view)
        await interaction.followup.send(
            f"✅ **{pct}% bonus** created! ({wager_mult}× wager requirement)",
            ephemeral=True,
        )


def _parse_limits(raw: str) -> tuple[int, int, int]:
    """Parse 'expire_hours,max_users,min_balance_forfeit'. Returns (hours, max_users, forfeit)."""
    expire_hours, max_users, forfeit = 0, 0, 0
    raw = raw.strip()
    if raw:
        parts = [p.strip() for p in raw.split(",")]
        try:
            expire_hours = int(parts[0]) if len(parts) > 0 and parts[0] else 0
            max_users    = int(parts[1]) if len(parts) > 1 and parts[1] else 0
            forfeit      = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        except ValueError:
            pass
    return expire_hours, max_users, forfeit


# ─────────────────────────────────────────────────────────────────────────────
# Promo Code Management (Admin Panel)
# ─────────────────────────────────────────────────────────────────────────────

import modules.promo as promo_engine


def _build_promo_list_embed() -> discord.Embed:
    """Build the promo codes overview embed."""
    promo_engine.auto_close_expired_promos()
    codes = promo_engine.get_promo_codes()
    embed = discord.Embed(
        title="🎟️  Promo Codes",
        description=(
            "Manage promo and free-bet codes. Users can redeem these codes from their "
            "private room to claim balance rewards or free game rounds."
        ),
        color=0x9b59b6,
    )

    if not codes:
        embed.add_field(name="📋 No Codes", value="No promo codes have been created yet.", inline=False)
    else:
        now = int(time.time())
        for code, info in list(codes.items())[:20]:
            ptype = info.get("type", "balance")
            enabled = info.get("enabled", True)
            uses = len(info.get("used_by", []))
            max_u = int(info.get("max_uses", 0))
            exp = info.get("expires_at")
            expired = exp and now > exp

            if ptype == "freegame":
                reward_str = f"🎮 {info.get('rounds', 0)}× {info.get('game', '?').title()} @ {format_balance(info.get('bet_amount', 0), 'real')}/round"
            else:
                reward_str = f"💰 {format_balance(info.get('reward_amount', 0), 'real')}"

            wager_str = f"{info.get('wager_multiplier', 1)}× wager"
            uses_str = f"{uses}/{max_u}" if max_u > 0 else str(uses)

            if expired and enabled:
                status_icon = "⏰"  # expired but still showing as enabled (shouldn't happen after auto_close)
            elif not enabled:
                status_icon = "❌"
            else:
                status_icon = "✅"

            expire_str = f"<t:{exp}:R>" if exp else "Never"

            # Requirements
            req_parts = []
            req_level = int(info.get("req_min_level", 0))
            req_wager = int(info.get("req_min_wagered", 0))
            if req_level > 0:
                req_parts.append(f"Lvl≥{req_level}")
            if req_wager > 0:
                req_parts.append(f"Wager≥{format_balance(req_wager, 'real')}")
            status_req = (info.get("req_status_contains") or "").strip()
            if status_req:
                req_parts.append(f"Status∋{status_req}")
            dep_days = int(info.get("req_deposit_within_days", 0) or 0)
            if dep_days > 0:
                req_parts.append(f"Dep≤{dep_days}d")
            req_str = f"🔒 {', '.join(req_parts)}" if req_parts else ""

            value_lines = [f"{reward_str}  ·  {wager_str}", f"Uses: **{uses_str}**  ·  Expires: {expire_str}"]
            if req_str:
                value_lines.append(req_str)
            promo_min_wd = int(info.get("promo_min_withdrawal", 0))
            promo_max_wd = int(info.get("promo_max_withdrawal", 0))
            if promo_min_wd > 0 or promo_max_wd > 0:
                wd_parts = []
                if promo_min_wd > 0:
                    wd_parts.append(f"min {format_balance(promo_min_wd, 'real')}")
                if promo_max_wd > 0:
                    wd_parts.append(f"max {format_balance(promo_max_wd, 'real')}")
                value_lines.append("💳 " + " · ".join(wd_parts))

            embed.add_field(
                name=f"{status_icon} `{code}`",
                value="\n".join(value_lines),
                inline=True,
            )

    embed.set_footer(text="Vegas Casino | Promo Code Management")
    return embed


# ─── Promo creation — multi-step Select → Modal flow ────────────────────────


def _parse_promo_requirements_row(raw: str) -> tuple[int, int, int, int, int, int]:
    """Min lvl, min wager, forfeit, min WD, max WD, deposit-within-days."""
    parts = [p.strip() for p in (raw or "0,0,0,0,0,0").split(",")]
    try:
        req_min_level = int(parts[0]) if parts and parts[0] else 0
        req_min_wagered = (
            int(parts[1].replace(",", "")) if len(parts) > 1 and parts[1] else 0
        )
        min_balance_forfeit = (
            int(parts[2].replace(",", "")) if len(parts) > 2 and parts[2] else 0
        )
        promo_min_wd = (
            int(parts[3].replace(",", "")) if len(parts) > 3 and parts[3] else 0
        )
        promo_max_wd = (
            int(parts[4].replace(",", "")) if len(parts) > 4 and parts[4] else 0
        )
        req_deposit_days = int(parts[5]) if len(parts) > 5 and parts[5] else 0
    except ValueError:
        return 0, 0, 0, 0, 0, 0
    return (
        req_min_level,
        req_min_wagered,
        min_balance_forfeit,
        promo_min_wd,
        promo_max_wd,
        max(0, req_deposit_days),
    )


def _format_promo_withdraw_limits(info: dict) -> str | None:
    """Min/max withdrawal after wager completes; None if no limits set."""
    promo_min_wd = int(info.get("promo_min_withdrawal", 0) or 0)
    promo_max_wd = int(info.get("promo_max_withdrawal", 0) or 0)
    if promo_min_wd <= 0 and promo_max_wd <= 0:
        return None
    parts = []
    if promo_min_wd > 0:
        parts.append(f"Min **{format_balance(promo_min_wd, 'real')}**")
    if promo_max_wd > 0:
        parts.append(f"Max **{format_balance(promo_max_wd, 'real')}**")
    return " · ".join(parts)


def _build_promo_announcement_embed(code: str, info: dict) -> discord.Embed:
    """Public promo announcement embed (posted with @everyone)."""
    ptype = info.get("type", "balance")
    wager = info.get("wager_multiplier", 1)
    max_u = int(info.get("max_uses", 0))
    exp = info.get("expires_at")
    expire_str = f"<t:{exp}:R>" if exp else "Never"

    if ptype == "freegame":
        reward = (
            f"🎮 **{info.get('rounds', 0)}×** {info.get('game', '?').title()} "
            f"@ **{format_balance(info.get('bet_amount', 0), 'real')}**/round"
        )
    else:
        reward = f"💰 **{format_balance(info.get('reward_amount', 0), 'real')}** bonus"

    desc_lines = [
        f"Use code **`{code}`** in your **private room** → Promo Code.",
        "",
        reward,
        f"🔄 Wager requirement: **{wager}×**",
    ]
    wd_limits = _format_promo_withdraw_limits(info)
    if wd_limits:
        desc_lines.append(f"💳 Withdraw after wager: {wd_limits}")

    embed = discord.Embed(
        title="🎟️ New Promo Code",
        description="\n".join(desc_lines),
        color=0x9B59B6,
    )
    details_lines = [
        f"**Uses:** {'Unlimited' if max_u <= 0 else max_u}",
        f"**Expires:** {expire_str}",
    ]
    min_forfeit = int(info.get("min_balance_forfeit", 0) or 0)
    if min_forfeit > 0:
        details_lines.append(
            f"**Forfeit below:** {format_balance(min_forfeit, 'real')}"
        )
    embed.add_field(
        name="Details",
        value="\n".join(details_lines),
        inline=False,
    )
    req_parts = []
    if int(info.get("req_min_level", 0)) > 0:
        req_parts.append(f"Level ≥ {info['req_min_level']}")
    if int(info.get("req_min_wagered", 0)) > 0:
        req_parts.append(f"Wagered ≥ {format_balance(info['req_min_wagered'], 'real')}")
    dep_days = int(info.get("req_deposit_within_days", 0) or 0)
    if dep_days > 0:
        req_parts.append(f"Deposit within last **{dep_days}** days")
    status_req = (info.get("req_status_contains") or "").strip()
    if status_req:
        req_parts.append(f"Custom status must include one of: **{status_req}**")
    if req_parts:
        embed.add_field(name="Requirements", value=" · ".join(req_parts), inline=False)
    desc_text = (info.get("description") or "").strip()
    if desc_text:
        embed.add_field(name="Note", value=desc_text, inline=False)
    embed.set_footer(text="Vegas Casino • Redeem in your private room")
    return embed


class PromoPublishChannelSelect(discord.ui.ChannelSelect):
    """Pick a channel; publishes the promo with @everyone."""

    def __init__(self, code: str):
        self.code = code.upper()
        super().__init__(
            placeholder="Select channel to publish (@everyone)…",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
            custom_id=f"promo_publish_ch:{code}",
        )

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", user_id=str(interaction.user.id)),
                ephemeral=True,
            )
        channel = self.values[0].resolve() or self.values[0]
        if not interaction.guild:
            return await interaction.response.send_message("❌ Guild only.", ephemeral=True)
        tmpl = promo_engine.get_promo_code(self.code)
        if not tmpl:
            return await interaction.response.send_message(
                f"❌ Code `{self.code}` not found.", ephemeral=True
            )
        try:
            await channel.send(
                content="@everyone",
                embed=_build_promo_announcement_embed(self.code, tmpl),
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
        except discord.HTTPException as e:
            return await interaction.response.send_message(
                f"❌ Could not publish: {e}", ephemeral=True
            )
        wd_note = _format_promo_withdraw_limits(tmpl)
        pub_desc = f"Promo **`{self.code}`** announced in {channel.mention} with @everyone."
        if wd_note:
            pub_desc += f"\n💳 Withdraw limits in post: {wd_note}"
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Published",
                description=pub_desc,
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


def _promo_status_req_field_value(code: str) -> str:
    tmpl = promo_engine.get_promo_code(code)
    if not tmpl:
        return "—"
    raw = (tmpl.get("req_status_contains") or "").strip()
    return raw if raw else "*(disabled — leave empty in Set Status Req)*"


class PromoStatusReqModal(discord.ui.Modal, title="📌 Custom Status Requirement"):
    keywords_input = discord.ui.TextInput(
        label="Status must contain (comma-separated)",
        placeholder="e.g. vegas, promo  — leave empty to disable",
        max_length=200,
        required=False,
        style=discord.TextStyle.short,
    )

    def __init__(self, code: str):
        super().__init__()
        self.code = code.upper()
        tmpl = promo_engine.get_promo_code(self.code) or {}
        existing = (tmpl.get("req_status_contains") or "").strip()
        if existing:
            self.keywords_input.default = existing

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.keywords_input.value or "").strip()
        ok, err = promo_engine.update_promo_code(
            self.code, req_status_contains=raw
        )
        if not ok:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Error", description=err, color=discord.Color.red()),
                ephemeral=True,
            )
        if interaction.message and interaction.message.embeds:
            src = interaction.message.embeds[0]
            embed = discord.Embed(
                title=src.title,
                description=src.description,
                color=src.color,
            )
            if src.footer:
                embed.set_footer(text=src.footer.text)
            for field in src.fields:
                if field.name != "📌 Status Requirement":
                    embed.add_field(name=field.name, value=field.value, inline=field.inline)
            embed.add_field(
                name="📌 Status Requirement",
                value=_promo_status_req_field_value(self.code),
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=PromoCodeManageView(self.code))
            return
        if raw:
            desc = f"Status must contain one of: **{promo_engine.normalize_status_keywords(raw)}**"
        else:
            desc = "Custom status requirement **disabled**."
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"✅ `{self.code}` Status Requirement",
                description=desc + "\n\nPublish the promo to show this in the announcement.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class PromoCodeManageView(discord.ui.View):
    """Publish, status requirement, and edit for one promo code."""

    def __init__(self, code: str):
        super().__init__(timeout=300)
        self.code = code.upper()
        self.add_item(PromoPublishChannelSelect(self.code))

        edit_btn = discord.ui.Button(
            label="✏️ Edit Parameters",
            style=discord.ButtonStyle.primary,
            row=1,
            custom_id=f"promo_edit_params:{self.code}",
        )
        edit_btn.callback = self._on_edit
        self.add_item(edit_btn)

        status_btn = discord.ui.Button(
            label="📌 Set Status Req",
            style=discord.ButtonStyle.secondary,
            row=1,
            custom_id=f"promo_status_req:{self.code}",
        )
        status_btn.callback = self._on_status_req
        self.add_item(status_btn)

    async def _on_edit(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", user_id=str(interaction.user.id)),
                ephemeral=True,
            )
        await interaction.response.send_modal(EditPromoModal(self.code))

    async def _on_status_req(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", user_id=str(interaction.user.id)),
                ephemeral=True,
            )
        await interaction.response.send_modal(PromoStatusReqModal(self.code))


class PromoCreatedView(PromoCodeManageView):
    """Shown after creating a promo — same controls as manage view."""
    pass


async def _send_promo_created_success(interaction: discord.Interaction, embed: discord.Embed, code: str) -> None:
    """Ephemeral success + channel picker to publish with @everyone."""
    embed.add_field(
        name="📢 Publish",
        value="Select a channel below to announce this code with **@everyone**.",
        inline=False,
    )
    embed.add_field(
        name="📌 Status Requirement",
        value=_promo_status_req_field_value(code),
        inline=False,
    )
    await interaction.response.send_message(
        embed=embed,
        view=PromoCodeManageView(code),
        ephemeral=True,
    )


_PROMO_GAMES = [
    ("Mines",    "mines"),
    ("Slot",     "slot"),
    ("Dice",     "dice"),
    ("CoinFlip", "coinflip"),
    ("Roulette", "roulette"),
    ("HiLo",     "hilo"),
    ("Limbo",    "limbo"),
    ("Slide",    "slide"),
    ("Market Predict", "market_predict"),
    ("Jackpot",  "jackpot"),
    ("Crystals", "crystals"),
    ("Towers",   "towers"),
]


class CreateBalancePromoModal(discord.ui.Modal, title="💰 Create Balance Promo"):
    """Admin modal — balance promo code."""

    code_input = discord.ui.TextInput(
        label="Code",
        placeholder="e.g. WELCOME500",
        min_length=2,
        max_length=20,
        style=discord.TextStyle.short,
    )
    amount_input = discord.ui.TextInput(
        label="Bonus Amount (coins)",
        placeholder="e.g. 500",
        max_length=12,
        style=discord.TextStyle.short,
    )
    wager_input = discord.ui.TextInput(
        label="Wager Multiplier",
        placeholder="e.g. 1",
        default="1",
        max_length=6,
        style=discord.TextStyle.short,
    )
    uses_expire_input = discord.ui.TextInput(
        label="Max Uses, Expire Hours  (0 = unlimited/never)",
        placeholder="e.g. 100, 72   or   0, 0",
        default="0, 0",
        max_length=20,
        required=False,
        style=discord.TextStyle.short,
    )
    requirements_input = discord.ui.TextInput(
        label="Lvl, Wager, Forfeit, MinWD, MaxWD, DepDays",
        placeholder="e.g. 0, 0, 0, 100, 5000, 7  (DepDays=0 off)",
        default="0, 0, 0, 0, 0, 0",
        max_length=60,
        required=False,
        style=discord.TextStyle.short,
    )

    async def on_submit(self, interaction: discord.Interaction):
        code = self.code_input.value.strip().upper()

        try:
            reward_amount = int(self.amount_input.value.strip().replace(",", ""))
            if reward_amount <= 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Invalid Amount", description="Enter a positive coin amount.", color=discord.Color.red()),
                ephemeral=True,
            )

        try:
            wager_mult = float(self.wager_input.value.strip())
        except ValueError:
            wager_mult = 1.0

        parts = [p.strip() for p in (self.uses_expire_input.value or "0,0").split(",")]
        try:
            max_uses     = int(parts[0]) if parts[0] else 0
            expire_hours = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        except ValueError:
            max_uses, expire_hours = 0, 0

        (
            req_min_level,
            req_min_wagered,
            min_balance_forfeit,
            promo_min_wd,
            promo_max_wd,
            req_deposit_days,
        ) = _parse_promo_requirements_row(self.requirements_input.value)

        expires_at = int(time.time()) + expire_hours * 3600 if expire_hours > 0 else None

        desc = ""
        ok, err = promo_engine.create_promo_code(
            code=code, promo_type="balance",
            reward_amount=reward_amount,
            wager_multiplier=wager_mult, max_uses=max_uses,
            expires_at=expires_at, expire_hours=expire_hours,
            description=desc,
            created_by=str(interaction.user.id),
            req_min_level=req_min_level,
            req_min_wagered=req_min_wagered,
            min_balance_forfeit=min_balance_forfeit,
            promo_min_withdrawal=promo_min_wd,
            promo_max_withdrawal=promo_max_wd,
            req_deposit_within_days=req_deposit_days,
        )
        if not ok:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Error", description=err, color=discord.Color.red()),
                ephemeral=True,
            )

        expire_line = f"<t:{expires_at}:R>" if expires_at else "Never"
        embed = discord.Embed(title="✅ Balance Promo Created", color=discord.Color.green())
        embed.add_field(name="🎟️ Code",       value=f"`{code}`",                                   inline=True)
        embed.add_field(name="💰 Reward",      value=format_balance(reward_amount, 'real'),          inline=True)
        embed.add_field(name="🔄 Wager Req.",  value=f"{wager_mult}×",                              inline=True)
        embed.add_field(name="👥 Max Uses",    value=str(max_uses) if max_uses else "Unlimited",    inline=True)
        embed.add_field(name="⏰ Expires",     value=expire_line,                                   inline=True)
        if req_min_level > 0:
            embed.add_field(name="🔒 Min Level",   value=str(req_min_level), inline=True)
        if req_min_wagered > 0:
            embed.add_field(name="🔒 Min Wagered",  value=format_balance(req_min_wagered, 'real'), inline=True)
        if min_balance_forfeit > 0:
            embed.add_field(name="🚨 Forfeit Below", value=format_balance(min_balance_forfeit, 'real'), inline=True)
        if promo_min_wd > 0:
            embed.add_field(name="📉 Min Withdraw", value=format_balance(promo_min_wd, 'real'), inline=True)
        if promo_max_wd > 0:
            embed.add_field(name="📈 Max Withdraw", value=format_balance(promo_max_wd, 'real'), inline=True)
        if req_deposit_days > 0:
            embed.add_field(
                name="📥 Deposit Req.",
                value=f"Within last **{req_deposit_days}** days",
                inline=True,
            )
        embed.set_footer(text="Vegas Casino | Promo Code Management")
        await _send_promo_created_success(interaction, embed, code)


class CreateFreegamePromoModal(discord.ui.Modal):
    """Admin modal — freegame promo code (game pre-selected via Select)."""

    code_input = discord.ui.TextInput(
        label="Code",
        placeholder="e.g. FREEMINES10",
        min_length=2,
        max_length=20,
        style=discord.TextStyle.short,
    )
    rounds_input = discord.ui.TextInput(
        label="Number of Free Rounds",
        placeholder="e.g. 10",
        max_length=4,
        style=discord.TextStyle.short,
    )
    bet_input = discord.ui.TextInput(
        label="Bet Per Round (coins)",
        placeholder="e.g. 50",
        max_length=12,
        style=discord.TextStyle.short,
    )
    opts_input = discord.ui.TextInput(
        label="Wager Mult, Max Uses, Expire Hours",
        placeholder="e.g. 1, 100, 72   or   1, 0, 0",
        default="1, 0, 0",
        max_length=30,
        required=False,
        style=discord.TextStyle.short,
    )
    requirements_input = discord.ui.TextInput(
        label="Lvl, Wager, Forfeit, MinWD, MaxWD, DepDays",
        placeholder="e.g. 0, 0, 0, 100, 5000, 7  (DepDays=0 off)",
        default="0, 0, 0, 0, 0, 0",
        max_length=60,
        required=False,
        style=discord.TextStyle.short,
    )

    def __init__(self, game: str):
        super().__init__(title=f"🎮 Free Rounds — {game.title()}")
        self.game = game

    async def on_submit(self, interaction: discord.Interaction):
        code = self.code_input.value.strip().upper()

        try:
            rounds = int(self.rounds_input.value.strip())
            if rounds <= 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Invalid Rounds", description="Enter a positive integer.", color=discord.Color.red()),
                ephemeral=True,
            )

        try:
            bet_amt = int(self.bet_input.value.strip().replace(",", ""))
            if bet_amt <= 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Invalid Bet", description="Enter a positive coin amount.", color=discord.Color.red()),
                ephemeral=True,
            )

        parts = [p.strip() for p in (self.opts_input.value or "1,0,0").split(",")]
        try:
            wager_mult   = float(parts[0]) if parts[0] else 1.0
            max_uses     = int(parts[1])   if len(parts) > 1 and parts[1] else 0
            expire_hours = int(parts[2])   if len(parts) > 2 and parts[2] else 0
        except (ValueError, IndexError):
            wager_mult, max_uses, expire_hours = 1.0, 0, 0

        (
            req_min_level,
            req_min_wagered,
            min_balance_forfeit,
            promo_min_wd,
            promo_max_wd,
            req_deposit_days,
        ) = _parse_promo_requirements_row(self.requirements_input.value)

        expires_at = int(time.time()) + expire_hours * 3600 if expire_hours > 0 else None

        ok, err = promo_engine.create_promo_code(
            code=code, promo_type="freegame",
            game=self.game, rounds=rounds, bet_amount=bet_amt,
            wager_multiplier=wager_mult, max_uses=max_uses,
            expires_at=expires_at, expire_hours=expire_hours,
            description="",
            created_by=str(interaction.user.id),
            req_min_level=req_min_level,
            req_min_wagered=req_min_wagered,
            min_balance_forfeit=min_balance_forfeit,
            promo_min_withdrawal=promo_min_wd,
            promo_max_withdrawal=promo_max_wd,
            req_deposit_within_days=req_deposit_days,
        )
        if not ok:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Error", description=err, color=discord.Color.red()),
                ephemeral=True,
            )

        expire_line = f"<t:{expires_at}:R>" if expires_at else "Never"
        embed = discord.Embed(title="✅ Free Rounds Promo Created", color=discord.Color.green())
        embed.add_field(name="🎟️ Code",       value=f"`{code}`",                                   inline=True)
        embed.add_field(name="🎮 Game",        value=self.game.title(),                              inline=True)
        embed.add_field(name="🎲 Rounds",      value=str(rounds),                                   inline=True)
        embed.add_field(name="💸 Bet/Round",   value=format_balance(bet_amt, 'real'),               inline=True)
        embed.add_field(name="🔄 Wager Req.",  value=f"{wager_mult}×",                              inline=True)
        embed.add_field(name="👥 Max Uses",    value=str(max_uses) if max_uses else "Unlimited",    inline=True)
        embed.add_field(name="⏰ Expires",     value=expire_line,                                   inline=True)
        if req_min_level > 0:
            embed.add_field(name="🔒 Min Level",   value=str(req_min_level), inline=True)
        if req_min_wagered > 0:
            embed.add_field(name="🔒 Min Wagered",  value=format_balance(req_min_wagered, 'real'), inline=True)
        if min_balance_forfeit > 0:
            embed.add_field(name="🚨 Forfeit Below", value=format_balance(min_balance_forfeit, 'real'), inline=True)
        if promo_min_wd > 0:
            embed.add_field(name="📉 Min Withdraw", value=format_balance(promo_min_wd, 'real'), inline=True)
        if promo_max_wd > 0:
            embed.add_field(name="📈 Max Withdraw", value=format_balance(promo_max_wd, 'real'), inline=True)
        if req_deposit_days > 0:
            embed.add_field(
                name="📥 Deposit Req.",
                value=f"Within last **{req_deposit_days}** days",
                inline=True,
            )
        embed.set_footer(text="Vegas Casino | Promo Code Management")
        await _send_promo_created_success(interaction, embed, code)


class PromoGameSelect(discord.ui.Select):
    """Step 2 — pick which game the free rounds apply to."""

    def __init__(self):
        options = [
            discord.SelectOption(label=name, value=val, emoji="🎮")
            for name, val in _PROMO_GAMES
        ]
        super().__init__(
            placeholder="Select game for free rounds…",
            options=options,
            custom_id="promo:game_select",
        )

    async def callback(self, interaction: discord.Interaction):
        game = self.values[0]
        await interaction.response.send_modal(CreateFreegamePromoModal(game))


class PromoGameSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(PromoGameSelect())


class PromoTypeSelect(discord.ui.Select):
    """Step 1 — pick balance or freegame."""

    def __init__(self):
        super().__init__(
            placeholder="Select promo type…",
            options=[
                discord.SelectOption(
                    label="💰 Balance Bonus",
                    value="balance",
                    description="Add coins directly to user balance",
                    emoji="💰",
                ),
                discord.SelectOption(
                    label="🎮 Free Game Rounds",
                    value="freegame",
                    description="Free spins for a specific game",
                    emoji="🎮",
                ),
            ],
            custom_id="promo:type_select",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "balance":
            await interaction.response.send_modal(CreateBalancePromoModal())
        else:
            embed = discord.Embed(
                title="🎮  Select Game",
                description="Which game should the free rounds be for?",
                color=0x9b59b6,
            )
            await interaction.response.edit_message(embed=embed, view=PromoGameSelectView())


class PromoTypeSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(PromoTypeSelect())


class PromoCodeActionSelect(discord.ui.Select):
    """Select a promo code for toggle/delete action."""

    def __init__(self, action: str):
        self.action = action
        codes = promo_engine.get_promo_codes()
        options = []
        for code, info in list(codes.items())[:25]:
            enabled = info.get("enabled", True)
            ptype   = info.get("type", "balance")
            icon    = "✅" if enabled else "❌"
            desc    = f"{'freegame' if ptype == 'freegame' else 'balance'} · uses: {len(info.get('used_by', []))}"
            options.append(discord.SelectOption(
                label=f"{icon} {code}",
                description=desc[:100],
                value=code,
                emoji="🎟️",
            ))

        if not options:
            options.append(discord.SelectOption(label="No codes available", value="_none_"))

        super().__init__(
            placeholder=f"Select a code to {action}...",
            options=options,
            custom_id=f"promo:{action}_select",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "_none_":
            return await interaction.response.send_message("No promo codes found.", ephemeral=True)

        code = self.values[0]
        if self.action == "toggle":
            new_state = promo_engine.toggle_promo_code(code)
            state_str = "✅ Enabled" if new_state else "❌ Disabled"
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=f"🔄 `{code}` {state_str}",
                    color=discord.Color.green() if new_state else discord.Color.orange(),
                ),
                ephemeral=True,
            )
        elif self.action == "delete":
            promo_engine.delete_promo_code(code)
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=f"🗑️ `{code}` Deleted",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )


class EditPromoModal(discord.ui.Modal, title="✏️ Edit Promo Code"):
    """Admin modal — edit an existing promo code's parameters."""

    wager_input = discord.ui.TextInput(
        label="Wager Multiplier",
        placeholder="e.g. 1",
        max_length=6,
        required=False,
        style=discord.TextStyle.short,
    )
    uses_input = discord.ui.TextInput(
        label="Max Uses (0 = unlimited)",
        placeholder="e.g. 100",
        max_length=10,
        required=False,
        style=discord.TextStyle.short,
    )
    expire_hours_input = discord.ui.TextInput(
        label="New Expire Hours from now (0 = never)",
        placeholder="e.g. 72  (0 keeps current expiry)",
        max_length=6,
        required=False,
        style=discord.TextStyle.short,
    )
    requirements_input = discord.ui.TextInput(
        label="Lvl, Wager, Forfeit, MinWD, MaxWD, DepDays",
        placeholder="e.g. 0, 0, 0, 100, 5000, 7  (DepDays=0 off)",
        max_length=60,
        required=False,
        style=discord.TextStyle.short,
    )
    desc_input = discord.ui.TextInput(
        label="Description (optional)",
        placeholder="Leave blank to keep existing description",
        max_length=100,
        required=False,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, code: str):
        super().__init__(title=f"✏️ Edit: {code}")
        self.code = code

    async def on_submit(self, interaction: discord.Interaction):
        updates = {}

        wager_val = self.wager_input.value.strip()
        if wager_val:
            try:
                updates["wager_multiplier"] = float(wager_val)
            except ValueError:
                pass

        uses_val = self.uses_input.value.strip()
        if uses_val:
            try:
                updates["max_uses"] = int(uses_val)
            except ValueError:
                pass

        expire_val = self.expire_hours_input.value.strip()
        if expire_val:
            try:
                hrs = int(expire_val)
                updates["expire_hours"] = hrs
                if hrs > 0:
                    updates["expires_at"] = int(time.time()) + hrs * 3600
                else:
                    updates["expires_at"] = None
            except ValueError:
                pass

        req_val = self.requirements_input.value.strip()
        if req_val:
            req_parts = [p.strip() for p in req_val.split(",")]
            field_names = (
                "req_min_level",
                "req_min_wagered",
                "min_balance_forfeit",
                "promo_min_withdrawal",
                "promo_max_withdrawal",
                "req_deposit_within_days",
            )
            for i, name in enumerate(field_names):
                if i >= len(req_parts) or req_parts[i] == "":
                    continue
                try:
                    updates[name] = int(req_parts[i].replace(",", ""))
                except ValueError:
                    pass

        desc_val = self.desc_input.value.strip()
        if desc_val:
            updates["description"] = desc_val

        if not updates:
            return await interaction.response.send_message(
                embed=discord.Embed(title="ℹ️ No Changes", description="No valid fields were provided.", color=discord.Color.greyple()),
                ephemeral=True,
            )

        ok, err = promo_engine.update_promo_code(self.code, **updates)
        if not ok:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Error", description=err, color=discord.Color.red()),
                ephemeral=True,
            )

        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"✅ `{self.code}` Updated",
                description="\n".join(f"• **{k}**: `{v}`" for k, v in updates.items()),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class EditPromoSelect(discord.ui.Select):
    """Select a promo code to edit."""

    def __init__(self):
        codes = promo_engine.get_promo_codes()
        options = []
        for code, info in list(codes.items())[:25]:
            enabled = info.get("enabled", True)
            icon = "✅" if enabled else "❌"
            ptype = info.get("type", "balance")
            desc = f"{'freegame' if ptype == 'freegame' else 'balance'} · uses: {len(info.get('used_by', []))}"
            options.append(discord.SelectOption(label=f"{icon} {code}", description=desc[:100], value=code, emoji="✏️"))

        if not options:
            options.append(discord.SelectOption(label="No codes available", value="_none_"))

        super().__init__(placeholder="Select a code to edit...", options=options, custom_id="promo:edit_select")

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "_none_":
            return await interaction.response.send_message("No promo codes found.", ephemeral=True)
        code = self.values[0]
        tmpl = promo_engine.get_promo_code(code) or {}
        embed = discord.Embed(
            title=f"✏️ `{code}`",
            description="Edit parameters, set status requirement, or publish to a channel.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="📌 Status Requirement",
            value=_promo_status_req_field_value(code),
            inline=False,
        )
        ptype = tmpl.get("type", "balance")
        embed.add_field(name="Type", value=ptype, inline=True)
        embed.add_field(name="Enabled", value="✅" if tmpl.get("enabled", True) else "❌", inline=True)
        await interaction.response.send_message(
            embed=embed,
            view=PromoCodeManageView(code),
            ephemeral=True,
        )


class ReactivatePromoSelect(discord.ui.Select):
    """Select a disabled/expired promo code to reactivate with original params."""

    def __init__(self):
        codes = promo_engine.get_promo_codes()
        now = int(time.time())
        options = []
        for code, info in list(codes.items())[:25]:
            enabled = info.get("enabled", True)
            exp = info.get("expires_at")
            expired = exp and now > exp
            if enabled and not expired:
                continue   # only show disabled / expired
            expire_h = int(info.get("expire_hours", 0))
            desc = f"{'expired' if expired else 'disabled'} · expire_hours: {expire_h}h"
            options.append(discord.SelectOption(label=f"❌ {code}", description=desc[:100], value=code, emoji="🔄"))

        if not options:
            options.append(discord.SelectOption(label="No inactive codes", value="_none_"))

        super().__init__(placeholder="Select a code to reactivate...", options=options, custom_id="promo:reactivate_select")

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "_none_":
            return await interaction.response.send_message("No inactive promo codes found.", ephemeral=True)
        code = self.values[0]
        ok, err = promo_engine.reactivate_promo_code(code)
        if not ok:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ Error", description=err, color=discord.Color.red()),
                ephemeral=True,
            )
        tmpl = promo_engine.get_promo_code(code)
        new_exp = tmpl.get("expires_at") if tmpl else None
        expire_line = f"<t:{new_exp}:R>" if new_exp else "Never"
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"✅ `{code}` Reactivated",
                description=f"The promo code is now **active** again.\nNew expiry: {expire_line}",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class PromoManagementView(discord.ui.View):
    """Admin panel view for promo code management."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="➕ Create Code", style=discord.ButtonStyle.success, row=0, emoji="🎟️")
    async def create_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="➕  Create Promo Code",
            description="Select the type of promo you want to create:",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=PromoTypeSelectView(), ephemeral=True)

    @discord.ui.button(label="✏️ Edit Code", style=discord.ButtonStyle.primary, row=0)
    async def edit_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        codes = promo_engine.get_promo_codes()
        if not codes:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ No Codes", description="No promo codes exist yet.", color=discord.Color.red()),
                ephemeral=True,
            )
        view = discord.ui.View(timeout=120)
        view.add_item(EditPromoSelect())
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✏️ Edit Promo Code",
                description="Select a code to edit its parameters:",
                color=discord.Color.blue(),
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="🔄 Toggle Enable", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        codes = promo_engine.get_promo_codes()
        if not codes:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ No Codes", description="No promo codes exist yet.", color=discord.Color.red()),
                ephemeral=True,
            )
        view = discord.ui.View(timeout=120)
        view.add_item(PromoCodeActionSelect("toggle"))
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🔄 Toggle Promo Code",
                description="Select a code to enable/disable:",
                color=discord.Color.blue(),
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="🗑️ Delete Code", style=discord.ButtonStyle.danger, row=0)
    async def delete_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        codes = promo_engine.get_promo_codes()
        if not codes:
            return await interaction.response.send_message(
                embed=discord.Embed(title="❌ No Codes", description="No promo codes exist yet.", color=discord.Color.red()),
                ephemeral=True,
            )
        view = discord.ui.View(timeout=120)
        view.add_item(PromoCodeActionSelect("delete"))
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🗑️ Delete Promo Code",
                description="Select a code to permanently delete:",
                color=discord.Color.red(),
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="🔓 Reactivate", style=discord.ButtonStyle.secondary, row=1)
    async def reactivate_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        codes = promo_engine.get_promo_codes()
        now = int(time.time())
        inactive = {c: v for c, v in codes.items() if not v.get("enabled", True) or (v.get("expires_at") and now > v["expires_at"])}
        if not inactive:
            return await interaction.response.send_message(
                embed=discord.Embed(title="ℹ️ No Inactive Codes", description="All promo codes are currently active.", color=discord.Color.greyple()),
                ephemeral=True,
            )
        view = discord.ui.View(timeout=120)
        view.add_item(ReactivatePromoSelect())
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🔓 Reactivate Promo Code",
                description="Select a disabled/expired code to reactivate with its original duration:",
                color=discord.Color.orange(),
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = _build_promo_list_embed()
        await interaction.response.edit_message(embed=embed, view=PromoManagementView())

    @discord.ui.button(label="⬅️ Bonus & Etkinlik", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.admin_panel_nav import HUB_REWARDS, go_hub

        await go_hub(interaction, HUB_REWARDS, user_id=interaction.user.id)


# ─────────────────────────────────────────────────────────────────────────────


# ─── Reset All Balances ───────────────────────────────────────────────────────

class _ConfirmClearAllPromosView(discord.ui.View):
    """Clear every user's active_promo KV record."""

    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="✅ Tüm promoları sıfırla", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message("❌ Admin yetkisi gerekli.", ephemeral=True)
        stats = promo_engine.reset_all_user_promo_states()
        cleared = int(stats.get("cleared", 0))
        by_status = stats.get("by_status") or {}
        lines = [f"**{k}:** {v}" for k, v in sorted(by_status.items())] if by_status else []
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Promo verileri sıfırlandı",
                description=(
                    f"**{cleared}** kullanıcının promo state'i temizlendi.\n"
                    + ("\n".join(lines) if lines else "Kayıt bulunamadı.")
                ),
                color=discord.Color.green(),
            ),
            view=None,
        )

    @discord.ui.button(label="❌ İptal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            content="İptal edildi.",
            embed=None,
            view=None,
        )


class _ConfirmResetBalancesView(discord.ui.View):
    """Double-confirm before wiping every user's real balance."""
    def __init__(self, mode: str):
        super().__init__(timeout=60)
        self._mode = mode  # "real" | "demo" | "both"

    @discord.ui.button(label="✅ Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message("❌ Owner only.", ephemeral=True)

        conn = __import__("modules.database", fromlist=["_get_conn"])._get_conn()
        if self._mode == "real":
            conn.execute("UPDATE users SET balance_real = 0")
        elif self._mode == "demo":
            conn.execute("UPDATE users SET balance_demo = 0")
        else:
            conn.execute("UPDATE users SET balance_real = 0, balance_demo = 0")
        conn.commit()

        rows = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
        count = rows["cnt"] if rows else "?"

        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Balances Reset",
                description=f"**Mode:** `{self._mode}`\n**Affected users:** `{count}`",
                color=discord.Color.green(),
            ),
            view=None,
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="❌ Cancelled", color=discord.Color.red()),
            view=None,
        )


class _ConfirmResetUserView(discord.ui.View):
    """Tek kullanıcının tüm istatistik/geçmiş verilerini sıfırlamak için onay view'ı."""

    def __init__(self, target_id: int, target_name: str):
        super().__init__(timeout=30)
        self._target_id   = target_id
        self._target_name = target_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if check_permission(interaction.user.id, "admin"):
            await interaction.response.send_message("❌ Admin yetkisi gerekli.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Evet, Sıfırla", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        from modules.database import _get_conn
        uid = str(self._target_id)
        conn = _get_conn()

        # Sıfırla: istatistikler
        conn.execute(
            """UPDATE user_stats
               SET total_plays=0, wins=0, losses=0, ties=0,
                   total_wagered=0, total_profit=0, real_plays=0,
                   demo_plays=0, total_deposit=0, games_json='{}'
               WHERE user_id=?""",
            (uid,),
        )
        # Sıfırla: oyun geçmişi
        conn.execute("DELETE FROM game_history WHERE user_id=?", (uid,))
        # Sıfırla: deposit geçmişi
        conn.execute("DELETE FROM deposit_history WHERE user_id=?", (uid,))
        # Sıfırla: withdraw geçmişi
        conn.execute("DELETE FROM withdraw_history WHERE user_id=?", (uid,))
        conn.commit()

        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Kullanıcı Verisi Sıfırlandı",
                description=(
                    f"**{self._target_name}** (`{self._target_id}`) için:\n"
                    "• İstatistikler sıfırlandı\n"
                    "• Oyun geçmişi silindi\n"
                    "• Deposit geçmişi silindi\n"
                    "• Withdraw geçmişi silindi"
                ),
                color=discord.Color.green(),
            ),
            view=None,
        )

    @discord.ui.button(label="❌ İptal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="❌ İptal Edildi", color=discord.Color.red()),
            view=None,
        )


# ─── Promo DOS — bulk rigged % for all rigged-capable games ───────────────────

PROMODOS_KEY = "server/promodos"

from modules.game_rig import (  # noqa: E402
    RIGGED_PROMO_GAMES as RIGGED_GAME_KEYS,
    roll_promodos_percentages,
    snapshot_rigged_percentages,
)


def _get_promodos_state() -> dict:
    data = get_data(PROMODOS_KEY) or {}
    return data if isinstance(data, dict) else {}


def _save_promodos_state(active: bool, rolled: dict) -> None:
    set_data(PROMODOS_KEY, {
        "active": active,
        "rolled": rolled,
        "updated_at": int(time.time()),
    })


def _roll_rigged_promodos(active: bool) -> dict:
    """Apply promo DOS rigged ranges; aktif modda mines/towers özel aralık."""
    _ensure_all_game_entries(get_data("server/games") or {})
    return roll_promodos_percentages("active" if active else "inactive")


def _rigged_snapshot_from_games() -> dict[str, float]:
    _ensure_all_game_entries(get_data("server/games") or {})
    return snapshot_rigged_percentages()


def _build_promodos_embed(active: bool) -> discord.Embed:
    rolled = _rigged_snapshot_from_games()
    title = "🟢 Aktif" if active else "🔴 Deaktif"
    color = discord.Color.green() if active else discord.Color.dark_grey()
    lines = [
        f"{emoji} **{label}** — `{rolled.get(key, 0.0):.2f}%`"
        for key, label, emoji in RIGGED_GAME_KEYS
    ]
    embed = discord.Embed(title=title, color=color)
    embed.add_field(
        name="",
        value="\n".join(lines) if lines else "—",
        inline=False,
    )
    embed.set_footer(text="Crystals rig yok · PvP rig yok · /promodos: Aktif/Deaktif, Düşük/Orta/Yüksek rig")
    return embed


class PromoDosView(discord.ui.View):
    """Toggle promo DOS mode and re-roll all rigged percentages."""

    def __init__(self, active: bool):
        super().__init__(timeout=300)
        self.active = active
        if active:
            btn = discord.ui.Button(
                label="🟢 Aktif — Deaktif yap",
                style=discord.ButtonStyle.success,
                row=0,
                custom_id="promodos:toggle",
            )
        else:
            btn = discord.ui.Button(
                label="🔴 Deaktif — Aktif yap",
                style=discord.ButtonStyle.danger,
                row=0,
                custom_id="promodos:toggle",
            )
        btn.callback = self._on_toggle
        self.add_item(btn)

        low_btn = discord.ui.Button(
            label="Düşük rig",
            style=discord.ButtonStyle.secondary,
            row=1,
            custom_id="promodos:low",
        )
        low_btn.callback = self._on_low
        self.add_item(low_btn)

        mid_btn = discord.ui.Button(
            label="Orta rig",
            style=discord.ButtonStyle.secondary,
            row=1,
            custom_id="promodos:mid",
        )
        mid_btn.callback = self._on_mid
        self.add_item(mid_btn)

        high_btn = discord.ui.Button(
            label="Yüksek rig",
            style=discord.ButtonStyle.primary,
            row=1,
            custom_id="promodos:high",
        )
        high_btn.callback = self._on_high
        self.add_item(high_btn)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if check_permission(interaction.user.id, "admin"):
            await interaction.response.send_message(
                t("errors.no_permission", user_id=str(interaction.user.id)),
                ephemeral=True,
            )
            return False
        return True

    async def _on_toggle(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        if self.active:
            rolled = _roll_rigged_promodos(active=False)
            new_active = False
        else:
            rolled = _roll_rigged_promodos(active=True)
            new_active = True
        _save_promodos_state(new_active, rolled)
        embed = _build_promodos_embed(new_active)
        await interaction.response.edit_message(embed=embed, view=PromoDosView(new_active))

    async def _on_low(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        rolled = roll_promodos_percentages("low")
        _save_promodos_state(self.active, rolled)
        await interaction.response.edit_message(
            embed=_build_promodos_embed(self.active),
            view=PromoDosView(self.active),
        )

    async def _on_high(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        rolled = roll_promodos_percentages("high")
        _save_promodos_state(self.active, rolled)
        await interaction.response.edit_message(
            embed=_build_promodos_embed(self.active),
            view=PromoDosView(self.active),
        )

    async def _on_mid(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        rolled = roll_promodos_percentages("mid")
        _save_promodos_state(self.active, rolled)
        await interaction.response.edit_message(
            embed=_build_promodos_embed(self.active),
            view=PromoDosView(self.active),
        )


class _ConfirmPlatformResetView(discord.ui.View):
    """Type RESET to wipe platform data (preserves cases, games config, admins)."""

    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="✅ RESET — Onayla", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not is_super_admin(interaction.user.id):
            if check_permission(interaction.user.id, "admin"):
                return await interaction.response.send_message(
                    "❌ Sadece super admin.", ephemeral=True,
                )
        await interaction.response.defer(ephemeral=True)
        from modules.platform_reset import run_platform_reset, run_flipbot_reset

        summary = run_platform_reset()
        flip = await run_flipbot_reset()
        lines = [
            f"• Server KV silindi: **{summary.get('server_kv_removed', 0)}**",
            f"• User KV silindi: **{summary.get('user_kv_removed', 0)}**",
            f"• Bakiye sıfırlanan kullanıcı: **{summary.get('users_zeroed', 0)}**",
            f"• Flipbot tabloları: **{sum(flip.values())}** işlem",
            f"• Rakeback sıfırlandı: **{'evet' if flip.get('users_rakeback') else 'hayır'}**",
        ]
        embed = discord.Embed(
            title="✅ Platform Sıfırlandı",
            description="\n".join(lines)
            + "\n\n**Korundu:** items/cases, server/games, server/admins",
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        self.stop()

    @discord.ui.button(label="❌ İptal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.edit_message(
            embed=discord.Embed(title="❌ İptal", color=discord.Color.red()),
            view=None,
        )
        self.stop()


async def setup(bot):
    """Cog yükleme fonksiyonu"""
    from modules.ticket_system import register_ticket_views
    register_ticket_views(bot)
    await bot.add_cog(AdminPanel(bot))


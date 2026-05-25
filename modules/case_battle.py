"""Case Battle — settings, lobbies, wager gate, bot & PvP battles."""

from __future__ import annotations

import time
import uuid
from typing import Optional

import discord

from modules.database import get_data, get_server_data, get_user_data, set_data
from modules.utils import format_balance

BATTLE_MODES = {
    "1v1": {"label": "1v1", "players": 2, "teams": None},
    "1v1v1": {"label": "1v1v1", "players": 3, "teams": None},
    "1v1v1v1": {"label": "1v1v1v1", "players": 4, "teams": None},
    "2v2": {"label": "2v2", "players": 4, "teams": ([0, 1], [2, 3])},
}

MAX_BATTLE_COUNT = 5


def expand_case_rounds(case_id: str, count: int) -> list[str]:
    """Repeat the same case id for each round (1–5)."""
    n = max(1, min(int(count), MAX_BATTLE_COUNT))
    return [case_id] * n


def case_battle_stake(case_id: str, count: int) -> int:
    cases = get_allowed_battle_cases()
    price = int(cases.get(case_id, {}).get("price", 0))
    return price * max(1, min(int(count), MAX_BATTLE_COUNT))


def cases_stake(case_ids: list[str]) -> int:
    """Legacy helper — sum prices for a list of case ids."""
    cases = get_allowed_battle_cases()
    return sum(int(cases.get(cid, {}).get("price", 0)) for cid in case_ids)


def get_case_battle_settings() -> dict:
    data = get_data("server/case_battle") or {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("log_channel_id", None)
    data.setdefault("ping_role_id", None)
    data.setdefault("allowed_case_ids", [])
    return data


def get_battle_ping_content(guild: discord.Guild | None) -> tuple[str, discord.AllowedMentions]:
    """Role mention for lobby posts (no @everyone)."""
    settings = get_case_battle_settings()
    role_id = settings.get("ping_role_id")
    if not role_id or not guild:
        return "", discord.AllowedMentions.none()
    role = guild.get_role(int(role_id))
    if not role:
        return "", discord.AllowedMentions.none()
    return role.mention, discord.AllowedMentions(roles=True)


def save_case_battle_settings(data: dict) -> None:
    set_data("server/case_battle", data)


def get_battle_channel_id(guild_id: int | str | None = None) -> int | None:
    settings = get_case_battle_settings()
    ch = settings.get("log_channel_id") or settings.get("battle_channel_id")
    return int(ch) if ch else None


def get_allowed_battle_cases() -> dict:
    from cogs.games import _get_cases_data

    settings = get_case_battle_settings()
    allowed_ids = settings.get("allowed_case_ids") or []
    all_cases = _get_cases_data().get("cases", {})
    if not allowed_ids:
        return {cid: c for cid, c in all_cases.items() if c.get("items")}
    return {
        cid: c
        for cid, c in all_cases.items()
        if cid in allowed_ids and c.get("items")
    }


def mode_player_count(mode: str) -> int:
    return BATTLE_MODES.get(mode, BATTLE_MODES["1v1"])["players"]


def user_wager_met(user_id: int | str, guild_id: int | str) -> tuple[bool, int, int, int]:
    """Same gate as withdraw: deposit multiplier + active bonus wager."""
    import modules.bonus as bonus_engine

    server_data = get_server_data(str(guild_id))
    multiplier = float(server_data.get("withdraw_min_multiplier", 0) or 0)
    if multiplier <= 0:
        return True, 0, 0, 0

    stats = get_user_data(user_id, "stats") or {}
    last_deposit = int(stats.get("last_deposit_amount", 0))
    if last_deposit <= 0:
        return True, 0, 0, 0

    required = int(last_deposit * multiplier)
    active_bonus = bonus_engine.get_active_bonus(str(user_id))
    if active_bonus:
        required += int(active_bonus.get("wager_requirement", 0))

    total_wagered = int(stats.get("total_wagered", 0))
    wagered_at_deposit = int(stats.get("wagered_at_last_deposit", 0))
    wagered_since = max(0, total_wagered - wagered_at_deposit)
    remaining = max(0, required - wagered_since)
    return remaining <= 0, required, wagered_since, remaining


def _lobbies() -> dict:
    data = get_data("server/case_battle_lobbies") or {}
    return data if isinstance(data, dict) else {}


def _save_lobbies(data: dict) -> None:
    set_data("server/case_battle_lobbies", data)


def create_lobby(
    *,
    guild_id: int,
    host_id: int,
    mode: str,
    case_id: str,
    case_count: int,
    stake: int,
) -> dict:
    lobby_id = uuid.uuid4().hex[:12]
    case_ids = expand_case_rounds(case_id, case_count)
    lobby = {
        "id": lobby_id,
        "guild_id": guild_id,
        "host_id": host_id,
        "mode": mode,
        "case_id": case_id,
        "case_count": max(1, min(int(case_count), MAX_BATTLE_COUNT)),
        "case_ids": case_ids,
        "stake": stake,
        "players": [host_id],
        "status": "open",
        "message_id": None,
        "channel_id": None,
        "created_at": int(time.time()),
    }
    all_l = _lobbies()
    all_l[lobby_id] = lobby
    _save_lobbies(all_l)
    return lobby


def get_lobby(lobby_id: str) -> dict | None:
    return _lobbies().get(lobby_id)


def update_lobby(lobby_id: str, **fields) -> dict | None:
    all_l = _lobbies()
    if lobby_id not in all_l:
        return None
    all_l[lobby_id].update(fields)
    _save_lobbies(all_l)
    return all_l[lobby_id]


def delete_lobby(lobby_id: str) -> None:
    all_l = _lobbies()
    all_l.pop(lobby_id, None)
    _save_lobbies(all_l)


def list_open_lobbies() -> list[dict]:
    return [l for l in _lobbies().values() if l.get("status") == "open"]


def build_lobby_embed(lobby: dict, guild: discord.Guild | None = None) -> discord.Embed:
    cases = get_allowed_battle_cases()
    mode_info = BATTLE_MODES.get(lobby.get("mode", "1v1"), BATTLE_MODES["1v1"])
    max_p = mode_info["players"]
    players = lobby.get("players", [])
    cid = lobby.get("case_id") or (lobby.get("case_ids") or [None])[0]
    count = int(lobby.get("case_count") or len(lobby.get("case_ids") or []) or 1)
    c = cases.get(cid, {}) if cid else {}
    unit = int(c.get("price", 0))
    case_line = (
        f"{c.get('emoji', '📦')} **{c.get('name', cid or '?')}** × **{count}**\n"
        f"{format_balance(unit, 'real')} each · "
        f"{format_balance(unit * count, 'real')} total per player"
    )
    host_mention = f"<@{lobby['host_id']}>"
    player_lines = [f"<@{pid}>" for pid in players]
    while len(player_lines) < max_p:
        player_lines.append("`— open slot —`")

    embed = discord.Embed(
        title="⚔️ Case Battle — Open Lobby",
        description=(
            f"**Host:** {host_mention}\n"
            f"**Mode:** {mode_info['label']}\n"
            f"**Entry:** {format_balance(lobby.get('stake', 0), 'real')} per player\n"
            f"**Players:** {len(players)}/{max_p}"
        ),
        color=0x9B59B6,
    )
    embed.add_field(
        name="Case",
        value=case_line,
        inline=False,
    )
    embed.add_field(
        name="Participants",
        value="\n".join(player_lines),
        inline=False,
    )
    embed.set_footer(
        text="Wager required • Winners take all case loot (entry fee sunk) • Host can cancel"
    )
    return embed


def cancel_lobby(lobby_id: str) -> dict | None:
    """Mark lobby cancelled and remove from storage."""
    lobby = get_lobby(lobby_id)
    if not lobby or lobby.get("status") != "open":
        return None
    update_lobby(lobby_id, status="cancelled")
    delete_lobby(lobby_id)
    return lobby


async def log_case_battle(
    interaction: discord.Interaction,
    *,
    opponent: str,
    challenger: discord.Member,
    case_name: str,
    stake: int,
    mode: str,
    player_item: dict,
    bot_item: dict,
    winner: str,
    game_uid: str,
    profit: int,
) -> None:
    from modules.game_log import post_solo_game_log

    if (mode or "").lower() != "real":
        return
    won = winner == "player"
    tie = winner == "tie"
    await post_solo_game_log(
        user_id=challenger.id,
        game_id="case_battle",
        bet=float(stake),
        won=won,
        payout=float(profit) if won and profit > 0 else 0.0,
        user=challenger,
        client=interaction.client,
        guild_id=interaction.guild.id if interaction.guild else None,
        mode=mode,
        tie=tie,
    )


async def log_pvp_case_battle(
    client: discord.Client,
    guild_id: int,
    player_a: discord.abc.User,
    player_b: discord.abc.User,
    winner: discord.abc.User | None,
    stake: int,
    mode: str,
    payout: int,
) -> None:
    from modules.game_log import post_pvp_game_log

    await post_pvp_game_log(
        player_a=player_a,
        player_b=player_b,
        game_id="case_battle",
        winner=winner,
        payout=float(payout),
        bet=float(stake),
        client=client,
        guild_id=guild_id,
        mode=mode,
    )

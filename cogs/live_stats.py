"""Live Stats Cog — auto-updating statistics embed.

• Refreshes every 60 seconds in the configured channel.
• Shows today's stats (resets at midnight) and all-time platform stats.
• Admin can configure the channel via /admin_panel → Server Settings → Live Stats.
• Admins can also run /live_stats to see the embed ephemerally.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import time

from modules.database import (
    check_permission, get_server_data, set_server_data,
    get_platform_alltime_stats,
)
from modules.live_stats_tracker import get_daily_stats, get_records
from modules.utils import format_balance
from modules.constants import FOOTER_TEXT


# ── Embed builder ──────────────────────────────────────────────────────────────

def _fmt_game(game_id: str) -> str:
    """Human-readable game name from game_id string."""
    return game_id.replace("_", " ").title()


def build_live_stats_embed(bot: discord.Client) -> discord.Embed:
    now     = int(time.time())
    daily   = get_daily_stats()
    alltime = get_platform_alltime_stats()
    records = get_records()

    # ── Today ──────────────────────────────────────────────────────────────────
    games_today   = int(daily.get("games_played", 0))
    wagered_today = int(daily.get("wagered", 0))
    wins_today    = int(daily.get("wins", 0))
    losses_today  = int(daily.get("losses", 0))
    deposit_today = int(daily.get("deposit", 0))
    withdraw_today= int(daily.get("withdraw", 0))

    decided = wins_today + losses_today
    win_rate_today = round(wins_today / decided * 100, 1) if decided > 0 else 0.0

    # Biggest win today
    bw_today  = daily.get("biggest_win") or {}
    bw_amount = int(bw_today.get("amount", 0))
    bw_uid    = bw_today.get("user_id")
    bw_game   = bw_today.get("game", "")

    # Most active player today
    ma = daily.get("most_active") or {}
    if ma:
        ma_uid   = max(ma, key=lambda k: ma[k])
        ma_count = ma[ma_uid]
    else:
        ma_uid   = None
        ma_count = 0

    # Top game today
    gc = daily.get("game_counts") or {}
    if gc:
        top_game       = max(gc, key=lambda k: gc[k])
        top_game_count = gc[top_game]
    else:
        top_game       = None
        top_game_count = 0

    # ── All-time ───────────────────────────────────────────────────────────────
    total_players  = alltime.get("total_players", 0)
    total_games    = alltime.get("total_games", 0)
    total_wagered  = alltime.get("total_wagered", 0)
    total_deposit   = alltime.get("total_deposit", 0)
    in_circulation  = alltime.get("in_circulation", 0)
    total_withdraw  = alltime.get("total_withdraw", 0)

    # ── Records ────────────────────────────────────────────────────────────────
    rec_bw     = records.get("biggest_win") or {}
    rec_amount = int(rec_bw.get("amount", 0))
    rec_uid    = rec_bw.get("user_id")
    rec_game   = rec_bw.get("game", "")
    rec_ts     = int(rec_bw.get("timestamp", 0))

    # ── Build embed ────────────────────────────────────────────────────────────
    embed = discord.Embed(
        title="📊  Vegas Casino — Live Statistics",
        color=0x2b2d31,
    )
    embed.description = f"🕐 Last updated: <t:{now}:R>"

    # ━━━ TODAY ━━━
    embed.add_field(
        name=f"╔══ 🗓️  TODAY — {daily.get('date', time.strftime('%Y-%m-%d'))} ══╗",
        value="\u200b",
        inline=False,
    )
    embed.add_field(
        name="📈 Stats",
        value=(
            f"🎮 **Games Played:** `{games_today:,}`\n"
            f"⚡ **Wagered:** {format_balance(wagered_today, 'real')}\n"
            f"🏆 **Win Rate:** `{win_rate_today}%`"
        ),
        inline=True,
    )
    embed.add_field(
        name="💳 Finance",
        value=(
            f"💰 **Deposits:** {format_balance(deposit_today, 'real')}\n"
            f"💸 **Withdrawals:** {format_balance(withdraw_today, 'real')}"
        ),
        inline=True,
    )
    # empty inline to force next to new row
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    # ━━━ TODAY HIGHLIGHTS ━━━
    bw_str = (
        f"{format_balance(bw_amount, 'real')} by <@{bw_uid}> ({_fmt_game(bw_game)})"
        if bw_uid else "`—`"
    )
    ma_str = (
        f"<@{ma_uid}> with `{ma_count}` plays"
        if ma_uid else "`—`"
    )
    tg_str = (
        f"`{_fmt_game(top_game)}` — `{top_game_count:,}` plays"
        if top_game else "`—`"
    )
    embed.add_field(
        name="✨  Today's Highlights",
        value=(
            f"🔥 **Biggest Win:** {bw_str}\n"
            f"👑 **Most Active:** {ma_str}\n"
            f"🎯 **Top Game:** {tg_str}"
        ),
        inline=False,
    )

    # ━━━ SEPARATOR ━━━
    embed.add_field(name="─" * 36, value="\u200b", inline=False)

    # ━━━ ALL TIME ━━━
    embed.add_field(
        name="╔══ 🌍  ALL TIME ══╗",
        value="\u200b",
        inline=False,
    )
    embed.add_field(
        name="👥 Players & Games",
        value=(
            f"👥 **Total Players:** `{total_players:,}`\n"
            f"🎮 **Total Games:** `{total_games:,}`\n"
            f"⚡ **Total Wagered:** {format_balance(total_wagered, 'real')}"
        ),
        inline=True,
    )
    embed.add_field(
        name="💳 Finance",
        value=(
            f"💰 **Deposits:** {format_balance(total_deposit, 'real')}\n"
            f"💸 **Withdrawals:** {format_balance(total_withdraw, 'real')}\n"
            f"💎 **In Circulation:** {format_balance(in_circulation, 'real')}"
        ),
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    # ━━━ RECORDS ━━━
    if rec_uid:
        rec_str = (
            f"{format_balance(rec_amount, 'real')} by <@{rec_uid}>"
            f" ({_fmt_game(rec_game)})"
            + (f" — <t:{rec_ts}:R>" if rec_ts else "")
        )
    else:
        rec_str = "`—`"
    embed.add_field(
        name="🏅  Records",
        value=f"🥇 **Biggest Win Ever:** {rec_str}",
        inline=False,
    )

    embed.set_footer(text="Vegas Casino  ·  Updates every 60 s")
    return embed


# ── Channel helpers ────────────────────────────────────────────────────────────

def get_live_stats_channel(guild_id: str):
    ch_id = get_server_data(guild_id).get("live_stats_channel")
    return int(ch_id) if ch_id else None


def get_live_stats_message(guild_id: str):
    msg_id = get_server_data(guild_id).get("live_stats_message_id")
    return int(msg_id) if msg_id else None


def _set_live_stats_message(guild_id: str, message_id: int) -> None:
    data = get_server_data(guild_id)
    data["live_stats_message_id"] = message_id
    set_server_data(guild_id, data)


# ── Cog ───────────────────────────────────────────────────────────────────────

class LiveStats(commands.Cog):
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._refresh_task.start()

    def cog_unload(self):
        self._refresh_task.cancel()

    @tasks.loop(seconds=60)
    async def _refresh_task(self):
        try:
            embed = build_live_stats_embed(self.bot)
            for guild in self.bot.guilds:
                guild_id   = str(guild.id)
                channel_id = get_live_stats_channel(guild_id)
                if not channel_id:
                    continue
                channel = self.bot.get_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    continue
                message_id = get_live_stats_message(guild_id)
                if message_id:
                    try:
                        msg = await channel.fetch_message(message_id)
                        await msg.edit(embed=embed)
                        continue
                    except discord.NotFound:
                        pass
                    except discord.HTTPException as e:
                        if e.status >= 500:
                            continue
                        raise
                # No existing message — post a new one
                msg = await channel.send(embed=embed)
                _set_live_stats_message(guild_id, msg.id)
        except Exception as e:
            print(f"[LiveStats] Refresh error: {e}")

    @_refresh_task.before_loop
    async def _before_refresh(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="live_stats",
        description="Show live platform statistics (Admin only)",
    )
    @app_commands.guild_only()
    async def live_stats_cmd(self, interaction: discord.Interaction):
        if check_permission(str(interaction.user.id), "admin"):
            return await interaction.response.send_message(
                "❌ You don't have permission.", ephemeral=True
            )
        embed = build_live_stats_embed(self.bot)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: discord.Client):
    await bot.add_cog(LiveStats(bot))

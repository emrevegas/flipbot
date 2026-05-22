"""Races / tournaments — wager leaderboard with prize pool."""
from __future__ import annotations

import asyncio
import time

import discord
from discord.ext import commands

from database import db
from modules import image_gen, utils


def _admin():
    async def pred(ctx: commands.Context) -> bool:
        if not utils.is_admin(ctx):
            raise commands.CheckFailure("No permission.")
        return True
    return commands.check(pred)


PRIZE_SPLITS = [0.50, 0.30, 0.20]  # top-3 split


class Races(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._end_task: asyncio.Task | None = None

    async def cog_load(self):
        race = await db.get_active_race()
        if race:
            remaining = race["ends_at"] - int(time.time())
            if remaining > 0:
                self._end_task = asyncio.create_task(self._auto_end(race["id"], remaining))
            else:
                await self._end_race(race["id"])

    @commands.command(name="race")
    async def race(self, ctx: commands.Context):
        """View current race standings. .race"""
        race = await db.get_active_race()
        if not race:
            return await ctx.send(embed=utils.info_embed("Race", "No active race at the moment."))

        rows = await db.get_race_leaderboard(race["id"], 10)
        ends_at = race["ends_at"]

        loop = asyncio.get_event_loop()
        img_buf = await loop.run_in_executor(None, image_gen.render_race_card, rows)

        embed = discord.Embed(
            title="🏁 Active Race",
            description=(
                f"**Prize Pool:** {utils.fmt_pts(race['prize_pts'])} pts\n"
                f"**Ends:** <t:{ends_at}:R>"
            ),
            color=0xF1C40F,
        )
        await ctx.send(embed=embed, file=discord.File(img_buf, "race.png"))

    @commands.group(name="raceadmin", invoke_without_command=True)
    @_admin()
    async def race_admin(self, ctx: commands.Context):
        """Race management. Subcommands: create, end"""
        await ctx.send_help(ctx.command)

    @race_admin.command(name="create")
    @_admin()
    async def race_create(self, ctx: commands.Context, prize_pts: float, duration_hours: float = 24):
        """Start a new race. .raceadmin create 10000 24"""
        existing = await db.get_active_race()
        if existing:
            return await ctx.send(embed=utils.error_embed("There is already an active race. End it first."))

        ends_at = int(time.time()) + int(duration_hours * 3600)
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT INTO races (prize_pts, ends_at, created_by) VALUES (?, ?, ?)",
            (prize_pts, ends_at, str(ctx.author.id)),
        )
        await dbc.commit()

        race = await db.get_active_race()
        self._end_task = asyncio.create_task(
            self._auto_end(race["id"], int(duration_hours * 3600))
        )

        embed = discord.Embed(title="🏁 Race Started!", color=0xF1C40F)
        embed.add_field(name="Prize Pool", value=f"`{utils.fmt_pts(prize_pts)} pts`", inline=True)
        embed.add_field(name="Duration", value=f"`{duration_hours}h`", inline=True)
        embed.add_field(name="Ends", value=f"<t:{ends_at}:R>", inline=True)
        embed.set_footer(text="Top wagerers win prizes! Check .race for standings.")
        await ctx.send(embed=embed)

    @race_admin.command(name="end")
    @_admin()
    async def race_end(self, ctx: commands.Context):
        """End the current race early. .raceadmin end"""
        race = await db.get_active_race()
        if not race:
            return await ctx.send(embed=utils.error_embed("No active race."))
        if self._end_task:
            self._end_task.cancel()
        await self._end_race(race["id"])
        await ctx.send(embed=utils.success_embed("Race ended."))

    async def _auto_end(self, race_id: int, delay: float):
        await asyncio.sleep(delay)
        await self._end_race(race_id)

    async def _end_race(self, race_id: int):
        dbc = await db.get_db()
        row = await (await dbc.execute("SELECT * FROM races WHERE id=?", (race_id,))).fetchone()
        if not row or dict(row)["ended"]:
            return
        await dbc.execute("UPDATE races SET ended=1 WHERE id=?", (race_id,))
        await dbc.commit()

        race = dict(row)
        leaderboard = await db.get_race_leaderboard(race_id, 10)
        if not leaderboard:
            return

        total_prize = float(race["prize_pts"])
        for i, entry in enumerate(leaderboard[:3]):
            split = PRIZE_SPLITS[i] if i < len(PRIZE_SPLITS) else 0
            prize = total_prize * split
            if prize > 0:
                await db.add_balance(
                    entry["user_id"], prize,
                    note=f"race finish #{i+1} (race {race_id})"
                )
                try:
                    user = await self.bot.fetch_user(int(entry["user_id"]))
                    if user:
                        await user.send(embed=discord.Embed(
                            description=f"🏁 Race ended! You finished **#{i+1}** and won **{utils.fmt_pts(prize)} pts**!",
                            color=0xF1C40F,
                        ))
                except Exception:
                    pass

        # Broadcast results to any channel that has race info (best-effort)
        # Post to first available text channel found
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    lines = []
                    for i, e in enumerate(leaderboard[:3]):
                        split = PRIZE_SPLITS[i] if i < len(PRIZE_SPLITS) else 0
                        uname = e.get("username") or e["user_id"]
                        lines.append(f"#{i+1} **{uname}** — {utils.fmt_pts(float(e['wagered']))} pts wagered — won {utils.fmt_pts(total_prize * split)} pts")
                    embed = discord.Embed(
                        title="🏁 Race Results!",
                        description="\n".join(lines),
                        color=0xF1C40F,
                    )
                    try:
                        await channel.send(embed=embed)
                    except Exception:
                        pass
                    break
            break


async def setup(bot: commands.Bot):
    await bot.add_cog(Races(bot))

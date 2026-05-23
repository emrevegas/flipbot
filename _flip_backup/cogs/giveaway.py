"""Giveaway system with react-to-enter."""
from __future__ import annotations

import asyncio
import random
import time

import discord
from discord.ext import commands

from database import db
from modules import utils


GIVEAWAY_EMOJI = "🎉"


def _admin():
    async def pred(ctx: commands.Context) -> bool:
        if not utils.is_admin(ctx):
            raise commands.CheckFailure("No permission.")
        return True
    return commands.check(pred)


class Giveaway(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._tasks: dict[int, asyncio.Task] = {}

    async def cog_load(self):
        # Resume any active giveaways on startup
        dbc = await db.get_db()
        rows = await (await dbc.execute(
            "SELECT * FROM giveaways WHERE ended=0"
        )).fetchall()
        for row in rows:
            row = dict(row)
            remaining = row["ends_at"] - int(time.time())
            if remaining > 0:
                self._tasks[row["id"]] = asyncio.create_task(
                    self._auto_end(row["id"], remaining)
                )
            else:
                await self._end_giveaway(row["id"])

    @commands.group(name="giveaway", aliases=["gw"], invoke_without_command=True)
    async def giveaway_group(self, ctx: commands.Context):
        """Giveaway commands. Subcommands: create, end, reroll"""
        await ctx.send_help(ctx.command)

    @giveaway_group.command(name="create")
    @_admin()
    async def gw_create(self, ctx: commands.Context, prize_pts: float, duration_minutes: int, winners: int = 1):
        """Create a giveaway. .giveaway create 1000 60 1"""
        if prize_pts <= 0:
            return await ctx.send(embed=utils.error_embed("Prize must be positive."))
        if duration_minutes <= 0:
            return await ctx.send(embed=utils.error_embed("Duration must be positive."))
        winners = max(1, winners)

        ends_at = int(time.time()) + duration_minutes * 60
        embed = discord.Embed(
            title=f"{GIVEAWAY_EMOJI} GIVEAWAY! {GIVEAWAY_EMOJI}",
            description=(
                f"**Prize:** {utils.fmt_pts(prize_pts)} pts\n"
                f"**Winners:** {winners}\n"
                f"**Ends:** <t:{ends_at}:R>\n\n"
                f"React with {GIVEAWAY_EMOJI} to enter!"
            ),
            color=0xF1C40F,
        )
        embed.set_footer(text=f"Hosted by {ctx.author.display_name}")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction(GIVEAWAY_EMOJI)

        dbc = await db.get_db()
        await dbc.execute(
            """INSERT INTO giveaways (message_id, channel_id, prize_pts, winners_count, ends_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (str(msg.id), str(ctx.channel.id), prize_pts, winners, ends_at, str(ctx.author.id)),
        )
        await dbc.commit()

        row = await (await dbc.execute(
            "SELECT id FROM giveaways WHERE message_id=?", (str(msg.id),)
        )).fetchone()
        gw_id = row["id"]

        self._tasks[gw_id] = asyncio.create_task(
            self._auto_end(gw_id, duration_minutes * 60)
        )

    @giveaway_group.command(name="end")
    @_admin()
    async def gw_end(self, ctx: commands.Context, message_id: str):
        """End a giveaway early. .giveaway end <message_id>"""
        dbc = await db.get_db()
        row = await (await dbc.execute(
            "SELECT * FROM giveaways WHERE message_id=? AND ended=0", (message_id,)
        )).fetchone()
        if not row:
            return await ctx.send(embed=utils.error_embed("Giveaway not found or already ended."))
        gw = dict(row)
        task = self._tasks.pop(gw["id"], None)
        if task:
            task.cancel()
        await self._end_giveaway(gw["id"])
        await ctx.send(embed=utils.success_embed("Giveaway ended."))

    @giveaway_group.command(name="reroll")
    @_admin()
    async def gw_reroll(self, ctx: commands.Context, message_id: str):
        """Reroll a giveaway winner. .giveaway reroll <message_id>"""
        dbc = await db.get_db()
        row = await (await dbc.execute(
            "SELECT * FROM giveaways WHERE message_id=?", (message_id,)
        )).fetchone()
        if not row:
            return await ctx.send(embed=utils.error_embed("Giveaway not found."))
        gw = dict(row)
        await self._pick_winners(gw, ctx.channel, reroll=True)

    async def _auto_end(self, gw_id: int, delay: float):
        await asyncio.sleep(delay)
        await self._end_giveaway(gw_id)

    async def _end_giveaway(self, gw_id: int):
        dbc = await db.get_db()
        row = await (await dbc.execute(
            "SELECT * FROM giveaways WHERE id=?", (gw_id,)
        )).fetchone()
        if not row:
            return
        gw = dict(row)
        if gw["ended"]:
            return

        await dbc.execute("UPDATE giveaways SET ended=1 WHERE id=?", (gw_id,))
        await dbc.commit()

        try:
            channel = self.bot.get_channel(int(gw["channel_id"]))
            if channel:
                await self._pick_winners(gw, channel)
        except Exception as e:
            pass

    async def _pick_winners(self, gw: dict, channel: discord.TextChannel, reroll: bool = False):
        # Collect entries from reactions
        entries = []
        try:
            msg = await channel.fetch_message(int(gw["message_id"]))
            for reaction in msg.reactions:
                if str(reaction.emoji) == GIVEAWAY_EMOJI:
                    async for user in reaction.users():
                        if not user.bot:
                            entries.append(user)
        except Exception:
            pass

        # Also check DB entries
        dbc = await db.get_db()
        db_entries = await db.get_giveaway_entries(gw["id"])
        db_user_ids = {e["user_id"] for e in db_entries}
        # merge
        for uid in db_user_ids:
            if not any(str(u.id) == uid for u in entries):
                try:
                    user = await self.bot.fetch_user(int(uid))
                    if user:
                        entries.append(user)
                except Exception:
                    pass

        n_winners = min(gw["winners_count"], len(entries))
        if not entries:
            embed = discord.Embed(
                title=f"{GIVEAWAY_EMOJI} Giveaway Ended",
                description="No participants — no winner.",
                color=0xE74C3C,
            )
            return await channel.send(embed=embed)

        winners = random.sample(entries, n_winners)
        prize_each = float(gw["prize_pts"]) / n_winners

        for w in winners:
            await db.ensure_user(w.id, w.name)
            await db.add_balance(w.id, prize_each, note=f"giveaway win (id={gw['id']})")
            try:
                await w.send(embed=discord.Embed(
                    description=f"🎉 You won a giveaway! **{utils.fmt_pts(prize_each)} pts** added to your balance!",
                    color=0xF1C40F,
                ))
            except Exception:
                pass

        winner_mentions = " ".join(w.mention for w in winners)
        label = "Rerolled Winner" if reroll else "Winner"
        embed = discord.Embed(
            title=f"{GIVEAWAY_EMOJI} Giveaway {'Rerolled' if reroll else 'Ended'}!",
            description=(
                f"**{label}{'s' if len(winners) > 1 else ''}:** {winner_mentions}\n"
                f"**Prize:** {utils.fmt_pts(float(gw['prize_pts']))} pts "
                f"({utils.fmt_pts(prize_each)} pts each)"
            ),
            color=0xF1C40F,
        )
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != GIVEAWAY_EMOJI or payload.member and payload.member.bot:
            return
        dbc = await db.get_db()
        row = await (await dbc.execute(
            "SELECT * FROM giveaways WHERE message_id=? AND ended=0",
            (str(payload.message_id),),
        )).fetchone()
        if not row:
            return
        gw = dict(row)
        await dbc.execute(
            "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
            (gw["id"], str(payload.user_id)),
        )
        await dbc.commit()


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaway(bot))

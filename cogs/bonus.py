"""Bonus / wager system."""
from __future__ import annotations

import time

import discord
from discord.ext import commands

from database import db
from modules import flip_utils as utils


def _admin():
    async def pred(ctx: commands.Context) -> bool:
        if not utils.is_admin(ctx):
            raise commands.CheckFailure("No permission.")
        return True
    return commands.check(pred)


class Bonus(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="bonus")
    async def bonus(self, ctx: commands.Context):
        """View your active bonus. .bonus"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        active = await db.get_active_bonus(ctx.author.id)
        if not active:
            return await ctx.send(embed=utils.info_embed(
                "Bonus", "You have no active bonus. Use `.bonuses` to see available bonuses."
            ))
        wagered = float(active["wagered"])
        req = float(active["wager_req"])
        pct = min(wagered / req * 100, 100) if req > 0 else 100
        remaining = max(0, req - wagered)

        embed = discord.Embed(title="🎁 Active Bonus", color=0x2ECC71)
        embed.add_field(name="Bonus Name", value=active["bonus_name"], inline=True)
        embed.add_field(name="Bonus Amount", value=f"`{utils.fmt_pts(active['bonus_amount'])} pts`", inline=True)
        embed.add_field(name="Wager Progress", value=f"`{utils.fmt_pts(wagered)} / {utils.fmt_pts(req)} pts` ({pct:.1f}%)", inline=False)
        embed.add_field(name="Remaining Wager", value=f"`{utils.fmt_pts(remaining)} pts`", inline=True)
        if active.get("description"):
            embed.add_field(name="Description", value=active["description"], inline=False)

        # progress bar
        bar_filled = int(pct / 10)
        bar = "🟩" * bar_filled + "⬛" * (10 - bar_filled)
        embed.add_field(name="Progress", value=bar, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="bonuses")
    async def bonuses(self, ctx: commands.Context):
        """List available bonuses. .bonuses"""
        all_bonuses = await db.get_all_bonuses()
        if not all_bonuses:
            return await ctx.send(embed=utils.info_embed("Bonuses", "No bonuses available at this time."))
        embed = discord.Embed(title="🎁 Available Bonuses", color=0x5865F2)
        for b in all_bonuses:
            embed.add_field(
                name=b["name"],
                value=(
                    f"{b.get('description') or 'No description'}\n"
                    f"Bonus: **{int(b['bonus_pct'] * 100)}%** | "
                    f"Wager requirement: **{b['wager_req']}x**"
                ),
                inline=False,
            )
        embed.set_footer(text="Contact staff to claim a bonus.")
        await ctx.send(embed=embed)

    @commands.command(name="wager")
    async def wager(self, ctx: commands.Context, member: discord.Member = None):
        """Show wager progress. .wager [@user]"""
        target = member or ctx.author
        if member and not utils.is_admin(ctx):
            return await ctx.send(embed=utils.error_embed("Only admins can view others' wager."))
        await db.ensure_user(target.id, target.name)
        user = await db.get_user(target.id)
        active = await db.get_active_bonus(target.id)

        embed = discord.Embed(title=f"📊 Wager Progress — {target.display_name}", color=0x5865F2)
        embed.add_field(name="Total Wagered", value=f"`{utils.fmt_pts(user['total_wagered'])} pts`", inline=True)
        if active:
            wagered = float(active["wagered"])
            req = float(active["wager_req"])
            pct = min(wagered / req * 100, 100) if req > 0 else 100
            embed.add_field(name="Active Bonus", value=active["bonus_name"], inline=True)
            embed.add_field(name="Bonus Progress", value=f"{pct:.1f}%", inline=True)
        else:
            embed.add_field(name="Active Bonus", value="None", inline=True)
        await ctx.send(embed=embed)

    # ── Admin bonus management ─────────────────────────────────────────────────

    @commands.group(name="bonusmgr", invoke_without_command=True)
    @_admin()
    async def bonus_mgr(self, ctx: commands.Context):
        """Bonus management. Subcommands: create, give"""
        await ctx.send_help(ctx.command)

    @bonus_mgr.command(name="create")
    @_admin()
    async def bonus_create(self, ctx: commands.Context, name: str, bonus_pct: float, wager_req: float, *, description: str = ""):
        """Create a bonus. .bonusmgr create "100% Reload" 1.0 10 "100% match bonus"
        bonus_pct = 1.0 means 100%, wager_req = multiplier e.g. 10x
        """
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT OR REPLACE INTO bonuses (name, description, bonus_pct, wager_req) VALUES (?, ?, ?, ?)",
            (name, description, bonus_pct, wager_req),
        )
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Bonus **{name}** created ({int(bonus_pct*100)}%, {wager_req}x wager)."))

    @bonus_mgr.command(name="give")
    @_admin()
    async def bonus_give(self, ctx: commands.Context, member: discord.Member, bonus_name: str, amount: float):
        """Give a bonus to a user. .bonusmgr give @user "100% Reload" 500"""
        dbc = await db.get_db()
        bonus_row = await (await dbc.execute(
            "SELECT * FROM bonuses WHERE LOWER(name)=LOWER(?)", (bonus_name,)
        )).fetchone()
        if not bonus_row:
            return await ctx.send(embed=utils.error_embed(f"Bonus `{bonus_name}` not found."))
        bonus_row = dict(bonus_row)
        wager_total = amount * float(bonus_row["wager_req"])
        await db.ensure_user(member.id, member.name)
        await db.add_balance(member.id, amount, note=f"bonus: {bonus_name}", by=str(ctx.author.id))
        await dbc.execute(
            "INSERT INTO active_bonuses (user_id, bonus_id, bonus_amount, wager_req) VALUES (?, ?, ?, ?)",
            (str(member.id), bonus_row["id"], amount, wager_total),
        )
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(
            f"Gave **{utils.fmt_pts(amount)} pts** bonus ({bonus_name}) to {member.mention}. "
            f"Wager requirement: **{utils.fmt_pts(wager_total)} pts**."
        ))
        try:
            await member.send(embed=discord.Embed(
                description=f"🎁 You received a **{bonus_name}** bonus of **{utils.fmt_pts(amount)} pts**! "
                            f"Wager **{utils.fmt_pts(wager_total)} pts** to complete it.",
                color=0x2ECC71,
            ))
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Bonus(bot))

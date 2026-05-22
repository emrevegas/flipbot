"""Admin commands: .add .remove .set .reset .promo"""
from __future__ import annotations

import time

import discord
from discord.ext import commands

from database import db
from modules import utils


def admin_only():
    async def pred(ctx: commands.Context) -> bool:
        if not utils.is_admin(ctx):
            raise commands.CheckFailure("No permission.")
        return True
    return commands.check(pred)


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Balance management ─────────────────────────────────────────────────────

    @commands.command(name="add")
    @admin_only()
    async def add_balance(self, ctx: commands.Context, member: discord.Member, amount: float, *, note: str = ""):
        """Add points to a user. Usage: .add @user 500 [reason]"""
        if amount <= 0:
            return await ctx.send(embed=utils.error_embed("Amount must be positive."))
        await db.ensure_user(member.id, member.name)
        new_bal = await db.add_balance(member.id, amount, note=note or "admin add", by=str(ctx.author.id))
        embed = discord.Embed(color=0x2ECC71)
        embed.add_field(name="➕ Added", value=f"`{utils.fmt_pts(amount)} pts`", inline=True)
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="New Balance", value=f"`{utils.fmt_pts(new_bal)} pts`", inline=True)
        if note:
            embed.add_field(name="Note", value=note, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="remove", aliases=["deduct"])
    @admin_only()
    async def remove_balance(self, ctx: commands.Context, member: discord.Member, amount: float, *, note: str = ""):
        """Remove points from a user. Usage: .remove @user 200 [reason]"""
        if amount <= 0:
            return await ctx.send(embed=utils.error_embed("Amount must be positive."))
        await db.ensure_user(member.id, member.name)
        new_bal = await db.add_balance(member.id, -amount, note=note or "admin remove", by=str(ctx.author.id))
        embed = discord.Embed(color=0xE74C3C)
        embed.add_field(name="➖ Removed", value=f"`{utils.fmt_pts(amount)} pts`", inline=True)
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="New Balance", value=f"`{utils.fmt_pts(new_bal)} pts`", inline=True)
        if note:
            embed.add_field(name="Note", value=note, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="setbal", aliases=["set"])
    @admin_only()
    async def set_balance(self, ctx: commands.Context, member: discord.Member, amount: float, *, note: str = ""):
        """Set a user's balance to an exact amount."""
        if amount < 0:
            return await ctx.send(embed=utils.error_embed("Amount cannot be negative."))
        await db.ensure_user(member.id, member.name)
        new_bal = await db.set_balance(member.id, amount, note=note or "admin set", by=str(ctx.author.id))
        embed = discord.Embed(color=0xF39C12)
        embed.add_field(name="🎯 Set Balance", value=f"`{utils.fmt_pts(new_bal)} pts`", inline=True)
        embed.add_field(name="User", value=member.mention, inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="resetbal")
    @admin_only()
    async def reset_balance(self, ctx: commands.Context, member: discord.Member, *, reason: str = "admin reset"):
        """Reset a user's balance to 0."""
        await db.ensure_user(member.id, member.name)
        await db.set_balance(member.id, 0.0, note=reason, by=str(ctx.author.id))
        await ctx.send(embed=utils.success_embed(f"Reset {member.mention}'s balance to 0."))

    # ── Promo management ───────────────────────────────────────────────────────

    @commands.group(name="promo", invoke_without_command=True)
    @admin_only()
    async def promo_group(self, ctx: commands.Context):
        """Promo code management. Subcommands: create, delete, list"""
        await ctx.send_help(ctx.command)

    @promo_group.command(name="create")
    @admin_only()
    async def promo_create(self, ctx: commands.Context, code: str, reward: float, max_uses: int = 0, expire_hours: int = 0):
        """Create a promo code. .promo create CODE 500 100 72"""
        code = code.upper()
        dbc = await db.get_db()
        existing = await db.get_promo(code)
        if existing:
            return await ctx.send(embed=utils.error_embed(f"Code `{code}` already exists."))
        expires_at = int(time.time()) + expire_hours * 3600 if expire_hours > 0 else None
        await dbc.execute(
            "INSERT INTO promo_codes (code, reward, max_uses, expires_at, created_by) VALUES (?, ?, ?, ?, ?)",
            (code, reward, max_uses, expires_at, str(ctx.author.id)),
        )
        await dbc.commit()
        embed = discord.Embed(title="✅ Promo Created", color=0x2ECC71)
        embed.add_field(name="Code", value=f"`{code}`", inline=True)
        embed.add_field(name="Reward", value=f"`{utils.fmt_pts(reward)} pts`", inline=True)
        embed.add_field(name="Max Uses", value=str(max_uses) if max_uses else "Unlimited", inline=True)
        embed.add_field(name="Expires", value=f"<t:{expires_at}:R>" if expires_at else "Never", inline=True)
        await ctx.send(embed=embed)

    @promo_group.command(name="delete", aliases=["del"])
    @admin_only()
    async def promo_delete(self, ctx: commands.Context, code: str):
        """Delete a promo code."""
        code = code.upper()
        dbc = await db.get_db()
        await dbc.execute("DELETE FROM promo_codes WHERE UPPER(code)=?", (code,))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Deleted promo `{code}`."))

    @promo_group.command(name="list")
    @admin_only()
    async def promo_list(self, ctx: commands.Context):
        """List all promo codes."""
        dbc = await db.get_db()
        rows = await (await dbc.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")).fetchall()
        if not rows:
            return await ctx.send(embed=utils.info_embed("Promos", "No promo codes created yet."))
        lines = []
        for r in rows:
            r = dict(r)
            exp = f"<t:{r['expires_at']}:R>" if r.get("expires_at") else "∞"
            lines.append(
                f"`{r['code']}` — **{utils.fmt_pts(r['reward'])} pts** | "
                f"{r['uses']}/{r['max_uses'] or '∞'} uses | expires {exp} | "
                f"{'✅' if r['enabled'] else '❌'}"
            )
        embed = discord.Embed(title="🎟️ Promo Codes", description="\n".join(lines), color=0x5865F2)
        await ctx.send(embed=embed)

    @promo_group.command(name="toggle")
    @admin_only()
    async def promo_toggle(self, ctx: commands.Context, code: str):
        """Enable/disable a promo code."""
        code = code.upper()
        dbc = await db.get_db()
        row = await db.get_promo(code)
        if not row:
            return await ctx.send(embed=utils.error_embed(f"Code `{code}` not found."))
        new_state = 0 if row["enabled"] else 1
        await dbc.execute("UPDATE promo_codes SET enabled=? WHERE UPPER(code)=?", (new_state, code))
        await dbc.commit()
        state_str = "enabled" if new_state else "disabled"
        await ctx.send(embed=utils.success_embed(f"Promo `{code}` is now **{state_str}**."))

    # ── Transaction history ────────────────────────────────────────────────────

    @commands.command(name="history", aliases=["txn"])
    @admin_only()
    async def history(self, ctx: commands.Context, member: discord.Member, limit: int = 10):
        """View recent transactions for a user."""
        dbc = await db.get_db()
        rows = await (await dbc.execute(
            "SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (str(member.id), min(limit, 25)),
        )).fetchall()
        if not rows:
            return await ctx.send(embed=utils.info_embed("History", "No transactions found."))
        lines = []
        for r in rows:
            r = dict(r)
            sign = "+" if r["type"] == "credit" else "-" if r["type"] == "debit" else "~"
            lines.append(
                f"`{sign}{utils.fmt_pts(r['amount'])} pts` — {r['type']} — "
                f"{r['note'] or '—'} — <t:{r['created_at']}:R>"
            )
        embed = discord.Embed(
            title=f"📋 Transactions — {member.display_name}",
            description="\n".join(lines),
            color=0x5865F2,
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))

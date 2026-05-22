"""Admin commands: .add .remove .set .reset .promo .ban .unban .mute .broadcast .setgame .setcap"""
from __future__ import annotations

import asyncio
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


    # ── User moderation ────────────────────────────────────────────────────────

    @commands.command(name="ban")
    @admin_only()
    async def ban_user(self, ctx: commands.Context, member: discord.Member, *, reason: str = ""):
        """Ban a user from using the bot. .ban @user [reason]"""
        await db.ensure_user(member.id, member.name)
        await db.ban_user(member.id, reason, str(ctx.author.id))
        embed = discord.Embed(color=0xE74C3C)
        embed.add_field(name="🔨 Banned", value=member.mention, inline=True)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        await ctx.send(embed=embed)
        try:
            await member.send(embed=discord.Embed(
                description=f"You have been banned from FlipBot. Reason: {reason or 'No reason given'}",
                color=0xE74C3C,
            ))
        except Exception:
            pass

    @commands.command(name="unban")
    @admin_only()
    async def unban_user(self, ctx: commands.Context, member: discord.Member):
        """Unban a user. .unban @user"""
        await db.unban_user(member.id)
        await ctx.send(embed=utils.success_embed(f"Unbanned {member.mention}."))

    @commands.command(name="mute")
    @admin_only()
    async def mute_user(self, ctx: commands.Context, member: discord.Member):
        """Mute a user from games. .mute @user"""
        await db.mute_user(member.id, str(ctx.author.id))
        await ctx.send(embed=utils.success_embed(f"Muted {member.mention} from games."))

    @commands.command(name="unmute")
    @admin_only()
    async def unmute_user(self, ctx: commands.Context, member: discord.Member):
        """Unmute a user. .unmute @user"""
        await db.unmute_user(member.id)
        await ctx.send(embed=utils.success_embed(f"Unmuted {member.mention}."))

    # ── Broadcast ──────────────────────────────────────────────────────────────

    @commands.command(name="broadcast")
    @admin_only()
    async def broadcast(self, ctx: commands.Context, *, message: str):
        """DM all registered users. .broadcast <message>"""
        user_ids = await db.get_all_user_ids()
        sent, failed = 0, 0
        status_msg = await ctx.send(embed=utils.info_embed("Broadcast", f"Sending to {len(user_ids)} users..."))
        for uid in user_ids:
            try:
                user = await self.bot.fetch_user(int(uid))
                if user:
                    await user.send(embed=discord.Embed(
                        title="📢 FlipBot Announcement",
                        description=message,
                        color=0x5865F2,
                    ))
                    sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.3)  # rate limit protection
        await status_msg.edit(embed=utils.success_embed(
            f"Broadcast complete. Sent: {sent} | Failed: {failed}"
        ))

    # ── Game management ────────────────────────────────────────────────────────

    @commands.group(name="setgame", invoke_without_command=True)
    @admin_only()
    async def setgame(self, ctx: commands.Context, game_id: str, setting: str, value: str):
        """Manage game settings. .setgame blackjack enabled/disabled / minbet / maxbet / rigged"""
        dbc = await db.get_db()
        game = await db.get_game_config(game_id)
        if not game:
            return await ctx.send(embed=utils.error_embed(f"Game `{game_id}` not found."))

        setting = setting.lower()
        if setting == "enabled":
            await dbc.execute("UPDATE games SET enabled=1 WHERE id=?", (game_id,))
            msg = f"Game `{game_id}` enabled."
        elif setting == "disabled":
            await dbc.execute("UPDATE games SET enabled=0 WHERE id=?", (game_id,))
            msg = f"Game `{game_id}` disabled."
        elif setting == "minbet":
            await dbc.execute("UPDATE games SET min_bet=? WHERE id=?", (float(value), game_id))
            msg = f"Min bet for `{game_id}` set to {utils.fmt_pts(float(value))} pts."
        elif setting == "maxbet":
            await dbc.execute("UPDATE games SET max_bet=? WHERE id=?", (float(value), game_id))
            msg = f"Max bet for `{game_id}` set to {utils.fmt_pts(float(value))} pts."
        elif setting == "rigged":
            pct = float(value)
            if not 0 <= pct <= 100:
                return await ctx.send(embed=utils.error_embed("Rigged chance must be 0-100."))
            await dbc.execute("UPDATE games SET rigged_chance=? WHERE id=?", (pct / 100, game_id))
            msg = f"Rigged chance for `{game_id}` set to {pct}%."
        else:
            return await ctx.send(embed=utils.error_embed(
                "Invalid setting. Use: `enabled`, `disabled`, `minbet`, `maxbet`, `rigged`"
            ))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(msg))

    @commands.command(name="games")
    @admin_only()
    async def list_games(self, ctx: commands.Context):
        """List all game configs. .games"""
        games = await db.get_all_games()
        if not games:
            return await ctx.send(embed=utils.info_embed("Games", "No games found."))
        lines = []
        for g in games:
            status = "✅" if g["enabled"] else "❌"
            lines.append(
                f"{status} `{g['id']}` — min:{utils.fmt_pts(g['min_bet'])} "
                f"max:{utils.fmt_pts(g['max_bet'])} "
                f"rigged:{int(g['rigged_chance']*100)}%"
            )
        embed = discord.Embed(title="🎮 Game Configs", description="\n".join(lines), color=0x5865F2)
        await ctx.send(embed=embed)

    # ── Balance cap management ─────────────────────────────────────────────────

    @commands.command(name="setcap")
    @admin_only()
    async def setcap(self, ctx: commands.Context, member: discord.Member, amount: float):
        """Set a balance cap for a user. .setcap @user 50000"""
        if amount <= 0:
            return await ctx.send(embed=utils.error_embed("Cap must be positive."))
        await db.set_balance_cap(member.id, amount, str(ctx.author.id))
        await ctx.send(embed=utils.success_embed(
            f"Balance cap for {member.mention} set to **{utils.fmt_pts(amount)} pts**."
        ))

    @commands.command(name="removecap")
    @admin_only()
    async def removecap(self, ctx: commands.Context, member: discord.Member):
        """Remove a user's balance cap. .removecap @user"""
        await db.remove_balance_cap(member.id)
        await ctx.send(embed=utils.success_embed(f"Balance cap for {member.mention} removed."))

    @commands.command(name="globalcap")
    @admin_only()
    async def globalcap(self, ctx: commands.Context, amount: float):
        """Set a global balance cap for all users. .globalcap 500000  (0 to remove)"""
        if amount <= 0:
            await db.set_global_setting("global_cap", "")
            return await ctx.send(embed=utils.success_embed("Global balance cap removed."))
        await db.set_global_setting("global_cap", str(amount))
        await ctx.send(embed=utils.success_embed(f"Global balance cap set to **{utils.fmt_pts(amount)} pts**."))

    @commands.command(name="viewcap")
    @admin_only()
    async def viewcap(self, ctx: commands.Context, member: discord.Member = None):
        """View balance cap info. .viewcap [@user]"""
        global_cap_str = await db.get_global_setting("global_cap", "")
        global_cap = float(global_cap_str) if global_cap_str else None

        embed = discord.Embed(title="🔒 Balance Caps", color=0x5865F2)
        embed.add_field(
            name="Global Cap",
            value=f"`{utils.fmt_pts(global_cap)} pts`" if global_cap else "None",
            inline=True,
        )
        if member:
            user_cap = await db.get_balance_cap(member.id)
            embed.add_field(
                name=f"{member.display_name}'s Cap",
                value=f"`{utils.fmt_pts(user_cap)} pts`" if user_cap else "None",
                inline=True,
            )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))

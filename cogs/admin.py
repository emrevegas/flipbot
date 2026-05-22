"""Admin prefix commands — balance/promo/ban quick ops for owner/admins.
All heavy management lives in /panel slash commands.
"""
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

    # ── Balance quick-ops ──────────────────────────────────────────────────────

    @commands.command(name="add")
    @admin_only()
    async def add_balance(self, ctx: commands.Context, member: discord.Member, amount: float, *, note: str = ""):
        """.add @user 500 [note]"""
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
        """.remove @user 200 [note]"""
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

    @commands.command(name="setbal")
    @admin_only()
    async def set_balance(self, ctx: commands.Context, member: discord.Member, amount: float, *, note: str = ""):
        """.setbal @user 1000"""
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
    async def reset_balance(self, ctx: commands.Context, member: discord.Member):
        """.resetbal @user"""
        await db.ensure_user(member.id, member.name)
        await db.set_balance(member.id, 0.0, note="admin reset", by=str(ctx.author.id))
        await ctx.send(embed=utils.success_embed(f"Reset {member.mention}'s balance to 0."))

    # ── Promo quick-ops ────────────────────────────────────────────────────────

    @commands.group(name="promo", invoke_without_command=True)
    @admin_only()
    async def promo_group(self, ctx: commands.Context):
        """Promo management. .promo create/delete/list/toggle"""
        await ctx.send_help(ctx.command)

    @promo_group.command(name="create")
    @admin_only()
    async def promo_create(self, ctx: commands.Context, code: str, reward: float, max_uses: int = 0, expire_hours: int = 0):
        """.promo create CODE 500 [max_uses] [expire_hours]"""
        code = code.upper()
        if await db.get_promo(code):
            return await ctx.send(embed=utils.error_embed(f"Code `{code}` already exists."))
        dbc = await db.get_db()
        expires_at = int(time.time()) + expire_hours * 3600 if expire_hours > 0 else None
        await dbc.execute(
            "INSERT INTO promo_codes (code, reward, max_uses, expires_at, created_by) VALUES (?,?,?,?,?)",
            (code, reward, max_uses, expires_at, str(ctx.author.id)),
        )
        await dbc.commit()
        embed = discord.Embed(title="✅ Promo Created", color=0x2ECC71)
        embed.add_field(name="Code", value=f"`{code}`", inline=True)
        embed.add_field(name="Reward", value=f"`{utils.fmt_pts(reward)} pts`", inline=True)
        embed.add_field(name="Max Uses", value=str(max_uses) if max_uses else "∞", inline=True)
        embed.add_field(name="Expires", value=f"<t:{expires_at}:R>" if expires_at else "Never", inline=True)
        await ctx.send(embed=embed)

    @promo_group.command(name="delete", aliases=["del"])
    @admin_only()
    async def promo_delete(self, ctx: commands.Context, code: str):
        """.promo delete CODE"""
        dbc = await db.get_db()
        await dbc.execute("DELETE FROM promo_codes WHERE UPPER(code)=?", (code.upper(),))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Deleted promo `{code.upper()}`."))

    @promo_group.command(name="list")
    @admin_only()
    async def promo_list(self, ctx: commands.Context):
        """.promo list"""
        dbc = await db.get_db()
        rows = await (await dbc.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")).fetchall()
        if not rows:
            return await ctx.send(embed=utils.info_embed("Promos", "No promo codes yet."))
        lines = []
        for r in rows:
            r = dict(r)
            exp = f"<t:{r['expires_at']}:R>" if r.get("expires_at") else "∞"
            icon = "✅" if r["enabled"] else "❌"
            lines.append(
                f"{icon} `{r['code']}` — **{utils.fmt_pts(r['reward'])} pts** | "
                f"{r['uses']}/{r['max_uses'] or '∞'} | {exp}"
            )
        await ctx.send(embed=discord.Embed(
            title="🎟️ Promo Codes",
            description="\n".join(lines),
            color=0x5865F2,
        ))

    @promo_group.command(name="toggle")
    @admin_only()
    async def promo_toggle(self, ctx: commands.Context, code: str):
        """.promo toggle CODE"""
        code = code.upper()
        dbc = await db.get_db()
        row = await db.get_promo(code)
        if not row:
            return await ctx.send(embed=utils.error_embed(f"Code `{code}` not found."))
        new_state = 0 if row["enabled"] else 1
        await dbc.execute("UPDATE promo_codes SET enabled=? WHERE UPPER(code)=?", (new_state, code))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(
            f"Promo `{code}` is now **{'enabled' if new_state else 'disabled'}**."
        ))

    # ── Ban / mute ─────────────────────────────────────────────────────────────

    @commands.command(name="ban")
    @admin_only()
    async def ban_user(self, ctx: commands.Context, member: discord.Member, *, reason: str = ""):
        """.ban @user [reason]"""
        await db.ensure_user(member.id, member.name)
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT OR REPLACE INTO user_bans (user_id, reason, banned_by) VALUES (?,?,?)",
            (str(member.id), reason or "No reason", str(ctx.author.id)),
        )
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Banned {member.mention}."))

    @commands.command(name="unban")
    @admin_only()
    async def unban_user(self, ctx: commands.Context, member: discord.Member):
        """.unban @user"""
        dbc = await db.get_db()
        await dbc.execute("DELETE FROM user_bans WHERE user_id=?", (str(member.id),))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Unbanned {member.mention}."))

    # ── Transaction history ────────────────────────────────────────────────────

    @commands.command(name="history", aliases=["txn"])
    @admin_only()
    async def history(self, ctx: commands.Context, member: discord.Member, limit: int = 10):
        """.history @user [limit]"""
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
                f"`{sign}{utils.fmt_pts(r['amount'])} pts` {r['type']} — "
                f"{r['note'] or '—'} <t:{r['created_at']}:R>"
            )
        await ctx.send(embed=discord.Embed(
            title=f"📋 {member.display_name}",
            description="\n".join(lines),
            color=0x5865F2,
        ))

    # ── Game settings ──────────────────────────────────────────────────────────

    @commands.group(name="game", invoke_without_command=True)
    @admin_only()
    async def game_group(self, ctx: commands.Context):
        """Game settings. .game list / .game set <id> <field> <value>"""
        await ctx.send_help(ctx.command)

    @game_group.command(name="list")
    @admin_only()
    async def game_list(self, ctx: commands.Context):
        """.game list"""
        dbc = await db.get_db()
        rows = await (await dbc.execute("SELECT * FROM games ORDER BY name")).fetchall()
        if not rows:
            return await ctx.send(embed=utils.info_embed("Games", "No games configured."))
        lines = [
            f"{'✅' if r['enabled'] else '❌'} `{r['id']}` **{r['name']}** — "
            f"min:{utils.fmt_pts(r['min_bet'])} max:{utils.fmt_pts(r['max_bet'])} "
            f"rigged:{r['rigged_chance']}%"
            for r in rows
        ]
        await ctx.send(embed=discord.Embed(
            title="🎮 Games", description="\n".join(lines), color=0x5865F2
        ))

    @game_group.command(name="set")
    @admin_only()
    async def game_set(self, ctx: commands.Context, game_id: str, field: str, value: str):
        """.game set coinflip enabled true | .game set dice minbet 50"""
        allowed = {"enabled", "min_bet", "max_bet", "rigged_chance"}
        field = field.lower()
        if field not in allowed:
            return await ctx.send(embed=utils.error_embed(
                f"Field must be one of: {', '.join(allowed)}"
            ))
        if field == "enabled":
            val = 1 if value.lower() in ("true", "1", "yes", "on") else 0
        else:
            try:
                val = float(value)
            except ValueError:
                return await ctx.send(embed=utils.error_embed("Value must be a number."))
        dbc = await db.get_db()
        await dbc.execute(f"UPDATE games SET {field}=? WHERE id=?", (val, game_id))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Set `{game_id}.{field}` = `{value}`."))

    # ── Balance cap ────────────────────────────────────────────────────────────

    @commands.command(name="setcap")
    @admin_only()
    async def set_cap(self, ctx: commands.Context, member: discord.Member, amount: float):
        """.setcap @user 5000"""
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT OR REPLACE INTO balance_caps (user_id, ceiling, enabled) VALUES (?,?,1)",
            (str(member.id), amount),
        )
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(
            f"Balance cap for {member.mention} set to **{utils.fmt_pts(amount)} pts**."
        ))

    @commands.command(name="removecap")
    @admin_only()
    async def remove_cap(self, ctx: commands.Context, member: discord.Member):
        """.removecap @user"""
        dbc = await db.get_db()
        await dbc.execute("UPDATE balance_caps SET enabled=0 WHERE user_id=?", (str(member.id),))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Balance cap removed for {member.mention}."))

    @commands.command(name="globalcap")
    @admin_only()
    async def global_cap(self, ctx: commands.Context, amount: float):
        """.globalcap 10000  (0 to disable)"""
        dbc = await db.get_db()
        enabled = 1 if amount > 0 else 0
        await dbc.execute(
            "INSERT OR REPLACE INTO global_settings (key, value) VALUES ('global_cap', ?)",
            (str(amount),),
        )
        await dbc.execute(
            "INSERT OR REPLACE INTO global_settings (key, value) VALUES ('global_cap_enabled', ?)",
            (str(enabled),),
        )
        await dbc.commit()
        if enabled:
            await ctx.send(embed=utils.success_embed(f"Global cap set to **{utils.fmt_pts(amount)} pts**."))
        else:
            await ctx.send(embed=utils.success_embed("Global cap disabled."))

    # ── Broadcast ──────────────────────────────────────────────────────────────

    @commands.command(name="broadcast")
    @admin_only()
    async def broadcast(self, ctx: commands.Context, *, message: str):
        """.broadcast <message> — DM all registered users"""
        dbc = await db.get_db()
        rows = await (await dbc.execute("SELECT user_id FROM users")).fetchall()
        sent, failed = 0, 0
        embed = discord.Embed(
            title="📢 Announcement",
            description=message,
            color=0xF59E0B,
        )
        embed.set_footer(text=f"From: {ctx.author.display_name}")
        status_msg = await ctx.send(embed=utils.info_embed("Broadcasting…", f"Sending to {len(rows)} users…"))
        import asyncio
        for row in rows:
            try:
                uid = int(row["user_id"])
                user = await ctx.bot.fetch_user(uid)
                await user.send(embed=embed)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.5)
        await status_msg.edit(embed=utils.success_embed(
            f"Broadcast complete. ✅ {sent} sent, ❌ {failed} failed."
        ))


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))

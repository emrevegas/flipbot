"""Deposit and withdrawal system."""
from __future__ import annotations

import time

import discord
from discord.ext import commands

import config
from database import db
from modules import utils


def _admin():
    async def pred(ctx: commands.Context) -> bool:
        if not utils.is_admin(ctx):
            raise commands.CheckFailure("No permission.")
        return True
    return commands.check(pred)


CASHIER_ROLE_NAME = "Cashier"


def _is_cashier(ctx: commands.Context) -> bool:
    if utils.is_admin(ctx):
        return True
    if ctx.guild:
        return any(r.name == CASHIER_ROLE_NAME for r in ctx.author.roles)
    return False


class Deposit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Deposit ────────────────────────────────────────────────────────────────

    @commands.group(name="deposit", invoke_without_command=True)
    async def deposit(self, ctx: commands.Context):
        """View deposit instructions. .deposit"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if await db.is_banned(ctx.author.id):
            return await ctx.send(embed=utils.error_embed("You are banned."))

        methods = await db.get_active_payment_methods()

        embed = discord.Embed(
            title="💳 Deposit Instructions",
            description=(
                "To deposit, open a ticket by contacting staff.\n"
                "Minimum deposit: **100 pts** ($1.00 USD)\n\n"
                f"**Conversion Rate:** 1 USD = {int(config.POINTS_PER_USD)} pts"
            ),
            color=0x2ECC71,
        )
        if methods:
            embed.add_field(
                name="Payment Methods",
                value="\n".join(f"• {m['name']}" + (f" — {m['details']}" if m.get("details") else "") for m in methods),
                inline=False,
            )
        embed.add_field(
            name="Instructions",
            value=(
                "1. Send payment to the address listed above\n"
                "2. DM a staff member with proof of payment\n"
                "3. Your balance will be credited within 24h"
            ),
            inline=False,
        )
        embed.set_footer(text="Use .deposit methods to see active payment methods")
        await ctx.send(embed=embed)

        # Log deposit request
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT INTO deposit_requests (user_id, status) VALUES (?, 'pending')",
            (str(ctx.author.id),),
        )
        await dbc.commit()

    @deposit.command(name="methods")
    async def deposit_methods(self, ctx: commands.Context):
        """List active payment methods. .deposit methods"""
        methods = await db.get_active_payment_methods()
        if not methods:
            return await ctx.send(embed=utils.info_embed("Payment Methods", "No payment methods configured."))
        embed = discord.Embed(title="💰 Payment Methods", color=0x5865F2)
        for m in methods:
            embed.add_field(
                name=m["name"],
                value=m.get("details") or "Contact staff for details",
                inline=False,
            )
        await ctx.send(embed=embed)

    # ── Withdrawal ─────────────────────────────────────────────────────────────

    @commands.command(name="withdraw", aliases=["wd"])
    async def withdraw(self, ctx: commands.Context, amount: float):
        """Request a withdrawal. .withdraw 500"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if await db.is_banned(ctx.author.id):
            return await ctx.send(embed=utils.error_embed("You are banned."))
        if amount <= 0:
            return await ctx.send(embed=utils.error_embed("Amount must be positive."))

        min_withdraw = 100.0
        if amount < min_withdraw:
            return await ctx.send(embed=utils.error_embed(f"Minimum withdrawal is **{utils.fmt_pts(min_withdraw)} pts**."))

        user = await db.get_user(ctx.author.id)
        if not user or float(user["balance"]) < amount:
            return await ctx.send(embed=utils.error_embed(
                f"Insufficient balance. You have **{utils.fmt_pts(user['balance'] if user else 0)} pts**."
            ))

        # Hold the funds
        await db.add_balance(ctx.author.id, -amount, note="withdrawal hold")
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT INTO withdrawal_requests (user_id, amount, status) VALUES (?, ?, 'pending')",
            (str(ctx.author.id), amount),
        )
        await dbc.commit()

        embed = discord.Embed(
            title="💸 Withdrawal Requested",
            description=(
                f"Your request for **{utils.fmt_pts(amount)} pts** "
                f"(${amount / config.POINTS_PER_USD:.2f} USD) has been submitted.\n\n"
                "A staff member will process it shortly. **Funds have been held.**"
            ),
            color=0xF1C40F,
        )
        embed.set_footer(text="Processing time: up to 24 hours")
        await ctx.send(embed=embed)

    # ── Cashier commands ───────────────────────────────────────────────────────

    @commands.command(name="approve")
    async def approve(self, ctx: commands.Context, member: discord.Member, amount: float, *, note: str = ""):
        """Approve a deposit or withdrawal for a user. .approve @user 500"""
        if not _is_cashier(ctx):
            return await ctx.send(embed=utils.error_embed("You need the Cashier role."))
        await db.ensure_user(member.id, member.name)
        new_bal = await db.add_balance(member.id, amount, note=note or "cashier approved deposit", by=str(ctx.author.id))
        await db.record_deposit(member.id, amount)

        # Update affiliate FTD if first deposit
        user = await db.get_user(member.id)
        if user and float(user.get("total_deposited", 0)) <= amount:
            dbc = await db.get_db()
            ref = await (await dbc.execute(
                "SELECT * FROM affiliate_refs WHERE referred_id=? AND ftd_paid=0",
                (str(member.id),),
            )).fetchone()
            if ref:
                ref = dict(ref)
                ftd_earn = amount * config.AFFILIATE_FTD_RATE
                await db.add_affiliate_earnings(ref["affiliate_id"], ftd=ftd_earn)
                await dbc.execute(
                    "UPDATE affiliate_refs SET ftd_paid=1, first_deposit=? WHERE ref_id=?",
                    (amount, ref["ref_id"]),
                )
                await dbc.commit()

        embed = discord.Embed(title="✅ Approved", color=0x2ECC71)
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Amount", value=f"`{utils.fmt_pts(amount)} pts`", inline=True)
        embed.add_field(name="New Balance", value=f"`{utils.fmt_pts(new_bal)} pts`", inline=True)
        if note:
            embed.add_field(name="Note", value=note, inline=False)
        await ctx.send(embed=embed)
        try:
            await member.send(
                embed=discord.Embed(
                    description=f"✅ Your deposit of **{utils.fmt_pts(amount)} pts** has been approved!",
                    color=0x2ECC71,
                )
            )
        except Exception:
            pass

    @commands.command(name="deny")
    async def deny(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        """Deny a withdrawal request and refund. .deny @user <reason>"""
        if not _is_cashier(ctx):
            return await ctx.send(embed=utils.error_embed("You need the Cashier role."))

        # Refund any pending withdrawal
        dbc = await db.get_db()
        pending = await (await dbc.execute(
            "SELECT * FROM withdrawal_requests WHERE user_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
            (str(member.id),),
        )).fetchone()
        if pending:
            pending = dict(pending)
            await db.add_balance(member.id, pending["amount"], note="withdrawal denied — refunded")
            await dbc.execute(
                "UPDATE withdrawal_requests SET status='denied', approved_by=?, note=? WHERE id=?",
                (str(ctx.author.id), reason, pending["id"]),
            )
            await dbc.commit()
        embed = discord.Embed(title="❌ Request Denied", color=0xE74C3C)
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await ctx.send(embed=embed)
        try:
            await member.send(
                embed=discord.Embed(
                    description=f"❌ Your withdrawal request was denied. Reason: {reason}",
                    color=0xE74C3C,
                )
            )
        except Exception:
            pass

    # ── Admin: payment method management ──────────────────────────────────────

    @commands.command(name="addmethod")
    @_admin()
    async def add_method(self, ctx: commands.Context, name: str, *, details: str = ""):
        """Add a payment method. .addmethod Bitcoin"""
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT OR REPLACE INTO payment_methods (name, details) VALUES (?, ?)",
            (name, details),
        )
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Payment method **{name}** added."))

    @commands.command(name="removemethod")
    @_admin()
    async def remove_method(self, ctx: commands.Context, name: str):
        """Remove a payment method. .removemethod Bitcoin"""
        dbc = await db.get_db()
        await dbc.execute(
            "UPDATE payment_methods SET enabled=0 WHERE LOWER(name)=LOWER(?)", (name,)
        )
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Payment method **{name}** disabled."))


async def setup(bot: commands.Bot):
    await bot.add_cog(Deposit(bot))

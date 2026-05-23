"""Deposit and withdrawal — button-based cashier system."""
from __future__ import annotations

import time
import uuid

import discord
from discord.ext import commands

import config
from database import db
from modules import flip_utils as utils

CASHIER_ROLE_NAME = "Cashier"


def _is_cashier(interaction_or_ctx) -> bool:
    if isinstance(interaction_or_ctx, discord.Interaction):
        user = interaction_or_ctx.user
        guild = interaction_or_ctx.guild
    else:
        user = interaction_or_ctx.author
        guild = interaction_or_ctx.guild
    if user.id in config.OWNER_IDS:
        return True
    if guild and hasattr(user, "guild_permissions") and user.guild_permissions.administrator:
        return True
    if guild and hasattr(user, "roles"):
        return any(r.name == CASHIER_ROLE_NAME for r in user.roles)
    return False


# ── Deposit flow views ─────────────────────────────────────────────────────────

class DepositMethodSelect(discord.ui.Select):
    def __init__(self, methods: list[dict], user_id: int):
        self.user_id = user_id
        options = [
            discord.SelectOption(label=m["name"], value=m["name"],
                                 description=m.get("details", "")[:50] or None)
            for m in methods[:25]
        ]
        super().__init__(placeholder="Select payment method…", options=options, custom_id="deposit:method")

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your panel."), ephemeral=True
            )
        method = self.values[0]
        await interaction.response.send_modal(_DepositAmountModal(method, self.user_id))


class DepositView(discord.ui.View):
    def __init__(self, methods: list[dict], user_id: int):
        super().__init__(timeout=120)
        if methods:
            self.add_item(DepositMethodSelect(methods, user_id))
        self.user_id = user_id

    @discord.ui.button(label="My Deposit History", style=discord.ButtonStyle.secondary, emoji="📋", row=1)
    async def history(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        dbc = await db.get_db()
        rows = await (await dbc.execute(
            "SELECT * FROM deposit_requests WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
            (str(self.user_id),),
        )).fetchall()
        if not rows:
            return await interaction.response.send_message(
                embed=utils.info_embed("History", "No deposits yet."), ephemeral=True
            )
        lines = []
        for r in rows:
            r = dict(r)
            status_icon = {"approved": "✅", "pending": "⏳", "denied": "❌"}.get(r["status"], "❓")
            amt = f"**{utils.fmt_pts(float(r['amount']))} pts**" if r.get("amount") else "—"
            lines.append(f"{status_icon} {amt} — {r['status']} — <t:{r['created_at']}:R>")
        await interaction.response.send_message(
            embed=discord.Embed(title="📋 Deposit History", description="\n".join(lines), color=0x5865F2),
            ephemeral=True,
        )


class _DepositAmountModal(discord.ui.Modal):
    amount_input = discord.ui.TextInput(label="Amount (pts)", placeholder="e.g. 500  (= $5.00 USD)", max_length=20)
    note_input   = discord.ui.TextInput(label="Payment note / TX ID", required=False, max_length=200)

    def __init__(self, method: str, user_id: int):
        super().__init__(title=f"Deposit via {method}")
        self.method  = method
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = float(self.amount_input.value.replace(",", ""))
        except ValueError:
            return await interaction.response.send_message(
                embed=utils.error_embed("Invalid amount."), ephemeral=True
            )
        if amount < 100:
            return await interaction.response.send_message(
                embed=utils.error_embed("Minimum deposit is **100 pts**."), ephemeral=True
            )

        req_id = str(uuid.uuid4())[:8].upper()
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT INTO deposit_requests (user_id, amount, method, status, note) VALUES (?,?,?,?,?)",
            (str(self.user_id), amount, self.method, "pending", self.note_input.value or ""),
        )
        await dbc.commit()

        # Find cashier channel or post in same channel with cashier view
        guild = interaction.guild
        cashier_ch = None
        if guild:
            cashier_ch = discord.utils.find(
                lambda c: "cashier" in c.name.lower() or "deposit" in c.name.lower(),
                guild.text_channels,
            )

        member = guild.get_member(self.user_id) if guild else None
        cashier_embed = discord.Embed(title="💳 Deposit Request", color=0xF59E0B)
        cashier_embed.add_field(name="User", value=f"<@{self.user_id}>", inline=True)
        cashier_embed.add_field(name="Amount", value=f"`{utils.fmt_pts(amount)} pts`", inline=True)
        cashier_embed.add_field(name="Method", value=self.method, inline=True)
        cashier_embed.add_field(name="Ref ID", value=f"`{req_id}`", inline=True)
        if self.note_input.value:
            cashier_embed.add_field(name="TX Note", value=self.note_input.value, inline=False)
        cashier_embed.set_footer(text="Use the buttons below to approve or deny.")

        cashier_view = _CashierDepositView(self.user_id, amount, req_id)
        if cashier_ch:
            await cashier_ch.send(embed=cashier_embed, view=cashier_view)
        else:
            await interaction.channel.send(embed=cashier_embed, view=cashier_view)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="⏳ Deposit Submitted",
                description=(
                    f"**Ref ID:** `{req_id}`\n"
                    f"Amount: **{utils.fmt_pts(amount)} pts** via **{self.method}**\n\n"
                    "Staff will review and approve shortly."
                ),
                color=0x2ECC71,
            ),
            ephemeral=True,
        )


# ── Withdrawal flow ────────────────────────────────────────────────────────────

class WithdrawView(discord.ui.View):
    def __init__(self, user_id: int, balance: float):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.balance = balance

    @discord.ui.button(label="Request Withdrawal", style=discord.ButtonStyle.danger, emoji="💸")
    async def request_wd(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        await interaction.response.send_modal(_WithdrawModal(self.user_id, self.balance))

    @discord.ui.button(label="Withdrawal History", style=discord.ButtonStyle.secondary, emoji="📋")
    async def wd_history(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        dbc = await db.get_db()
        rows = await (await dbc.execute(
            "SELECT * FROM withdrawal_requests WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
            (str(self.user_id),),
        )).fetchall()
        if not rows:
            return await interaction.response.send_message(
                embed=utils.info_embed("History", "No withdrawals yet."), ephemeral=True
            )
        lines = []
        for r in rows:
            r = dict(r)
            status_icon = {"approved": "✅", "pending": "⏳", "denied": "❌"}.get(r["status"], "❓")
            lines.append(
                f"{status_icon} **{utils.fmt_pts(float(r['amount']))} pts** — "
                f"{r['status']} — <t:{r['created_at']}:R>"
            )
        await interaction.response.send_message(
            embed=discord.Embed(title="📋 Withdrawal History", description="\n".join(lines), color=0x5865F2),
            ephemeral=True,
        )


class _WithdrawModal(discord.ui.Modal):
    amount_input  = discord.ui.TextInput(label="Amount (pts)", placeholder="e.g. 500", max_length=20)
    method_input  = discord.ui.TextInput(label="Payment method / address", max_length=200)

    def __init__(self, user_id: int, balance: float):
        super().__init__(title="Withdrawal Request")
        self.user_id = user_id
        self.balance = balance

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = float(self.amount_input.value.replace(",", ""))
        except ValueError:
            return await interaction.response.send_message(embed=utils.error_embed("Invalid amount."), ephemeral=True)

        if amount < 100:
            return await interaction.response.send_message(
                embed=utils.error_embed("Minimum withdrawal is **100 pts**."), ephemeral=True
            )
        if amount > self.balance:
            return await interaction.response.send_message(
                embed=utils.error_embed(
                    f"Insufficient balance. You have **{utils.fmt_pts(self.balance)} pts**."
                ),
                ephemeral=True,
            )

        req_id = str(uuid.uuid4())[:8].upper()
        await db.add_balance(self.user_id, -amount, note="withdrawal hold")
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT INTO withdrawal_requests (user_id, amount, method, status) VALUES (?,?,?,?)",
            (str(self.user_id), amount, self.method_input.value, "pending"),
        )
        await dbc.commit()

        guild = interaction.guild
        cashier_ch = None
        if guild:
            cashier_ch = discord.utils.find(
                lambda c: "cashier" in c.name.lower() or "withdraw" in c.name.lower(),
                guild.text_channels,
            )

        cashier_embed = discord.Embed(title="💸 Withdrawal Request", color=0xE74C3C)
        cashier_embed.add_field(name="User", value=f"<@{self.user_id}>", inline=True)
        cashier_embed.add_field(name="Amount", value=f"`{utils.fmt_pts(amount)} pts` (${amount/config.POINTS_PER_USD:.2f})", inline=True)
        cashier_embed.add_field(name="Method/Address", value=self.method_input.value, inline=False)
        cashier_embed.add_field(name="Ref ID", value=f"`{req_id}`", inline=True)
        cashier_embed.set_footer(text="Funds held. Approve = send & release | Deny = refund.")

        cashier_view = _CashierWithdrawView(self.user_id, amount, req_id)
        target_ch = cashier_ch or interaction.channel
        await target_ch.send(embed=cashier_embed, view=cashier_view)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="⏳ Withdrawal Submitted",
                description=(
                    f"**Ref ID:** `{req_id}`\n"
                    f"Amount: **{utils.fmt_pts(amount)} pts** held.\n\n"
                    "Staff will process shortly."
                ),
                color=0xF1C40F,
            ),
            ephemeral=True,
        )


# ── Cashier action views ───────────────────────────────────────────────────────

class _CashierDepositView(discord.ui.View):
    def __init__(self, user_id: int, amount: float, ref_id: str):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.amount  = amount
        self.ref_id  = ref_id

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, _):
        if not _is_cashier(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Cashier only."), ephemeral=True)
        new_bal = await db.add_balance(self.user_id, self.amount, note=f"deposit approved {self.ref_id}", by=str(interaction.user.id))
        await db.record_deposit(self.user_id, self.amount)
        await _handle_affiliate_ftd(self.user_id, self.amount)
        await _update_request_status("deposit_requests", self.user_id, "approved", str(interaction.user.id))
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Deposit Approved",
                description=f"<@{self.user_id}> | **{utils.fmt_pts(self.amount)} pts** | New balance: `{utils.fmt_pts(new_bal)} pts`\nApproved by {interaction.user.mention}",
                color=0x2ECC71,
            ),
            view=None,
        )
        try:
            guild = interaction.guild
            member = guild.get_member(self.user_id) if guild else None
            if member:
                await member.send(embed=discord.Embed(
                    description=f"✅ Your deposit of **{utils.fmt_pts(self.amount)} pts** (Ref: `{self.ref_id}`) has been approved!\nNew balance: **{utils.fmt_pts(new_bal)} pts**",
                    color=0x2ECC71,
                ))
        except Exception:
            pass

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, _):
        if not _is_cashier(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Cashier only."), ephemeral=True)
        await interaction.response.send_modal(_DenyReasonModal(self.user_id, self.amount, self.ref_id, is_deposit=True))


class _CashierWithdrawView(discord.ui.View):
    def __init__(self, user_id: int, amount: float, ref_id: str):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.amount  = amount
        self.ref_id  = ref_id

    @discord.ui.button(label="✅ Approve (Paid)", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, _):
        if not _is_cashier(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Cashier only."), ephemeral=True)
        await _update_request_status("withdrawal_requests", self.user_id, "approved", str(interaction.user.id))
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Withdrawal Sent",
                description=f"<@{self.user_id}> | **{utils.fmt_pts(self.amount)} pts** paid.\nApproved by {interaction.user.mention}",
                color=0x2ECC71,
            ),
            view=None,
        )
        try:
            guild = interaction.guild
            member = guild.get_member(self.user_id) if guild else None
            if member:
                await member.send(embed=discord.Embed(
                    description=f"✅ Your withdrawal of **{utils.fmt_pts(self.amount)} pts** (Ref: `{self.ref_id}`) has been sent!",
                    color=0x2ECC71,
                ))
        except Exception:
            pass

    @discord.ui.button(label="❌ Deny (Refund)", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, _):
        if not _is_cashier(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Cashier only."), ephemeral=True)
        await interaction.response.send_modal(_DenyReasonModal(self.user_id, self.amount, self.ref_id, is_deposit=False))


class _DenyReasonModal(discord.ui.Modal):
    reason_input = discord.ui.TextInput(label="Reason", required=False, max_length=200)

    def __init__(self, user_id: int, amount: float, ref_id: str, *, is_deposit: bool):
        super().__init__(title="Deny Reason (optional)")
        self.user_id    = user_id
        self.amount     = amount
        self.ref_id     = ref_id
        self.is_deposit = is_deposit

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason_input.value or "No reason given"
        table  = "deposit_requests" if self.is_deposit else "withdrawal_requests"
        await _update_request_status(table, self.user_id, "denied", str(interaction.user.id))
        if not self.is_deposit:
            await db.add_balance(self.user_id, self.amount, note=f"withdrawal refunded {self.ref_id}")
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="❌ Request Denied",
                description=f"<@{self.user_id}> | **{utils.fmt_pts(self.amount)} pts**\nReason: {reason}\nDenied by {interaction.user.mention}",
                color=0xE74C3C,
            ),
            view=None,
        )
        try:
            guild = interaction.guild
            member = guild.get_member(self.user_id) if guild else None
            if member:
                msg = "refunded" if not self.is_deposit else "not credited"
                await member.send(embed=discord.Embed(
                    description=f"❌ Your {'withdrawal' if not self.is_deposit else 'deposit'} request (Ref: `{self.ref_id}`) was denied — {msg}.\nReason: {reason}",
                    color=0xE74C3C,
                ))
        except Exception:
            pass


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _handle_affiliate_ftd(user_id: int, amount: float):
    try:
        user = await db.get_user(user_id)
        if not user or float(user.get("total_deposited", 0)) > amount:
            return
        dbc = await db.get_db()
        ref = await (await dbc.execute(
            "SELECT * FROM affiliate_refs WHERE referred_id=? AND ftd_paid=0",
            (str(user_id),),
        )).fetchone()
        if not ref:
            return
        ref = dict(ref)
        ftd_earn = amount * config.AFFILIATE_FTD_RATE
        await db.add_affiliate_earnings(ref["affiliate_id"], ftd=ftd_earn)
        await dbc.execute(
            "UPDATE affiliate_refs SET ftd_paid=1, first_deposit=? WHERE ref_id=?",
            (amount, ref["ref_id"]),
        )
        await dbc.commit()
    except Exception:
        pass


async def _update_request_status(table: str, user_id: int, status: str, by: str):
    dbc = await db.get_db()
    await dbc.execute(
        f"UPDATE {table} SET status=?, approved_by=? WHERE user_id=? AND status='pending'",
        (status, by, str(user_id)),
    )
    await dbc.commit()


# ── Cog ────────────────────────────────────────────────────────────────────────

class Deposit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="deposit", aliases=["dep"])
    async def deposit(self, ctx: commands.Context):
        """Open the deposit panel. .deposit"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if await db.is_banned(ctx.author.id):
            return await ctx.send(embed=utils.error_embed("You are banned."))

        methods = await db.get_active_payment_methods()
        embed = discord.Embed(
            title="💳 Deposit",
            description=(
                f"**Rate:** {int(config.POINTS_PER_USD)} pts = $1.00 USD\n"
                f"**Minimum:** 100 pts ($1.00)\n\n"
                "Select a payment method below to submit a deposit request."
            ),
            color=0x2ECC71,
        )
        if methods:
            embed.add_field(
                name="Available Methods",
                value="\n".join(f"• **{m['name']}**" + (f" — {m['details'][:60]}" if m.get("details") else "") for m in methods),
                inline=False,
            )
        else:
            embed.add_field(name="Methods", value="Contact staff — no self-serve methods configured yet.", inline=False)

        view = DepositView(methods, ctx.author.id)
        await ctx.send(embed=embed, view=view)

    @commands.command(name="withdraw", aliases=["wd"])
    async def withdraw(self, ctx: commands.Context):
        """Open the withdrawal panel. .withdraw"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if await db.is_banned(ctx.author.id):
            return await ctx.send(embed=utils.error_embed("You are banned."))

        user = await db.get_user(ctx.author.id)
        balance = float(user["balance"]) if user else 0.0

        embed = discord.Embed(
            title="💸 Withdrawal",
            description=(
                f"**Your balance:** `{utils.fmt_pts(balance)} pts` (${balance/config.POINTS_PER_USD:.2f})\n"
                f"**Minimum:** 100 pts\n\n"
                "Click below to request a withdrawal. Funds will be held until staff approves."
            ),
            color=0xF59E0B,
        )
        view = WithdrawView(ctx.author.id, balance)
        await ctx.send(embed=embed, view=view)

    @commands.command(name="addmethod")
    async def add_method(self, ctx: commands.Context, name: str, *, details: str = ""):
        """Admin: add a payment method. .addmethod Bitcoin <address>"""
        if not utils.is_admin(ctx):
            return await ctx.send(embed=utils.error_embed("Admins only."))
        dbc = await db.get_db()
        await dbc.execute("INSERT OR REPLACE INTO payment_methods (name, details) VALUES (?, ?)", (name, details))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Payment method **{name}** added."))

    @commands.command(name="removemethod")
    async def remove_method(self, ctx: commands.Context, name: str):
        """Admin: disable a payment method."""
        if not utils.is_admin(ctx):
            return await ctx.send(embed=utils.error_embed("Admins only."))
        dbc = await db.get_db()
        await dbc.execute("UPDATE payment_methods SET enabled=0 WHERE LOWER(name)=LOWER(?)", (name,))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Payment method **{name}** disabled."))

    @commands.command(name="pending")
    async def pending_requests(self, ctx: commands.Context):
        """Cashier: view pending deposit/withdrawal requests."""
        if not _is_cashier(ctx):
            return await ctx.send(embed=utils.error_embed("Cashier only."))
        dbc = await db.get_db()
        deps = await (await dbc.execute(
            "SELECT * FROM deposit_requests WHERE status='pending' ORDER BY created_at DESC LIMIT 10"
        )).fetchall()
        wds = await (await dbc.execute(
            "SELECT * FROM withdrawal_requests WHERE status='pending' ORDER BY created_at DESC LIMIT 10"
        )).fetchall()
        embed = discord.Embed(title="⏳ Pending Requests", color=0xF59E0B)
        if deps:
            lines = [f"• <@{d['user_id']}> — `{utils.fmt_pts(float(d['amount']))} pts` via {d.get('method','?')} — <t:{d['created_at']}:R>" for d in deps]
            embed.add_field(name=f"Deposits ({len(deps)})", value="\n".join(lines), inline=False)
        if wds:
            lines = [f"• <@{w['user_id']}> — `{utils.fmt_pts(float(w['amount']))} pts` — <t:{w['created_at']}:R>" for w in wds]
            embed.add_field(name=f"Withdrawals ({len(wds)})", value="\n".join(lines), inline=False)
        if not deps and not wds:
            embed.description = "No pending requests."
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Deposit(bot))

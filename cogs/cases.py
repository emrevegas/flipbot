"""Cases system — .cases opens a full button-based UI."""
from __future__ import annotations

import asyncio
import random

import discord
from discord.ext import commands

from database import db
from modules import image_gen, utils


# ── Case browser view ──────────────────────────────────────────────────────────

class CaseBrowserView(discord.ui.View):
    """Persistent case browser: select a case, choose quantity, open."""

    def __init__(self, cases: list[dict], user_id: int, user_balance: float):
        super().__init__(timeout=120)
        self.cases        = cases
        self.user_id      = user_id
        self.user_balance = user_balance
        self.selected     = None

        if cases:
            self.add_item(_CaseSelect(cases, user_id))

    async def _refresh(self, interaction: discord.Interaction):
        """Rebuild embed after selection changes."""
        embed = _browser_embed(self.cases, self.user_balance, self.selected)
        await interaction.response.edit_message(embed=embed, view=self)


class _CaseSelect(discord.ui.Select):
    def __init__(self, cases: list[dict], user_id: int):
        self.user_id = user_id
        options = [
            discord.SelectOption(
                label=c["name"],
                value=str(c["id"]),
                description=f"Price: {utils.fmt_pts(float(c['price']))} pts",
                emoji="📦",
            )
            for c in cases[:25]
        ]
        super().__init__(placeholder="Select a case to preview…", options=options, custom_id="cases:select")

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your panel."), ephemeral=True
            )
        view: CaseBrowserView = self.view
        case_id = int(self.values[0])
        view.selected = next((c for c in view.cases if c["id"] == case_id), None)
        # rebuild view with open buttons
        view.clear_items()
        view.add_item(_CaseSelect(view.cases, self.user_id))
        if view.selected:
            view.add_item(_OpenButton(view.selected, self.user_id, 1))
            view.add_item(_OpenButton(view.selected, self.user_id, 3))
            view.add_item(_OpenButton(view.selected, self.user_id, 5))
            view.add_item(_PreviewButton(view.selected, self.user_id))
        embed = _browser_embed(view.cases, view.user_balance, view.selected)
        await interaction.response.edit_message(embed=embed, view=view)


class _OpenButton(discord.ui.Button):
    def __init__(self, case: dict, user_id: int, count: int):
        price = float(case["price"]) * count
        super().__init__(
            label=f"Open ×{count}  ({utils.fmt_pts(price)} pts)",
            style=discord.ButtonStyle.primary if count == 1 else discord.ButtonStyle.secondary,
            emoji="🎰",
            row=1,
        )
        self.case    = case
        self.user_id = user_id
        self.count   = count

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your panel."), ephemeral=True
            )
        await interaction.response.defer()
        await _do_open(interaction, self.case, self.count)


class _PreviewButton(discord.ui.Button):
    def __init__(self, case: dict, user_id: int):
        super().__init__(label="Preview Items", style=discord.ButtonStyle.secondary, emoji="🔍", row=2)
        self.case    = case
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your panel."), ephemeral=True
            )
        items = await db.get_case_items(self.case["id"])
        if not items:
            return await interaction.response.send_message(
                embed=utils.info_embed("Items", "No items in this case yet."), ephemeral=True
            )
        total_weight = sum(float(i["chance"]) for i in items)
        lines = []
        for item in sorted(items, key=lambda x: float(x["item_value"]), reverse=True):
            pct = (float(item["chance"]) / total_weight * 100) if total_weight else 0
            lines.append(
                f"• **{item['item_name']}** — `{utils.fmt_pts(float(item['item_value']))} pts` — {pct:.1f}%"
            )
        embed = discord.Embed(
            title=f"📦 {self.case['name']} — Items",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        embed.set_footer(text=f"Case price: {utils.fmt_pts(float(self.case['price']))} pts")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Open logic ─────────────────────────────────────────────────────────────────

async def _do_open(interaction: discord.Interaction, case: dict, count: int):
    user = await db.get_user(interaction.user.id)
    if not user:
        return await interaction.followup.send(embed=utils.error_embed("Register first."), ephemeral=True)

    total_cost = float(case["price"]) * count
    if float(user["balance"]) < total_cost:
        return await interaction.followup.send(
            embed=utils.error_embed(
                f"Insufficient balance. Cost: **{utils.fmt_pts(total_cost)} pts**, "
                f"you have **{utils.fmt_pts(float(user['balance']))} pts**."
            ),
            ephemeral=True,
        )

    items = await db.get_case_items(case["id"])
    if not items:
        return await interaction.followup.send(
            embed=utils.error_embed("This case has no items."), ephemeral=True
        )

    await db.add_balance(interaction.user.id, -total_cost, note=f"case open x{count} {case['name']}")

    total_weight = sum(float(i["chance"]) for i in items)
    won_items = []
    total_value = 0.0
    for _ in range(count):
        roll = random.uniform(0, total_weight)
        cum = 0.0
        item = items[-1]
        for it in items:
            cum += float(it["chance"])
            if roll <= cum:
                item = it
                break
        won_items.append(item)
        val = float(item["item_value"])
        total_value += val
        await db.add_balance(interaction.user.id, val, note=f"case item: {item['item_name']}")

    last = won_items[-1]
    profit = total_value - total_cost
    profit_str = f"{'🟢 +' if profit >= 0 else '🔴 -'}{utils.fmt_pts(abs(profit))} pts"

    # render image card
    buf = image_gen.render_case_open_card(last["item_name"], float(last["item_value"]), case["name"])

    content = (
        f"🎰 **{interaction.user.display_name}** opened **{count}× {case['name']}**\n"
        f"Total value: **{utils.fmt_pts(total_value)} pts** | {profit_str}"
    )
    if count > 1:
        item_lines = "\n".join(f"• {i['item_name']} — {utils.fmt_pts(float(i['item_value']))} pts" for i in won_items)
        content += f"\n\n{item_lines}"

    await interaction.followup.send(content=content, file=discord.File(buf, "case_open.png"))

    # refresh panel balance
    fresh_user = await db.get_user(interaction.user.id)
    if interaction.message:
        try:
            all_cases = await db.get_all_cases()
            new_bal = float(fresh_user["balance"]) if fresh_user else 0
            view = CaseBrowserView(all_cases, interaction.user.id, new_bal)
            embed = _browser_embed(all_cases, new_bal, case)
            await interaction.message.edit(embed=embed, view=view)
        except Exception:
            pass


# ── Embed builder ──────────────────────────────────────────────────────────────

def _browser_embed(cases: list[dict], balance: float, selected: dict | None) -> discord.Embed:
    embed = discord.Embed(title="📦 Case Store", color=0xF1C40F)
    embed.add_field(name="Your Balance", value=f"`{utils.fmt_pts(balance)} pts`", inline=True)
    embed.add_field(name="Cases Available", value=str(len(cases)), inline=True)

    if selected:
        embed.add_field(name="\u200b", value="\u200b", inline=False)
        embed.add_field(
            name=f"📦 {selected['name']}",
            value=f"Price: **{utils.fmt_pts(float(selected['price']))} pts**",
            inline=False,
        )
    else:
        embed.description = "Select a case from the dropdown to preview and open it."
    return embed


# ── Cog ────────────────────────────────────────────────────────────────────────

class Cases(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="cases", aliases=["case"])
    async def cases(self, ctx: commands.Context):
        """Open the case store. .cases"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if await db.is_banned(ctx.author.id):
            return await ctx.send(embed=utils.error_embed("You are banned."))

        all_cases = await db.get_all_cases()
        user = await db.get_user(ctx.author.id)
        balance = float(user["balance"]) if user else 0.0

        if not all_cases:
            return await ctx.send(embed=utils.info_embed(
                "📦 Cases", "No cases available yet. Check back later!"
            ))

        view  = CaseBrowserView(all_cases, ctx.author.id, balance)
        embed = _browser_embed(all_cases, balance, None)
        await ctx.send(embed=embed, view=view)

    # ── Admin case management ──────────────────────────────────────────────────

    @commands.group(name="caseadmin", aliases=["caadmin"], invoke_without_command=True)
    async def case_admin(self, ctx: commands.Context):
        """Admin: manage cases. Subcommands: create, additem, remove, items"""
        if not utils.is_admin(ctx):
            return await ctx.send(embed=utils.error_embed("Admins only."))
        await ctx.send_help(ctx.command)

    @case_admin.command(name="create")
    async def case_create(self, ctx: commands.Context, name: str, price: float):
        """Create a new case. .caseadmin create starter 100"""
        if not utils.is_admin(ctx):
            return await ctx.send(embed=utils.error_embed("Admins only."))
        existing = await db.get_case(name)
        if existing:
            return await ctx.send(embed=utils.error_embed(f"Case `{name}` already exists."))
        dbc = await db.get_db()
        await dbc.execute("INSERT INTO cases (name, price) VALUES (?, ?)", (name, price))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Case **{name}** created — price: {utils.fmt_pts(price)} pts."))

    @case_admin.command(name="additem")
    async def case_add_item(self, ctx: commands.Context, case_name: str, value: float, chance: float, *, item_name: str):
        """Add item to case. .caseadmin additem starter 500 2.0 Gold Bar"""
        if not utils.is_admin(ctx):
            return await ctx.send(embed=utils.error_embed("Admins only."))
        case = await db.get_case(case_name)
        if not case:
            return await ctx.send(embed=utils.error_embed(f"Case `{case_name}` not found."))
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT INTO case_items (case_id, item_name, item_value, chance) VALUES (?,?,?,?)",
            (case["id"], item_name, value, chance),
        )
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(
            f"Added **{item_name}** (value: {utils.fmt_pts(value)} pts, weight: {chance}) to **{case_name}**."
        ))

    @case_admin.command(name="remove")
    async def case_remove(self, ctx: commands.Context, name: str):
        """Delete a case. .caseadmin remove starter"""
        if not utils.is_admin(ctx):
            return await ctx.send(embed=utils.error_embed("Admins only."))
        dbc = await db.get_db()
        await dbc.execute("DELETE FROM cases WHERE LOWER(name)=LOWER(?)", (name,))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Case **{name}** removed."))

    @case_admin.command(name="items")
    async def case_items(self, ctx: commands.Context, name: str):
        """List items in a case. .caseadmin items starter"""
        if not utils.is_admin(ctx):
            return await ctx.send(embed=utils.error_embed("Admins only."))
        case = await db.get_case(name)
        if not case:
            return await ctx.send(embed=utils.error_embed(f"Case `{name}` not found."))
        items = await db.get_case_items(case["id"])
        if not items:
            return await ctx.send(embed=utils.info_embed("Items", "No items in this case."))
        total_weight = sum(float(i["chance"]) for i in items)
        lines = [
            f"`{i['id']}` **{i['item_name']}** — {utils.fmt_pts(float(i['item_value']))} pts — "
            f"weight {i['chance']} ({float(i['chance'])/total_weight*100:.1f}%)"
            for i in items
        ]
        await ctx.send(embed=discord.Embed(
            title=f"📦 {case['name']} Items",
            description="\n".join(lines),
            color=0xF1C40F,
        ))


async def setup(bot: commands.Bot):
    await bot.add_cog(Cases(bot))

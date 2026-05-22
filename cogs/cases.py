"""Cases system — open cases to win items."""
from __future__ import annotations

import asyncio
import random
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


class Cases(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="cases")
    async def list_cases(self, ctx: commands.Context):
        """List available cases. .cases"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        cases = await db.get_all_cases()
        if not cases:
            return await ctx.send(embed=utils.info_embed("Cases", "No cases available."))
        embed = discord.Embed(title="🎁 Available Cases", color=0xF1C40F)
        for c in cases:
            items = await db.get_case_items(c["id"])
            val_range = ""
            if items:
                vals = [float(i["item_value"]) for i in items]
                val_range = f" | Items: {utils.fmt_pts(min(vals))} – {utils.fmt_pts(max(vals))} pts"
            embed.add_field(
                name=f"📦 {c['name']}",
                value=f"Price: **{utils.fmt_pts(c['price'])} pts**{val_range}\nOpen with: `.open {c['name'].lower()}`",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="open")
    async def open_case(self, ctx: commands.Context, case_name: str, count: int = 1):
        """Open a case. .open starter [count]"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if await db.is_banned(ctx.author.id):
            return await ctx.send(embed=utils.error_embed("You are banned."))

        case = await db.get_case(case_name)
        if not case:
            return await ctx.send(embed=utils.error_embed(f"Case `{case_name}` not found."))

        count = max(1, min(count, 10))
        total_cost = case["price"] * count
        user = await db.ensure_user(ctx.author.id, ctx.author.name)
        if float(user["balance"]) < total_cost:
            return await ctx.send(embed=utils.error_embed(
                f"Insufficient balance. Cost: **{utils.fmt_pts(total_cost)} pts**."
            ))

        items = await db.get_case_items(case["id"])
        if not items:
            return await ctx.send(embed=utils.error_embed("This case has no items yet."))

        await db.add_balance(ctx.author.id, -total_cost, note=f"case open x{count}")

        total_value = 0.0
        last_item = None
        for i in range(count):
            item = self._roll_item(items)
            last_item = item
            val = float(item["item_value"])
            total_value += val
            await db.add_balance(ctx.author.id, val, note=f"case item: {item['item_name']}")

        # Show card for last item (or only item)
        loop = asyncio.get_event_loop()
        img_buf = await loop.run_in_executor(
            None, image_gen.render_case_open_card,
            last_item["item_name"], float(last_item["item_value"]), case["name"],
        )
        profit = total_value - total_cost
        content = (
            f"🎁 Opened **{count}x {case['name']}** | "
            f"Total value: **{utils.fmt_pts(total_value)} pts** | "
            f"{'Profit' if profit >= 0 else 'Loss'}: **{utils.fmt_pts(abs(profit))} pts**"
        )
        if count > 1:
            content += f"\n*(Showing last item)*"
        await ctx.send(content=content, file=discord.File(img_buf, "case_open.png"))

    def _roll_item(self, items: list[dict]) -> dict:
        total_weight = sum(float(i["chance"]) for i in items)
        roll = random.uniform(0, total_weight)
        cumulative = 0
        for item in items:
            cumulative += float(item["chance"])
            if roll <= cumulative:
                return item
        return items[-1]

    # ── Admin case management ──────────────────────────────────────────────────

    @commands.group(name="case", invoke_without_command=True)
    @_admin()
    async def case_group(self, ctx: commands.Context):
        """Case management. Subcommands: create, additem, remove"""
        await ctx.send_help(ctx.command)

    @case_group.command(name="create")
    @_admin()
    async def case_create(self, ctx: commands.Context, name: str, price: float):
        """Create a case. .case create starter 100"""
        dbc = await db.get_db()
        existing = await db.get_case(name)
        if existing:
            return await ctx.send(embed=utils.error_embed(f"Case `{name}` already exists."))
        await dbc.execute(
            "INSERT INTO cases (name, price) VALUES (?, ?)", (name, price)
        )
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Case **{name}** created (price: {utils.fmt_pts(price)} pts)."))

    @case_group.command(name="additem")
    @_admin()
    async def case_add_item(self, ctx: commands.Context, case_name: str, item_name: str, value: float, chance: float = 1.0):
        """Add item to a case. .case additem starter "Gold Coin" 500 2.0"""
        case = await db.get_case(case_name)
        if not case:
            return await ctx.send(embed=utils.error_embed(f"Case `{case_name}` not found."))
        dbc = await db.get_db()
        await dbc.execute(
            "INSERT INTO case_items (case_id, item_name, item_value, chance) VALUES (?, ?, ?, ?)",
            (case["id"], item_name, value, chance),
        )
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(
            f"Added **{item_name}** (value: {utils.fmt_pts(value)} pts, chance: {chance}) to **{case_name}**."
        ))

    @case_group.command(name="remove")
    @_admin()
    async def case_remove(self, ctx: commands.Context, name: str):
        """Delete a case."""
        dbc = await db.get_db()
        await dbc.execute("DELETE FROM cases WHERE LOWER(name)=LOWER(?)", (name,))
        await dbc.commit()
        await ctx.send(embed=utils.success_embed(f"Case **{name}** removed."))


async def setup(bot: commands.Bot):
    await bot.add_cog(Cases(bot))

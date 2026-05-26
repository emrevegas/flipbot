"""Daily reward — .daily and .set daily admin configuration."""

from __future__ import annotations

import discord
from discord.ext import commands

from database import db
from modules import daily_rewards as daily
from modules import flip_utils as utils
from modules import server_tag
from modules.database import check_permission
from modules.player import Player


def panel_admin_only():
    async def pred(ctx: commands.Context) -> bool:
        if check_permission(str(ctx.author.id), "admin"):
            raise commands.CheckFailure("No permission.")
        return True

    return commands.check(pred)


class Daily(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="daily")
    async def daily_claim(self, ctx: commands.Context):
        """Claim your daily reward. Requires custom status if configured."""
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return await ctx.send(
                embed=utils.error_embed("Daily can only be used in a server."),
                delete_after=10,
            )

        cfg = daily.get_config()
        if not cfg.get("enabled", True):
            return await ctx.send(
                embed=utils.error_embed("Daily rewards are disabled right now."),
                delete_after=10,
            )

        await db.ensure_user(ctx.author.id, ctx.author.name)

        ok_req, req_err = await daily.check_daily_requirements(
            ctx.author, ctx.guild, ctx.author.id,
        )
        if not ok_req:
            return await ctx.send(embed=utils.error_embed(req_err), delete_after=12)

        can, remain = daily.can_claim(ctx.author.id, cfg)
        if not can:
            hours = remain // 3600
            mins = (remain % 3600) // 60
            return await ctx.send(
                embed=utils.error_embed(
                    f"You already claimed daily. Try again in **{hours}h {mins}m**."
                ),
                delete_after=10,
            )

        amount, label = daily.compute_reward(ctx.author, cfg)
        if amount <= 0:
            return await ctx.send(
                embed=utils.error_embed(
                    "Daily reward is not configured yet. Ask staff to run `.set daily <amount>`."
                ),
                delete_after=10,
            )

        player = Player(ctx.author.id)
        player.add_balance(
            "real",
            float(amount),
            by="system",
            reason=f"Daily reward ({label})",
        )
        daily.record_claim(ctx.author.id, amount, label)
        new_bal = player.get_balance("real")

        embed = discord.Embed(
            title="☀️ Daily Reward",
            description=(
                f"You received **{utils.fmt_pts(amount)} pts** ({label}).\n"
                f"Balance: **{utils.fmt_pts(new_bal)} pts**"
            ),
            color=0xF1C40F,
        )
        cd_h = int(cfg.get("cooldown_hours", 24) or 24)
        embed.set_footer(text=f"Next claim in {cd_h} hours.")
        await ctx.send(embed=embed)

        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.group(name="set", invoke_without_command=True)
    @panel_admin_only()
    async def set_group(self, ctx: commands.Context):
        await ctx.send(
            embed=utils.info_embed(
                "Set commands",
                "`.set daily <pts>` — default reward\n"
                "`.set daily booster <pts>` — server booster reward\n"
                "`.set daily <role_id> <pts>` — role tier reward\n"
                "`.set daily status <keywords>` — custom status requirement\n"
                "`.set daily tag on|off` — Server Tag required (promo + daily)\n"
                "`.set daily show` — current settings",
            )
        )

    @set_group.command(name="daily")
    @panel_admin_only()
    async def set_daily(self, ctx: commands.Context, *args: str):
        """
        .set daily 100
        .set daily booster 250
        .set daily 123456789012345678 500
        .set daily status vegas, flip
        .set daily show
        """
        if not args:
            return await ctx.send(embed=utils.error_embed("Missing arguments. Use `.set daily show`."))

        head = args[0].lower()

        if head in ("show", "list", "config"):
            summary = daily.format_config_summary()
            tag_on = server_tag.require_server_tag_enabled()
            summary += f"\n\n**Server tag required:** {'Yes (promo + daily)' if tag_on else 'No'}"
            return await ctx.send(embed=utils.info_embed("Daily settings", summary))

        if head == "tag":
            if len(args) < 2:
                return await ctx.send(
                    embed=utils.error_embed("Usage: `.set daily tag on` or `.set daily tag off`")
                )
            mode = args[1].lower()
            if mode in ("on", "enable", "true", "1"):
                server_tag.set_require_server_tag(True)
                return await ctx.send(
                    embed=utils.success_embed(
                        "Server Tag is now **required** for `.daily` and `.redeem`."
                    )
                )
            if mode in ("off", "disable", "false", "0"):
                server_tag.set_require_server_tag(False)
                return await ctx.send(
                    embed=utils.success_embed(
                        "Server Tag requirement **disabled** for `.daily` and `.redeem`."
                    )
                )
            return await ctx.send(embed=utils.error_embed("Use `on` or `off`."))

        if head == "booster":
            if len(args) < 2:
                return await ctx.send(embed=utils.error_embed("Usage: `.set daily booster <amount>`"))
            try:
                amount = int(float(args[1]))
            except ValueError:
                return await ctx.send(embed=utils.error_embed("Invalid amount."))
            daily.set_booster_amount(amount)
            return await ctx.send(
                embed=utils.success_embed(f"Booster daily set to **{amount:,} pts**.")
            )

        if head == "status":
            keywords = " ".join(args[1:]).strip()
            daily.set_status_requirement(keywords)
            if keywords:
                msg = f"Daily status requirement: **{keywords}**"
            else:
                msg = "Daily status requirement **disabled**."
            return await ctx.send(embed=utils.success_embed(msg))

        if head.isdigit() and len(args) >= 2:
            try:
                role_id = int(args[0])
                amount = int(float(args[1]))
            except ValueError:
                return await ctx.send(embed=utils.error_embed("Invalid role id or amount."))
            daily.set_role_amount(role_id, amount)
            return await ctx.send(
                embed=utils.success_embed(
                    f"Role <@&{role_id}> daily set to **{amount:,} pts**."
                )
            )

        try:
            amount = int(float(args[0]))
        except ValueError:
            return await ctx.send(
                embed=utils.error_embed(
                    "Usage: `.set daily <pts>` | `booster <pts>` | `<role_id> <pts>` | `status <words>`"
                )
            )
        daily.set_default_amount(amount)
        await ctx.send(embed=utils.success_embed(f"Default daily set to **{amount:,} pts**."))


async def setup(bot: commands.Bot):
    await bot.add_cog(Daily(bot))

"""Interactive help command with dropdown categories."""
from __future__ import annotations
import discord
from discord.ext import commands
from modules import utils
import config

PREFIX = config.PREFIX

CATEGORIES = {
    "games": {
        "emoji": "🎮", "label": "Games",
        "color": 0xFEE75C,
        "title": "🎮 Games",
        "description": "All casino games are played with prefix commands. Bets: amount, **all** (full balance), or **half** (50%).",
        "fields": [
            ("Coin Flip", f"`{PREFIX}cf <amount> [hot/cold]` — vs bot (animated GIF in message)\n"
                           f"`{PREFIX}cf @user <amount> hot|cold` — PvP (30s accept)"),
            ("Dice", f"`{PREFIX}dice <bet>` — vs **{config.BOT_DISPLAY_NAME}** (animated dice GIF)\n"
                           f"`{PREFIX}dice @user <bet>` — PvP challenge (30s accept)"),
            ("HTW", f"`{PREFIX}htw <bet>` — vs **{config.BOT_DISPLAY_NAME}**\n"
                     f"`{PREFIX}htw @user <bet>` — PvP challenge (30s to accept; Decline & Cancel)."),
            ("Blackjack", f"`{PREFIX}blackjack <amount>`  or  `{PREFIX}bj <amount>`\nPlay blackjack with interactive buttons (Hit / Stand / Double)."),
            ("Mines", f"`{PREFIX}mines <amount> [mine_count]` — e.g. `{PREFIX}mines all 1`, `{PREFIX}mines half 5`\nClick the grid to reveal gems. Cashout button in the grid."),
            ("Hi-Lo", f"`{PREFIX}hilo <amount>`\nAnimated cards — **Higher** / **Lower** / **Cash Out** buttons on the game message."),
            ("Limbo", f"`{PREFIX}limbo <amount> <target_multiplier>`\nAnimated multiplier — land at or above your target to win."),
            ("Slide", f"`{PREFIX}slide <amount>`\nMultiplier strip slides left — pointer picks your payout (GIF result holds 20s)."),
            ("Market Predict", f"`{PREFIX}market <amount> up|down` (or `u|d`)\nCenter line chart — if rigged, animation flips against your bet.\nWin pays ~**1.96x** (after 2% fee)."),
            ("Jackpot", f"In the **Jackpot room** (admin-set channel):\n"
                         f"`{PREFIX}jp <bet>` or `{PREFIX}jackpot <bet>` — join the pool (chance = your bet ÷ total).\n"
                         f"`{PREFIX}canceljp` — leave before spin (refund). Min **2** players to start."),
            ("Slots", f"`{PREFIX}slots <amount>`\n3×5 slot — 30 paylines, animated reels."),
            ("Crystals", f"`{PREFIX}crystals <bet>`\nReveal 5 crystals and match for prizes."),
            ("Towers", f"`{PREFIX}towers <bet> [easy|normal|hard]`\nClimb the tower grid."),
            ("Chicken Road", f"`{PREFIX}chickenroad <bet> [easy|normal|hard]`  (`{PREFIX}cr`)\nCross lanes before the car hits."),
        ],
    },
    "wallet": {
        "emoji": "💳", "label": "Wallet",
        "color": 0x2ECC71,
        "title": "💳 Wallet",
        "description": "Manage your balance, deposits, and withdrawals.",
        "fields": [
            ("Balance", f"`{PREFIX}balance`  or  `{PREFIX}bal`\nView your current balance as an image card."),
            ("Deposit", f"`{PREFIX}deposit`  or  `{PREFIX}dep`\nCrypto, in-game (Growtopia), and panel payment methods — optional deposit bonus."),
            ("Withdraw", f"`{PREFIX}withdraw`  or  `{PREFIX}wd`\nRequest a withdrawal."),
            ("Wallet", f"`{PREFIX}wallet`\nView your full wallet card."),
            ("Convert", f"`{PREFIX}convert <amount>`\nConvert between points and USD."),
        ],
    },
    "rakeback": {
        "emoji": "♻️", "label": "Rakeback",
        "color": 0xA855F7,
        "title": "♻️ Rakeback",
        "description": "Earn a percentage of your wagers back over time.",
        "fields": [
            ("View Status", f"`{PREFIX}rakeback`  or  `{PREFIX}rb`\nView your rakeback card with tier and accumulated amount."),
            ("Claim", f"`{PREFIX}rakeback claim`\nClaim your accumulated rakeback to your balance."),
            ("Tiers", f"`{PREFIX}rakeback tiers`\nView all rakeback tiers and rates."),
            ("How it works", "Rakeback is earned on every wager. Higher wager = higher tier = higher rate. Tiers are managed by admins via `/panel rakeback`."),
        ],
    },
    "affiliate": {
        "emoji": "🤝", "label": "Affiliate",
        "color": 0xF59E0B,
        "title": "🤝 Affiliate Program",
        "description": f"Earn 10% of your referrals' daily net deposits (deposits − withdrawals). Settled daily at 00:00 UTC.",
        "fields": [
            ("Create Code", f"`{PREFIX}affiliate create <CODE>`\nCreate your personal affiliate code."),
            ("Use a Code", f"`{PREFIX}affiliate use <CODE>`\nApply someone's affiliate code to your account."),
            ("Stats Card", f"`{PREFIX}affiliate stats`\nView your affiliate earnings as an image card."),
            ("Today's Earnings", f"`{PREFIX}affiliate today`\nSee live (unsettled) earnings for today."),
            ("Referred Users", f"`{PREFIX}affiliate referred`\nList everyone who used your code."),
            ("Claim Earnings", f"`{PREFIX}affiliate claim`\nClaim earned commissions to your balance."),
        ],
    },
    "promo": {
        "emoji": "🎟️", "label": "Promos & Bonuses",
        "color": 0x5865F2,
        "title": "🎟️ Promos & Bonuses",
        "description": "Redeem promo codes and track deposit bonuses.",
        "fields": [
            ("Redeem Promo", f"`{PREFIX}redeem <CODE>`\nRedeem a promo code for free points."),
            ("Daily Reward", f"`{PREFIX}daily`\nClaim daily points (status required if configured)."),
            ("Active Bonus", f"`{PREFIX}bonus`\nView your current active deposit bonus."),
            ("Available Bonuses", f"`{PREFIX}bonuses`\nList all available deposit bonuses."),
            ("Wager Progress", f"`{PREFIX}wager`\nCheck your bonus wager progress."),
        ],
    },
    "cases": {
        "emoji": "📦", "label": "Cases",
        "color": 0xF39C12,
        "title": "📦 Cases",
        "description": "Open cases to win items or point rewards.",
        "fields": [
            ("Browse & Open", f"`{PREFIX}cases`\nOfficial & community cases — pick a case, quantity ×1–×4, animated GIF reveal."),
            ("How it works", "Select a case from the dropdown, preview its items, then open it. Winnings are added to your balance automatically."),
        ],
    },
    "community": {
        "emoji": "🏆", "label": "Community",
        "color": 0xE91E63,
        "title": "🏆 Community",
        "description": "Leaderboards, races, giveaways and more.",
        "fields": [
            ("Leaderboard", f"`{PREFIX}leaderboard`  or  `{PREFIX}lb`\nView the top balances leaderboard."),
            ("Stats", f"`{PREFIX}stats [@user]`\nView game statistics as an image card."),
            ("Races", f"`{PREFIX}race`\nView the current wagering race standings."),
            ("Giveaways", f"Giveaways are created by admins and announced in the server."),
            ("Threads", (
                f"`{PREFIX}thread create [name]`  —  Create a private thread\n"
                f"`{PREFIX}thread add @user`  —  Add someone\n"
                f"`{PREFIX}thread remove @user`  —  Remove someone\n"
                f"`{PREFIX}thread close`  —  Archive thread"
            )),
        ],
    },
}


def _category_embed(cat_key: str) -> discord.Embed:
    cat = CATEGORIES[cat_key]
    embed = discord.Embed(title=cat["title"], description=cat["description"], color=cat["color"])
    for name, value in cat["fields"]:
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text=f"Use the dropdown to switch categories  •  prefix: {PREFIX}")
    return embed


def _main_embed() -> discord.Embed:
    lines = "\n".join(
        f"{v['emoji']} **{v['label']}** — {v['fields'][0][0] if v['fields'] else ''}"
        for v in CATEGORIES.values()
    )
    embed = discord.Embed(
        title="📖 FlipBot Help",
        description=(
            "Welcome to **FlipBot** — your casino in Discord.\n\n"
            "Use the dropdown below to browse help categories.\n"
            f"**Command prefix:** `{PREFIX}`\n"
            f"**Slash commands:** `/panel` (admin)  ·  `/user_panel` (profile)\n\n"
            + lines
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="Select a category below for detailed commands.")
    return embed


class _HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=v["label"], value=k, emoji=v["emoji"],
                description=v["description"][:50],
            )
            for k, v in CATEGORIES.items()
        ]
        super().__init__(placeholder="Browse help categories…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        embed = _category_embed(key)
        view = _HelpCategoryView(active_key=key)
        await interaction.response.edit_message(embed=embed, view=view)


class _HelpCategorySelect(discord.ui.Select):
    def __init__(self, active_key: str):
        options = [
            discord.SelectOption(
                label=v["label"], value=k, emoji=v["emoji"],
                description=v["description"][:50],
                default=(k == active_key),
            )
            for k, v in CATEGORIES.items()
        ]
        super().__init__(placeholder="Switch category…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        embed = _category_embed(key)
        view = _HelpCategoryView(active_key=key)
        await interaction.response.edit_message(embed=embed, view=view)


class _HelpCategoryView(discord.ui.View):
    def __init__(self, active_key: str):
        super().__init__(timeout=300)
        self.add_item(_HelpCategorySelect(active_key))
        back_btn = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary, row=1)

        async def _back(interaction: discord.Interaction):
            await interaction.response.edit_message(embed=_main_embed(), view=_HelpMainView())

        back_btn.callback = _back
        self.add_item(back_btn)


class _HelpMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(_HelpSelect())


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.remove_command("help")

    @commands.command(name="help", aliases=["h", "commands"])
    async def help_cmd(self, ctx: commands.Context, *, category: str = ""):
        """Show the help menu. .help [category]"""
        if category:
            cat_key = category.lower()
            if cat_key in CATEGORIES:
                embed = _category_embed(cat_key)
                view = _HelpCategoryView(active_key=cat_key)
                return await ctx.send(embed=embed, view=view)
        await ctx.send(embed=_main_embed(), view=_HelpMainView())

    @commands.command(name="games", aliases=["g"])
    async def games_cmd(self, ctx: commands.Context):
        """List game commands quickly. .games"""
        g = CATEGORIES["games"]
        lines = []
        for name, value in g["fields"]:
            lines.append(f"**{name}**\n{value}")
        embed = discord.Embed(
            title="🎮 Games",
            description=(
                f"Prefix: `{PREFIX}`\n"
                "Bets: number, **all**, **half** (if balance > max bet, **all** becomes max bet).\n\n"
                + "\n\n".join(lines[:12])
            ),
            color=g["color"],
        )
        embed.set_footer(text=f"Use `{PREFIX}help games` for the full help menu.")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))

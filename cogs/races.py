"""
Races Cog — Wager Race & Deposit Race leaderboard system.

- Background task refreshes the leaderboard embed every 15 seconds.
- Auto-ends race and distributes prizes when the timer expires.
- Admin commands: /race_panel  (opens management panel)
"""
import discord
from discord import app_commands
from discord.ext import commands, tasks
import time
import asyncio

from modules.database import get_data, check_permission
from modules.player import Player
from modules.utils import format_balance
import modules.race as race_engine


# ── Medal & rank formatting ────────────────────────────────────────────────────

_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
_PERIOD_LABELS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}
_TYPE_LABELS   = {"wager": "Wager", "deposit": "Deposit"}
_TYPE_EMOJIS   = {"wager": "🎰", "deposit": "💳"}


def _rank_emoji(rank: int) -> str:
    return _MEDALS.get(rank, f"**#{rank}**")


# ── Embed builder ──────────────────────────────────────────────────────────────

def _build_race_embed(race: dict, bot: discord.Client) -> discord.Embed:
    """Build the live leaderboard embed."""
    now      = int(time.time())
    ends_at  = int(race.get("ends_at", 0))
    started  = int(race.get("starts_at", now))
    expired  = now > ends_at

    r_type   = race.get("type", "wager")
    period   = race.get("period", "daily")
    name     = race.get("name", "Race")
    prizes   = race.get("prizes", {})

    type_emoji  = _TYPE_EMOJIS.get(r_type, "🏆")
    period_lbl  = _PERIOD_LABELS.get(period, period.title())
    type_lbl    = _TYPE_LABELS.get(r_type, r_type.title())

    color = discord.Color.gold() if not expired else discord.Color.greyple()

    embed = discord.Embed(
        title=f"{type_emoji} {name}",
        color=color,
    )

    # ── Status line ────────────────────────────────────────────────────────────
    if expired:
        embed.description = "🏁 **Race has ended!** Final results below."
    else:
        embed.description = (
            f"⏱️ **Ends:** <t:{ends_at}:R>  ·  <t:{ends_at}:F>\n"
            f"📅 **Period:** {period_lbl}  ·  📊 **Type:** {type_lbl} Race"
        )

    # ── Leaderboard ────────────────────────────────────────────────────────────
    board = race_engine.get_leaderboard(race, top_n=10)

    if not board:
        lb_text = "*No entries yet — be the first!*"
    else:
        lines = []
        for rank, (uid, amount) in enumerate(board, start=1):
            medal = _rank_emoji(rank)
            prize = prizes.get(str(rank))
            prize_str = f"  →  🎁 **+{format_balance(prize, 'real')}**" if prize else ""

            # Try to resolve username
            member = None
            try:
                # bot.get_user is O(1) from cache
                user_obj = bot.get_user(int(uid))
                if user_obj:
                    member = user_obj.display_name
            except Exception:
                pass
            display = member or f"<@{uid}>"

            lines.append(
                f"{medal} {display}\n"
                f"┗ {format_balance(amount, 'real')} {type_lbl.lower()}ed{prize_str}"
            )
        lb_text = "\n".join(lines)

    embed.add_field(name="🏆 Leaderboard", value=lb_text, inline=False)

    # ── Prize pool ─────────────────────────────────────────────────────────────
    if prizes:
        prize_lines = []
        for place_str, reward in sorted(prizes.items(), key=lambda x: int(x[0])):
            place = int(place_str)
            prize_lines.append(
                f"{_rank_emoji(place)} Place — **{format_balance(reward, 'real')}**"
            )
        embed.add_field(name="🎁 Prize Pool", value="\n".join(prize_lines), inline=True)

    # ── Stats ──────────────────────────────────────────────────────────────────
    entries = race.get("entries", {})
    total   = sum(entries.values())
    embed.add_field(
        name="📈 Stats",
        value=(
            f"👥 **Participants:** {len(entries)}\n"
            f"💰 **Total {type_lbl}ed:** {format_balance(total, 'real')}"
        ),
        inline=True,
    )

    embed.set_footer(text=f"Vegas Casino  ·  Updates every 15s  ·  Started <t:{started}:R>")
    return embed


def _build_ended_embed(race: dict, bot: discord.Client) -> discord.Embed:
    """Final results embed shown after race ends."""
    embed = _build_race_embed(race, bot)
    embed.title = f"🏁 {race.get('name', 'Race')} — Final Results"
    embed.color = discord.Color.og_blurple()
    return embed


# ── Background task cog ────────────────────────────────────────────────────────

class Races(commands.Cog):
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._refresh_task.start()

    def cog_unload(self):
        self._refresh_task.cancel()

    @tasks.loop(seconds=15)
    async def _refresh_task(self):
        """Refresh leaderboard embeds for all active races every 15 seconds."""
        try:
            races = race_engine.get_active_races()
            if not races:
                return

            for race_id, race in list(races.items()):
                channel_id = race.get("channel_id")
                message_id = race.get("message_id")

                if not channel_id:
                    continue

                channel = self.bot.get_channel(int(channel_id))
                if not isinstance(channel, discord.TextChannel):
                    continue

                if race_engine.is_race_expired(race):
                    await self._end_and_distribute(race, channel, message_id)
                    continue

                embed = _build_race_embed(race, self.bot)

                if message_id:
                    try:
                        msg = await channel.fetch_message(int(message_id))
                        await msg.edit(embed=embed)
                        continue
                    except discord.NotFound:
                        pass
                    except discord.HTTPException as e:
                        # 5xx = Discord-side transient error; skip this cycle silently
                        if e.status >= 500:
                            continue
                        raise

                # Post new message and save ID
                msg = await channel.send(embed=embed)
                race_engine.set_message_id(race_id, msg.id)

        except Exception as e:
            print(f"[Races] Refresh error: {e}")

    @_refresh_task.before_loop
    async def _before_refresh(self):
        await self.bot.wait_until_ready()

    async def _end_and_distribute(self, race: dict, channel: discord.TextChannel, message_id, distribute: bool = True):
        """End a specific race, optionally pay out prizes, update embed."""
        race_id = race.get("race_id")
        final = race_engine.end_race(race_id)
        if not final:
            return

        board  = race_engine.get_leaderboard(final, top_n=10)
        prizes = final.get("prizes", {})

        winners = []
        if distribute:
            for rank, (uid, amount) in enumerate(board, start=1):
                reward = prizes.get(str(rank))
                if reward and reward > 0:
                    try:
                        player = Player(int(uid))
                        player.add_balance("real", int(reward))
                        winners.append((rank, uid, reward))
                    except Exception:
                        pass

        # Post final embed
        embed = _build_ended_embed(final, self.bot)

        if distribute and winners:
            lines = [
                f"{_rank_emoji(rank)} <@{uid}> — **+{format_balance(reward, 'real')}** credited!"
                for rank, uid, reward in winners
            ]
            embed.add_field(name="💸 Prizes Distributed", value="\n".join(lines), inline=False)
        elif not distribute:
            embed.add_field(name="⚠️ Prizes", value="Prizes were **not** distributed for this race.", inline=False)

        if message_id:
            try:
                msg = await channel.fetch_message(int(message_id))
                await msg.edit(embed=embed)
            except Exception:
                await channel.send(embed=embed)
        else:
            await channel.send(embed=embed)

        # Ping winners
        if distribute and winners:
            mention_str = " ".join(f"<@{uid}>" for _, uid, _ in winners)
            try:
                await channel.send(
                    f"🎉 The **{final.get('name', 'Race')}** has ended! "
                    f"Congratulations to our winners: {mention_str} — prizes have been credited!",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except Exception:
                pass

    # ── Admin slash command ────────────────────────────────────────────────────

    @app_commands.command(name="race_panel", description="Open the Race management panel (Admin only)")
    @app_commands.guild_only()
    async def race_panel(self, interaction: discord.Interaction):
        if check_permission(str(interaction.user.id), "admin"):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
        embed  = _build_race_panel_embed()
        view   = RacePanelView(interaction.user.id)
        await interaction.response.send_message(embed=embed, ephemeral=True, view=view)


# ── Admin panel UI ─────────────────────────────────────────────────────────────

def _build_race_panel_embed() -> discord.Embed:
    races = race_engine.get_active_races()
    embed = discord.Embed(
        title="🏁 Race Management",
        color=discord.Color.gold(),
    )
    if races:
        embed.description = f"**{len(races)} active race(s) running.**"
        for rid, race in races.items():
            ends_at = race.get("ends_at", 0)
            entries = race.get("entries", {})
            r_type  = race.get("type", "wager")
            period  = race.get("period", "daily")
            prizes  = race.get("prizes", {})
            prize_text = ""
            if prizes:
                prize_text = "\n".join(
                    f"{_rank_emoji(int(p))} → {format_balance(v, 'real')}"
                    for p, v in sorted(prizes.items(), key=lambda x: int(x[0]))
                )
            embed.add_field(
                name=f"{_TYPE_EMOJIS.get(r_type, '🏆')} {race.get('name', '—')}",
                value=(
                    f"📊 {r_type.title()} · 📅 {_PERIOD_LABELS.get(period, period.title())}\n"
                    f"⏰ <t:{ends_at}:R> · 👥 {len(entries)} participants\n"
                    f"📢 <#{race.get('channel_id', 0)}>"
                    + (f"\n🎁 {prize_text}" if prize_text else "")
                ),
                inline=False,
            )
    else:
        embed.description = "No active races. Start one below."
    embed.set_footer(text="Vegas Casino | Race Panel")
    return embed


class RacePanelView(discord.ui.View):
    def __init__(self, admin_id: int):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        races = race_engine.get_active_races()
        if races:
            self.add_item(_EndRaceSelect(races, row=0))
        self.add_item(_StartRaceSelect(admin_id, row=1 if races else 0))


class _EndRaceSelect(discord.ui.Select):
    """Select which active race to end."""

    def __init__(self, races: dict, row: int = 0):
        options = [
            discord.SelectOption(
                label=f"{r.get('name', rid)[:80]}",
                value=rid,
                description=f"{r.get('type','?').title()} race · ends <t:{r.get('ends_at',0)}:R>",
                emoji="🛑",
            )
            for rid, r in list(races.items())[:25]
        ]
        super().__init__(
            placeholder="🛑  Select a race to end…",
            options=options,
            min_values=1,
            max_values=1,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        race_id = self.values[0]
        races = race_engine.get_active_races()
        race = races.get(race_id)
        if not race:
            return await interaction.response.send_message(
                "❌ Race not found — it may have already ended.", ephemeral=True
            )
        embed = discord.Embed(
            title=f"🛑 End Race: {race.get('name', race_id)}",
            description=(
                "Do you want to **distribute prizes** to the top players?\n\n"
                f"**Participants:** {len(race.get('entries', {}))}\n"
                f"**Ends:** <t:{race.get('ends_at', 0)}:R>"
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Vegas Casino | Race Panel")
        view = _EndRaceConfirmView(race_id, race.get("name", "Race"))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class _EndRaceConfirmView(discord.ui.View):
    """Confirm whether to distribute prizes when ending a race."""

    def __init__(self, race_id: str, race_name: str):
        super().__init__(timeout=120)
        self.race_id   = race_id
        self.race_name = race_name

    @discord.ui.button(label="End + Distribute Prizes", style=discord.ButtonStyle.success, emoji="✅")
    async def end_with_prizes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do_end(interaction, distribute=True)

    @discord.ui.button(label="End Without Prizes", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def end_without_prizes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do_end(interaction, distribute=False)

    async def _do_end(self, interaction: discord.Interaction, distribute: bool):
        races = race_engine.get_active_races()
        race  = races.get(self.race_id)
        if not race:
            return await interaction.response.send_message(
                "❌ Race already ended.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        channel_id = race.get("channel_id")
        message_id = race.get("message_id")
        channel = interaction.client.get_channel(int(channel_id)) if channel_id else None
        race_cog = interaction.client.cogs.get("Races")
        if race_cog and channel:
            await race_cog._end_and_distribute(race, channel, message_id, distribute=distribute)
        else:
            race_engine.end_race(self.race_id)
        prize_note = "✅ Prizes distributed to top players!" if distribute else "⚠️ Prizes were **not** distributed."
        embed = discord.Embed(
            title=f"🏁 Race Ended: {self.race_name}",
            description=prize_note,
            color=discord.Color.green() if distribute else discord.Color.orange(),
        )
        embed.set_footer(text="Vegas Casino | Race Panel")
        await interaction.edit_original_response(embed=embed, view=None)


class _StartRaceSelect(discord.ui.Select):
    """Pick race type first."""

    def __init__(self, admin_id: int, row: int = 0):
        self.admin_id = admin_id
        super().__init__(
            placeholder="Select race type to create…",
            row=row,
            options=[
                discord.SelectOption(
                    label="🎰 Wager Race",
                    value="wager",
                    description="Ranks players by total coins wagered",
                    emoji="🎰",
                ),
                discord.SelectOption(
                    label="💳 Deposit Race",
                    value="deposit",
                    description="Ranks players by total coins deposited",
                    emoji="💳",
                ),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        race_type = self.values[0]
        await interaction.response.send_modal(
            CreateRaceModal(race_type=race_type)
        )


class CreateRaceModal(discord.ui.Modal):
    """Admin modal to configure and launch a race."""

    name_input = discord.ui.TextInput(
        label="Race Name",
        placeholder="e.g. Weekly Wager Race",
        max_length=50,
        style=discord.TextStyle.short,
    )
    period_input = discord.ui.TextInput(
        label="Period  (daily / weekly / monthly)",
        placeholder="weekly",
        default="weekly",
        max_length=10,
        style=discord.TextStyle.short,
    )
    duration_input = discord.ui.TextInput(
        label="Duration Hours (0 = auto from period)",
        placeholder="0",
        default="0",
        max_length=6,
        required=False,
        style=discord.TextStyle.short,
    )
    prizes_input = discord.ui.TextInput(
        label="Prizes: place:amount  (e.g. 1:5000,2:2500)",
        placeholder="1:5000, 2:2500, 3:1000, 4:500, 5:250",
        max_length=200,
        required=False,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, race_type: str):
        super().__init__(title=f"🏁 Create {race_type.title()} Race")
        self.race_type = race_type
        self.channel_select = discord.ui.Label(
            text="Leaderboard Channel",
            component=discord.ui.ChannelSelect(
                placeholder="Select leaderboard channel…",
                channel_types=[discord.ChannelType.text],
            ),
        )
        self.add_item(self.channel_select)

    async def on_submit(self, interaction: discord.Interaction):
        # Resolve channel from select
        selected = self.channel_select.component.values
        if not selected:
            return await interaction.response.send_message(
                "❌ Please select a channel.", ephemeral=True
            )
        channel_id = selected[0].id
        channel = interaction.client.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ Channel not found.", ephemeral=True
            )

        # Parse period
        period = self.period_input.value.strip().lower()
        if period not in ("daily", "weekly", "monthly"):
            return await interaction.response.send_message(
                "❌ Period must be `daily`, `weekly`, or `monthly`.", ephemeral=True
            )

        # Parse duration
        try:
            duration_hours = int(self.duration_input.value.strip() or "0")
        except ValueError:
            duration_hours = 0

        # Parse prizes  e.g. "1:5000, 2:2500, 3:1000"
        prizes = {}
        raw_prizes = self.prizes_input.value.strip()
        if raw_prizes:
            for part in raw_prizes.replace("\n", ",").split(","):
                part = part.strip()
                if ":" in part:
                    left, _, right = part.partition(":")
                    try:
                        prizes[str(int(left.strip()))] = int(right.strip().replace(",", ""))
                    except ValueError:
                        pass

        name = self.name_input.value.strip() or f"{period.title()} {self.race_type.title()} Race"

        ok, err, new_race_id = race_engine.create_race(
            race_type=self.race_type,
            period=period,
            name=name,
            channel_id=channel_id,
            prizes=prizes,
            duration_hours=duration_hours,
        )
        if not ok:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)

        ends_at = race_engine.get_active_races().get(new_race_id, {}).get("ends_at", 0)
        embed = discord.Embed(
            title="✅ Race Created!",
            color=discord.Color.green(),
        )
        embed.add_field(name="📛 Name",    value=name,                                           inline=True)
        embed.add_field(name="📊 Type",    value=self.race_type.title(),                         inline=True)
        embed.add_field(name="📅 Period",  value=period.title(),                                 inline=True)
        embed.add_field(name="📢 Channel", value=f"<#{channel_id}>",                            inline=True)
        embed.add_field(name="⏰ Ends",    value=f"<t:{ends_at}:R>",                             inline=True)
        if prizes:
            prize_text = "\n".join(
                f"{_rank_emoji(int(p))} → {format_balance(v, 'real')}"
                for p, v in sorted(prizes.items(), key=lambda x: int(x[0]))
            )
            embed.add_field(name="🎁 Prizes", value=prize_text, inline=False)
        embed.description = (
            f"The leaderboard will appear in <#{channel_id}> within 15 seconds."
        )
        embed.set_footer(text="Vegas Casino | Race Panel")
        await interaction.response.edit_message(embed=embed, view=None)


# ── Setup ──────────────────────────────────────────────────────────────────────

async def setup(bot):
    await bot.add_cog(Races(bot))

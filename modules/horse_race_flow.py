"""Horse race — V2 layout, chip select, per-horse cumulative stakes, race GIF."""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

import discord
from discord import ui

from database import db
from Games.horse_race import (
    DEFAULT_HORSE_UNICODE,
    NUM_HORSES,
    bet_tiers,
    gross_payout,
    pick_lane_emojis,
    pick_winner_index,
    win_chances,
)
from modules import flip_balance_cap as bc
from modules import flip_utils as utils
from modules import image_gen
from modules.database import get_data
from modules.horse_race_media_v2 import BETS_ATTACHMENT, RACE_ATTACHMENT, WAITING_ATTACHMENT

if TYPE_CHECKING:
    from discord.ext import commands

GAME_ID = "horse_race"
_horse_msg_to_user: dict[str, int] = {}


def _emoji_pool_from_config(em: dict) -> list[str]:
    if not isinstance(em, dict):
        return []
    pool = em.get("pool")
    if isinstance(pool, list) and pool:
        return [str(x).strip() for x in pool if str(x).strip()]
    legacy: list[str] = []
    for i in range(1, NUM_HORSES + 1):
        raw = em.get(f"horse_{i}")
        if raw:
            legacy.append(str(raw).strip())
    return legacy


def filter_guild_horse_emojis(guild_emojis: list) -> list:
    """Discord custom emojis whose name ends with `_horse`."""
    out = []
    for em in guild_emojis:
        name = (getattr(em, "name", None) or "").strip().lower()
        if name.endswith("_horse"):
            out.append(em)
    return out


def get_horse_race_settings() -> dict:
    games = get_data("server/games") or {}
    hr = games.get(GAME_ID) if isinstance(games.get(GAME_ID), dict) else {}
    em = hr.get("emojis") if isinstance(hr.get("emojis"), dict) else {}
    pool = _emoji_pool_from_config(em)
    return {
        "emoji_pool": pool,
        "finish_emoji": str(em.get("finish") or "🏁"),
        "emoji_guild_id": em.get("guild_id"),
        "house_edge_percent": float(hr.get("house_edge", 5.0) or 5.0),
        "min_bet": float(hr.get("min_bet", 10)),
        "max_bet": float(hr.get("max_bet", 10000)),
        "favorite_min": float(hr.get("favorite_min", 1.10)),
        "favorite_max": float(hr.get("favorite_max", 1.35)),
        "longshot_min": float(hr.get("longshot_min", 10.0)),
        "longshot_max": float(hr.get("longshot_max", 20.0)),
        "mid_min": float(hr.get("mid_min", 2.0)),
        "mid_max": float(hr.get("mid_max", 8.5)),
    }


def _roll_odds(settings: dict) -> tuple[float, ...]:
    from Games.horse_race import roll_race_odds

    return roll_race_odds(
        favorite_min=settings.get("favorite_min", 1.10),
        favorite_max=settings.get("favorite_max", 1.35),
        longshot_min=settings.get("longshot_min", 10.0),
        longshot_max=settings.get("longshot_max", 20.0),
        mid_min=settings.get("mid_min", 2.0),
        mid_max=settings.get("mid_max", 8.5),
    )


def _empty_stakes() -> list[float]:
    return [0.0] * NUM_HORSES


def save_horse_race_emoji_pool(
    *,
    pool: list[str],
    finish: str = "🏁",
    guild_id: int | None = None,
) -> None:
    from cogs.admin_panel import _ensure_horse_race_game_entry

    games = _ensure_horse_race_game_entry(get_data("server/games") or {})
    hr = games[GAME_ID]
    em = hr.setdefault("emojis", {})
    em["pool"] = [(e or "").strip() for e in pool if (e or "").strip()]
    em["finish"] = (finish or "🏁").strip() or "🏁"
    if guild_id is not None:
        em["guild_id"] = int(guild_id)
    for key in list(em.keys()):
        if key.startswith("horse_") and key[6:].isdigit():
            em.pop(key, None)
    hr["last_modified"] = int(__import__("time").time())
    games[GAME_ID] = hr
    from modules.database import set_data

    set_data("server/games", games)


def roll_race_lane_emojis(settings: dict) -> list[str]:
    """Pick 6 lane emojis for one race from the configured pool."""
    return pick_lane_emojis(settings.get("emoji_pool") or [], NUM_HORSES)


def _parse_button_emoji(raw: str) -> str | discord.PartialEmoji | None:
    s = (raw or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"<a?:(\w+):(\d+)>", s)
    if m:
        return discord.PartialEmoji(
            name=m.group(1), id=int(m.group(2)), animated=s.startswith("<a:"),
        )
    return s


async def _build_attachments(
    settings: dict,
    odds: tuple[float, ...],
    win_pcts: tuple[float, ...],
    stakes: list[float],
    chip_bet: float | None,
    horse_emojis: list[str],
    *,
    race_gif: io.BytesIO | None = None,
) -> list[discord.File]:
    bets_buf = await image_gen.render_horse_race_bets_png(
        horse_emojis=horse_emojis,
        stakes=stakes,
        odds=odds,
        win_pcts=win_pcts,
        chip_bet=chip_bet,
    )
    files = [discord.File(io.BytesIO(bets_buf.getvalue()), filename=BETS_ATTACHMENT)]
    if race_gif is not None:
        files.append(discord.File(io.BytesIO(race_gif.getvalue()), filename=RACE_ATTACHMENT))
    else:
        wait_buf = await image_gen.render_horse_race_waiting_png()
        files.append(discord.File(io.BytesIO(wait_buf.getvalue()), filename=WAITING_ATTACHMENT))
    return files


def _status_text(
    chip_bet: float | None,
    stakes: list[float],
    odds: tuple[float, ...],
    *,
    pool_size: int = 0,
) -> str:
    total = sum(stakes)
    chip_s = f"**{utils.fmt_pts(chip_bet)}** pts" if chip_bet else "—"
    lines = []
    for i, amt in enumerate(stakes):
        if amt > 0:
            lines.append(f"**#{i + 1}** — **{utils.fmt_pts(amt)}** pts ({odds[i]:.2f}x)")
    stake_block = "\n".join(lines) if lines else "—"
    return (
        f"## 🏇 Horse Race\n"
        f"**Chip (per tap):** {chip_s}\n"
        f"**Total staked:** **{utils.fmt_pts(total)}** pts\n"
        f"**Bets on horses:**\n{stake_block}\n\n"
        f"Select a chip, tap horses to **add** that amount each time. "
        f"You need balance for the **full total**. Then **Start Race**.\n"
        f"-# Pool: **{pool_size}** `*_horse` emojis · 6 random per race."
    )


def _populate_setup_view(view: "HorseRaceSetupView") -> None:
    accent = discord.Colour(0xC9A227)

    head = ui.Container(accent_color=accent)
    head.add_item(ui.TextDisplay(_status_text(
        view.chip_bet, view.stakes, view.odds,
        pool_size=len(view.settings.get("emoji_pool") or []),
    )))
    view.add_item(head)

    gal = ui.Container(accent_color=accent)
    gal.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    g1 = ui.MediaGallery()
    g1.add_item(media=f"attachment://{BETS_ATTACHMENT}")
    gal.add_item(g1)
    view.add_item(gal)

    wait_c = ui.Container(accent_color=accent)
    wait_c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    g2 = ui.MediaGallery()
    g2.add_item(media=f"attachment://{WAITING_ATTACHMENT}")
    wait_c.add_item(g2)
    view.add_item(wait_c)

    bet_c = ui.Container(accent_color=accent)
    bet_c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    bet_c.add_item(ui.TextDisplay("### Chip amount (added per horse tap)"))
    row_bet = ui.ActionRow()
    row_bet.add_item(HorseRaceBetSelect(view.tiers, view.user_id))
    bet_c.add_item(row_bet)
    view.add_item(bet_c)

    horse_c = ui.Container(accent_color=accent)
    horse_c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    horse_c.add_item(ui.TextDisplay("### Tap horse to add chip"))
    row1 = ui.ActionRow()
    row2 = ui.ActionRow()
    for i in range(NUM_HORSES):
        btn = HorsePickButton(
            index=i,
            emoji_raw=view.horse_emojis[i],
            odds=view.odds[i],
            stake=view.stakes[i],
        )
        (row1 if i < 3 else row2).add_item(btn)
    horse_c.add_item(row1)
    horse_c.add_item(row2)
    view.add_item(horse_c)

    go_c = ui.Container(accent_color=accent)
    go_c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    row_go = ui.ActionRow()
    row_go.add_item(HorseRaceStartButton())
    row_go.add_item(HorseRaceClearBetsButton())
    go_c.add_item(row_go)
    view.add_item(go_c)


class HorseRaceBetSelect(ui.Select):
    def __init__(self, tiers: list[int], owner_id: int):
        opts = [
            discord.SelectOption(
                label=f"{t:,} pts per tap",
                value=str(t),
                description="Added to a horse each time you tap it",
            )
            for t in tiers[:25]
        ]
        super().__init__(
            placeholder="Select chip amount…",
            options=opts,
            min_values=1,
            max_values=1,
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        view: HorseRaceSetupView = self.view  # type: ignore[assignment]
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your race."), ephemeral=True,
            )
        view.chip_bet = float(self.values[0])
        await view.refresh(
            interaction,
            ephemeral_confirm=(
                f"Chip set to **{utils.fmt_pts(view.chip_bet)}** pts — "
                "each horse tap adds this amount."
            ),
        )


class HorsePickButton(ui.Button):
    def __init__(self, *, index: int, emoji_raw: str, odds: float, stake: float):
        if stake > 0:
            label = f"#{index + 1} · {utils.fmt_pts(stake)}"
            style = discord.ButtonStyle.success
        else:
            label = f"#{index + 1} · {odds:.1f}x"
            style = discord.ButtonStyle.secondary
        super().__init__(
            label=label[:80],
            style=style,
            emoji=_parse_button_emoji(emoji_raw),
        )
        self.horse_index = index

    async def callback(self, interaction: discord.Interaction):
        view: HorseRaceSetupView = self.view  # type: ignore[assignment]
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your race."), ephemeral=True,
            )
        if view.chip_bet is None or view.chip_bet <= 0:
            return await interaction.response.send_message(
                embed=utils.error_embed("Select a chip amount first."),
                ephemeral=True,
            )
        chip = float(view.chip_bet)
        new_total = view.total_stake() + chip
        user = await db.get_user(view.user_id)
        bal = float((user or {}).get("balance", 0))
        if bal < new_total:
            return await interaction.response.send_message(
                embed=utils.error_embed(
                    f"Insufficient balance. Need **{utils.fmt_pts(new_total)}** pts "
                    f"(you have **{utils.fmt_pts(bal)}**)."
                ),
                ephemeral=True,
            )
        i = self.horse_index
        view.stakes[i] += chip
        await view.refresh(
            interaction,
            ephemeral_confirm=(
                f"**#{i + 1}** — **{utils.fmt_pts(view.stakes[i])}** pts "
                f"(+{utils.fmt_pts(chip)}). Total: **{utils.fmt_pts(view.total_stake())}** pts."
            ),
        )


class HorseRaceClearBetsButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Clear Bets",
            style=discord.ButtonStyle.secondary,
            emoji="🗑️",
        )

    async def callback(self, interaction: discord.Interaction):
        view: HorseRaceSetupView = self.view  # type: ignore[assignment]
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your race."), ephemeral=True,
            )
        view.stakes = _empty_stakes()
        await view.refresh(interaction)


class HorseRaceStartButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Start Race",
            style=discord.ButtonStyle.success,
            emoji="🏁",
        )

    async def callback(self, interaction: discord.Interaction):
        view: HorseRaceSetupView = self.view  # type: ignore[assignment]
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your race."), ephemeral=True,
            )
        await view.start_race(interaction)


class HorseRacePlayAgainButton(ui.Button):
    def __init__(self, owner_id: int):
        super().__init__(label="New Race", style=discord.ButtonStyle.primary, emoji="🔄")
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your race."), ephemeral=True,
            )
        await interaction.response.defer()
        settings = get_horse_race_settings()
        if not settings.get("emoji_pool"):
            return await interaction.followup.send(
                embed=utils.error_embed(
                    "Horse Race emoji pool not configured. Use Game Management → 🏇 Horse Emojis.",
                ),
                ephemeral=True,
            )
        cfg = await db.get_game_config(GAME_ID)
        min_b = float(cfg["min_bet"]) if cfg else settings["min_bet"]
        max_b = float(cfg["max_bet"]) if cfg else settings["max_bet"]
        tiers = bet_tiers(min_b, max_b, 25)
        odds = _roll_odds(settings)
        win_pcts = win_chances(odds)
        lane_emojis = roll_race_lane_emojis(settings)
        files = await _build_attachments(
            settings, odds, win_pcts, _empty_stakes(), None, lane_emojis,
        )
        view = HorseRaceSetupView(
            self.owner_id, settings, tiers,
            odds=odds, win_pcts=win_pcts, horse_emojis=lane_emojis,
        )
        await interaction.message.edit(attachments=files, view=view)
        _horse_msg_to_user[str(interaction.message.id)] = self.owner_id


class HorseRaceSetupView(ui.LayoutView):
    def __init__(
        self,
        user_id: int,
        settings: dict,
        tiers: list[int],
        *,
        odds: tuple[float, ...] | None = None,
        win_pcts: tuple[float, ...] | None = None,
        chip_bet: float | None = None,
        stakes: list[float] | None = None,
        horse_emojis: list[str] | None = None,
        racing: bool = False,
    ):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.settings = settings
        self.tiers = tiers
        self.odds = odds or _roll_odds(settings)
        self.win_pcts = win_pcts or win_chances(self.odds)
        self.horse_emojis = horse_emojis or roll_race_lane_emojis(settings)
        self.chip_bet = chip_bet
        self.stakes = list(stakes) if stakes else _empty_stakes()
        if len(self.stakes) < NUM_HORSES:
            self.stakes.extend([0.0] * (NUM_HORSES - len(self.stakes)))
        self.stakes = self.stakes[:NUM_HORSES]
        self._racing = racing
        _populate_setup_view(self)

    def total_stake(self) -> float:
        return sum(self.stakes)

    def staked_lanes(self) -> list[int]:
        return [i for i, s in enumerate(self.stakes) if s > 0]

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        ephemeral_confirm: str | bool = False,
    ) -> None:
        if self._racing:
            return
        if ephemeral_confirm:
            await interaction.response.send_message(
                ephemeral_confirm if isinstance(ephemeral_confirm, str) else "Updated.",
                ephemeral=True,
            )
        else:
            await interaction.response.defer()
        files = await _build_attachments(
            self.settings, self.odds, self.win_pcts, self.stakes, self.chip_bet,
            self.horse_emojis,
        )
        new_view = HorseRaceSetupView(
            self.user_id, self.settings, self.tiers,
            odds=self.odds, win_pcts=self.win_pcts,
            chip_bet=self.chip_bet, stakes=list(self.stakes),
            horse_emojis=list(self.horse_emojis),
        )
        if ephemeral_confirm:
            await interaction.message.edit(
                content=None, embed=None, attachments=files, view=new_view,
            )
        else:
            await interaction.message.edit(
                content=None, embed=None, attachments=files, view=new_view,
            )
        _horse_msg_to_user[str(interaction.message.id)] = self.user_id

    async def start_race(self, interaction: discord.Interaction) -> None:
        from cogs.games import _check_game_interaction, _earn_rakeback, _record

        total = self.total_stake()
        staked = self.staked_lanes()
        if not staked:
            return await interaction.response.send_message(
                embed=utils.error_embed(
                    "Place at least one bet — select chip, then tap horse(s).",
                ),
                ephemeral=True,
            )
        if not await _check_game_interaction(
            interaction, self.user_id, GAME_ID, total,
        ):
            return

        self._racing = True
        self.stop()
        await interaction.response.defer()

        stakes = list(self.stakes)
        settings = self.settings
        odds = self.odds

        await db.ensure_user(self.user_id, interaction.user.name)
        await db.add_balance(self.user_id, -total, note="horse_race bets")

        prospective = max(
            gross_payout(stakes[i], i, odds) for i in staked
        )
        rigged = await bc.should_rig_outcome(
            self.user_id, GAME_ID, total, gross=prospective,
        )
        winner = pick_winner_index(
            odds, rig_lose=rigged, player_picks=staked,
        )
        lane_bet = stakes[winner]
        won = lane_bet > 0
        gross = gross_payout(lane_bet, winner, odds) if won else 0.0

        game_cfg = await db.get_game_config(GAME_ID)
        he = float(game_cfg["house_edge"]) if game_cfg else 0.05
        net = gross * (1 - he) if gross > 0 else 0.0

        if won and net > 0:
            await db.add_balance(self.user_id, net, note="horse_race win")
        await db.add_wager(self.user_id, total)
        await _earn_rakeback(
            self.user_id, total,
            interaction.user if isinstance(interaction.user, discord.Member) else None,
        )
        await _record(
            self.user_id, won, total, net if won else 0.0,
            game_id=GAME_ID,
            user=interaction.user,
            client=interaction.client,
            guild_id=interaction.guild.id if interaction.guild else None,
        )

        if won:
            footer = (
                f"✅ **Horse #{winner + 1}** won at **{odds[winner]:.2f}x**! "
                f"Bet on lane: **{utils.fmt_pts(lane_bet)}** pts → "
                f"payout **{utils.fmt_pts(net)}** pts "
                f"(total staked **{utils.fmt_pts(total)}** pts)."
            )
        else:
            footer = (
                f"❌ **Horse #{winner + 1}** won ({odds[winner]:.2f}x). "
                f"Lost **{utils.fmt_pts(total)}** pts total."
            )

        race_gif = await image_gen.render_horse_race_gif(
            horse_emojis=self.horse_emojis,
            winner_index=winner,
            finish_emoji=settings["finish_emoji"],
        )
        files = await _build_attachments(
            settings, odds, self.win_pcts, stakes, self.chip_bet, self.horse_emojis,
            race_gif=race_gif,
        )

        result_view = _HorseRaceResultView(
            self.user_id, settings, self.tiers,
            header=footer, stakes=stakes, chip_bet=self.chip_bet,
            odds=odds, win_pcts=self.win_pcts,
        )
        await interaction.message.edit(
            content=None, embed=None, attachments=files, view=result_view,
        )
        _horse_msg_to_user.pop(str(interaction.message.id), None)


class _HorseRaceResultView(ui.LayoutView):
    def __init__(
        self,
        user_id: int,
        settings: dict,
        tiers: list[int],
        *,
        header: str,
        stakes: list[float],
        chip_bet: float | None,
        odds: tuple[float, ...],
        win_pcts: tuple[float, ...],
    ):
        super().__init__(timeout=120)
        accent = discord.Colour(0x2ECC71)

        head = ui.Container(accent_color=accent)
        head.add_item(ui.TextDisplay(f"## 🏇 Race Result\n{header}"))
        self.add_item(head)

        gal = ui.Container(accent_color=accent)
        gal.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        g1 = ui.MediaGallery()
        g1.add_item(media=f"attachment://{BETS_ATTACHMENT}")
        gal.add_item(g1)
        self.add_item(gal)

        race_c = ui.Container(accent_color=accent)
        race_c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        g2 = ui.MediaGallery()
        g2.add_item(media=f"attachment://{RACE_ATTACHMENT}")
        race_c.add_item(g2)
        self.add_item(race_c)

        foot = ui.Container(accent_color=accent)
        foot.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        foot.add_item(ui.TextDisplay("-# `.horse` or **New Race** for fresh odds"))
        row = ui.ActionRow()
        row.add_item(HorseRacePlayAgainButton(user_id))
        foot.add_item(row)
        self.add_item(foot)


async def start_horse_race(ctx: commands.Context) -> None:
    settings = get_horse_race_settings()
    cfg = await db.get_game_config(GAME_ID)
    if not cfg or not cfg.get("enabled"):
        return await ctx.send(embed=utils.error_embed("Horse Race is disabled."))

    existing = await db.get_game_session(ctx.author.id)
    if existing:
        from cogs.games import _resolve_expired_session, _session_expired

        if _session_expired(existing):
            await _resolve_expired_session(ctx.author.id, existing)
        else:
            return await ctx.send(embed=utils.error_embed(
                f"You already have an active **{existing['game']}** game.",
            ))

    min_b = float(cfg["min_bet"])
    max_b = float(cfg["max_bet"])
    tiers = bet_tiers(min_b, max_b, 25)
    odds = _roll_odds(settings)
    win_pcts = win_chances(odds)

    if not settings.get("emoji_pool"):
        return await ctx.send(embed=utils.error_embed(
            "Horse Race emojileri ayarlanmamış. "
            "Game Management → **🏇 Horse Emojis** ile sunucu seçin.",
        ))

    await db.ensure_user(ctx.author.id, ctx.author.name)
    lane_emojis = roll_race_lane_emojis(settings)
    files = await _build_attachments(
        settings, odds, win_pcts, _empty_stakes(), None, lane_emojis,
    )
    view = HorseRaceSetupView(
        ctx.author.id, settings, tiers,
        odds=odds, win_pcts=win_pcts, horse_emojis=lane_emojis,
    )
    msg = await ctx.send(files=files, view=view)
    _horse_msg_to_user[str(msg.id)] = ctx.author.id

"""Horse race — V2 layout, bet select, toggle horse buttons, race GIF."""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

import discord
from discord import ui

from database import db
from Games.horse_race import (
    NUM_HORSES,
    bet_tiers,
    gross_payout,
    pick_winner_index,
    roll_race_odds,
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


def get_horse_race_settings() -> dict:
    games = get_data("server/games") or {}
    hr = games.get(GAME_ID) if isinstance(games.get(GAME_ID), dict) else {}
    em = hr.get("emojis") if isinstance(hr.get("emojis"), dict) else {}
    horses = [str(em.get(f"horse_{i}") or "🐴") for i in range(1, NUM_HORSES + 1)]
    return {
        "horse_emojis": horses,
        "finish_emoji": str(em.get("finish") or "🏁"),
        "house_edge_percent": float(hr.get("house_edge", 5.0) or 5.0),
        "min_bet": float(hr.get("min_bet", 10)),
        "max_bet": float(hr.get("max_bet", 10000)),
    }


def save_horse_race_emojis(
    *,
    horses: list[str],
    finish: str,
) -> None:
    from cogs.admin_panel import _ensure_horse_race_game_entry

    games = _ensure_horse_race_game_entry(get_data("server/games") or {})
    hr = games[GAME_ID]
    em = hr.setdefault("emojis", {})
    for i, h in enumerate(horses[:NUM_HORSES], start=1):
        em[f"horse_{i}"] = (h or "🐴").strip() or "🐴"
    em["finish"] = (finish or "🏁").strip() or "🏁"
    hr["last_modified"] = int(__import__("time").time())
    games[GAME_ID] = hr
    from modules.database import set_data

    set_data("server/games", games)


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
    selected: list[int],
    bet: float | None,
    *,
    race_gif: io.BytesIO | None = None,
) -> list[discord.File]:
    bets_buf = await image_gen.render_horse_race_bets_png(
        horse_emojis=settings["horse_emojis"],
        selected=selected,
        bet=bet,
        odds=odds,
        win_pcts=win_pcts,
    )
    files = [discord.File(io.BytesIO(bets_buf.getvalue()), filename=BETS_ATTACHMENT)]
    if race_gif is not None:
        files.append(discord.File(io.BytesIO(race_gif.getvalue()), filename=RACE_ATTACHMENT))
    else:
        wait_buf = await image_gen.render_horse_race_waiting_png()
        files.append(discord.File(io.BytesIO(wait_buf.getvalue()), filename=WAITING_ATTACHMENT))
    return files


def _status_text(bet: float | None, picks: list[int], odds: tuple[float, ...]) -> str:
    bet_s = f"**{utils.fmt_pts(bet)}** pts" if bet else "—"
    if picks:
        lanes = ", ".join(
            f"**#{i + 1}** ({odds[i]:.2f}x)" for i in sorted(picks)
        )
    else:
        lanes = "—"
    return (
        f"## 🏇 Horse Race\n"
        f"**Bet:** {bet_s}\n"
        f"**Your horses:** {lanes}\n\n"
        f"Odds refresh each race (up to **20x**). Tap horses (gray → green), pick bet, **Start Race**."
    )


def _populate_setup_view(view: "HorseRaceSetupView") -> None:
    """Build V2 sections — separators between gallery / bet / horses / start."""
    accent = discord.Colour(0xC9A227)

    head = ui.Container(accent_color=accent)
    head.add_item(ui.TextDisplay(_status_text(view.bet, view.picks, view.odds)))
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
    bet_c.add_item(ui.TextDisplay("### Bet amount"))
    row_bet = ui.ActionRow()
    row_bet.add_item(HorseRaceBetSelect(view.tiers, view.user_id))
    bet_c.add_item(row_bet)
    view.add_item(bet_c)

    horse_c = ui.Container(accent_color=accent)
    horse_c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    horse_c.add_item(ui.TextDisplay("### Pick horse(s)"))
    row1 = ui.ActionRow()
    row2 = ui.ActionRow()
    for i in range(NUM_HORSES):
        btn = HorsePickButton(
            index=i,
            emoji_raw=view.settings["horse_emojis"][i],
            odds=view.odds[i],
            selected=i in view.picks,
        )
        (row1 if i < 3 else row2).add_item(btn)
    horse_c.add_item(row1)
    horse_c.add_item(row2)
    view.add_item(horse_c)

    go_c = ui.Container(accent_color=accent)
    go_c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    row_go = ui.ActionRow()
    row_go.add_item(HorseRaceStartButton())
    go_c.add_item(row_go)
    view.add_item(go_c)


class HorseRaceBetSelect(ui.Select):
    def __init__(self, tiers: list[int], owner_id: int):
        opts = [
            discord.SelectOption(label=f"{t:,} pts", value=str(t))
            for t in tiers[:25]
        ]
        super().__init__(
            placeholder="Select bet amount…",
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
        view.bet = float(self.values[0])
        await view.refresh(interaction)


class HorsePickButton(ui.Button):
    def __init__(self, *, index: int, emoji_raw: str, odds: float, selected: bool):
        super().__init__(
            label=f"#{index + 1} · {odds:.1f}x",
            style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary,
            emoji=_parse_button_emoji(emoji_raw),
        )
        self.horse_index = index

    async def callback(self, interaction: discord.Interaction):
        view: HorseRaceSetupView = self.view  # type: ignore[assignment]
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your race."), ephemeral=True,
            )
        i = self.horse_index
        if i in view.picks:
            view.picks.remove(i)
        else:
            view.picks.append(i)
            view.picks.sort()
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
        cfg = await db.get_game_config(GAME_ID)
        min_b = float(cfg["min_bet"]) if cfg else settings["min_bet"]
        max_b = float(cfg["max_bet"]) if cfg else settings["max_bet"]
        tiers = bet_tiers(min_b, max_b, 25)
        odds = roll_race_odds()
        win_pcts = win_chances(odds)
        files = await _build_attachments(settings, odds, win_pcts, [], None)
        view = HorseRaceSetupView(
            self.owner_id, settings, tiers, odds=odds, win_pcts=win_pcts,
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
        bet: float | None = None,
        picks: list[int] | None = None,
        racing: bool = False,
    ):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.settings = settings
        self.tiers = tiers
        self.odds = odds or roll_race_odds()
        self.win_pcts = win_pcts or win_chances(self.odds)
        self.bet = bet
        self.picks = list(picks or [])
        self._racing = racing
        _populate_setup_view(self)

    async def refresh(self, interaction: discord.Interaction) -> None:
        if self._racing:
            return
        await interaction.response.defer()
        files = await _build_attachments(
            self.settings, self.odds, self.win_pcts, self.picks, self.bet,
        )
        new_view = HorseRaceSetupView(
            self.user_id, self.settings, self.tiers,
            odds=self.odds, win_pcts=self.win_pcts,
            bet=self.bet, picks=self.picks,
        )
        await interaction.message.edit(
            content=None, embed=None, attachments=files, view=new_view,
        )
        _horse_msg_to_user[str(interaction.message.id)] = self.user_id

    async def start_race(self, interaction: discord.Interaction) -> None:
        from cogs.games import _check_game_interaction, _earn_rakeback, _payout, _record

        if not self.picks:
            return await interaction.response.send_message(
                embed=utils.error_embed("Select at least one horse."), ephemeral=True,
            )
        if self.bet is None or self.bet <= 0:
            return await interaction.response.send_message(
                embed=utils.error_embed("Select a bet amount."), ephemeral=True,
            )
        if not await _check_game_interaction(
            interaction, self.user_id, GAME_ID, self.bet,
        ):
            return

        self._racing = True
        self.stop()
        await interaction.response.defer()

        bet = float(self.bet)
        picks = list(self.picks)
        settings = self.settings
        odds = self.odds

        await db.ensure_user(self.user_id, interaction.user.name)

        prospective = max(gross_payout(bet, i, odds) for i in picks)
        rigged = await bc.should_rig_outcome(
            self.user_id, GAME_ID, bet, gross=prospective,
        )
        winner = pick_winner_index(odds, rig_lose=rigged, player_picks=picks)
        won = winner in picks
        gross = gross_payout(bet, winner, odds) if won else 0.0

        if won:
            net = await _payout(self.user_id, GAME_ID, bet, gross)
            await _record(
                self.user_id, True, bet, net,
                game_id=GAME_ID,
                user=interaction.user,
                client=interaction.client,
                guild_id=interaction.guild.id if interaction.guild else None,
            )
            footer = (
                f"✅ **Horse #{winner + 1}** won at **{odds[winner]:.2f}x**! "
                f"Payout **{utils.fmt_pts(net)}** pts."
            )
        else:
            await db.add_balance(self.user_id, -bet, note="horse_race bet")
            await db.add_wager(self.user_id, bet)
            await _earn_rakeback(
                self.user_id, bet,
                interaction.user if isinstance(interaction.user, discord.Member) else None,
            )
            await _record(
                self.user_id, False, bet, 0.0,
                game_id=GAME_ID,
                user=interaction.user,
                client=interaction.client,
                guild_id=interaction.guild.id if interaction.guild else None,
            )
            footer = f"❌ **Horse #{winner + 1}** won ({odds[winner]:.2f}x). Your picks lost."

        race_gif = await image_gen.render_horse_race_gif(
            horse_emojis=settings["horse_emojis"],
            winner_index=winner,
            finish_emoji=settings["finish_emoji"],
        )
        files = await _build_attachments(
            settings, odds, self.win_pcts, picks, bet, race_gif=race_gif,
        )

        result_view = _HorseRaceResultView(
            self.user_id, settings, self.tiers,
            header=footer, picks=picks, bet=bet,
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
        picks: list[int],
        bet: float,
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
    odds = roll_race_odds()
    win_pcts = win_chances(odds)

    await db.ensure_user(ctx.author.id, ctx.author.name)
    files = await _build_attachments(settings, odds, win_pcts, [], None)
    view = HorseRaceSetupView(
        ctx.author.id, settings, tiers, odds=odds, win_pcts=win_pcts,
    )
    msg = await ctx.send(files=files, view=view)
    _horse_msg_to_user[str(msg.id)] = ctx.author.id

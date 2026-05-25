"""Components V2 layout for prefix `.cases` opening hub."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Awaitable

import discord
from discord import ui

from modules.ui_v2 import (
    ACCENT_BRAND,
    add_controls_to_container,
    add_text,
    clip_select_description,
    new_container,
    panel_markdown,
)
from modules.constants import FOOTER_TEXT
from modules.player import Player
from modules.utils import format_balance

if TYPE_CHECKING:
    pass

CASE_GIF_ATTACHMENT = "cases.gif"
MAX_OPEN = 4


def hub_panel_body(
    *,
    view_mode: str,
    case_count: int,
    balance: int,
    selected_case: dict | None,
    count: int,
    total_price: int | None,
    item_preview: str,
) -> str:
    mode_lbl = "Official" if view_mode == "house" else "Community"
    lines = [
        f"**{mode_lbl}** · {case_count} cases",
        f"**Balance:** {format_balance(balance, 'real')}",
    ]
    if selected_case:
        lines.append(
            f"\n{selected_case.get('emoji', '📦')} **{selected_case.get('name', 'Case')}**"
        )
        if total_price is not None:
            unit = int(selected_case.get("price", 0))
            lines.append(
                f"**Price:** {format_balance(unit, 'real')} × **{count}** = "
                f"**{format_balance(total_price, 'real')}**"
            )
        if item_preview:
            lines.append(f"**Items:** {item_preview}")
    else:
        lines.append("\nSelect a case, pick quantity **×1–×4**, then **Open**.")
    return "\n".join(lines)


class _CasePickSelect(ui.Select):
    def __init__(
        self,
        hub: "CasesOpenHubLayout",
        options: list[discord.SelectOption],
    ):
        super().__init__(
            placeholder="Select a case…",
            options=options or [
                discord.SelectOption(label="No cases", value="_none", description="—"),
            ],
            min_values=1,
            max_values=1,
            custom_id="cases_hub:pick",
        )
        self._hub = hub

    async def callback(self, interaction: discord.Interaction):
        await self._hub._handle_case_pick(interaction, self.values[0])


class CasesOpenHubLayout(ui.LayoutView):
    """Single message: V2 panel + reel GIF (edited on each open)."""

    def __init__(
        self,
        user_id: int,
        *,
        view_mode: str = "house",
        case_id: str | None = None,
        is_community: bool = False,
        count: int = 1,
        show_gif: bool = False,
        list_cases_fn: Callable[[dict, str], list] | None = None,
        get_db_fn: Callable[[], dict] | None = None,
        selected_fn: Callable[[dict, str | None, bool], tuple | None] | None = None,
        preview_fn: Callable[[dict, dict], str] | None = None,
        open_fn: Callable[..., Awaitable[tuple[str | None, object | None]]] | None = None,
    ):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.view_mode = view_mode
        self.case_id = case_id
        self.is_community = is_community
        self.count = max(1, min(MAX_OPEN, count))
        self.show_gif = show_gif
        self._list_cases = list_cases_fn
        self._get_db = get_db_fn
        self._selected_fn = selected_fn
        self._preview_fn = preview_fn
        self._open_fn = open_fn
        self._rebuild()

    def _rebuild(self) -> None:
        self.clear_items()
        db = self._get_db() if self._get_db else {}
        cases = self._list_cases(db, self.view_mode) if self._list_cases else []
        selected = (
            self._selected_fn(db, self.case_id, self.is_community)
            if self._selected_fn and self.case_id
            else None
        )

        case_dict = selected[1] if selected else None
        preview = ""
        total_price = None
        if case_dict and self._preview_fn:
            preview = self._preview_fn(db, case_dict)
            total_price = int(case_dict.get("price", 0)) * self.count

        bal = Player(self.user_id).get_balance("real")
        body = hub_panel_body(
            view_mode=self.view_mode,
            case_count=len(cases),
            balance=bal,
            selected_case=case_dict,
            count=self.count,
            total_price=total_price,
            item_preview=preview,
        )

        container = new_container(accent=ACCENT_BRAND)
        add_text(
            container,
            panel_markdown(
                title="Case Opening",
                body=body,
                footer=f"{FOOTER_TEXT}  ·  Max ×{MAX_OPEN}",
                emoji="📦",
            ),
        )

        if self.show_gif:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            gallery = ui.MediaGallery()
            gallery.add_item(media=f"attachment://{CASE_GIF_ATTACHMENT}")
            container.add_item(gallery)

        controls: list[ui.Item] = []
        if cases:
            opts = [
                discord.SelectOption(
                    label=c.get("name", "?")[:25],
                    value=f"{'c' if is_cc else 'h'}:{cid}",
                    emoji=c.get("emoji", "📦"),
                    description=clip_select_description(
                        format_balance(c.get("price", 0), "real")
                    ),
                    default=(cid == self.case_id and is_cc == self.is_community),
                )
                for cid, c, is_cc in cases[:25]
            ]
            controls.append(_CasePickSelect(self, opts))

        for n, em in [(1, "1️⃣"), (2, "2️⃣"), (3, "3️⃣"), (4, "4️⃣")]:
            btn = ui.Button(
                label=f"×{n}",
                emoji=em,
                style=discord.ButtonStyle.primary if self.count == n else discord.ButtonStyle.secondary,
            )
            btn.callback = self._make_count_cb(n)
            controls.append(btn)

        toggle = ui.Button(
            label="Community" if self.view_mode == "house" else "Official",
            emoji="🌐" if self.view_mode == "house" else "🏠",
            style=discord.ButtonStyle.secondary,
        )
        toggle.callback = self._on_toggle
        controls.append(toggle)

        open_btn = ui.Button(
            label=f"Open ×{self.count}",
            emoji="🎰",
            style=discord.ButtonStyle.success,
            disabled=not self.case_id,
        )
        open_btn.callback = self._on_open
        controls.append(open_btn)

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        add_controls_to_container(container, controls)
        self.add_item(container)

    def _make_count_cb(self, n: int):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("Not your panel.", ephemeral=True)
            self.count = n
            self._rebuild()
            await interaction.response.edit_message(view=self)
        return _cb

    async def _handle_case_pick(self, interaction: discord.Interaction, raw: str):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        if raw == "_none":
            return await interaction.response.defer()
        self.is_community = raw.startswith("c:")
        self.case_id = raw.split(":", 1)[1]
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_toggle(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        self.view_mode = "community" if self.view_mode == "house" else "house"
        self.case_id = None
        self.is_community = self.view_mode == "community"
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_open(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        if not self.case_id:
            return await interaction.response.send_message("Select a case first.", ephemeral=True)
        if not self._open_fn:
            return await interaction.response.send_message("Open handler missing.", ephemeral=True)

        await interaction.response.defer()
        err, gif_buf = await self._open_fn(
            interaction.user,
            self.case_id,
            self.is_community,
            self.count,
            client=interaction.client,
            guild_id=interaction.guild.id if interaction.guild else None,
        )
        if err:
            return await interaction.followup.send(err, ephemeral=True)

        self.show_gif = True
        self._rebuild()
        files = [discord.File(gif_buf, CASE_GIF_ATTACHMENT)] if gif_buf else []
        await interaction.message.edit(
            content=None,
            embed=None,
            attachments=files,
            view=self,
        )


def make_cases_hub(
    user_id: int,
    *,
    view_mode: str = "house",
    case_id: str | None = None,
    is_community: bool = False,
    count: int = 1,
    show_gif: bool = False,
) -> CasesOpenHubLayout:
    from cogs import cases as cases_mod

    async def open_fn(user, cid, is_cc, cnt):
        return await cases_mod._settle_case_opens(user, cid, is_cc, cnt)

    return CasesOpenHubLayout(
        user_id,
        view_mode=view_mode,
        case_id=case_id,
        is_community=is_community,
        count=count,
        show_gif=show_gif,
        list_cases_fn=cases_mod._hub_list_cases,
        get_db_fn=cases_mod._get_db,
        selected_fn=cases_mod._hub_selected,
        preview_fn=cases_mod._hub_item_preview,
        open_fn=open_fn,
    )

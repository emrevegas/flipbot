"""Components V2 layout for prefix `.cases` opening hub."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Awaitable, Callable

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

CASE_PREVIEW_ATTACHMENT = "case_preview.png"
CASE_GIF_ATTACHMENT = "cases.gif"
MAX_OPEN = 4
CASES_PER_SELECT = 25
CASES_PER_PAGE = 75  # up to 3 selects × 25 per page


def hub_panel_body(
    *,
    view_mode: str,
    case_count: int,
    balance: int,
    selected_case: dict | None,
    count: int,
    total_price: int | None,
    item_preview: str,
    page_info: str = "",
) -> str:
    mode_lbl = "Official" if view_mode == "house" else "Community"
    lines = [
        f"**{mode_lbl}** · {case_count} cases",
        f"**Balance:** {format_balance(balance, 'real')}",
    ]
    if page_info:
        lines.append(page_info)
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
        lines.append(
            "\n**Loot table** (rates & values) is shown above."
            + (f"\n**Open animation** below after you spin." if item_preview == "__opened__" else "")
        )
        if item_preview and item_preview != "__opened__":
            lines.append(f"**Top items:** {item_preview}")
    else:
        lines.append("\nSelect a case, pick quantity **×1–×4**, then **Open**.")
    return "\n".join(lines)


def _case_select_options(
    cases: list[tuple],
    chunk: list[tuple],
    *,
    selected_case_id: str | None,
    selected_is_community: bool,
) -> list[discord.SelectOption]:
    opts: list[discord.SelectOption] = []
    for cid, c, is_cc in chunk:
        opts.append(
            discord.SelectOption(
                label=c.get("name", "?")[:25],
                value=f"{'c' if is_cc else 'h'}:{cid}",
                emoji=c.get("emoji", "📦"),
                description=clip_select_description(
                    format_balance(c.get("price", 0), "real")
                ),
                default=(
                    cid == selected_case_id and is_cc == selected_is_community
                ),
            )
        )
    return opts or [
        discord.SelectOption(label="No cases", value="_none", description="—"),
    ]


class _CasePickSelect(ui.Select):
    def __init__(
        self,
        hub: "CasesOpenHubLayout",
        options: list[discord.SelectOption],
        *,
        placeholder: str = "Select a case…",
        custom_id: str = "cases_hub:pick:0:0",
    ):
        super().__init__(
            placeholder=placeholder[:100],
            options=options,
            min_values=1,
            max_values=1,
            custom_id=custom_id,
        )
        self._hub = hub

    async def callback(self, interaction: discord.Interaction):
        await self._hub._handle_case_pick(interaction, self.values[0])


class CasesOpenHubLayout(ui.LayoutView):
    """Single message: V2 panel + loot PNG when selected + reel GIF after open."""

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
        self._preview_buf: io.BytesIO | None = None
        self._gif_buf: io.BytesIO | None = None
        self.case_page = 0
        self._rebuild()

    def _attachment_files(self) -> list[discord.File]:
        files: list[discord.File] = []
        if self._preview_buf is not None:
            self._preview_buf.seek(0)
            files.append(discord.File(self._preview_buf, CASE_PREVIEW_ATTACHMENT))
        if self.show_gif and self._gif_buf is not None:
            self._gif_buf.seek(0)
            files.append(discord.File(self._gif_buf, CASE_GIF_ATTACHMENT))
        return files

    def _add_media_sections(self, container: ui.Container) -> None:
        """Loot PNG and open GIF in separate galleries (stacked), not side-by-side."""
        if not self.case_id or self._preview_buf is None:
            return

        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        preview_gallery = ui.MediaGallery()
        preview_gallery.add_item(media=f"attachment://{CASE_PREVIEW_ATTACHMENT}")
        container.add_item(preview_gallery)

        if self.show_gif and self._gif_buf is not None:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            open_gallery = ui.MediaGallery()
            open_gallery.add_item(media=f"attachment://{CASE_GIF_ATTACHMENT}")
            container.add_item(open_gallery)

    async def _refresh_preview(self) -> None:
        if not self.case_id or not self._get_db or not self._selected_fn:
            self._preview_buf = None
            return
        selected = self._selected_fn(self._get_db(), self.case_id, self.is_community)
        if not selected:
            self._preview_buf = None
            return
        from cogs import cases as cases_mod

        _, case_dict, _ = selected
        self._preview_buf = await cases_mod.build_case_preview_buffer(self._get_db(), case_dict)

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
        preview_flag = "__opened__" if self.show_gif else preview
        page_info = ""
        if len(cases) > CASES_PER_SELECT:
            total_pages = max(1, (len(cases) - 1) // CASES_PER_PAGE + 1)
            page = min(self.case_page, total_pages - 1)
            self.case_page = page
            page_info = f"**Cases page {page + 1} / {total_pages}** (25 per dropdown)"
        body = hub_panel_body(
            view_mode=self.view_mode,
            case_count=len(cases),
            balance=bal,
            selected_case=case_dict,
            count=self.count,
            total_price=total_price,
            item_preview=preview_flag,
            page_info=page_info,
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

        self._add_media_sections(container)

        controls: list[ui.Item] = []
        controls.extend(self._build_case_list_controls(cases))

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

    def _build_case_list_controls(self, cases: list) -> list[ui.Item]:
        """Up to 3 selects (25 each) per page; next/back when more than 75 cases."""
        out: list[ui.Item] = []
        total = len(cases)
        if total == 0:
            return out

        if total <= CASES_PER_SELECT:
            opts = _case_select_options(
                cases, cases,
                selected_case_id=self.case_id,
                selected_is_community=self.is_community,
            )
            out.append(_CasePickSelect(self, opts))
            return out

        total_pages = max(1, (total - 1) // CASES_PER_PAGE + 1)
        page = min(self.case_page, total_pages - 1)
        self.case_page = page
        page_start = page * CASES_PER_PAGE

        if page > 0:
            back = ui.Button(
                label="Back",
                emoji="◀️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"cases_hub:page:{page}:back",
            )
            back.callback = self._make_page_cb(-1)
            out.append(back)

        for slot, offset in enumerate((0, 25, 50)):
            abs_start = page_start + offset
            if abs_start >= total or offset >= CASES_PER_PAGE:
                break
            abs_end = min(abs_start + CASES_PER_SELECT, total, page_start + CASES_PER_PAGE)
            chunk = cases[abs_start:abs_end]
            if not chunk:
                continue
            placeholder = f"Cases {abs_start + 1}–{abs_end}"
            opts = _case_select_options(
                cases,
                chunk,
                selected_case_id=self.case_id,
                selected_is_community=self.is_community,
            )
            out.append(
                _CasePickSelect(
                    self,
                    opts,
                    placeholder=placeholder,
                    custom_id=f"cases_hub:pick:{page}:{slot}",
                )
            )

        if page_start + CASES_PER_PAGE < total:
            nxt = ui.Button(
                label="Next page",
                emoji="▶️",
                style=discord.ButtonStyle.primary,
                custom_id=f"cases_hub:page:{page}:next",
            )
            nxt.callback = self._make_page_cb(1)
            out.append(nxt)

        return out

    def _make_page_cb(self, delta: int):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("Not your panel.", ephemeral=True)
            self.case_page = max(0, self.case_page + delta)
            self._rebuild()
            await interaction.response.edit_message(
                attachments=self._attachment_files(),
                view=self,
            )
        return _cb

    def _make_count_cb(self, n: int):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("Not your panel.", ephemeral=True)
            self.count = n
            self._rebuild()
            files = self._attachment_files()
            await interaction.response.edit_message(
                attachments=files or [],
                view=self,
            )
        return _cb

    async def _handle_case_pick(self, interaction: discord.Interaction, raw: str):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        if raw == "_none":
            return await interaction.response.defer()
        await interaction.response.defer()
        self.is_community = raw.startswith("c:")
        self.case_id = raw.split(":", 1)[1]
        self.show_gif = False
        self._gif_buf = None
        await self._refresh_preview()
        self._rebuild()
        await interaction.message.edit(
            attachments=self._attachment_files(),
            view=self,
        )

    async def _on_toggle(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        self.view_mode = "community" if self.view_mode == "house" else "house"
        self.case_id = None
        self.is_community = self.view_mode == "community"
        self.case_page = 0
        self._preview_buf = None
        self._gif_buf = None
        self.show_gif = False
        self._rebuild()
        await interaction.response.edit_message(attachments=[], view=self)

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

        if self._preview_buf is None:
            await self._refresh_preview()

        self._gif_buf = gif_buf if isinstance(gif_buf, io.BytesIO) else None
        self.show_gif = self._gif_buf is not None
        self._rebuild()
        await interaction.message.edit(
            attachments=self._attachment_files(),
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

    async def open_fn(user, cid, is_cc, cnt, **kwargs):
        return await cases_mod._settle_case_opens(user, cid, is_cc, cnt, **kwargs)

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

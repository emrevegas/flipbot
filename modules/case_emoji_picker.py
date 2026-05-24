"""Paginated emoji picker (guild + application) and item-library pagination."""

from __future__ import annotations

import os
from typing import Awaitable, Callable

import discord
from discord.ui import Button, Select, View

from modules.constants import FOOTER_TEXT

CHUNK_SIZE = 25          # options per select
MAX_SELECTS = 4          # max 4 selects per view (4 × 25 = 100)
BIG_PAGE_SIZE = 100      # emojis per navigation page
SELECT_PAGE_SIZE = 25    # item list pagination


def _adm(key: str, lang: str, **kwargs) -> str:
    from modules.translator import t

    s = t(f"cases.admin.{key}", lang=lang, **kwargs)
    if s == f"cases.admin.{key}":
        defaults = {
            "emoji_pick_ph": "Pick emoji for {name}",
            "emoji_pick_ph_page": "{name} — {start}-{end} / {total}",
            "btn_prev": "◀",
            "btn_next": "▶",
            "btn_prev_100": "◀ 100",
            "btn_next_100": "100 ▶",
            "btn_confirm_select": "✅ Select",
            "select_add_items": "Select items…",
            "select_remove_items": "Remove items…",
            "pick_one": "Select one emoji first.",
            "pick_one_only": "Select only one emoji.",
            "page_footer": "Page {page}/{pages}",
        }
        return defaults.get(key, key).format(**kwargs)
    return s


def emoji_to_str(emoji: discord.Emoji | discord.PartialEmoji) -> str:
    return str(emoji)


async def fetch_emoji_pool(
    guild: discord.Guild | None,
    client: discord.Client,
) -> list:
    """Guild emojis first, then application emojis (deduped by id)."""
    seen: set[int] = set()
    pool: list = []

    if guild:
        for e in guild.emojis:
            if e.id not in seen:
                seen.add(e.id)
                pool.append(e)

    try:
        for e in await client.fetch_application_emojis():
            eid = getattr(e, "id", None)
            if eid and eid not in seen:
                seen.add(eid)
                pool.append(e)
    except Exception:
        pass

    return pool


def _big_page_count(total: int) -> int:
    return max(1, (total + BIG_PAGE_SIZE - 1) // BIG_PAGE_SIZE)


def _big_page_chunk(pool: list, big_page: int) -> list:
    start = big_page * BIG_PAGE_SIZE
    return pool[start : start + BIG_PAGE_SIZE]


def _num_selects_for_count(n: int) -> int:
    if n <= 0:
        return 0
    return min(MAX_SELECTS, (n + CHUNK_SIZE - 1) // CHUNK_SIZE)


def _split_into_select_chunks(chunk: list) -> list[list]:
    n_sel = _num_selects_for_count(len(chunk))
    return [chunk[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE] for i in range(n_sel)]


class _EmojiSelect(Select):
    def __init__(self, emojis: list, row: int, label_name: str, lang: str, slot: int, total_in_page: int):
        options = [
            discord.SelectOption(label=e.name[:100], value=str(e.id), emoji=e)
            for e in emojis
        ]
        start = slot * CHUNK_SIZE + 1
        end = start + len(emojis) - 1
        ph = _adm("emoji_pick_ph_page", lang, name=label_name, page=slot + 1, pages=total_in_page)
        if len(emojis) == 1:
            ph = f"{label_name[:20]} — #{start}"[:150]
        super().__init__(
            placeholder=ph[:150],
            options=options,
            min_values=0,
            max_values=1,
            row=row,
            custom_id=f"cases_emoji:{row}:{os.urandom(4).hex()}",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class EmojiPickerView(View):
    """
    Up to 4 selects × 25 emojis (100 per screen).
    If total > 100, ◀ / ▶ switch the 100-emoji block (101–200, etc.).
    """

    def __init__(
        self,
        *,
        label_name: str,
        emoji_pool: list,
        save_fn: Callable[[str], Awaitable[tuple]],
        user_id: int,
        lang: str = "en",
        big_page: int = 0,
        embed: discord.Embed | None = None,
    ):
        super().__init__(timeout=180)
        self.label_name = label_name
        self.emoji_pool = emoji_pool
        self.save_fn = save_fn
        self.user_id = user_id
        self.lang = lang
        self.big_page = big_page
        self.embed = embed
        self._selects: list[_EmojiSelect] = []
        self._rebuild()

    def _rebuild(self) -> None:
        self.clear_items()
        total = len(self.emoji_pool)
        pages = _big_page_count(total)
        self.big_page = max(0, min(self.big_page, pages - 1))
        chunk = _big_page_chunk(self.emoji_pool, self.big_page)
        parts = _split_into_select_chunks(chunk)
        self._selects = []

        global_start = self.big_page * BIG_PAGE_SIZE + 1
        global_end = min((self.big_page + 1) * BIG_PAGE_SIZE, total)

        for i, part in enumerate(parts):
            sel = _EmojiSelect(
                part, row=i, label_name=self.label_name, lang=self.lang,
                slot=i, total_in_page=len(parts),
            )
            self._selects.append(sel)
            self.add_item(sel)

        nav_row = min(len(parts), 3) if len(parts) < MAX_SELECTS else MAX_SELECTS
        if total > BIG_PAGE_SIZE:
            if self.big_page > 0:
                prev = Button(
                    label=_adm("btn_prev_100", self.lang),
                    style=discord.ButtonStyle.secondary,
                    row=nav_row,
                )
                prev.callback = self._on_prev_page
                self.add_item(prev)
            if self.big_page < pages - 1:
                nxt = Button(
                    label=_adm("btn_next_100", self.lang),
                    style=discord.ButtonStyle.secondary,
                    row=nav_row,
                )
                nxt.callback = self._on_next_page
                self.add_item(nxt)

        confirm = Button(
            label=_adm("btn_confirm_select", self.lang),
            style=discord.ButtonStyle.success,
            row=4,
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        if self.embed:
            self.embed.set_footer(
                text=(
                    f"{FOOTER_TEXT}  •  "
                    f"{_adm('emoji_pick_ph_page', self.lang, name=self.label_name, page=self.big_page + 1, pages=pages)}"
                    f"  •  {global_start}-{global_end}/{total}"
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    async def _on_confirm(self, interaction: discord.Interaction):
        chosen: list[str] = []
        for sel in self._selects:
            chosen.extend(sel.values)

        if not chosen:
            return await interaction.response.send_message(
                _adm("pick_one", self.lang), ephemeral=True
            )
        if len(chosen) > 1:
            return await interaction.response.send_message(
                _adm("pick_one_only", self.lang), ephemeral=True
            )

        eid = int(chosen[0])
        emoji_obj = discord.utils.get(self.emoji_pool, id=eid)
        emoji_str = emoji_to_str(emoji_obj) if emoji_obj else f"<:{eid}>"
        result_embed, result_view = await self.save_fn(emoji_str)
        await interaction.response.edit_message(embed=result_embed, view=result_view)

    async def _on_prev_page(self, interaction: discord.Interaction):
        self.big_page -= 1
        self._rebuild()
        await interaction.response.edit_message(embed=self.embed, view=self)

    async def _on_next_page(self, interaction: discord.Interaction):
        self.big_page += 1
        self._rebuild()
        await interaction.response.edit_message(embed=self.embed, view=self)


def build_emoji_picker_message(
    label_name: str,
    emoji_pool: list,
    save_fn,
    user_id: int,
    lang: str,
    *,
    big_page: int = 0,
) -> tuple[discord.Embed, EmojiPickerView]:
    from modules.translator import t

    uid = str(user_id)
    total = len(emoji_pool)
    pages = _big_page_count(total)
    big_page = max(0, min(big_page, pages - 1))
    gs = big_page * BIG_PAGE_SIZE + 1
    ge = min((big_page + 1) * BIG_PAGE_SIZE, total)

    embed = discord.Embed(
        title=t("cases.pick_emoji_title", user_id=uid),
        description=t("cases.pick_emoji_desc", user_id=uid, name=label_name),
        color=0x5865F2,
    )
    embed.set_footer(
        text=f"{FOOTER_TEXT}  •  {_adm('page_footer', lang, page=big_page + 1, pages=pages)}  •  {gs}-{ge}/{total}"
    )
    view = EmojiPickerView(
        label_name=label_name,
        emoji_pool=emoji_pool,
        save_fn=save_fn,
        user_id=user_id,
        lang=lang,
        big_page=big_page,
        embed=embed,
    )
    return embed, view


async def open_emoji_picker_after_item_modal(
    interaction: discord.Interaction,
    *,
    label_name: str,
    save_fn,
    user_id: int,
    lang: str,
) -> bool:
    """After Add/Edit item modal. Returns False if caller should open text emoji modal."""
    pool = await fetch_emoji_pool(interaction.guild, interaction.client)
    if not pool:
        return False

    embed, view = build_emoji_picker_message(label_name, pool, save_fn, user_id, lang)
    await interaction.response.edit_message(embed=embed, view=view)
    return True


def _page_slice(pool: list, page: int) -> tuple[list, int, int, int]:
    total = len(pool)
    pages = max(1, (total + SELECT_PAGE_SIZE - 1) // SELECT_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * SELECT_PAGE_SIZE
    end = min(start + SELECT_PAGE_SIZE, total)
    return pool[start:end], page, pages, total


class PaginatedItemListView(View):
    """Paginated multi-select for adding/removing items from a case."""

    def __init__(
        self,
        *,
        user_id: int,
        entries: list[tuple[str, dict]],
        placeholder_key: str,
        on_submit: Callable[[discord.Interaction, list[str]], Awaitable[None]],
        on_back: Callable[[discord.Interaction], Awaitable[None]],
        lang: str,
        page: int = 0,
        accent: int = 0x5865F2,
    ):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.entries = entries
        self.placeholder_key = placeholder_key
        self.on_submit_cb = on_submit
        self.on_back_cb = on_back
        self.lang = lang
        self.page = page
        self.accent = accent
        self._rebuild()

    def _rebuild(self) -> None:
        self.clear_items()
        chunk, self.page, pages, total = _page_slice(self.entries, self.page)

        if chunk:
            options = [
                discord.SelectOption(
                    label=item.get("name", "?")[:50],
                    value=iid,
                    emoji=item.get("emoji", "❓"),
                    description=f"ID: {iid}"[:100],
                )
                for iid, item in chunk
            ]
            start = self.page * SELECT_PAGE_SIZE + 1
            end = min((self.page + 1) * SELECT_PAGE_SIZE, total)
            ph = f"{_adm(self.placeholder_key, self.lang)} ({start}-{end}/{total})"[:150]
            sel = Select(
                placeholder=ph,
                options=options,
                min_values=1,
                max_values=min(len(options), 10),
                row=0,
            )
            sel.callback = self._on_select
            self.add_item(sel)

        row_nav = 1
        if self.page > 0:
            prev = Button(label=_adm("btn_prev", self.lang), style=discord.ButtonStyle.secondary, row=row_nav)
            prev.callback = self._on_prev
            self.add_item(prev)
        if self.page < pages - 1:
            nxt = Button(label=_adm("btn_next", self.lang), style=discord.ButtonStyle.secondary, row=row_nav)
            nxt.callback = self._on_next
            self.add_item(nxt)

        back = Button(label=_adm("btn_cancel", self.lang), style=discord.ButtonStyle.secondary, row=4)
        back.callback = self._on_back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        await self.on_submit_cb(interaction, interaction.data["values"])

    async def _on_prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_next(self, interaction: discord.Interaction):
        pages = max(1, (len(self.entries) + SELECT_PAGE_SIZE - 1) // SELECT_PAGE_SIZE)
        self.page = min(pages - 1, self.page + 1)
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_back(self, interaction: discord.Interaction):
        await self.on_back_cb(interaction)

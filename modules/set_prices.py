"""Sequential /setprices flow — add application emojis to the item library."""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

import discord
from discord.ui import Button, Modal, TextInput, View

from modules.constants import FOOTER_TEXT
from modules.database import get_data, replace_data
from modules.translator import t
from modules.utils import format_balance, create_error_embed, get_user_lang

if TYPE_CHECKING:
    pass

_EMOJI_ID_RE = re.compile(r"<a?:(\w+):(\d+)>")


def _get_db() -> dict:
    data = get_data("server/cases") or {}
    data.setdefault("items", {})
    return data


def _save_db(data: dict) -> None:
    replace_data("server/cases", data)


def _sp(key: str, lang: str, **kwargs) -> str:
    s = t(f"cases.setprices.{key}", lang=lang, **kwargs)
    if s == f"cases.setprices.{key}":
        defaults = {
            "title": "Set Item Price",
            "desc": "Set a price for this application emoji to add it to the library.",
            "emoji_raw": "Emoji ID name: `{name}`",
            "item_name": "Item name: **{name}**",
            "progress": "Remaining: **{remaining}**  •  Step **{step}/{total}**",
            "btn_set_price": "Set price",
            "btn_skip": "Skip",
            "btn_close": "Close",
            "modal_title": "Item price",
            "modal_value_label": "Price (points)",
            "modal_value_ph": "e.g. 5000",
            "done_title": "All done",
            "done_desc": "Every application emoji is already in the item library.",
            "complete_title": "Finished",
            "complete_desc": "Added **{added}** item(s) this session. **{remaining}** emoji(s) still without a price.",
            "all_complete_desc": "All pending emojis have been priced and added.",
            "fetch_failed": "Could not fetch application emojis.",
            "no_pending": "No application emojis left to price.",
            "saved_toast": "Added **{name}** — {value}",
        }
        return defaults.get(key, key).format(**kwargs)
    return s


def format_emoji_item_name(raw_name: str) -> str:
    """AncestralLensOfRiches -> Ancestral Lens Of Riches; Decayed_lock -> Decayed Lock."""
    s = raw_name.replace("_", " ")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", s)
    return " ".join(part.capitalize() for part in s.split() if part)


def emoji_id_from_item_str(emoji_str: str) -> int | None:
    if not emoji_str:
        return None
    m = _EMOJI_ID_RE.search(emoji_str)
    return int(m.group(2)) if m else None


def library_emoji_ids(db: dict) -> set[int]:
    ids: set[int] = set()
    for item in db.get("items", {}).values():
        eid = emoji_id_from_item_str(str(item.get("emoji", "")))
        if eid is not None:
            ids.add(eid)
    return ids


async def fetch_pending_app_emojis(client: discord.Client) -> tuple[list, str | None]:
    db = _get_db()
    in_library = library_emoji_ids(db)
    try:
        app_emojis = await client.fetch_application_emojis()
    except Exception:
        return [], "fetch_failed"

    pending = [e for e in app_emojis if getattr(e, "id", None) not in in_library]
    pending.sort(key=lambda e: (e.name or "").lower())
    return pending, None


def add_app_emoji_item(emoji: discord.Emoji, value: int) -> tuple[str, str]:
    db = _get_db()
    iid = str(uuid.uuid4())[:8]
    name = format_emoji_item_name(emoji.name or "?")
    db.setdefault("items", {})[iid] = {"name": name, "emoji": str(emoji), "value": value}
    _save_db(db)
    return iid, name


def build_setprices_embed(
    *,
    emoji: discord.Emoji | None,
    lang: str,
    step: int,
    total: int,
    remaining: int,
    added_session: int,
    done: bool = False,
    all_done: bool = False,
) -> discord.Embed:
    if all_done:
        return discord.Embed(
            title=f"✅ {_sp('done_title', lang)}",
            description=_sp("done_desc", lang),
            color=0x57F287,
        ).set_footer(text=FOOTER_TEXT)

    if done:
        desc = (
            _sp("all_complete_desc", lang)
            if remaining <= 0
            else _sp("complete_desc", lang, added=added_session, remaining=remaining)
        )
        return discord.Embed(
            title=f"✅ {_sp('complete_title', lang)}",
            description=desc,
            color=0x57F287,
        ).set_footer(text=FOOTER_TEXT)

    display = format_emoji_item_name(emoji.name or "?")
    embed = discord.Embed(
        title=f"💰 {_sp('title', lang)}",
        description=_sp("desc", lang),
        color=0x5865F2,
    )
    embed.add_field(name="", value=f"{emoji}  **{display}**", inline=False)
    embed.add_field(name="", value=_sp("emoji_raw", lang, name=emoji.name), inline=False)
    embed.add_field(name="", value=_sp("item_name", lang, name=display), inline=False)
    embed.add_field(
        name="",
        value=_sp("progress", lang, remaining=remaining, step=step, total=total),
        inline=False,
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed


class SetPriceModal(Modal):
    def __init__(self, hub: "SetPricesView", emoji: discord.Emoji):
        lang = hub.lang
        super().__init__(title=_sp("modal_title", lang))
        self.hub = hub
        self.emoji = emoji
        self.value_in = TextInput(
            label=_sp("modal_value_label", lang),
            placeholder=_sp("modal_value_ph", lang),
            max_length=12,
        )
        self.add_item(self.value_in)

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        try:
            value = int(self.value_in.value.replace(",", "").replace(".", "").strip())
            if value < 1:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                t("cases.invalid_value", user_id=uid), ephemeral=True
            )

        iid, name = add_app_emoji_item(self.emoji, value)
        self.hub.added_session += 1
        self.hub.index += 1

        embed, view = self.hub.render()
        toast = f"✅ {_sp('saved_toast', self.hub.lang, name=name, value=format_balance(value, 'real'))}"
        if view is None:
            await interaction.response.edit_message(content=toast, embed=embed, view=None)
        else:
            await interaction.response.edit_message(content=toast, embed=embed, view=view)


class SetPricesView(View):
    def __init__(
        self,
        user_id: int,
        pending: list,
        *,
        lang: str = "en",
        index: int = 0,
        added_session: int = 0,
    ):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.pending = pending
        self.lang = lang
        self.index = index
        self.added_session = added_session
        self._rebuild()

    @property
    def remaining(self) -> int:
        return max(0, len(self.pending) - self.index)

    @property
    def current(self) -> discord.Emoji | None:
        if self.index >= len(self.pending):
            return None
        return self.pending[self.index]

    def render(self) -> tuple[discord.Embed, View | None]:
        if self.current is None:
            if self.added_session == 0 and self.index == 0:
                return build_setprices_embed(
                    emoji=None, lang=self.lang, step=0, total=0, remaining=0,
                    added_session=0, all_done=True,
                ), None
            return build_setprices_embed(
                emoji=None,
                lang=self.lang,
                step=self.index,
                total=len(self.pending),
                remaining=self.remaining,
                added_session=self.added_session,
                done=True,
            ), None
        return build_setprices_embed(
            emoji=self.current,
            lang=self.lang,
            step=self.index + 1,
            total=len(self.pending),
            remaining=self.remaining,
            added_session=self.added_session,
        ), self

    def _rebuild(self) -> None:
        self.clear_items()
        if self.current is None:
            return

        set_btn = Button(
            label=_sp("btn_set_price", self.lang),
            style=discord.ButtonStyle.success,
            emoji="💰",
        )
        set_btn.callback = self._on_set_price
        self.add_item(set_btn)

        skip_btn = Button(
            label=_sp("btn_skip", self.lang),
            style=discord.ButtonStyle.secondary,
        )
        skip_btn.callback = self._on_skip
        self.add_item(skip_btn)

        close_btn = Button(
            label=_sp("btn_close", self.lang),
            style=discord.ButtonStyle.danger,
        )
        close_btn.callback = self._on_close
        self.add_item(close_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    async def _on_set_price(self, interaction: discord.Interaction):
        if not self.current:
            return await interaction.response.defer()
        await interaction.response.send_modal(SetPriceModal(self, self.current))

    async def _on_skip(self, interaction: discord.Interaction):
        self.index += 1
        embed, view = self.render()
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    async def _on_close(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content=None, embed=None, view=None)


async def start_set_prices_flow(interaction: discord.Interaction) -> None:
    lang = get_user_lang(interaction.user.id)
    pending, err = await fetch_pending_app_emojis(interaction.client)

    if err == "fetch_failed":
        return await interaction.response.send_message(
            embed=create_error_embed(_sp("fetch_failed", lang)), ephemeral=True
        )

    if not pending:
        return await interaction.response.send_message(
            embed=build_setprices_embed(
                emoji=None, lang=lang, step=0, total=0, remaining=0,
                added_session=0, all_done=True,
            ),
            ephemeral=True,
        )

    view = SetPricesView(interaction.user.id, pending, lang=lang)
    embed, _ = view.render()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

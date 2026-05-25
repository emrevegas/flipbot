"""
Kasa Sistemi v2
─────────────────
Admin:
  /cases           → Official kasa yönetim paneli
  /items           → Global item kütüphanesi

Kullanıcı:
  /community_cases → Kendi kasalarını oluştur / keşfet / aç

Veri yapısı (server/cases.json):
{
  "items": {               ← Global item kütüphanesi (emoji, isim, değer)
    "<item_id>": { name, emoji, value }
  },
  "cases": {               ← Official kasalar
    "<case_id>": { name, emoji, house_edge, price, item_ids: [...] }
  },
  "community_cases": {     ← Kullanıcı kasaları
    "<case_id>": { name, emoji, owner_id, price, item_ids, created_at }
  }
}
"""

import discord
import uuid
import time
import os
import random
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Select, Button, Modal, TextInput
from typing import Optional

from modules.database import get_data, set_data, replace_data, check_permission, get_user_data, set_user_data
from modules import image_gen
from modules.player import Player
from modules.translator import t
from modules.utils import format_balance, create_error_embed, create_success_embed, get_user_lang
from modules.constants import FOOTER_TEXT

MAX_CASE_OPEN_COUNT = 4

# ════════════════════════════════════════════════════════
# CONSTANTS & COLORS
# ════════════════════════════════════════════════════════

PLATFORM_FEE_PCT      = 2.5
OFFICIAL_HE_MIN       = 1.0
OFFICIAL_HE_MAX       = 50.0
COMMUNITY_HOUSE_EDGE  = 10.0   # Fixed house edge for all community cases
MAX_ITEMS_PER_CASE    = 10
MIN_ITEMS_TO_PUBLISH  = 2

CLR_BRAND  = 0x5865F2
CLR_GOLD   = 0xF0B232
CLR_RED    = 0xED4245
CLR_GREEN  = 0x57F287
CLR_GREY   = 0x2B2D31
CLR_TEAL   = 0x1ABC9C


def _adm(key: str, lang: str, **kwargs) -> str:
    return t(f"cases.admin.{key}", lang=lang, **kwargs)


RARITY_COLORS = {
    "common":    0xAAAAAA,
    "uncommon":  0x4CAF50,
    "rare":      0x2196F3,
    "epic":      0x9C27B0,
    "legendary": 0xF0B232,
}

RARITY_STARS = {
    "common": "⬜", "uncommon": "🟩", "rare": "🟦", "epic": "🟪", "legendary": "🟨"
}

# ════════════════════════════════════════════════════════
# DATA ACCESS
# ════════════════════════════════════════════════════════

def _get_db() -> dict:
    data = get_data("server/cases") or {}
    data.setdefault("items", {})
    data.setdefault("cases", {})
    data.setdefault("community_cases", {})
    data.setdefault("settings", {})
    return data

def _get_publish_fee(db: dict) -> int:
    return int(db.get("settings", {}).get("publish_fee", 0))

def _set_publish_fee(db: dict, fee: int):
    db.setdefault("settings", {})["publish_fee"] = fee

def _save_db(data: dict):
    replace_data("server/cases", data)

def _get_items(db: dict) -> dict:
    return db.get("items", {})

def _get_item(db: dict, item_id: str) -> Optional[dict]:
    return db.get("items", {}).get(item_id)

def _get_case(db: dict, case_id: str) -> Optional[dict]:
    return db.get("cases", {}).get(case_id)

def _get_community_case(db: dict, case_id: str) -> Optional[dict]:
    return db.get("community_cases", {}).get(case_id)

# ════════════════════════════════════════════════════════
# BUSINESS LOGIC
# ════════════════════════════════════════════════════════

def _case_items_resolved(db: dict, item_ids: list) -> list:
    lib = _get_items(db)
    return [lib[iid] | {"id": iid} for iid in item_ids if iid in lib]

def _get_item_weights(items: list, chances: dict) -> list:
    """Return weights list. Explicitly set items use custom weight; unset items use 1/value fallback."""
    if chances:
        return [
            float(chances[i.get("id", "")]) if i.get("id", "") in chances
            else 1.0 / max(1, int(i.get("value", 1)))
            for i in items
        ]
    return [1.0 / max(1, int(i.get("value", 1))) for i in items]

def _ev(items: list, chances: dict = None) -> float:
    if not items:
        return 0.0
    weights = _get_item_weights(items, chances or {})
    total_w = sum(weights)
    if total_w == 0:
        return 0.0
    return sum(i["value"] * (w / total_w) for i, w in zip(items, weights))

def _calculate_price(items: list, house_edge_pct: float, chances: dict = None) -> int:
    ev = _ev(items, chances)
    if ev == 0:
        return 0
    edge = max(0.0, min(0.99, house_edge_pct / 100.0))
    return max(1, round(ev / (1.0 - edge)))

def _recalculate_case_price(db: dict, case_id: str, bucket: str = "cases") -> None:
    case = db.get(bucket, {}).get(case_id)
    if not case:
        return
    items = _case_items_resolved(db, case.get("item_ids", []))
    he = float(case.get("house_edge", 5.0))
    chances = case.get("item_chances", {})
    case["price"] = _calculate_price(items, he, chances)

def _open_case_item(items: list, chances: dict = None) -> dict:
    if not items:
        return {}
    weights = _get_item_weights(items, chances or {})
    total_w = sum(weights)
    r = random.random() * total_w
    cumulative = 0.0
    for item, w in zip(items, weights):
        cumulative += w
        if r <= cumulative:
            return item
    return items[-1]

def _item_probability(item: dict, items: list, chances: dict = None) -> float:
    weights = _get_item_weights(items, chances or {})
    total_w = sum(weights)
    if total_w == 0:
        return 0.0
    iid = item.get("id", "")
    if chances and iid in chances:
        w = float(chances[iid])
    else:
        w = 1.0 / max(1, int(item.get("value", 1)))
    return (w / total_w) * 100.0

def _rarity_label(value: int, items: list) -> str:
    if not items:
        return "common"
    top = max(i["value"] for i in items)
    pct = value / max(1, top)
    if pct >= 0.85: return "legendary"
    if pct >= 0.65: return "epic"
    if pct >= 0.40: return "rare"
    if pct >= 0.20: return "uncommon"
    return "common"

# ════════════════════════════════════════════════════════
# EMBED BUILDERS
# ════════════════════════════════════════════════════════

def _divider() -> str:
    return "━" * 32

def _item_library_embed(db: dict, page: int = 0, lang: str = "en") -> discord.Embed:
    items = _get_items(db)
    all_items = list(items.items())
    total = len(all_items)
    per_page = 9
    pages = max(1, -(-total // per_page))
    page = max(0, min(page, pages - 1))
    chunk = all_items[page * per_page:(page + 1) * per_page]

    embed = discord.Embed(
        title=_adm("library_title", lang),
        description=_adm("library_desc", lang, total=total, page=page + 1, pages=pages) + f"\n{_divider()}",
        color=CLR_BRAND,
    )
    if chunk:
        lines = [
            f"{item.get('emoji','❓')}  **{item.get('name','?')}**"
            f"  ╴  {format_balance(item.get('value',0), 'real')}"
            f"  ╴  `{iid}`"
            for iid, item in chunk
        ]
        embed.add_field(name="", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(
            name=_adm("library_empty_title", lang),
            value=_adm("library_empty_desc", lang),
            inline=False,
        )
    embed.set_footer(text=f"{FOOTER_TEXT}  •  {_adm('library_footer', lang)}")
    return embed

def _official_cases_embed(db: dict, lang: str = "en") -> discord.Embed:
    cases = db.get("cases", {})
    embed = discord.Embed(
        title=_adm("panel_title", lang),
        description=_adm("panel_desc", lang, count=len(cases)) + f"\n{_divider()}",
        color=CLR_GREY,
    )
    for cid, case in list(cases.items())[:9]:
        items = _case_items_resolved(db, case.get("item_ids", []))
        embed.add_field(
            name=f"{case.get('emoji','📦')}  {case.get('name','?')}",
            value=(
                _adm("case_price", lang, price=format_balance(case.get('price',0), 'real')) + "\n"
                + _adm("case_items", lang, count=len(items), max=MAX_ITEMS_PER_CASE) + "\n"
                + _adm("case_house_edge", lang, edge=f"{case.get('house_edge',5.0):.1f}")
            ),
            inline=True,
        )
    if not cases:
        embed.add_field(
            name=_adm("panel_empty_title", lang),
            value=_adm("panel_empty_desc", lang),
            inline=False,
        )
    embed.set_footer(text=f"{FOOTER_TEXT}  •  {_adm('panel_footer', lang)}")
    return embed

def _case_detail_embed(db: dict, case_id: str, is_community: bool = False, lang: str = "en") -> discord.Embed:
    case = (_get_community_case if is_community else _get_case)(db, case_id) or {}
    items = _case_items_resolved(db, case.get("item_ids", []))
    color = CLR_TEAL if is_community else CLR_BRAND

    embed = discord.Embed(
        title=f"{case.get('emoji','📦')}  {case.get('name','?')}",
        color=color,
    )

    meta = [
        f"💰 **{format_balance(case.get('price',0), 'real')}**",
        _adm("case_meta_items", lang, count=len(items), max=MAX_ITEMS_PER_CASE),
    ]
    if is_community:
        meta.append(t("cases.platform_fee_field", lang=lang, pct=PLATFORM_FEE_PCT))
        meta.append(f"{t('cases.owner_field', lang=lang)} <@{case.get('owner_id','?')}>")
        published = case.get("published", False)
        meta.append(t("cases.published_badge", lang=lang) if published else t("cases.draft_badge", lang=lang))
    desc_text = case.get("description", "").strip()
    if desc_text:
        meta.append(f"📝 {desc_text}")
    embed.add_field(name="", value="\n".join(meta), inline=False)
    embed.add_field(name=_divider(), value="", inline=False)

    if items:
        sorted_items = sorted(items, key=lambda i: i.get("value", 0), reverse=True)
        chances = case.get("item_chances", {})
        weights_list = _get_item_weights(items, chances)
        total_w = sum(weights_list)
        weight_map = {i.get("id", ""): w for i, w in zip(items, weights_list)}
        lines = []
        for item in sorted_items[:20]:
            iid = item.get("id", "")
            w = weight_map.get(iid, 0.0)
            prob = (w / total_w * 100) if total_w else 0.0
            rarity = _rarity_label(item.get("value", 0), items)
            star = RARITY_STARS.get(rarity, "⬜")
            lines.append(
                f"{star} {item.get('emoji','❓')} **{item.get('name','?')}**"
                f" — {format_balance(item.get('value',0), 'real')}"
                f"  *({prob:.2f}%)*"
            )
        # Truncate to Discord's 1024-char field limit
        value = "\n".join(lines)
        if len(value) > 1024:
            value = value[:1021] + "…"
        embed.add_field(name=t("cases.items_field", lang=lang), value=value, inline=False)
    else:
        embed.add_field(name=t("cases.items_field", lang=lang), value=t("cases.no_items_added", lang=lang), inline=False)

    embed.set_footer(text=f"{FOOTER_TEXT}  •  {_adm('case_detail_footer', lang)}")
    return embed

def _community_browse_embed(db: dict, page: int = 0, sort: str = "newest", lang: str = "en") -> discord.Embed:
    cc = db.get("community_cases", {})
    all_cases = [(cid, c) for cid, c in cc.items() if c.get("published", False)]
    if sort == "cheapest":   all_cases.sort(key=lambda x: x[1].get("price", 0))
    elif sort == "priciest": all_cases.sort(key=lambda x: x[1].get("price", 0), reverse=True)
    else:                    all_cases.sort(key=lambda x: x[1].get("created_at", 0), reverse=True)

    total = len(all_cases)
    per_page = 6
    pages = max(1, -(-total // per_page))
    page = max(0, min(page, pages - 1))
    chunk = all_cases[page * per_page:(page + 1) * per_page]

    sort_labels = {
        "newest":   t("cases.browse_sort_newest", lang=lang),
        "cheapest": t("cases.browse_sort_cheapest", lang=lang),
        "priciest": t("cases.browse_sort_priciest", lang=lang),
    }
    embed = discord.Embed(
        title=t("cases.browse_title", lang=lang),
        description=(
            t("cases.browse_desc", lang=lang, total=total, sort=sort_labels.get(sort, sort), page=page + 1, pages=pages) + "\n"
            + _divider()
        ),
        color=CLR_TEAL,
    )
    for cid, case in chunk:
        items = _case_items_resolved(db, case.get("item_ids", []))
        desc_text = case.get("description", "").strip()
        field_val = (
            f"💰 {format_balance(case.get('price',0), 'real')}"
            f"  •  📋 {len(items)} item\n"
            f"👤 <@{case.get('owner_id','?')}>"
        )
        if desc_text:
            field_val += f"\n📝 *{desc_text[:80]}*"
        embed.add_field(
            name=f"{case.get('emoji','📦')}  {case.get('name','?')}",
            value=field_val,
            inline=True,
        )
    if not chunk:
        embed.add_field(name="", value=t("cases.browse_empty", lang=lang), inline=False)
    embed.set_footer(text=f"{FOOTER_TEXT}  •  Community Cases")
    return embed

def _my_community_cases_embed(db: dict, user_id: int, lang: str = "en") -> discord.Embed:
    cc = db.get("community_cases", {})
    my = [(cid, c) for cid, c in cc.items() if c.get("owner_id") == user_id]
    embed = discord.Embed(
        title=t("cases.my_cases_title", lang=lang),
        description=(
            t("cases.my_cases_desc", lang=lang, count=len(my), pct=PLATFORM_FEE_PCT) + "\n"
            + _divider()
        ),
        color=CLR_TEAL,
    )
    for cid, case in my[:9]:
        items = _case_items_resolved(db, case.get("item_ids", []))
        pub_badge = t("cases.published_badge", lang=lang) if case.get("published", False) else t("cases.draft_badge", lang=lang)
        embed.add_field(
            name=f"{case.get('emoji','📦')}  {case.get('name','?')}",
            value=(
                f"{pub_badge}"
                f"  •  💰 {format_balance(case.get('price',0), 'real')}"
                f"  •  📋 {len(items)} item\n"
                f"`{cid}`"
            ),
            inline=True,
        )
    if not my:
        embed.add_field(
            name=t("cases.my_cases_empty_title", lang=lang),
            value=t("cases.my_cases_empty", lang=lang),
            inline=False,
        )
    embed.set_footer(text=f"{FOOTER_TEXT}  •  Community Cases")
    return embed

def _open_result_embed(item: dict, case: dict, paid: int, profit: int, is_community: bool, lang: str = "en") -> discord.Embed:
    rarity = _rarity_label(item.get("value", 0), [item])
    color  = RARITY_COLORS.get(rarity, CLR_GOLD)
    net_sign = "+" if profit >= 0 else ""
    rarity_key = f"rarity_{rarity}"

    embed = discord.Embed(
        title=f"{item.get('emoji','❓')}  {item.get('name','?')}",
        description=(
            f"**{case.get('emoji','📦')} {case.get('name','Case')}** "
            + t("cases.result_opened", lang=lang, emoji=case.get('emoji','📦'), name=case.get('name','Case')).split(" ", 2)[-1] + "\n"
            + _divider()
        ),
        color=color,
    )
    embed.add_field(
        name=t("cases.result_field", lang=lang),
        value=(
            f"{t('cases.result_paid', lang=lang)} {format_balance(paid, 'real')}\n"
            f"{t('cases.result_won', lang=lang)} {format_balance(item.get('value',0), 'real')}\n"
            f"{'📈' if profit >= 0 else '📉'} {t('cases.result_net', lang=lang)} {net_sign}{format_balance(abs(profit), 'real')}"
        ),
        inline=False,
    )
    rarity_bar = {"common":"░░░░░","uncommon":"▒░░░░","rare":"▒▒░░░","epic":"▒▒▒░░","legendary":"▒▒▒▒▒"}
    rarity_label_str = t(f"cases.{rarity_key}", lang=lang) if t(f"cases.{rarity_key}", lang=lang) != f"cases.{rarity_key}" else rarity.upper()
    embed.add_field(
        name=t("cases.result_rarity", lang=lang),
        value=f"{RARITY_STARS.get(rarity,'⬜')} **{rarity_label_str}**  {rarity_bar.get(rarity,'')}",
        inline=False,
    )
    if is_community:
        embed.set_footer(text=t("cases.platform_fee_footer", lang=lang, pct=PLATFORM_FEE_PCT) + f"  •  {FOOTER_TEXT}")
    else:
        embed.set_footer(text=FOOTER_TEXT)
    return embed

# ════════════════════════════════════════════════════════
# EMOJI PICKER (paginated guild + application)
# ════════════════════════════════════════════════════════


class _AddItemEmojiTextModal(Modal):
    """Fallback: guild emojisi yokken metin olarak emoji al (item ekle)."""

    def __init__(self, admin_id: int, name: str, value: int, page: int, lang: str):
        super().__init__(title=_adm("emoji_modal_title", lang))
        self.admin_id = admin_id
        self.name     = name
        self.value    = value
        self.page     = page
        self.lang     = lang
        self.emoji_in = TextInput(
            label=_adm("emoji_label", lang),
            placeholder=_adm("emoji_ph", lang),
            default="❓",
            max_length=80,
        )
        self.add_item(self.emoji_in)

    async def on_submit(self, interaction: discord.Interaction):
        emoji_str = self.emoji_in.value.strip() or "❓"
        db  = _get_db()
        iid = str(uuid.uuid4())[:8]
        db["items"][iid] = {"name": self.name, "emoji": emoji_str, "value": self.value}
        _save_db(db)
        await interaction.response.edit_message(
            embed=_item_library_embed(db, self.page, lang=self.lang),
            view=ItemLibraryView(self.admin_id, self.page, lang=self.lang),
        )


class _EditItemEmojiTextModal(Modal):
    """Fallback: guild emojisi yokken metin olarak emoji al (item düzenle)."""

    def __init__(self, admin_id: int, item_id: str, name: str, value: int, page: int, lang: str):
        super().__init__(title=_adm("emoji_modal_title", lang))
        self.admin_id = admin_id
        self.item_id  = item_id
        self.name     = name
        self.value    = value
        self.page     = page
        self.lang     = lang
        self.emoji_in = TextInput(
            label=_adm("emoji_label", lang),
            placeholder=_adm("emoji_ph", lang),
            max_length=80,
        )
        self.add_item(self.emoji_in)

    async def on_submit(self, interaction: discord.Interaction):
        emoji_str = self.emoji_in.value.strip() or "❓"
        db = _get_db()
        if self.item_id not in db["items"]:
            return await interaction.response.send_message(
                t("cases.item_not_found", user_id=str(interaction.user.id)), ephemeral=True
            )
        db["items"][self.item_id] = {"name": self.name, "emoji": emoji_str, "value": self.value}
        for cid in db["cases"]:
            if self.item_id in db["cases"][cid].get("item_ids", []):
                _recalculate_case_price(db, cid, "cases")
        for cid in db["community_cases"]:
            if self.item_id in db["community_cases"][cid].get("item_ids", []):
                _recalculate_case_price(db, cid, "community_cases")
        _save_db(db)
        await interaction.response.edit_message(
            embed=_item_library_embed(db, self.page, lang=self.lang),
            view=ItemLibraryView(self.admin_id, self.page, lang=self.lang),
        )


# ════════════════════════════════════════════════════════
# ITEM LIBRARY — ADMIN
# ════════════════════════════════════════════════════════

class ItemLibraryView(View):
    def __init__(self, admin_id: int, page: int = 0, lang: str | None = None):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.page = page
        self.lang = lang or get_user_lang(admin_id)
        self._build()

    def _build(self):
        self.clear_items()
        db = _get_db()
        items = _get_items(db)
        total = len(items)
        per_page = 10
        pages = max(1, -(-total // per_page))
        lang = self.lang

        add_btn = Button(label=_adm("btn_add_item", lang), style=discord.ButtonStyle.success, row=0)
        add_btn.callback = self._on_add
        self.add_item(add_btn)

        if items:
            edit_btn = Button(label=_adm("btn_edit", lang), style=discord.ButtonStyle.primary, row=0)
            edit_btn.callback = self._on_edit
            self.add_item(edit_btn)

            del_btn = Button(label=_adm("btn_delete", lang), style=discord.ButtonStyle.danger, row=0)
            del_btn.callback = self._on_delete
            self.add_item(del_btn)

        if self.page > 0:
            prev = Button(label=_adm("btn_prev", lang), style=discord.ButtonStyle.secondary, row=1)
            prev.callback = self._on_prev
            self.add_item(prev)

        if self.page < pages - 1:
            nxt = Button(label=_adm("btn_next", lang), style=discord.ButtonStyle.secondary, row=1)
            nxt.callback = self._on_next
            self.add_item(nxt)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                t("cases.not_yours", user_id=str(interaction.user.id)), ephemeral=True
            )
            return False
        return True

    async def _on_add(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddItemModal(self.admin_id, self.page, lang=self.lang))

    async def _on_edit(self, interaction: discord.Interaction):
        db = _get_db()
        items = list(_get_items(db).items())
        if not items:
            return await interaction.response.send_message(_adm("no_items_short", self.lang), ephemeral=True)
        view = ItemSelectView(self.admin_id, items, "edit", self.page, lang=self.lang)
        await interaction.response.edit_message(embed=_item_library_embed(db, self.page, lang=self.lang), view=view)

    async def _on_delete(self, interaction: discord.Interaction):
        db = _get_db()
        items = list(_get_items(db).items())
        if not items:
            return await interaction.response.send_message(_adm("no_items_short", self.lang), ephemeral=True)
        view = ItemSelectView(self.admin_id, items, "delete", self.page, lang=self.lang)
        await interaction.response.edit_message(embed=_item_library_embed(db, self.page, lang=self.lang), view=view)

    async def _on_prev(self, interaction: discord.Interaction):
        self.page -= 1
        self._build()
        db = _get_db()
        await interaction.response.edit_message(embed=_item_library_embed(db, self.page, lang=self.lang), view=self)

    async def _on_next(self, interaction: discord.Interaction):
        self.page += 1
        self._build()
        db = _get_db()
        await interaction.response.edit_message(embed=_item_library_embed(db, self.page, lang=self.lang), view=self)


class ItemSelectView(View):
    def __init__(self, admin_id: int, items: list, action: str, page: int = 0, lang: str | None = None):
        super().__init__(timeout=120)
        self.admin_id = admin_id
        self.action   = action
        self.page     = page
        self.lang     = lang or get_user_lang(admin_id)

        per_page = 10
        chunk = items[page * per_page:(page + 1) * per_page]
        options = [
            discord.SelectOption(
                label=item.get("name", "?")[:50],
                value=iid,
                emoji=item.get("emoji", "❓"),
                description=f"{format_balance(item.get('value',0),'real')}  •  ID: {iid}"[:100],
            )
            for iid, item in chunk
        ]
        sel = Select(placeholder=_adm("select_item", self.lang), options=options, row=0)
        sel.callback = self._on_select
        self.add_item(sel)

        back = Button(label=_adm("btn_back", self.lang), style=discord.ButtonStyle.secondary, row=1)
        back.callback = self._on_back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                t("cases.not_yours", user_id=str(interaction.user.id)), ephemeral=True
            )
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        iid  = interaction.data["values"][0]
        db   = _get_db()
        item = _get_item(db, iid)
        if not item:
            return await interaction.response.send_message(
                t("cases.item_not_found", user_id=str(interaction.user.id)), ephemeral=True
            )
        if self.action == "edit":
            await interaction.response.send_modal(EditItemModal(self.admin_id, iid, item, self.page, lang=self.lang))
        else:
            embed = discord.Embed(
                title=_adm("delete_item_title", self.lang),
                description=_adm(
                    "delete_item_desc", self.lang,
                    emoji=item.get('emoji', '❓'),
                    name=item.get('name', '?'),
                ),
                color=CLR_RED,
            )
            embed.set_footer(text=FOOTER_TEXT)
            await interaction.response.edit_message(
                embed=embed,
                view=ConfirmDeleteItemView(self.admin_id, iid, self.page, lang=self.lang),
            )

    async def _on_back(self, interaction: discord.Interaction):
        db = _get_db()
        await interaction.response.edit_message(
            embed=_item_library_embed(db, self.page, lang=self.lang),
            view=ItemLibraryView(self.admin_id, self.page, lang=self.lang),
        )


class ConfirmDeleteItemView(View):
    def __init__(self, admin_id: int, item_id: str, page: int = 0, lang: str | None = None):
        super().__init__(timeout=30)
        self.admin_id = admin_id
        self.item_id  = item_id
        self.page     = page
        self.lang     = lang or get_user_lang(admin_id)

        yes = Button(label=_adm("confirm_yes_delete", self.lang), style=discord.ButtonStyle.danger)
        yes.callback = self._on_yes
        self.add_item(yes)

        no = Button(label=_adm("btn_cancel", self.lang), style=discord.ButtonStyle.secondary)
        no.callback = self._on_no
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                t("cases.not_yours", user_id=str(interaction.user.id)), ephemeral=True
            )
            return False
        return True

    async def _on_yes(self, interaction: discord.Interaction):
        db = _get_db()
        db["items"].pop(self.item_id, None)
        for case in db["cases"].values():
            case["item_ids"] = [i for i in case.get("item_ids", []) if i != self.item_id]
        for case in db["community_cases"].values():
            case["item_ids"] = [i for i in case.get("item_ids", []) if i != self.item_id]
        _save_db(db)
        await interaction.response.edit_message(
            embed=_item_library_embed(db, self.page, lang=self.lang),
            view=ItemLibraryView(self.admin_id, self.page, lang=self.lang),
        )

    async def _on_no(self, interaction: discord.Interaction):
        db = _get_db()
        await interaction.response.edit_message(
            embed=_item_library_embed(db, self.page, lang=self.lang),
            view=ItemLibraryView(self.admin_id, self.page, lang=self.lang),
        )


class AddItemModal(Modal):
    def __init__(self, admin_id: int, page: int = 0, lang: str = "en"):
        super().__init__(title=_adm("add_item_modal_title", lang))
        self.admin_id = admin_id
        self.page     = page
        self.lang     = lang
        self.name_in  = TextInput(label=_adm("item_name_label", lang), placeholder=_adm("item_name_ph", lang), max_length=50)
        self.value_in = TextInput(label=_adm("item_value_label", lang), placeholder=_adm("item_value_ph", lang), max_length=12)
        self.add_item(self.name_in)
        self.add_item(self.value_in)

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        try:
            value = int(self.value_in.value.replace(",", "").replace(".", "").strip())
            if value < 1: raise ValueError
        except ValueError:
            return await interaction.response.send_message(t("cases.invalid_value", user_id=uid), ephemeral=True)

        name     = self.name_in.value.strip()
        admin_id = self.admin_id
        page     = self.page
        lang     = self.lang

        async def save_fn(emoji_str: str):
            db  = _get_db()
            iid = str(uuid.uuid4())[:8]
            db["items"][iid] = {"name": name, "emoji": emoji_str, "value": value}
            _save_db(db)
            return _item_library_embed(db, page, lang=lang), ItemLibraryView(admin_id, page, lang=lang)

        from modules.case_emoji_picker import open_emoji_picker_after_item_modal

        ok = await open_emoji_picker_after_item_modal(
            interaction,
            label_name=name,
            save_fn=save_fn,
            user_id=interaction.user.id,
            lang=lang,
        )
        if not ok:
            await interaction.response.send_modal(
                _AddItemEmojiTextModal(admin_id, name, value, page, lang)
            )


class EditItemModal(Modal):
    def __init__(self, admin_id: int, item_id: str, item: dict, page: int = 0, lang: str = "en"):
        super().__init__(title=_adm("edit_item_modal_title", lang))
        self.admin_id = admin_id
        self.item_id  = item_id
        self.page     = page
        self.lang     = lang
        self.name_in  = TextInput(label=_adm("item_name_label", lang), max_length=50)
        self.value_in = TextInput(label=_adm("item_value_label", lang), max_length=12)
        self.name_in.default  = item.get("name", "")
        self.value_in.default = str(item.get("value", 1))
        self.add_item(self.name_in)
        self.add_item(self.value_in)

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        try:
            value = int(self.value_in.value.replace(",", "").replace(".", "").strip())
            if value < 1: raise ValueError
        except ValueError:
            return await interaction.response.send_message(t("cases.invalid_value", user_id=uid), ephemeral=True)

        name     = self.name_in.value.strip()
        admin_id = self.admin_id
        item_id  = self.item_id
        page     = self.page
        lang     = self.lang

        async def save_fn(emoji_str: str):
            db = _get_db()
            if item_id not in db["items"]:
                return create_error_embed(t("cases.item_not_found", user_id=uid)), None
            db["items"][item_id] = {"name": name, "emoji": emoji_str, "value": value}
            for cid in db["cases"]:
                if item_id in db["cases"][cid].get("item_ids", []):
                    _recalculate_case_price(db, cid, "cases")
            for cid in db["community_cases"]:
                if item_id in db["community_cases"][cid].get("item_ids", []):
                    _recalculate_case_price(db, cid, "community_cases")
            _save_db(db)
            return _item_library_embed(db, page, lang=lang), ItemLibraryView(admin_id, page, lang=lang)

        from modules.case_emoji_picker import open_emoji_picker_after_item_modal

        ok = await open_emoji_picker_after_item_modal(
            interaction,
            label_name=name,
            save_fn=save_fn,
            user_id=interaction.user.id,
            lang=lang,
        )
        if not ok:
            await interaction.response.send_modal(
                _EditItemEmojiTextModal(admin_id, item_id, name, value, page, lang)
            )

# ════════════════════════════════════════════════════════
# OFFICIAL CASE MANAGEMENT — ADMIN
# ════════════════════════════════════════════════════════

class OfficialCasesView(View):
    def __init__(self, admin_id: int, lang: str | None = None):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.lang = lang or get_user_lang(admin_id)
        self._build()

    def _build(self):
        self.clear_items()
        db    = _get_db()
        cases = db.get("cases", {})
        lang  = self.lang

        if cases:
            options = [
                discord.SelectOption(
                    label=c.get("name", "?")[:25],
                    value=cid,
                    emoji=c.get("emoji", "📦"),
                    description=f"{format_balance(c.get('price',0),'real')}  •  {len(c.get('item_ids',[]))} item"[:100],
                )
                for cid, c in list(cases.items())[:25]
            ]
            sel = Select(placeholder=_adm("select_case", lang), options=options, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        add = Button(label=_adm("btn_new_case", lang), style=discord.ButtonStyle.success, row=1)
        add.callback = self._on_add
        self.add_item(add)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                t("cases.not_yours", user_id=str(interaction.user.id)), ephemeral=True
            )
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        cid = interaction.data["values"][0]
        db  = _get_db()
        if not _get_case(db, cid):
            return await interaction.response.send_message(_adm("no_case_found", self.lang), ephemeral=True)
        await interaction.response.edit_message(
            embed=_case_detail_embed(db, cid, lang=self.lang),
            view=OfficialCaseDetailView(self.admin_id, cid, lang=self.lang),
        )

    async def _on_add(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddCaseModal(self.admin_id, lang=self.lang))


class OfficialCaseDetailView(View):
    def __init__(self, admin_id: int, case_id: str, lang: str | None = None):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.case_id  = case_id
        self.lang = lang or get_user_lang(admin_id)
        self._build()

    def _build(self):
        self.clear_items()
        db    = _get_db()
        case  = _get_case(db, self.case_id) or {}
        cur   = set(case.get("item_ids", []))
        avail = [(iid, i) for iid, i in _get_items(db).items() if iid not in cur]
        lang  = self.lang

        if avail and len(cur) < MAX_ITEMS_PER_CASE:
            add_i = Button(label=_adm("btn_add_to_case", lang), style=discord.ButtonStyle.success, row=0)
            add_i.callback = self._on_add_item
            self.add_item(add_i)

        if cur:
            rem_i = Button(label=_adm("btn_remove_from_case", lang), style=discord.ButtonStyle.danger, row=0)
            rem_i.callback = self._on_remove_item
            self.add_item(rem_i)

            chance_btn = Button(label=_adm("btn_set_chances", lang), style=discord.ButtonStyle.primary, row=0)
            chance_btn.callback = self._on_set_chances
            self.add_item(chance_btn)

        edit = Button(label=_adm("btn_edit_case", lang), style=discord.ButtonStyle.secondary, row=1)
        edit.callback = self._on_edit
        self.add_item(edit)

        if cur:
            emoji_btn = Button(label="Manage Emoji", style=discord.ButtonStyle.primary, row=1)
            emoji_btn.callback = self._on_manage_emoji
            self.add_item(emoji_btn)

        del_btn = Button(label=_adm("btn_delete_case", lang), style=discord.ButtonStyle.danger, row=1)
        del_btn.callback = self._on_delete
        self.add_item(del_btn)

        back = Button(label=_adm("btn_back", lang), style=discord.ButtonStyle.secondary, row=2)
        back.callback = self._on_back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                t("cases.not_yours", user_id=str(interaction.user.id)), ephemeral=True
            )
            return False
        return True

    async def _on_add_item(self, interaction: discord.Interaction):
        db   = _get_db()
        case = _get_case(db, self.case_id) or {}
        cur  = set(case.get("item_ids", []))
        avail = [(iid, i) for iid, i in _get_items(db).items() if iid not in cur]
        if not avail:
            return await interaction.response.send_message(_adm("no_more_items_avail", self.lang), ephemeral=True)
        if len(cur) >= MAX_ITEMS_PER_CASE:
            return await interaction.response.send_message(
                t("cases.max_items", lang=self.lang, max=MAX_ITEMS_PER_CASE), ephemeral=True
            )
        view = AddItemToCaseView(self.admin_id, self.case_id, avail, is_community=False, lang=self.lang)
        embed = discord.Embed(
            title=_adm("add_to_case_title", self.lang),
            description=_adm("add_to_case_desc", self.lang),
            color=CLR_BRAND,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_remove_item(self, interaction: discord.Interaction):
        db    = _get_db()
        case  = _get_case(db, self.case_id) or {}
        items = _case_items_resolved(db, case.get("item_ids", []))
        if not items:
            return await interaction.response.send_message(_adm("no_items_in_case", self.lang), ephemeral=True)
        view = RemoveItemFromCaseView(self.admin_id, self.case_id, items, is_community=False, lang=self.lang)
        embed = discord.Embed(
            title=_adm("remove_from_case_title", self.lang),
            description=_adm("remove_from_case_desc", self.lang),
            color=CLR_RED,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_set_chances(self, interaction: discord.Interaction):
        db    = _get_db()
        case  = _get_case(db, self.case_id) or {}
        items = _case_items_resolved(db, case.get("item_ids", []))
        if not items:
            return await interaction.response.send_message(_adm("no_items_in_case", self.lang), ephemeral=True)
        embed = _set_chances_embed(db, self.case_id, is_community=False, lang=self.lang)
        await interaction.response.edit_message(
            embed=embed,
            view=SetItemChancesView(self.admin_id, self.case_id, is_community=False, lang=self.lang),
        )

    async def _on_edit(self, interaction: discord.Interaction):
        db   = _get_db()
        case = _get_case(db, self.case_id) or {}
        await interaction.response.send_modal(EditCaseModal(self.admin_id, self.case_id, case, lang=self.lang))

    async def _on_manage_emoji(self, interaction: discord.Interaction):
        db = _get_db()
        case = _get_case(db, self.case_id) or {}
        if not case.get("item_ids"):
            return await interaction.response.send_message(
                _adm("manage_emoji_need_items", self.lang), ephemeral=True
            )
        from modules.case_emoji_manager import CaseEmojiManageView

        view = CaseEmojiManageView(
            self.admin_id,
            self.case_id,
            is_community=False,
            lang=self.lang,
            template_id=case.get("case_icon_template"),
            selected_item_ids=case.get("case_icon_items") or [],
        )
        await interaction.response.edit_message(embed=view._embed(), view=view)

    async def _on_delete(self, interaction: discord.Interaction):
        db   = _get_db()
        case = _get_case(db, self.case_id) or {}
        embed = discord.Embed(
            title=_adm("delete_case_title", self.lang),
            description=_adm(
                "delete_case_desc", self.lang,
                emoji=case.get('emoji', '📦'),
                name=case.get('name', '?'),
            ),
            color=CLR_RED,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(
            embed=embed,
            view=ConfirmDeleteCaseView(self.admin_id, self.case_id, is_community=False, lang=self.lang),
        )

    async def _on_back(self, interaction: discord.Interaction):
        db = _get_db()
        await interaction.response.edit_message(
            embed=_official_cases_embed(db, lang=self.lang),
            view=OfficialCasesView(self.admin_id, lang=self.lang),
        )


def AddItemToCaseView(
    user_id: int,
    case_id: str,
    available: list,
    is_community: bool = False,
    lang: str | None = None,
):
    from modules.case_emoji_picker import PaginatedItemListView

    lang = lang or get_user_lang(user_id)
    entries = list(available)

    async def on_submit(interaction: discord.Interaction, sel_ids: list[str]) -> None:
        db = _get_db()
        bucket = "community_cases" if is_community else "cases"
        case = db[bucket].get(case_id)
        if not case:
            return await interaction.response.send_message(
                t("cases.community_not_found", user_id=str(interaction.user.id)), ephemeral=True
            )
        cur = case.setdefault("item_ids", [])
        for iid in sel_ids:
            if iid not in cur and len(cur) < MAX_ITEMS_PER_CASE:
                cur.append(iid)
        _recalculate_case_price(db, case_id, bucket)
        _save_db(db)
        embed = _case_detail_embed(db, case_id, is_community, lang=lang)
        view = (
            CommunityCaseDetailView(user_id, case_id)
            if is_community
            else OfficialCaseDetailView(user_id, case_id, lang=lang)
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def on_back(interaction: discord.Interaction) -> None:
        db = _get_db()
        embed = _case_detail_embed(db, case_id, is_community, lang=lang)
        view = (
            CommunityCaseDetailView(user_id, case_id)
            if is_community
            else OfficialCaseDetailView(user_id, case_id, lang=lang)
        )
        await interaction.response.edit_message(embed=embed, view=view)

    return PaginatedItemListView(
        user_id=user_id,
        entries=entries,
        placeholder_key="select_add_items",
        on_submit=on_submit,
        on_back=on_back,
        lang=lang,
        accent=CLR_TEAL if is_community else CLR_BRAND,
    )


def RemoveItemFromCaseView(
    user_id: int,
    case_id: str,
    items: list,
    is_community: bool = False,
    lang: str | None = None,
):
    from modules.case_emoji_picker import PaginatedItemListView

    lang = lang or get_user_lang(user_id)
    entries = [(item.get("id", item.get("name", "?")), item) for item in items]

    async def on_submit(interaction: discord.Interaction, sel_ids: list[str]) -> None:
        remove = set(sel_ids)
        db = _get_db()
        bucket = "community_cases" if is_community else "cases"
        case = db[bucket].get(case_id)
        if not case:
            return await interaction.response.send_message(
                t("cases.community_not_found", user_id=str(interaction.user.id)), ephemeral=True
            )
        case["item_ids"] = [i for i in case.get("item_ids", []) if i not in remove]
        if "item_chances" in case:
            for iid in remove:
                case["item_chances"].pop(iid, None)
        _recalculate_case_price(db, case_id, bucket)
        _save_db(db)
        embed = _case_detail_embed(db, case_id, is_community, lang=lang)
        view = (
            CommunityCaseDetailView(user_id, case_id)
            if is_community
            else OfficialCaseDetailView(user_id, case_id, lang=lang)
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def on_back(interaction: discord.Interaction) -> None:
        db = _get_db()
        embed = _case_detail_embed(db, case_id, is_community, lang=lang)
        view = (
            CommunityCaseDetailView(user_id, case_id)
            if is_community
            else OfficialCaseDetailView(user_id, case_id, lang=lang)
        )
        await interaction.response.edit_message(embed=embed, view=view)

    return PaginatedItemListView(
        user_id=user_id,
        entries=entries,
        placeholder_key="select_remove_items",
        on_submit=on_submit,
        on_back=on_back,
        lang=lang,
        accent=CLR_TEAL if is_community else CLR_BRAND,
    )


class ConfirmDeleteCaseView(View):
    def __init__(self, user_id: int, case_id: str, is_community: bool, lang: str | None = None):
        super().__init__(timeout=30)
        self.user_id      = user_id
        self.case_id      = case_id
        self.is_community = is_community
        self.lang         = lang or get_user_lang(user_id)

        yes = Button(label=_adm("confirm_yes_delete", self.lang), style=discord.ButtonStyle.danger)
        yes.callback = self._on_yes
        self.add_item(yes)

        no = Button(label=_adm("btn_cancel", self.lang), style=discord.ButtonStyle.secondary)
        no.callback = self._on_no
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("cases.not_yours", user_id=str(interaction.user.id)), ephemeral=True
            )
            return False
        return True

    async def _on_yes(self, interaction: discord.Interaction):
        db     = _get_db()
        bucket = "community_cases" if self.is_community else "cases"
        case   = db.get(bucket, {}).get(self.case_id)
        if case and not self.is_community:
            from modules.case_emoji_manager import delete_case_app_emoji
            await delete_case_app_emoji(interaction.client, case)
        db[bucket].pop(self.case_id, None)
        _save_db(db)
        if self.is_community:
            await interaction.response.edit_message(
                embed=_my_community_cases_embed(db, self.user_id, lang=self.lang),
                view=MyCommunityCasesView(self.user_id),
            )
        else:
            await interaction.response.edit_message(
                embed=_official_cases_embed(db, lang=self.lang),
                view=OfficialCasesView(self.user_id, lang=self.lang),
            )

    async def _on_no(self, interaction: discord.Interaction):
        db    = _get_db()
        embed = _case_detail_embed(db, self.case_id, self.is_community, lang=self.lang)
        view  = (
            CommunityCaseDetailView(self.user_id, self.case_id)
            if self.is_community
            else OfficialCaseDetailView(self.user_id, self.case_id, lang=self.lang)
        )
        await interaction.response.edit_message(embed=embed, view=view)

# ════════════════════════════════════════════════════════
# OFFICIAL CASE MODALS
# ════════════════════════════════════════════════════════

class AddCaseModal(Modal):
    def __init__(self, admin_id: int, lang: str = "en"):
        super().__init__(title=_adm("add_case_modal_title", lang))
        self.admin_id = admin_id
        self.lang     = lang
        self.name_in  = TextInput(label=_adm("case_name_label", lang), placeholder=_adm("case_name_ph", lang), max_length=50)
        self.edge_in  = TextInput(label=_adm("house_edge_label", lang), placeholder=_adm("house_edge_ph", lang), default="5", max_length=5)
        self.add_item(self.name_in)
        self.add_item(self.edge_in)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            edge = max(OFFICIAL_HE_MIN, min(OFFICIAL_HE_MAX, float(self.edge_in.value.replace(",", "."))))
        except ValueError:
            edge = 5.0
        db  = _get_db()
        cid = str(uuid.uuid4())[:8]
        db["cases"][cid] = {
            "name":       self.name_in.value.strip() or "Case",
            "emoji":      "📦",
            "house_edge": edge,
            "price":      0,
            "item_ids":   [],
            "type":       "official",
        }
        _save_db(db)
        embed = _case_detail_embed(db, cid, lang=self.lang)
        embed.title = _adm("case_created_banner", self.lang) + embed.title
        await interaction.response.edit_message(
            embed=embed,
            view=OfficialCaseDetailView(self.admin_id, cid, lang=self.lang),
        )


class EditCaseModal(Modal):
    def __init__(self, admin_id: int, case_id: str, case: dict, lang: str = "en"):
        super().__init__(title=_adm("edit_case_modal_title", lang))
        self.admin_id = admin_id
        self.case_id  = case_id
        self.lang     = lang
        self.name_in  = TextInput(label=_adm("case_name_label", lang), max_length=50)
        self.edge_in  = TextInput(label=_adm("house_edge_label", lang), max_length=5)
        self.name_in.default = case.get("name", "")
        self.edge_in.default = str(case.get("house_edge", 5.0))
        self.add_item(self.name_in)
        self.add_item(self.edge_in)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            edge = max(OFFICIAL_HE_MIN, min(OFFICIAL_HE_MAX, float(self.edge_in.value.replace(",", "."))))
        except ValueError:
            edge = 5.0
        db   = _get_db()
        case = _get_case(db, self.case_id)
        if not case:
            return await interaction.response.send_message(_adm("no_case_found", self.lang), ephemeral=True)
        case["name"]       = self.name_in.value.strip()
        case["house_edge"] = edge
        _recalculate_case_price(db, self.case_id, "cases")
        _save_db(db)
        await interaction.response.edit_message(
            embed=_case_detail_embed(db, self.case_id, lang=self.lang),
            view=OfficialCaseDetailView(self.admin_id, self.case_id, lang=self.lang),
        )


class _EmojiTextModal(Modal):
    def __init__(self, user_id: int, case_id: str, is_community: bool, lang: str | None = None):
        lang = lang or get_user_lang(user_id)
        super().__init__(title=_adm("change_emoji_modal_title", lang))
        self.user_id      = user_id
        self.case_id      = case_id
        self.is_community = is_community
        self.lang         = lang
        self.emoji_in = TextInput(
            label=_adm("emoji_label", lang),
            placeholder=_adm("emoji_ph", lang),
            max_length=80,
        )
        self.add_item(self.emoji_in)

    async def on_submit(self, interaction: discord.Interaction):
        emoji_str = self.emoji_in.value.strip() or "📦"
        db        = _get_db()
        bucket    = "community_cases" if self.is_community else "cases"
        if self.case_id in db[bucket]:
            db[bucket][self.case_id]["emoji"] = emoji_str
            _save_db(db)
        embed = _case_detail_embed(db, self.case_id, self.is_community, lang=self.lang)
        view  = (
            CommunityCaseDetailView(self.user_id, self.case_id)
            if self.is_community
            else OfficialCaseDetailView(self.user_id, self.case_id, lang=self.lang)
        )
        await interaction.response.edit_message(embed=embed, view=view)

# ════════════════════════════════════════════════════════
# COMMUNITY CASES — USER
# ════════════════════════════════════════════════════════

class MyCommunityCasesView(View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self._build()

    def _build(self):
        self.clear_items()
        db = _get_db()
        cc = db.get("community_cases", {})
        my = [(cid, c) for cid, c in cc.items() if c.get("owner_id") == self.user_id]

        if my:
            options = [
                discord.SelectOption(
                    label=c.get("name", "?")[:25],
                    value=cid,
                    emoji=c.get("emoji", "📦"),
                    description=f"{format_balance(c.get('price',0),'real')}  •  {len(c.get('item_ids',[]))} item"[:100],
                )
                for cid, c in my[:25]
            ]
            sel = Select(placeholder="📦 Kasanızı seçin...", options=options, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        add = Button(label="➕ Yeni Kasa", style=discord.ButtonStyle.success, row=1)
        add.callback = self._on_add
        self.add_item(add)

    async def _on_select(self, interaction: discord.Interaction):
        cid  = interaction.data["values"][0]
        db   = _get_db()
        case = _get_community_case(db, cid)
        lang = get_user_lang(interaction.user.id)
        if not case or case.get("owner_id") != interaction.user.id:
            return await interaction.response.send_message(t("cases.community_access_denied", lang=lang), ephemeral=True)
        await interaction.response.edit_message(embed=_case_detail_embed(db, cid, True, lang=lang), view=CommunityCaseDetailView(interaction.user.id, cid))

    async def _on_add(self, interaction: discord.Interaction):
        db = _get_db()
        if not _get_items(db):
            return await interaction.response.send_message(
                t("cases.no_items_library", user_id=str(interaction.user.id)), ephemeral=True
            )
        await interaction.response.send_modal(CreateCommunityCaseModal(interaction.user.id))


class CommunityCaseDetailView(View):
    def __init__(self, user_id: int, case_id: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.case_id = case_id
        self._build()

    def _build(self):
        self.clear_items()
        db    = _get_db()
        case  = _get_community_case(db, self.case_id) or {}
        cur   = set(case.get("item_ids", []))
        avail = [(iid, i) for iid, i in _get_items(db).items() if iid not in cur]

        if avail and len(cur) < MAX_ITEMS_PER_CASE:
            add_i = Button(label="➕ Item Ekle", style=discord.ButtonStyle.success, row=0)
            add_i.callback = self._on_add_item
            self.add_item(add_i)

        if cur:
            rem_i = Button(label="➖ Item Çıkar", style=discord.ButtonStyle.danger, row=0)
            rem_i.callback = self._on_remove_item
            self.add_item(rem_i)

            chance_btn = Button(label="⚖️ Şansları Ayarla", style=discord.ButtonStyle.primary, row=0)
            chance_btn.callback = self._on_set_chances
            self.add_item(chance_btn)

        edit = Button(label="✏️ Ad / Emoji", style=discord.ButtonStyle.secondary, row=1)
        edit.callback = self._on_edit
        self.add_item(edit)

        del_btn = Button(label="🗑️ Kasayı Sil", style=discord.ButtonStyle.danger, row=1)
        del_btn.callback = self._on_delete
        self.add_item(del_btn)

        published = case.get("published", False)
        pub_label = "🔴 Yayından Kaldır" if published else "🚀 Yayınla"
        pub_style = discord.ButtonStyle.danger if published else discord.ButtonStyle.success
        pub_btn = Button(label=pub_label, style=pub_style, row=2)
        pub_btn.callback = self._on_publish
        self.add_item(pub_btn)

        back = Button(label="◀ Geri", style=discord.ButtonStyle.secondary, row=2)
        back.callback = self._on_back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        db   = _get_db()
        case = _get_community_case(db, self.case_id)
        if not case or case.get("owner_id") != interaction.user.id:
            lang = get_user_lang(interaction.user.id)
            await interaction.response.send_message(t("cases.not_yours", lang=lang), ephemeral=True)
            return False
        return True

    async def _on_add_item(self, interaction: discord.Interaction):
        db   = _get_db()
        lang = get_user_lang(interaction.user.id)
        case = _get_community_case(db, self.case_id) or {}
        cur  = set(case.get("item_ids", []))
        avail = [(iid, i) for iid, i in _get_items(db).items() if iid not in cur]
        if not avail:
            return await interaction.response.send_message(t("cases.community_no_items", lang=lang), ephemeral=True)
        view = AddItemToCaseView(interaction.user.id, self.case_id, avail, is_community=True)
        embed = discord.Embed(title=t("cases.community_add_item_title", lang=lang), description=t("cases.community_add_item_desc", lang=lang), color=CLR_TEAL)
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_remove_item(self, interaction: discord.Interaction):
        db    = _get_db()
        lang  = get_user_lang(interaction.user.id)
        case  = _get_community_case(db, self.case_id) or {}
        items = _case_items_resolved(db, case.get("item_ids", []))
        if not items:
            return await interaction.response.send_message(t("cases.no_items_in_case", lang=lang), ephemeral=True)
        view = RemoveItemFromCaseView(interaction.user.id, self.case_id, items, is_community=True)
        embed = discord.Embed(title=t("cases.community_remove_item_title", lang=lang), description=t("cases.community_remove_item_desc", lang=lang), color=CLR_RED)
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_set_chances(self, interaction: discord.Interaction):
        db    = _get_db()
        lang  = get_user_lang(interaction.user.id)
        case  = _get_community_case(db, self.case_id) or {}
        items = _case_items_resolved(db, case.get("item_ids", []))
        if not items:
            return await interaction.response.send_message(t("cases.no_items_in_case", lang=lang), ephemeral=True)
        embed = _set_chances_embed(db, self.case_id, is_community=True, lang=lang)
        await interaction.response.edit_message(embed=embed, view=SetItemChancesView(self.user_id, self.case_id, is_community=True))

    async def _on_edit(self, interaction: discord.Interaction):
        db   = _get_db()
        case = _get_community_case(db, self.case_id) or {}
        await interaction.response.send_modal(EditCommunityCaseModal(interaction.user.id, self.case_id, case))

    async def _on_delete(self, interaction: discord.Interaction):
        db   = _get_db()
        lang = get_user_lang(interaction.user.id)
        case = _get_community_case(db, self.case_id) or {}
        embed = discord.Embed(
            title=t("cases.community_delete_title", lang=lang),
            description=t("cases.community_delete_desc", lang=lang, emoji=case.get('emoji','📦'), name=case.get('name','?')),
            color=CLR_RED,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(embed=embed, view=ConfirmDeleteCaseView(interaction.user.id, self.case_id, is_community=True))

    async def _on_back(self, interaction: discord.Interaction):
        db   = _get_db()
        lang = get_user_lang(interaction.user.id)
        await interaction.response.edit_message(embed=_my_community_cases_embed(db, interaction.user.id, lang=lang), view=MyCommunityCasesView(interaction.user.id))

    async def _on_publish(self, interaction: discord.Interaction):
        db   = _get_db()
        lang = get_user_lang(interaction.user.id)
        case = _get_community_case(db, self.case_id)
        if not case:
            return await interaction.response.send_message(t("cases.community_not_found", lang=lang), ephemeral=True)

        if case.get("published", False):
            # Unpublish
            case["published"] = False
            _save_db(db)
            await interaction.response.edit_message(
                embed=_case_detail_embed(db, self.case_id, True, lang=lang),
                view=CommunityCaseDetailView(self.user_id, self.case_id),
            )
            return

        # Publish — check item count
        items = _case_items_resolved(db, case.get("item_ids", []))
        if len(items) < MIN_ITEMS_TO_PUBLISH:
            return await interaction.response.send_message(
                t("cases.community_min_items", lang=lang, min=MIN_ITEMS_TO_PUBLISH, count=len(items)),
                ephemeral=True,
            )

        # Max 1 published case per user
        cc = db.get("community_cases", {})
        already_published = [
            cid for cid, c in cc.items()
            if c.get("owner_id") == self.user_id and c.get("published", False) and cid != self.case_id
        ]
        if already_published:
            return await interaction.response.send_message(
                t("cases.community_max_published", lang=lang), ephemeral=True
            )

        fee = _get_publish_fee(db)
        if fee > 0:
            player = Player(interaction.user.id)
            if player.get_balance("real") < fee:
                return await interaction.response.send_message(
                    t("cases.community_no_balance_fee", lang=lang,
                      fee=format_balance(fee, 'real'),
                      balance=format_balance(player.get_balance('real'), 'real')),
                    ephemeral=True,
                )
            confirm_embed = discord.Embed(
                title=t("cases.community_publish_fee_title", lang=lang),
                description=t("cases.community_publish_fee_desc", lang=lang,
                              emoji=case.get('emoji','📦'), name=case.get('name','?'),
                              fee=format_balance(fee, 'real'),
                              balance=format_balance(player.get_balance('real'), 'real')),
                color=CLR_TEAL,
            )
            confirm_embed.set_footer(text=FOOTER_TEXT)
            await interaction.response.edit_message(
                embed=confirm_embed,
                view=ConfirmPublishCaseView(self.user_id, self.case_id, fee),
            )
        else:
            case["published"] = True
            _save_db(db)
            await interaction.response.edit_message(
                embed=_case_detail_embed(db, self.case_id, True, lang=lang),
                view=CommunityCaseDetailView(self.user_id, self.case_id),
            )


class ConfirmPublishCaseView(View):
    def __init__(self, user_id: int, case_id: str, fee: int):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.case_id = case_id
        self.fee     = fee

        yes = Button(label="✅ Yayınla", style=discord.ButtonStyle.success)
        yes.callback = self._on_yes
        self.add_item(yes)

        no = Button(label="❌ İptal", style=discord.ButtonStyle.secondary)
        no.callback = self._on_no
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Bu panel size ait değil.", ephemeral=True)
            return False
        return True

    async def _on_yes(self, interaction: discord.Interaction):
        db   = _get_db()
        lang = get_user_lang(self.user_id)
        case = _get_community_case(db, self.case_id)
        if not case:
            return await interaction.response.send_message(t("cases.community_not_found", lang=lang), ephemeral=True)
        items = _case_items_resolved(db, case.get("item_ids", []))
        if len(items) < MIN_ITEMS_TO_PUBLISH:
            return await interaction.response.send_message(
                t("cases.community_insufficient_items_publish", lang=lang, min=MIN_ITEMS_TO_PUBLISH), ephemeral=True
            )
        # Max 1 published case per user
        cc = db.get("community_cases", {})
        already_published = [
            cid for cid, c in cc.items()
            if c.get("owner_id") == self.user_id and c.get("published", False) and cid != self.case_id
        ]
        if already_published:
            return await interaction.response.send_message(
                t("cases.community_max_published", lang=lang), ephemeral=True
            )
        if self.fee > 0:
            player = Player(interaction.user.id)
            if player.get_balance("real") < self.fee:
                return await interaction.response.send_message(
                    t("cases.community_no_balance_fee", lang=lang,
                      fee=format_balance(self.fee, 'real'),
                      balance=format_balance(player.get_balance('real'), 'real')),
                    ephemeral=True
                )
            player.remove_balance("real", self.fee)
        case["published"] = True
        _save_db(db)
        await interaction.response.edit_message(
            embed=_case_detail_embed(db, self.case_id, True, lang=lang),
            view=CommunityCaseDetailView(self.user_id, self.case_id),
        )

    async def _on_no(self, interaction: discord.Interaction):
        db   = _get_db()
        lang = get_user_lang(self.user_id)
        await interaction.response.edit_message(
            embed=_case_detail_embed(db, self.case_id, True, lang=lang),
            view=CommunityCaseDetailView(self.user_id, self.case_id),
        )


class CreateCommunityCaseModal(Modal):
    def __init__(self, user_id: int):
        lang = get_user_lang(user_id)
        super().__init__(title=t("cases.community_create_title", lang=lang))
        self.user_id = user_id
        self.lang    = lang

        self.name_in = TextInput(
            label=t("cases.community_name_label", lang=lang),
            placeholder=t("cases.community_name_placeholder", lang=lang),
            max_length=10,
        )
        self.emoji_in = TextInput(
            label=t("cases.community_emoji_label", lang=lang),
            placeholder=t("cases.community_emoji_placeholder", lang=lang),
            default="🎁",
            max_length=80,
        )
        self.desc_in = TextInput(
            label=t("cases.community_desc_label", lang=lang),
            placeholder=t("cases.community_desc_placeholder", lang=lang),
            max_length=200,
            required=False,
            style=discord.TextStyle.short,
        )
        self.add_item(self.name_in)
        self.add_item(self.emoji_in)
        self.add_item(self.desc_in)

    async def on_submit(self, interaction: discord.Interaction):
        db  = _get_db()
        cid = str(uuid.uuid4())[:8]
        db["community_cases"][cid] = {
            "name":             self.name_in.value.strip() or "New Case",
            "emoji":            self.emoji_in.value.strip() or "🎁",
            "description":      self.desc_in.value.strip(),
            "owner_id":         self.user_id,
            "house_edge":       COMMUNITY_HOUSE_EDGE,
            "price":            0,
            "item_ids":         [],
            "platform_fee_pct": PLATFORM_FEE_PCT,
            "created_at":       int(time.time()),
            "published":        False,
        }
        _save_db(db)
        lang  = self.lang
        embed = _case_detail_embed(db, cid, is_community=True, lang=lang)
        embed.title = t("cases.community_create_success", lang=lang) + embed.title
        await interaction.response.edit_message(embed=embed, view=CommunityCaseDetailView(self.user_id, cid))


class EditCommunityCaseModal(Modal):
    def __init__(self, user_id: int, case_id: str, case: dict):
        lang = get_user_lang(user_id)
        super().__init__(title=t("cases.community_edit_title", lang=lang))
        self.user_id  = user_id
        self.case_id  = case_id
        self.lang     = lang

        self.name_in = TextInput(
            label=t("cases.community_name_label", lang=lang),
            max_length=10,
            default=case.get("name", ""),
        )
        self.emoji_in = TextInput(
            label=t("cases.community_emoji_label", lang=lang),
            max_length=80,
            default=case.get("emoji", "🎁"),
        )
        self.desc_in = TextInput(
            label=t("cases.community_desc_label", lang=lang),
            placeholder=t("cases.community_desc_placeholder", lang=lang),
            max_length=200,
            required=False,
            style=discord.TextStyle.short,
            default=case.get("description", ""),
        )
        self.add_item(self.name_in)
        self.add_item(self.emoji_in)
        self.add_item(self.desc_in)

    async def on_submit(self, interaction: discord.Interaction):
        db   = _get_db()
        case = _get_community_case(db, self.case_id)
        if not case or case.get("owner_id") != self.user_id:
            return await interaction.response.send_message(
                t("cases.community_access_denied", lang=self.lang), ephemeral=True
            )
        case["name"]        = self.name_in.value.strip() or case["name"]
        case["emoji"]       = self.emoji_in.value.strip() or "🎁"
        case["description"] = self.desc_in.value.strip()
        # house_edge stays at COMMUNITY_HOUSE_EDGE — recalculate price
        _recalculate_case_price(db, self.case_id, "community_cases")
        _save_db(db)
        await interaction.response.edit_message(
            embed=_case_detail_embed(db, self.case_id, True, lang=self.lang),
            view=CommunityCaseDetailView(self.user_id, self.case_id),
        )

# ════════════════════════════════════════════════════════
# SET ITEM CHANCES
# ════════════════════════════════════════════════════════

def _set_chances_embed(db: dict, case_id: str, is_community: bool, lang: str = "en") -> discord.Embed:
    bucket  = "community_cases" if is_community else "cases"
    case    = db.get(bucket, {}).get(case_id) or {}
    items   = _case_items_resolved(db, case.get("item_ids", []))
    chances = case.get("item_chances", {})
    embed = discord.Embed(
        title=t("cases.chances_title", lang=lang, emoji=case.get("emoji", "📦"), name=case.get("name", "?")),
        description=t("cases.chances_desc", lang=lang) + f"\n{_divider()}",
        color=CLR_GOLD,
    )
    if items:
        weights_list = _get_item_weights(items, chances)
        total_w = sum(weights_list)
        lines = []
        for item, w in zip(items, weights_list):
            iid    = item.get("id", "")
            pct    = (w / total_w * 100) if total_w else 0.0
            is_set = bool(chances) and iid in chances
            source = "" if is_set else f" *({t('cases.chances_auto_label', lang=lang)})*"
            lines.append(
                f"{item.get('emoji','❓')} **{item.get('name','?')}**"
                f"  — ⚖️ {w:.4g}{source}  •  **{pct:.2f}%**"
            )
        value = "\n".join(lines)
        if len(value) > 1024:
            value = value[:1021] + "…"
        embed.add_field(name=t("cases.chances_distribution", lang=lang), value=value, inline=False)
        if not chances:
            embed.add_field(
                name=t("cases.chances_auto_mode_title", lang=lang),
                value=t("cases.chances_auto_mode_desc", lang=lang),
                inline=False,
            )
    else:
        embed.add_field(name=t("cases.no_items_added", lang=lang), value=t("cases.no_items_in_case", lang=lang), inline=False)
    embed.set_footer(text=f"{FOOTER_TEXT}  •  {t('cases.chances_footer', lang=lang)}")
    return embed


class SetItemChancesView(View):
    """Select an item to set its custom weight/chance."""
    def __init__(self, user_id: int, case_id: str, is_community: bool, lang: str | None = None):
        super().__init__(timeout=120)
        self.user_id      = user_id
        self.case_id      = case_id
        self.is_community = is_community
        self.lang         = lang or get_user_lang(user_id)
        self._build()

    def _build(self):
        self.clear_items()
        db      = _get_db()
        bucket  = "community_cases" if self.is_community else "cases"
        case    = db.get(bucket, {}).get(self.case_id) or {}
        items   = _case_items_resolved(db, case.get("item_ids", []))
        chances = case.get("item_chances", {})
        lang    = self.lang

        if items:
            weights_list = _get_item_weights(items, chances)
            total_w      = sum(weights_list)
            options = []
            for item, w in zip(items, weights_list):
                iid    = item.get("id", "")
                is_set = bool(chances) and iid in chances
                if is_set:
                    pct  = (w / total_w * 100) if total_w else 0.0
                    desc = f"⚖️ {w:.4g}  •  ~{pct:.1f}%"
                else:
                    desc = t("cases.chances_auto_desc_option", lang=lang)
                options.append(discord.SelectOption(
                    label=item.get("name", "?")[:50],
                    value=iid,
                    emoji=item.get("emoji", "❓"),
                    description=desc[:100],
                ))
            sel = Select(
                placeholder=t("cases.chances_select_placeholder", lang=lang),
                options=options[:25],
                row=0,
            )
            sel.callback = self._on_select
            self.add_item(sel)

        reset_btn = Button(label=t("cases.chances_reset_btn", lang=lang), style=discord.ButtonStyle.danger, row=1)
        reset_btn.callback = self._on_reset
        self.add_item(reset_btn)

        back = Button(label=t("cases.chances_back_btn", lang=lang), style=discord.ButtonStyle.secondary, row=1)
        back.callback = self._on_back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.is_community:
            db   = _get_db()
            case = db.get("community_cases", {}).get(self.case_id)
            if not case or case.get("owner_id") != interaction.user.id:
                lang = get_user_lang(interaction.user.id)
                await interaction.response.send_message(t("cases.not_yours", lang=lang), ephemeral=True)
                return False
        elif interaction.user.id != self.user_id:
            await interaction.response.send_message(t("cases.not_yours", lang=self.lang), ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        iid    = interaction.data["values"][0]
        db     = _get_db()
        bucket = "community_cases" if self.is_community else "cases"
        case   = db.get(bucket, {}).get(self.case_id) or {}
        item   = _get_item(db, iid)
        lang   = self.lang
        if not item:
            return await interaction.response.send_message(t("cases.item_not_found", lang=lang), ephemeral=True)
        chances        = case.get("item_chances", {})
        current_weight = float(chances.get(iid, 0))
        await interaction.response.send_modal(
            SetItemChanceModal(self.user_id, self.case_id, iid, item.get("name", "?"), self.is_community, current_weight, lang)
        )

    async def _on_reset(self, interaction: discord.Interaction):
        db     = _get_db()
        bucket = "community_cases" if self.is_community else "cases"
        case   = db.get(bucket, {}).get(self.case_id)
        lang   = self.lang
        if not case:
            return await interaction.response.send_message(t("cases.community_not_found", lang=lang), ephemeral=True)
        case.pop("item_chances", None)
        _recalculate_case_price(db, self.case_id, bucket)
        _save_db(db)
        embed = _case_detail_embed(db, self.case_id, self.is_community, lang=lang)
        view  = (
            CommunityCaseDetailView(self.user_id, self.case_id)
            if self.is_community
            else OfficialCaseDetailView(self.user_id, self.case_id, lang=lang)
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_back(self, interaction: discord.Interaction):
        db    = _get_db()
        lang  = self.lang
        embed = _case_detail_embed(db, self.case_id, self.is_community, lang=lang)
        view  = (
            CommunityCaseDetailView(self.user_id, self.case_id)
            if self.is_community
            else OfficialCaseDetailView(self.user_id, self.case_id, lang=lang)
        )
        await interaction.response.edit_message(embed=embed, view=view)


class SetItemChanceModal(Modal, title="⚖️  Set Item Chance"):

    def __init__(self, user_id: int, case_id: str, item_id: str, item_name: str, is_community: bool, current_weight: float, lang: str = "en"):
        super().__init__()
        self.user_id      = user_id
        self.case_id      = case_id
        self.item_id      = item_id
        self.is_community = is_community
        self.lang         = lang
        self.weight_in = TextInput(
            label=t("cases.chances_modal_label", lang=lang, name=item_name[:24]),
            placeholder=t("cases.chances_modal_placeholder", lang=lang),
            max_length=7,
            default=str(int(current_weight)) if current_weight > 0 else discord.utils.MISSING,
        )
        self.add_item(self.weight_in)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            weight = int(float(self.weight_in.value.replace(",", ".").strip()))
            if weight < 1:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                t("cases.chances_invalid_weight", lang=self.lang), ephemeral=True
            )
        weight = min(weight, 10000)
        db     = _get_db()
        bucket = "community_cases" if self.is_community else "cases"
        case   = db.get(bucket, {}).get(self.case_id)
        if not case:
            return await interaction.response.send_message(t("cases.community_not_found", lang=self.lang), ephemeral=True)

        chances = case.setdefault("item_chances", {})
        chances[self.item_id] = weight
        _recalculate_case_price(db, self.case_id, bucket)
        _save_db(db)
        embed = _set_chances_embed(db, self.case_id, self.is_community, lang=self.lang)
        await interaction.response.edit_message(
            embed=embed,
            view=SetItemChancesView(self.user_id, self.case_id, self.is_community, lang=self.lang),
        )


# ════════════════════════════════════════════════════════
# COMMUNITY BROWSE + OPEN
# ════════════════════════════════════════════════════════

class BrowseCommunityView(View):
    def __init__(self, user_id: int, page: int = 0, sort: str = "newest"):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.page    = page
        self.sort    = sort
        self._build()

    def _build(self):
        self.clear_items()
        db   = _get_db()
        cc   = db.get("community_cases", {})
        # Only show published cases
        all_ = [(cid, c) for cid, c in cc.items() if c.get("published", False)]
        if self.sort == "cheapest":   all_.sort(key=lambda x: x[1].get("price", 0))
        elif self.sort == "priciest": all_.sort(key=lambda x: x[1].get("price", 0), reverse=True)
        else:                         all_.sort(key=lambda x: x[1].get("created_at", 0), reverse=True)

        per_page = 6
        pages  = max(1, -(-(len(all_)) // per_page))
        chunk  = all_[self.page * per_page:(self.page + 1) * per_page]

        if chunk:
            options = [
                discord.SelectOption(
                    label=c.get("name", "?")[:25],
                    value=cid,
                    emoji=c.get("emoji", "📦"),
                    description=f"{format_balance(c.get('price',0),'real')}  •  {len(c.get('item_ids',[]))} item"[:100],
                )
                for cid, c in chunk
            ]
            sel = Select(placeholder="📦 Açmak istediğiniz kasayı seçin...", options=options, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        sort_defs = [
            ("newest",   "browse_sort_newest"),
            ("cheapest", "browse_sort_cheapest"),
            ("priciest", "browse_sort_priciest"),
        ]
        lang = get_user_lang(self.user_id)
        for val, key in sort_defs:
            btn = Button(
                label=t(f"cases.{key}", lang=lang),
                style=discord.ButtonStyle.primary if self.sort == val else discord.ButtonStyle.secondary,
                row=1,
            )
            btn.callback = self._make_sort_cb(val)
            self.add_item(btn)

        if self.page > 0:
            prev = Button(label="◀", style=discord.ButtonStyle.secondary, row=2)
            prev.callback = self._on_prev
            self.add_item(prev)

        if self.page < pages - 1:
            nxt = Button(label="▶", style=discord.ButtonStyle.secondary, row=2)
            nxt.callback = self._on_next
            self.add_item(nxt)

        back = Button(label=t("cases.my_cases_btn", lang=get_user_lang(self.user_id)), style=discord.ButtonStyle.secondary, row=2)
        back.callback = self._on_back
        self.add_item(back)

    def _make_sort_cb(self, sort_val: str):
        async def _cb(interaction: discord.Interaction):
            self.sort = sort_val
            self.page = 0
            self._build()
            db   = _get_db()
            lang = get_user_lang(self.user_id)
            await interaction.response.edit_message(embed=_community_browse_embed(db, self.page, self.sort, lang=lang), view=self)
        return _cb

    async def _on_select(self, interaction: discord.Interaction):
        cid   = interaction.data["values"][0]
        db    = _get_db()
        lang  = get_user_lang(interaction.user.id)
        case  = _get_community_case(db, cid)
        if not case:
            return await interaction.response.send_message(t("cases.community_not_found", lang=lang), ephemeral=True)
        items = _case_items_resolved(db, case.get("item_ids", []))
        if not items:
            return await interaction.response.send_message(t("cases.no_items_in_case", lang=lang), ephemeral=True)

        price  = case.get("price", 0)
        player = Player(interaction.user.id)
        embed  = discord.Embed(
            title=t("cases.open_confirm_title", lang=lang),
            description=t("cases.open_confirm_desc", lang=lang,
                          price=format_balance(price, 'real'),
                          balance=format_balance(player.get_balance('real'), 'real'),
                          pct=PLATFORM_FEE_PCT),
            color=CLR_TEAL,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(embed=embed, view=ConfirmOpenCaseView(interaction.user.id, cid, is_community=True))

    async def _on_prev(self, interaction: discord.Interaction):
        self.page -= 1
        self._build()
        db   = _get_db()
        lang = get_user_lang(self.user_id)
        await interaction.response.edit_message(embed=_community_browse_embed(db, self.page, self.sort, lang=lang), view=self)

    async def _on_next(self, interaction: discord.Interaction):
        self.page += 1
        self._build()
        db   = _get_db()
        lang = get_user_lang(self.user_id)
        await interaction.response.edit_message(embed=_community_browse_embed(db, self.page, self.sort, lang=lang), view=self)

    async def _on_back(self, interaction: discord.Interaction):
        db   = _get_db()
        lang = get_user_lang(interaction.user.id)
        await interaction.response.edit_message(embed=_my_community_cases_embed(db, interaction.user.id, lang=lang), view=MyCommunityCasesView(interaction.user.id))


class ConfirmOpenCaseView(View):
    def __init__(self, user_id: int, case_id: str, is_community: bool):
        super().__init__(timeout=60)
        self.user_id      = user_id
        self.case_id      = case_id
        self.is_community = is_community

        go = Button(label="🎲 Aç!", style=discord.ButtonStyle.success)
        go.callback = self._on_go
        self.add_item(go)

        cancel = Button(label="❌ İptal", style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("cases.not_your_interaction", user_id=str(interaction.user.id)), ephemeral=True
            )
            return False
        return True

    async def _on_go(self, interaction: discord.Interaction):
        await interaction.response.defer()
        err, gif_buf = await _settle_case_opens(
            interaction.user,
            self.case_id,
            self.is_community,
            1,
        )
        if err:
            return await interaction.followup.send(err, ephemeral=True)
        lang = get_user_lang(interaction.user.id)
        db = _get_db()
        getter = _get_community_case if self.is_community else _get_case
        case = getter(db, self.case_id) or {}
        price = case.get("price", 0)
        player = Player(interaction.user.id)
        embed = discord.Embed(
            title=f"{case.get('emoji', '📦')} {case.get('name', 'Case')}",
            description=(
                f"**Price:** {format_balance(price, 'real')}\n"
                f"**Balance:** {format_balance(player.get_balance('real'), 'real')}"
            ),
            color=CLR_TEAL if self.is_community else CLR_BRAND,
        )
        embed.set_footer(text=FOOTER_TEXT)
        files = [discord.File(gif_buf, "cases.gif")] if gif_buf else []
        await interaction.message.edit(embed=embed, attachments=files, view=_OpenAgainView(self.user_id, self.case_id, self.is_community))

    async def _on_cancel(self, interaction: discord.Interaction):
        db   = _get_db()
        lang = get_user_lang(interaction.user.id)
        if self.is_community:
            await interaction.response.edit_message(embed=_community_browse_embed(db, lang=lang), view=BrowseCommunityView(interaction.user.id))
        else:
            await interaction.response.edit_message(embed=discord.Embed(title="↩️ Cancelled.", color=CLR_GREY), view=None)


class _OpenAgainView(View):
    def __init__(self, user_id: int, case_id: str, is_community: bool):
        super().__init__(timeout=60)
        self.user_id      = user_id
        self.case_id      = case_id
        self.is_community = is_community

        again = Button(label="🔄 Tekrar Aç", style=discord.ButtonStyle.success)
        again.callback = self._on_again
        self.add_item(again)

        browse = Button(label="🌐 Diğer Kasalar", style=discord.ButtonStyle.secondary)
        browse.callback = self._on_browse
        self.add_item(browse)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            lang = get_user_lang(interaction.user.id)
            await interaction.response.send_message(t("cases.not_your_interaction", lang=lang), ephemeral=True)
            return False
        return True

    async def _on_again(self, interaction: discord.Interaction):
        db     = _get_db()
        lang   = get_user_lang(self.user_id)
        getter = _get_community_case if self.is_community else _get_case
        case   = getter(db, self.case_id)
        if not case:
            return await interaction.response.send_message(t("cases.community_not_found", lang=lang), ephemeral=True)
        price  = case.get("price", 0)
        player = Player(interaction.user.id)
        embed  = discord.Embed(
            title=t("cases.open_again_title", lang=lang, emoji=case.get('emoji','📦'), name=case.get('name','Case')),
            description=t("cases.open_again_desc", lang=lang,
                          price=format_balance(price, 'real'),
                          balance=format_balance(player.get_balance('real'), 'real')),
            color=CLR_TEAL if self.is_community else CLR_BRAND,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(embed=embed, view=ConfirmOpenCaseView(self.user_id, self.case_id, self.is_community))

    async def _on_browse(self, interaction: discord.Interaction):
        db   = _get_db()
        lang = get_user_lang(self.user_id)
        await interaction.response.edit_message(embed=_community_browse_embed(db, lang=lang), view=BrowseCommunityView(interaction.user.id))

# ════════════════════════════════════════════════════════
# ADMIN — COMMUNITY CASES MANAGEMENT
# ════════════════════════════════════════════════════════

def _admin_community_cases_embed(db: dict) -> discord.Embed:
    cc    = db.get("community_cases", {})
    fee   = _get_publish_fee(db)
    pub   = sum(1 for c in cc.values() if c.get("published", False))
    draft = len(cc) - pub

    embed = discord.Embed(
        title="🌐  Community Kasa Yönetimi",
        description=(
            f"**{len(cc)}** toplam kasa  •  ✅ {pub} yayında  •  🔴 {draft} taslak\n"
            f"💰 **Yayınlama Ücreti:** {format_balance(fee, 'real') if fee > 0 else 'Ücretsiz'}\n"
            f"{_divider()}"
        ),
        color=CLR_TEAL,
    )
    for cid, case in list(cc.items())[:9]:
        items = _case_items_resolved(db, case.get("item_ids", []))
        status = "✅" if case.get("published") else "🔴"
        embed.add_field(
            name=f"{status} {case.get('emoji','📦')}  {case.get('name','?')}",
            value=(
                f"👤 <@{case.get('owner_id','?')}>\n"
                f"📋 {len(items)} item  •  💰 {format_balance(case.get('price', 0), 'real')}"
            ),
            inline=True,
        )
    if not cc:
        embed.add_field(name="Boş", value="Henüz community kasası yok.", inline=False)
    embed.set_footer(text=f"{FOOTER_TEXT}  •  Admin Panel")
    return embed


class AdminCommunityCasesView(View):
    def __init__(self, admin_id: int, page: int = 0):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.page     = page
        self._build()

    def _build(self):
        self.clear_items()
        db     = _get_db()
        cc     = db.get("community_cases", {})
        cases  = list(cc.items())
        per_pg = 25
        pages  = max(1, -(-len(cases) // per_pg))
        chunk  = cases[self.page * per_pg:(self.page + 1) * per_pg]

        if chunk:
            options = [
                discord.SelectOption(
                    label=f"{'✅' if c.get('published') else '🔴'} {c.get('name', '?')[:22]}",
                    value=cid,
                    emoji=c.get("emoji", "📦"),
                    description=f"{len(c.get('item_ids',[]))} item  •  {c.get('owner_id','?')}"[:100],
                )
                for cid, c in chunk
            ]
            sel = Select(placeholder="🌐 Yönetmek istediğiniz kasayı seçin...", options=options, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        fee_btn = Button(label="💰 Yayınlama Ücretini Ayarla", style=discord.ButtonStyle.primary, row=1)
        fee_btn.callback = self._on_set_fee
        self.add_item(fee_btn)

        back = Button(label="◀ Geri", style=discord.ButtonStyle.secondary, row=1)
        back.callback = self._on_back
        self.add_item(back)

        if self.page > 0:
            prev = Button(label="◀ Önceki", style=discord.ButtonStyle.secondary, row=2)
            prev.callback = self._on_prev
            self.add_item(prev)

        if self.page < pages - 1:
            nxt = Button(label="Sonraki ▶", style=discord.ButtonStyle.secondary, row=2)
            nxt.callback = self._on_next
            self.add_item(nxt)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ Bu panel size ait değil.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        cid  = interaction.data["values"][0]
        db   = _get_db()
        case = _get_community_case(db, cid)
        if not case:
            return await interaction.response.send_message("❌ Kasa bulunamadı.", ephemeral=True)
        await interaction.response.edit_message(
            embed=_case_detail_embed(db, cid, is_community=True),
            view=AdminCaseActionView(self.admin_id, cid),
        )

    async def _on_set_fee(self, interaction: discord.Interaction):
        db = _get_db()
        await interaction.response.send_modal(AdminSetPublishFeeModal(self.admin_id, _get_publish_fee(db)))

    async def _on_back(self, interaction: discord.Interaction):
        from cogs.admin_panel import _build_admin_panel_embed, AdminPanelView
        embed = _build_admin_panel_embed(interaction)
        await interaction.response.edit_message(embed=embed, view=AdminPanelView(self.admin_id))

    async def _on_prev(self, interaction: discord.Interaction):
        self.page -= 1
        self._build()
        db = _get_db()
        await interaction.response.edit_message(embed=_admin_community_cases_embed(db), view=self)

    async def _on_next(self, interaction: discord.Interaction):
        self.page += 1
        self._build()
        db = _get_db()
        await interaction.response.edit_message(embed=_admin_community_cases_embed(db), view=self)


class AdminCaseActionView(View):
    """Admin actions for a single community case."""
    def __init__(self, admin_id: int, case_id: str):
        super().__init__(timeout=120)
        self.admin_id = admin_id
        self.case_id  = case_id

        db        = _get_db()
        case      = _get_community_case(db, case_id) or {}
        published = case.get("published", False)

        rename_btn = Button(label="✏️ İsim Değiştir", style=discord.ButtonStyle.secondary, row=0)
        rename_btn.callback = self._on_rename
        self.add_item(rename_btn)

        pub_label = "🔴 Yayından Kaldır" if published else "✅ Yayınla"
        pub_style = discord.ButtonStyle.danger if published else discord.ButtonStyle.success
        pub_btn = Button(label=pub_label, style=pub_style, row=0)
        pub_btn.callback = self._on_toggle_publish
        self.add_item(pub_btn)

        del_btn = Button(label="🗑️ Sil", style=discord.ButtonStyle.danger, row=1)
        del_btn.callback = self._on_delete
        self.add_item(del_btn)

        back = Button(label="◀ Geri", style=discord.ButtonStyle.secondary, row=1)
        back.callback = self._on_back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ Bu panel size ait değil.", ephemeral=True)
            return False
        return True

    async def _on_rename(self, interaction: discord.Interaction):
        db   = _get_db()
        case = _get_community_case(db, self.case_id) or {}
        await interaction.response.send_modal(AdminCasesRenameModal(self.admin_id, self.case_id, case.get("name", "")))

    async def _on_toggle_publish(self, interaction: discord.Interaction):
        db   = _get_db()
        case = _get_community_case(db, self.case_id)
        if not case:
            return await interaction.response.send_message("❌ Kasa bulunamadı.", ephemeral=True)
        case["published"] = not case.get("published", False)
        _save_db(db)
        await interaction.response.edit_message(
            embed=_case_detail_embed(db, self.case_id, True),
            view=AdminCaseActionView(self.admin_id, self.case_id),
        )

    async def _on_delete(self, interaction: discord.Interaction):
        db   = _get_db()
        case = _get_community_case(db, self.case_id) or {}
        embed = discord.Embed(
            title="⚠️  Community Kasayı Sil",
            description=(
                f"**{case.get('emoji','📦')} {case.get('name','?')}** kalıcı olarak silinecek.\n"
                f"Bu işlem geri alınamaz!"
            ),
            color=CLR_RED,
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(
            embed=embed,
            view=AdminConfirmDeleteCommunityView(self.admin_id, self.case_id),
        )

    async def _on_back(self, interaction: discord.Interaction):
        db = _get_db()
        await interaction.response.edit_message(
            embed=_admin_community_cases_embed(db),
            view=AdminCommunityCasesView(self.admin_id),
        )


class AdminConfirmDeleteCommunityView(View):
    def __init__(self, admin_id: int, case_id: str):
        super().__init__(timeout=30)
        self.admin_id = admin_id
        self.case_id  = case_id

        yes = Button(label="✅ Evet, Sil", style=discord.ButtonStyle.danger)
        yes.callback = self._on_yes
        self.add_item(yes)

        no = Button(label="❌ İptal", style=discord.ButtonStyle.secondary)
        no.callback = self._on_no
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ Bu panel size ait değil.", ephemeral=True)
            return False
        return True

    async def _on_yes(self, interaction: discord.Interaction):
        db = _get_db()
        db["community_cases"].pop(self.case_id, None)
        _save_db(db)
        await interaction.response.edit_message(
            embed=_admin_community_cases_embed(db),
            view=AdminCommunityCasesView(self.admin_id),
        )

    async def _on_no(self, interaction: discord.Interaction):
        db = _get_db()
        await interaction.response.edit_message(
            embed=_admin_community_cases_embed(db),
            view=AdminCommunityCasesView(self.admin_id),
        )


class AdminCasesRenameModal(Modal, title="✏️  Kasa İsmini Değiştir"):
    name_in = TextInput(label="Yeni İsim", max_length=10, placeholder="Maks 10 karakter")

    def __init__(self, admin_id: int, case_id: str, current_name: str):
        super().__init__()
        self.admin_id = admin_id
        self.case_id  = case_id
        self.name_in.default = current_name

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.name_in.value.strip()
        if not new_name:
            return await interaction.response.send_message("❌ İsim boş olamaz.", ephemeral=True)
        db   = _get_db()
        case = _get_community_case(db, self.case_id)
        if not case:
            return await interaction.response.send_message("❌ Kasa bulunamadı.", ephemeral=True)
        case["name"] = new_name
        _save_db(db)
        await interaction.response.edit_message(
            embed=_case_detail_embed(db, self.case_id, True),
            view=AdminCaseActionView(self.admin_id, self.case_id),
        )


class AdminSetPublishFeeModal(Modal, title="💰  Yayınlama Ücreti Ayarla"):
    fee_in = TextInput(label="Ücret (0 = Ücretsiz)", placeholder="örn: 1000", max_length=12)

    def __init__(self, admin_id: int, current_fee: int):
        super().__init__()
        self.admin_id = admin_id
        self.fee_in.default = str(current_fee)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            fee = int(self.fee_in.value.strip().replace(",", "").replace(".", ""))
            fee = max(0, fee)
        except ValueError:
            return await interaction.response.send_message("❌ Geçersiz değer.", ephemeral=True)
        db = _get_db()
        _set_publish_fee(db, fee)
        _save_db(db)
        await interaction.response.edit_message(
            embed=_admin_community_cases_embed(db),
            view=AdminCommunityCasesView(self.admin_id),
        )

# ════════════════════════════════════════════════════════
# PREFIX — CASE OPEN HUB (.cases)
# ════════════════════════════════════════════════════════

def _hub_list_cases(db: dict, view_mode: str) -> list[tuple[str, dict, bool]]:
    """(case_id, case_dict, is_community) with at least one item."""
    out: list[tuple[str, dict, bool]] = []
    if view_mode == "house":
        for cid, case in db.get("cases", {}).items():
            if _case_items_resolved(db, case.get("item_ids", [])):
                out.append((cid, case, False))
    else:
        for cid, case in db.get("community_cases", {}).items():
            if not case.get("published"):
                continue
            if _case_items_resolved(db, case.get("item_ids", [])):
                out.append((cid, case, True))
    out.sort(key=lambda x: x[1].get("price", 0))
    return out


def _hub_selected(
    db: dict,
    case_id: str | None,
    is_community: bool,
) -> tuple[str, dict, bool] | None:
    if not case_id:
        return None
    getter = _get_community_case if is_community else _get_case
    case = getter(db, case_id)
    if not case:
        return None
    return case_id, case, is_community


def _hub_item_preview(db: dict, case: dict) -> str:
    items = _case_items_resolved(db, case.get("item_ids", []))
    return " ".join(
        f"{it.get('emoji', '?')}"
        for it in sorted(items, key=lambda x: -x.get("value", 0))[:8]
    )


async def _settle_case_opens(
    user: discord.User | discord.Member,
    case_id: str,
    is_community: bool,
    count: int,
) -> tuple[str | None, object | None]:
    """Deduct, roll, credit, render GIF. Returns (error, gif_bytes_io)."""
    count = max(1, min(MAX_CASE_OPEN_COUNT, int(count)))
    db = _get_db()
    getter = _get_community_case if is_community else _get_case
    case = getter(db, case_id)
    if not case:
        return t("cases.not_found", user_id=str(user.id)), None

    items = _case_items_resolved(db, case.get("item_ids", []))
    if not items:
        return t("cases.no_items_in_case", user_id=str(user.id)), None

    unit = int(case.get("price", 0))
    total_cost = unit * count
    player = Player(user.id)
    if player.get_balance("real") < total_cost:
        return (
            t(
                "cases.insufficient_balance",
                user_id=str(user.id),
                price=format_balance(total_cost, "real"),
            ),
            None,
        )

    player.remove_balance("real", total_cost)
    chances = case.get("item_chances", {})
    from modules import flip_balance_cap as bc
    from modules.game_rig import rig_case_winners

    rigged = await bc.should_rig_outcome(user.id, "case_opening", float(total_cost))
    if rigged:
        winners = rig_case_winners(items, count)
    else:
        winners = [_open_case_item(items, chances) for _ in range(count)]

    if is_community:
        owner_id = case.get("owner_id")
        fee_each = round(unit * (PLATFORM_FEE_PCT / 100))
        if owner_id and owner_id != user.id and fee_each > 0:
            for _ in range(count):
                Player(owner_id).add_balance("real", fee_each)

    total_won = sum(int(w.get("value", 0)) for w in winners)
    if total_won > 0:
        player.add_balance("real", total_won)

    gif = await image_gen.render_case_open_gif(
        items,
        winners,
        float(unit),
        case_name=case.get("name", "Case"),
    )
    return None, gif


# ════════════════════════════════════════════════════════
# COG
# ════════════════════════════════════════════════════════

class CasesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="items", description=t("cases.cmd_items_desc", lang="en"))
    async def items_admin(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        from modules.database import can_manage_items
        if not can_manage_items(interaction.user.id):
            return await interaction.response.send_message(
                embed=create_error_embed(t("cases.item_manager_only", user_id=uid)), ephemeral=True
            )
        lang = get_user_lang(interaction.user.id)
        db = _get_db()
        await interaction.response.send_message(
            embed=_item_library_embed(db, lang=lang),
            view=ItemLibraryView(interaction.user.id, lang=lang),
            ephemeral=True,
        )

    @app_commands.command(name="cases", description=t("cases.cmd_cases_desc", lang="en"))
    async def cases_admin(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        from modules.database import can_manage_items
        if not can_manage_items(interaction.user.id):
            return await interaction.response.send_message(
                embed=create_error_embed(t("cases.item_manager_only", user_id=uid)), ephemeral=True
            )
        lang = get_user_lang(interaction.user.id)
        db = _get_db()
        await interaction.response.send_message(
            embed=_official_cases_embed(db, lang=lang),
            view=OfficialCasesView(interaction.user.id, lang=lang),
            ephemeral=True,
        )

    @commands.command(name="cases", aliases=["case", "kasa"])
    async def cases_prefix(self, ctx: commands.Context):
        """Case opening hub — official & community cases."""
        from database import db as flip_db

        await flip_db.ensure_user(ctx.author.id, ctx.author.name)
        if await flip_db.is_banned(ctx.author.id):
            return await ctx.send(embed=create_error_embed("You are banned."))

        db = _get_db()
        if not _hub_list_cases(db, "house") and not _hub_list_cases(db, "community"):
            return await ctx.send(
                embed=discord.Embed(
                    title="📦 Cases",
                    description="No cases available yet.",
                    color=CLR_GREY,
                )
            )
        from modules.cases_hub_v2 import make_cases_hub

        layout = make_cases_hub(ctx.author.id)
        await ctx.send(view=layout)

    @app_commands.command(name="setprices", description=t("cases.cmd_setprices_desc", lang="en"))
    async def setprices_admin(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        from modules.database import can_manage_items
        if not can_manage_items(interaction.user.id):
            return await interaction.response.send_message(
                embed=create_error_embed(t("cases.item_manager_only", user_id=uid)), ephemeral=True
            )
        from modules.set_prices import start_set_prices_flow

        await start_set_prices_flow(interaction)

    @app_commands.command(name="community_cases", description="Community kasa panelini açar")
    async def community_cases(self, interaction: discord.Interaction):
        uid  = str(interaction.user.id)
        db   = _get_db()
        lang = get_user_lang(interaction.user.id)
        if not _get_items(db):
            return await interaction.response.send_message(
                t("cases.no_items_library", user_id=uid), ephemeral=True
            )
        await interaction.response.send_message(embed=_my_community_cases_embed(db, interaction.user.id, lang=lang), view=MyCommunityCasesView(interaction.user.id), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CasesCog(bot))
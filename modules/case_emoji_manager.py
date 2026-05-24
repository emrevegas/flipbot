"""Manage case icons as Discord application emojis."""

from __future__ import annotations

import re

import discord
from discord.ui import Button, Select, View

from modules.case_icon_gen import TEMPLATE_IDS, TEMPLATE_LABELS, render_case_icon
from modules.constants import FOOTER_TEXT
from modules.database import get_data, replace_data

_EMOJI_ID_RE = re.compile(r"<a?:(\w+):(\d+)>")


def _get_db() -> dict:
    data = get_data("server/cases") or {}
    data.setdefault("items", {})
    data.setdefault("cases", {})
    data.setdefault("community_cases", {})
    return data


def _save_db(data: dict) -> None:
    replace_data("server/cases", data)


def case_emoji_name(case_id: str) -> str:
    return f"case_{case_id}"[:32]


def emoji_id_from_str(emoji_str: str) -> int | None:
    if not emoji_str:
        return None
    m = _EMOJI_ID_RE.search(str(emoji_str))
    return int(m.group(2)) if m else None


async def delete_case_app_emoji(client: discord.Client, case: dict) -> None:
    eid = case.get("app_emoji_id")
    if not eid:
        eid = emoji_id_from_str(case.get("emoji", ""))
    if not eid:
        return
    try:
        emoji = await client.fetch_application_emoji(int(eid))
        await emoji.delete()
    except Exception:
        pass


async def upload_case_app_emoji(
    client: discord.Client,
    *,
    case_id: str,
    template_id: str,
    item_emojis: list[str],
) -> discord.Emoji:
    png = await render_case_icon(template_id, item_emojis)
    name = case_emoji_name(case_id)
    try:
        emoji = await client.create_application_emoji(name=name, image=png)
    except discord.HTTPException:
        # Name collision — remove stale emoji with same name then retry once.
        for existing in await client.fetch_application_emojis():
            if existing.name == name:
                await existing.delete()
                break
        emoji = await client.create_application_emoji(name=name, image=png)
    return emoji


class CaseEmojiManageView(View):
    """Pick template + up to 4 case items, save as application emoji."""

    def __init__(
        self,
        admin_id: int,
        case_id: str,
        *,
        is_community: bool = False,
        lang: str = "en",
        template_id: str | None = None,
        selected_item_ids: list[str] | None = None,
    ):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.case_id = case_id
        self.is_community = is_community
        self.lang = lang
        self.template_id = template_id or TEMPLATE_IDS[0]
        self.selected_item_ids = list(selected_item_ids or [])
        self._rebuild()

    def _case(self) -> dict | None:
        db = _get_db()
        if self.is_community:
            return db.get("community_cases", {}).get(self.case_id)
        return db.get("cases", {}).get(self.case_id)

    def _case_items(self) -> list[tuple[str, dict]]:
        db = _get_db()
        case = self._case() or {}
        lib = db.get("items", {})
        out: list[tuple[str, dict]] = []
        for iid in case.get("item_ids", []):
            if iid in lib:
                out.append((iid, lib[iid]))
        return out

    def _embed(self) -> discord.Embed:
        case = self._case() or {}
        items = self._case_items()
        tpl_label = TEMPLATE_LABELS.get(self.template_id, self.template_id)
        picked = [it.get("name", "?") for iid, it in items if iid in self.selected_item_ids]

        embed = discord.Embed(
            title="Manage Case Emoji",
            description=(
                "Choose a **chest template** and **up to 4 items** from this case.\n"
                "Saving uploads a new **application emoji** and sets it as the case emoji."
            ),
            color=0x5865F2,
        )
        embed.add_field(name="Case", value=f"{case.get('emoji', '📦')} **{case.get('name', '?')}**", inline=False)
        embed.add_field(name="Template", value=tpl_label, inline=True)
        embed.add_field(name="Items selected", value=str(len(self.selected_item_ids)), inline=True)
        if picked:
            embed.add_field(name="Selection", value=", ".join(picked)[:1024], inline=False)
        elif items:
            embed.add_field(name="Selection", value="*None yet — pick items below.*", inline=False)
        embed.set_footer(text=f"{FOOTER_TEXT}  •  Max 4 items")
        return embed

    def _rebuild(self) -> None:
        self.clear_items()
        items = self._case_items()

        tpl_opts = [
            discord.SelectOption(
                label=TEMPLATE_LABELS[tid],
                value=tid,
                default=(tid == self.template_id),
            )
            for tid in TEMPLATE_IDS
        ]
        tpl_sel = Select(placeholder="Chest template…", options=tpl_opts, row=0)
        tpl_sel.callback = self._on_template
        self.add_item(tpl_sel)

        if items:
            opts = [
                discord.SelectOption(
                    label=it.get("name", "?")[:100],
                    value=iid,
                    emoji=it.get("emoji", "❓"),
                    default=(iid in self.selected_item_ids),
                )
                for iid, it in items[:25]
            ]
            item_sel = Select(
                placeholder="Case items (max 4)…",
                options=opts,
                min_values=0,
                max_values=min(4, len(opts)),
                row=1,
            )
            item_sel.callback = self._on_items
            self.add_item(item_sel)

        save = Button(label="Save emoji", style=discord.ButtonStyle.success, emoji="💾", row=2)
        save.callback = self._on_save
        save.disabled = not items or not self.selected_item_ids
        self.add_item(save)

        back = Button(label="Back", style=discord.ButtonStyle.secondary, row=2)
        back.callback = self._on_back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    async def _on_template(self, interaction: discord.Interaction):
        self.template_id = interaction.data["values"][0]
        self._rebuild()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def _on_items(self, interaction: discord.Interaction):
        self.selected_item_ids = interaction.data["values"][:4]
        self._rebuild()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def _on_save(self, interaction: discord.Interaction):
        if not self.selected_item_ids:
            return await interaction.response.send_message("Select at least one item.", ephemeral=True)

        db = _get_db()
        bucket = "community_cases" if self.is_community else "cases"
        case = db.get(bucket, {}).get(self.case_id)
        if not case:
            return await interaction.response.send_message("Case not found.", ephemeral=True)

        lib = db.get("items", {})
        emoji_strs = [
            str(lib[iid]["emoji"])
            for iid in self.selected_item_ids
            if iid in lib and lib[iid].get("emoji")
        ]
        if not emoji_strs:
            return await interaction.response.send_message("Selected items have no emoji.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            await delete_case_app_emoji(interaction.client, case)
            new_emoji = await upload_case_app_emoji(
                interaction.client,
                case_id=self.case_id,
                template_id=self.template_id,
                item_emojis=emoji_strs,
            )
            case["emoji"] = str(new_emoji)
            case["app_emoji_id"] = new_emoji.id
            case["case_icon_template"] = self.template_id
            case["case_icon_items"] = list(self.selected_item_ids)
            _save_db(db)
        except Exception as exc:
            return await interaction.followup.send(
                f"Failed to create case emoji: {exc}", ephemeral=True
            )

        from cogs.cases import OfficialCaseDetailView, _case_detail_embed

        embed = _case_detail_embed(db, self.case_id, self.is_community, lang=self.lang)
        view = OfficialCaseDetailView(self.admin_id, self.case_id, lang=self.lang)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _on_back(self, interaction: discord.Interaction):
        from cogs.cases import OfficialCaseDetailView, _case_detail_embed

        db = _get_db()
        embed = _case_detail_embed(db, self.case_id, self.is_community, lang=self.lang)
        view = OfficialCaseDetailView(self.admin_id, self.case_id, lang=self.lang)
        await interaction.response.edit_message(embed=embed, view=view)

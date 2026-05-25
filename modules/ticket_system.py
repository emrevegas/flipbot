"""Categorized support tickets — panel in channels + persistent controls."""
from __future__ import annotations

import time

import discord

from modules.database import get_data, get_user_data, set_data, set_user_data
from modules.translator import t

CATEGORY_LABELS = {
    "balance": "💰 Balance Operations",
    "technical": "🔧 Technical Support",
    "bug": "🐛 Bug Report",
    "general": "💬 General Support",
}


def build_ticket_panel_embed() -> discord.Embed:
    return discord.Embed(
        title="🎫 Destek Talebi",
        description=(
            "Aşağıdan bir **kategori** seçerek ticket açın.\n\n"
            "• **Balance** — Bakiye, yatırım, çekim\n"
            "• **Technical** — Teknik sorunlar\n"
            "• **Bug** — Hata bildirimi\n"
            "• **General** — Diğer konular\n\n"
            "Ekibimiz en kısa sürede yanıt verecektir."
        ),
        color=discord.Color.blurple(),
    )


def _category_select_options() -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=t("support.category_balance", lang="en"),
            description="Balance, deposit, withdrawal issues",
            emoji="💰",
            value="balance",
        ),
        discord.SelectOption(
            label=t("support.category_technical", lang="en"),
            description="Technical problems and errors",
            emoji="🔧",
            value="technical",
        ),
        discord.SelectOption(
            label=t("support.category_bug", lang="en"),
            description="Report bugs and issues",
            emoji="🐛",
            value="bug",
        ),
        discord.SelectOption(
            label=t("support.category_general", lang="en"),
            description="General questions",
            emoji="💬",
            value="general",
        ),
    ]


async def _user_has_open_ticket(guild: discord.Guild, user_id: int) -> discord.TextChannel | None:
    tickets_data = get_data("server/tickets") or {}
    guild_id = str(guild.id)
    for ticket_id, ticket_info in (tickets_data.get(guild_id) or {}).items():
        if ticket_info.get("user_id") == user_id and ticket_info.get("status") == "open":
            ch = guild.get_channel(int(ticket_id))
            if ch and isinstance(ch, discord.TextChannel):
                return ch
    return None


def _staff_overwrites(
    guild: discord.Guild,
    opener: discord.Member,
    category: str,
) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        opener: discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
        ),
        guild.me: discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            manage_webhooks=True,
        ),
    }
    admins_data = get_data("server/admins") or {}
    if category == "bug":
        perm_keys = ("admin",)
    else:
        perm_keys = ("admin", "ticketAdmin")
    for user_id, perms in admins_data.items():
        plist = perms if isinstance(perms, list) else [perms]
        if any(p in plist for p in perm_keys):
            member = guild.get_member(int(user_id))
            if member:
                overwrites[member] = discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    embed_links=True,
                    attach_files=True,
                    read_message_history=True,
                )
    return overwrites


async def create_support_ticket(
    interaction: discord.Interaction,
    category: str,
    description: str,
) -> None:
    """Create ticket channel after category + description collected."""
    if not interaction.guild:
        await interaction.response.send_message("❌ Sunucu içinde kullanın.", ephemeral=True)
        return

    existing = await _user_has_open_ticket(interaction.guild, interaction.user.id)
    if existing:
        embed = discord.Embed(
            title="⚠️ Aktif Ticket",
            description=t("support.already_has_ticket", lang="en").format(channel=existing.mention),
            color=discord.Color.orange(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    ticket_settings = get_data("server/ticket_settings") or {}
    ticket_category_id = ticket_settings.get("category_id")
    if not ticket_category_id:
        embed = discord.Embed(
            title="❌ Yapılandırılmamış",
            description=t("support.no_category_configured", lang="en"),
            color=discord.Color.red(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    ticket_category = interaction.guild.get_channel(int(ticket_category_id))
    if not ticket_category or not isinstance(ticket_category, discord.CategoryChannel):
        embed = discord.Embed(
            title="❌ Kategori Bulunamadı",
            description=t("support.category_not_found", lang="en"),
            color=discord.Color.red(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    tickets_data = get_data("server/tickets") or {}
    guild_id = str(interaction.guild.id)
    tickets_data.setdefault(guild_id, {})

    ticket_count = len([
        t for t in tickets_data[guild_id].values()
        if t.get("user_id") == interaction.user.id
    ]) + 1
    channel_name = f"ticket-{interaction.user.name}-{ticket_count}"[:100]

    try:
        channel = await ticket_category.create_text_channel(
            name=channel_name,
            overwrites=_staff_overwrites(interaction.guild, interaction.user, category),
            topic=f"Support | {interaction.user.name} | {category}",
        )
        tickets_data[guild_id][str(channel.id)] = {
            "user_id": interaction.user.id,
            "category": category,
            "status": "open",
            "claimed_by": None,
            "created_at": int(time.time()),
            "description": description,
        }
        set_data("server/tickets", tickets_data)

        embed = discord.Embed(
            title=f"🎫 {t('support.ticket_created', lang='en')}",
            description=t("support.ticket_welcome", lang="en").format(
                user=interaction.user.mention,
                category=CATEGORY_LABELS.get(category, category),
            ),
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Category", value=CATEGORY_LABELS.get(category, category), inline=True)
        embed.add_field(name="Status", value="🟢 Open", inline=True)
        embed.add_field(name="Issue Description", value=description[:1024], inline=False)
        embed.set_footer(text=f"Ticket ID: {channel.id}")

        await channel.send(embed=embed, view=TicketControlView())

        success = discord.Embed(
            title="✅ Ticket Oluşturuldu",
            description=t("support.ticket_created_success", lang="en").format(channel=channel.mention),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=success, ephemeral=True)
    except Exception as exc:
        err = discord.Embed(
            title="❌ Hata",
            description=f"Ticket oluşturulamadı: {exc}",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=err, ephemeral=True)


class TicketCategorySelect(discord.ui.Select):
    def __init__(self, *, custom_id: str = "ticket_panel:category"):
        super().__init__(
            placeholder=t("support.select_category", lang="en"),
            options=_category_select_options(),
            custom_id=custom_id,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketDescriptionModal(self.values[0]))


class TicketDescriptionModal(discord.ui.Modal, title="📝 Sorununuzu Açıklayın"):
    def __init__(self, category: str):
        super().__init__(timeout=300)
        self.category = category
        self.description = discord.ui.TextInput(
            label="Açıklama",
            style=discord.TextStyle.paragraph,
            placeholder="Sorununuzu detaylı yazın…",
            required=True,
            max_length=1000,
            min_length=10,
        )
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        await create_support_ticket(interaction, self.category, self.description.value)


class TicketPanelView(discord.ui.View):
    """Persistent — post in a channel via /ticket_panel."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect(custom_id="ticket_panel:category"))


class SupportCategoryView(discord.ui.View):
    """Ephemeral support picker (private room menu)."""

    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(TicketCategorySelect(custom_id="support:category_select"))


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.green, emoji="✋", custom_id="ticket:claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        tickets_data = get_data("server/tickets") or {}
        guild_id = str(interaction.guild.id)
        channel_id = str(interaction.channel.id)

        if guild_id not in tickets_data or channel_id not in tickets_data[guild_id]:
            await interaction.response.send_message("❌ Ticket data not found!", ephemeral=True)
            return

        ticket_info = tickets_data[guild_id][channel_id]
        if ticket_info.get("claimed_by"):
            claimed_user = interaction.guild.get_member(ticket_info["claimed_by"])
            await interaction.response.send_message(
                f"⚠️ Already claimed by {claimed_user.mention if claimed_user else 'Unknown'}",
                ephemeral=True,
            )
            return

        admins_data = get_data("server/admins") or {}
        user_perms = admins_data.get(str(interaction.user.id), [])
        if isinstance(user_perms, str):
            user_perms = [user_perms]

        if ticket_info["category"] == "bug" and "admin" not in user_perms:
            await interaction.response.send_message("❌ Only admins can claim bug tickets!", ephemeral=True)
            return
        if "admin" not in user_perms and "ticketAdmin" not in user_perms:
            await interaction.response.send_message("❌ You don't have permission to claim tickets!", ephemeral=True)
            return

        ticket_info["claimed_by"] = interaction.user.id
        tickets_data[guild_id][channel_id] = ticket_info
        set_data("server/tickets", tickets_data)

        account_data = get_user_data(interaction.user.id, "account")
        agent_name = account_data.get("name", interaction.user.display_name) if account_data else interaction.user.display_name

        embed = discord.Embed(
            description=f"✅ **Agent {agent_name}** görüşmeye katıldı.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.send_message(embed=embed)

        button.disabled = True
        button.label = f"Claimed by {agent_name}"
        try:
            if interaction.message:
                await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, emoji="🔒", custom_id="ticket:close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        tickets_data = get_data("server/tickets") or {}
        guild_id = str(interaction.guild.id)
        channel_id = str(interaction.channel.id)

        if guild_id not in tickets_data or channel_id not in tickets_data[guild_id]:
            await interaction.response.send_message("❌ Ticket data not found!", ephemeral=True)
            return

        ticket_info = tickets_data[guild_id][channel_id]
        admins_data = get_data("server/admins") or {}
        user_perms = admins_data.get(str(interaction.user.id), [])
        if isinstance(user_perms, str):
            user_perms = [user_perms]

        is_owner = interaction.user.id == ticket_info["user_id"]
        is_staff = "admin" in user_perms or "ticketAdmin" in user_perms
        if not is_owner and not is_staff:
            await interaction.response.send_message(
                "❌ Only ticket owner or staff can close this ticket!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🔒 Close Ticket",
            description="Are you sure you want to close this ticket?",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=TicketCloseConfirmView(channel_id, guild_id),
            ephemeral=True,
        )


class TicketCloseConfirmView(discord.ui.View):
    def __init__(self, channel_id: str, guild_id: str):
        super().__init__(timeout=60)
        self.channel_id = channel_id
        self.guild_id = guild_id

    @discord.ui.button(label="Yes, Close", style=discord.ButtonStyle.danger, emoji="🔒")
    async def confirm_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            tickets_data = get_data("server/tickets") or {}
            if self.guild_id not in tickets_data or self.channel_id not in tickets_data[self.guild_id]:
                await interaction.response.send_message("❌ Ticket not found.", ephemeral=True)
                return

            ticket_info = tickets_data[self.guild_id][self.channel_id]
            user_id = str(ticket_info["user_id"])
            channel = interaction.guild.get_channel(int(self.channel_id))

            if channel:
                messages_data = []
                async for message in channel.history(limit=200, oldest_first=True):
                    messages_data.append({
                        "author": message.author.name,
                        "author_id": message.author.id,
                        "content": message.content,
                        "timestamp": message.created_at.isoformat(),
                        "attachments": [att.url for att in message.attachments],
                    })

                ticket_history = get_user_data(user_id, "ticket_history") or []
                ticket_history.append({
                    "ticket_id": self.channel_id,
                    "category": ticket_info.get("category", "unknown"),
                    "description": ticket_info.get("description", ""),
                    "created_at": ticket_info.get("created_at", int(time.time())),
                    "closed_at": int(time.time()),
                    "claimed_by": ticket_info.get("claimed_by"),
                    "messages": messages_data,
                })
                set_user_data(user_id, "ticket_history", ticket_history)

            del tickets_data[self.guild_id][self.channel_id]
            set_data("server/tickets", tickets_data)

            await interaction.response.send_message("🔒 Closing ticket…", ephemeral=True)
            if channel:
                await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except Exception as exc:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {exc}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {exc}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Cancelled.", ephemeral=True)
        self.stop()


def register_ticket_views(bot: discord.Client) -> None:
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())

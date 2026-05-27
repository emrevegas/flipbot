import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional, Dict
import time
import asyncio
import re
from modules.database import get_data, set_data, get_server_data, get_user_data, set_user_data, check_permission
from modules.translator import t
from modules.utils import *
from modules.player import Player
import modules.bonus as bonus_engine
import modules.promo as promo_engine
import modules.race as race_engine
from modules.private_room_hub import (
    deposit_gate_message,
    is_play_hub_channel,
    owner_room_for_interaction,
    reset_menu_message,
)

class CreateReferralCodeModal(discord.ui.Modal, title="Create Referral Code"):
    """Referral kodu oluşturma modalı"""
    
    code_input = discord.ui.TextInput(
        label="Referral Code",
        placeholder="Enter 4-12 alphanumeric characters",
        min_length=4,
        max_length=12,
        required=True
    )
    
    def __init__(self, user_id: str):
        super().__init__()
        self.user_id = user_id
    
    async def on_submit(self, interaction: discord.Interaction):
        """Modal gönderildiğinde"""
        code = self.code_input.value.strip().upper()
        
        # Kod formatını kontrol et (sadece harf ve rakam)
        if not re.match(r'^[A-Z0-9]+$', code):
            embed = discord.Embed(
                title="❌ Invalid Code",
                description=t("referral.invalid_code_format", lang="en"),
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        referrals_data = get_data("server/referrals")
        
        # Kullanıcının zaten kodu var mı kontrol et
        if self.user_id in referrals_data and "code" in referrals_data[self.user_id]:
            existing_code = referrals_data[self.user_id]["code"]
            embed = discord.Embed(
                title="❌ Code Already Exists",
                description=t("referral.code_exists", lang="en").format(code=existing_code),
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Kod daha önce alınmış mı kontrol et (case-insensitive)
        for user_data in referrals_data.values():
            if user_data.get("code", "").upper() == code:
                embed = discord.Embed(
                    title="❌ Code Taken",
                    description=t("referral.code_already_taken", lang="en"),
                    color=discord.Color.red()
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Yeni referral kodu oluştur
        referrals_data[self.user_id] = {
            "code": code,
            "commission_rate": 5,  # Default %5
            "total_earned": 0,
            "available_balance": 0,
            "today_earned": 0,
            "referred_users": [],
            "referral_earnings": {},
            "created_at": int(time.time())
        }
        set_data("server/referrals", referrals_data)
        
        embed = discord.Embed(
            title=t("referral.code_created_title", lang="en"),
            description=t("referral.code_created_description", lang="en").format(
                code=code,
                rate=5
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="Vegas Casino | Referral System")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class PrivateRoomButton(discord.ui.Button):
    """Özel oda oluşturma butonu"""
    
    def __init__(self):
        super().__init__(
            label=t("private_rooms.button_create", lang="en"),
            style=discord.ButtonStyle.primary,
            emoji="🏠",
            custom_id="private_room:create"
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Buton tıklandığında çalışır"""
        await interaction.response.defer(ephemeral=True)
        
        # Kullanıcı kayıt kontrolü
        account_data = get_user_data(interaction.user.id, "account")
        if not account_data:
            embed = discord.Embed(
                title="❌ Not Registered",
                description=t("private_rooms.not_registered", user_id=str(interaction.user.id)),
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        from modules.utils import get_user_lang

        lang = get_user_lang(interaction.user.id)
        gate = deposit_gate_message(interaction.user.id, lang=lang)
        if gate:
            embed = discord.Embed(
                title="❌ Deposit Required",
                description=gate,
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Mevcut odaları kontrol et
        rooms_data = get_data("server/private_rooms")
        user_id = interaction.user.id
        guild_id = str(interaction.guild.id)
        
        # Kullanıcının zaten bir odası var mı? (channel_id üzerinden kontrol)
        if guild_id in rooms_data:
            for channel_id, room_info in rooms_data[guild_id].items():
                if int(room_info.get("owner")) == user_id:
                    channel = interaction.guild.get_channel(int(channel_id))
                    
                    if channel:
                        embed = discord.Embed(
                            title=t("private_rooms.already_has_room_title", lang="en"),
                            description=t("private_rooms.already_has_room_description", lang="en").format(
                                channel=channel.mention
                            ),
                            color=discord.Color.orange()
                        )
                        await interaction.followup.send(embed=embed, ephemeral=True)
                        return
                    else:
                        # Kanal silinmiş, veriyi temizle
                        del rooms_data[guild_id][channel_id]
                        set_data("server/private_rooms", rooms_data)
                        break
        
        # Özel oda kategorisini al
        server_data = get_server_data(guild_id)
        category_id = server_data.get("private_category_id")
        
        if not category_id:
            embed = discord.Embed(
                title=t("private_rooms.no_category_title", lang="en"),
                description=t("private_rooms.no_category_description", lang="en"),
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        category = interaction.guild.get_channel(int(category_id))
        if not category or not isinstance(category, discord.CategoryChannel):
            embed = discord.Embed(
                title=t("private_rooms.no_category_title", lang="en"),
                description=t("private_rooms.no_category_description", lang="en"),
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        try:
            # Özel kanalı oluştur
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(
                    read_messages=False
                ),
                interaction.user: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    manage_channels=True,
                    manage_permissions=True,
                    manage_messages=True,
                    embed_links=True,
                    attach_files=True,
                    read_message_history=True,
                    mention_everyone=True,
                    add_reactions=True
                ),
                interaction.guild.me: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    manage_channels=True,
                    manage_permissions=True
                )
            }
            
            channel = await category.create_text_channel(
                name=f"🏠│{interaction.user.name}-room",
                overwrites=overwrites,
                topic=f"🏠 {interaction.user.name} adlı kullanıcının özel odası | Oluşturulma: {discord.utils.format_dt(discord.utils.utcnow(), 'F')}"
            )
            
            # Veriyi kaydet (yeni yapı: channel_id -> room_data)
            current_time = int(time.time())
            if guild_id not in rooms_data:
                rooms_data[guild_id] = {}
            
            rooms_data[guild_id][str(channel.id)] = {
                "owner": user_id,
                "created_at": current_time,
                "last_activity": current_time,
                "owner_name": interaction.user.name,
                "users": []  # Eklenen kullanıcılar listesi
            }
            set_data("server/private_rooms", rooms_data)
            
            # Hoş geldin mesajı gönder
            from modules.utils import get_user_lang

            welcome_view = build_welcome_menu_layout(
                str(interaction.guild.id),
                str(channel.id),
                lang=get_user_lang(interaction.user.id),
                owner_mention=interaction.user.mention,
                avatar_url=str(interaction.user.display_avatar.url),
            )
            await channel.send(view=welcome_view)
            
            # Başarılı oluşturma mesajı
            success_embed = discord.Embed(
                title=t("private_rooms.room_created_title", lang="en"),
                description=t("private_rooms.room_created_description", lang="en").format(
                    channel=channel.mention,
                    timestamp=current_time
                ),
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            success_embed.set_footer(text="Vegas Casino | Özel Oda Sistemi")
            await interaction.followup.send(embed=success_embed, ephemeral=True)
            
        except Exception as e:
            error_embed = discord.Embed(
                title=t("private_rooms.error_creating_title", lang="en"),
                description=t("private_rooms.error_creating_description", lang="en").format(
                    error=str(e)
                ),
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)


class EntertainmentSelect(discord.ui.Select):
    """Eğlence menüsü - Oyunlar, İstatistikler ve Referral"""
    
    def __init__(self, lang: str = "en", *, hub_mode: bool = False):
        self._hub_mode = hub_mode
        from modules.ui_v2 import clip_select_description

        self._lang = lang
        _d = clip_select_description
        options = [
            discord.SelectOption(
                label=t("private_rooms.games_option", lang=lang),
                description=_d("Play available games"),
                emoji="🎲",
                value="games"
            ),
            discord.SelectOption(
                label=t("private_rooms.statistics_option", lang=lang),
                description=_d("View your gaming statistics"),
                emoji="📊",
                value="statistics"
            ),
            discord.SelectOption(
                label=t("referral.menu_option", lang=lang),
                description=_d(t("referral.menu_description", lang=lang)),
                emoji="🎁",
                value="referral"
            ),
            discord.SelectOption(
                label=t("support.menu_option", lang=lang),
                description=_d(t("support.menu_description", lang=lang)),
                emoji="🎫",
                value="support"
            ),
            discord.SelectOption(
                label=t("rakeback.menu_option", lang=lang),
                description=_d(t("rakeback.menu_description", lang=lang)),
                emoji="💸",
                value="rakeback"
            ),
            discord.SelectOption(
                label="Promo / Free Bet",
                description=_d("Redeem a promo code or view your free-bet status"),
                emoji="🎟️",
                value="promo_code"
            ),
        ]
        
        super().__init__(
            placeholder=t("private_rooms.placeholder_entertainment", lang=lang),
            options=options,
            custom_id="private_room:entertainment",
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Entertainment seçimi"""
        # Acknowledge the interaction first so Discord doesn't time out
        await interaction.response.defer(ephemeral=True)

        from modules.utils import get_user_lang

        lang = get_user_lang(interaction.user.id)
        ctx, err = owner_room_for_interaction(interaction, lang=lang)
        if err:
            embed = discord.Embed(
                title="❌ Access Denied",
                description=err,
                color=discord.Color.red(),
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        await reset_menu_message(
            interaction,
            ctx.guild_id,
            ctx.channel_id,
            hub_mode=self._hub_mode,
        )

        guild_id = ctx.guild_id
        user_id = interaction.user.id
        room_channel = ctx.channel
        
        if self.values[0] == "games":
            from cogs.games import GameSession
            from modules.games_hub_v2 import build_game_menu_layout
            from modules.utils import get_user_lang

            existing_session = GameSession.find_user_session(user_id)
            if existing_session:
                session_msg_id, last_activity = existing_session
                expire_ts = last_activity + 60
                embed = discord.Embed(
                    title=t("games.errors.session_already_active_title", lang=lang),
                    description=(
                        t("games.errors.session_already_active_desc", lang=lang)
                        + f"\n\n⏳ {t('games.errors.session_expires_in', lang=lang)} <t:{expire_ts}:R>"
                    ),
                    color=discord.Color.red()
                )
                embed.set_footer(text=t("games.footer", lang=lang))
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            if not room_channel:
                return await interaction.followup.send(
                    t("private_rooms.no_room_for_hub", lang=lang, user_id=str(user_id)),
                    ephemeral=True,
                )
            message = await room_channel.send(content="\u200b")
            msg_id = str(message.id)
            GameSession.create_session(
                message_id=msg_id,
                user_id=interaction.user.id,
                channel_id=room_channel.id,
                guild_id=interaction.guild.id if interaction.guild else None,
            )
            layout = build_game_menu_layout(msg_id, interaction.user, lang)
            await message.edit(content=None, view=layout)
            if ctx.is_play_hub and room_channel.id == interaction.channel.id:
                hint_key = "private_rooms.hub_action_in_play"
            else:
                hint_key = "private_rooms.hub_action_in_room"
            hint = discord.Embed(
                description=t(
                    hint_key,
                    lang=lang,
                    channel=room_channel.mention,
                ),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=hint, ephemeral=True)
            
        elif self.values[0] == "statistics":
            await self.show_statistics(interaction)
            
        elif self.values[0] == "referral":
            # Referral sistemini göster
            await self.show_referral_system(interaction)
        
        elif self.values[0] == "support":
            from modules.ticket_system import SupportCategoryView, build_ticket_panel_embed

            await interaction.response.send_message(
                embed=build_ticket_panel_embed(),
                view=SupportCategoryView(),
                ephemeral=True,
            )

        elif self.values[0] == "rakeback":
            await self.show_rakeback_info(interaction)

        elif self.values[0] == "promo_code":
            await self.show_promo_panel(interaction)

    async def show_promo_panel(self, interaction: discord.Interaction):
        """Show the promo code redeem panel / active freebet status."""
        from modules.utils import get_user_lang
        from cogs.room_panels_v2 import build_active_promo_layout, build_promo_redeem_layout
        from modules.ui_v2 import send_ephemeral

        user_id = interaction.user.id
        lang = get_user_lang(user_id)
        active = promo_engine.get_promo_display_state(user_id)
        if active:
            await send_ephemeral(interaction, build_active_promo_layout(user_id, active, interaction.user))
        else:
            await send_ephemeral(interaction, build_promo_redeem_layout(user_id, lang))

    async def show_rakeback_info(self, interaction: discord.Interaction):
        """Show the user's rakeback status and withdrawal button."""
        from database import db as flip_db
        from modules.rakeback_engine import get_min_withdrawal, resolve_tier
        from modules.utils import get_user_lang

        user_id = interaction.user.id
        lang = get_user_lang(user_id)
        user = await flip_db.get_user(user_id)
        accumulated = int(float((user or {}).get("rakeback_accumulated", 0)))
        total_earned = int(float((user or {}).get("rakeback_accumulated", 0)) + float((user or {}).get("rakeback_total_claimed", 0)))
        total_wagered = int(float((user or {}).get("total_wagered", 0)))
        min_withdrawal = get_min_withdrawal()

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is not None:
            from modules.rakeback_roles import sync_rakeback_tier_roles
            await sync_rakeback_tier_roles(member, float(total_wagered))

        tier = resolve_tier(float(total_wagered))
        best_tier = {
            "role_id": tier.get("role_id"),
            "role_name": tier.get("name"),
            "percentage": tier.get("percentage", tier.get("rate", 0) * 100),
        } if float(tier.get("rate", 0)) > 0 else None

        from cogs.room_panels_v2 import build_rakeback_layout
        from modules.ui_v2 import send_ephemeral

        can_withdraw = accumulated >= min_withdrawal
        view = build_rakeback_layout(
            user_id,
            best_tier=best_tier,
            accumulated=accumulated,
            total_earned=total_earned,
            min_withdrawal=min_withdrawal,
            total_wagered=total_wagered,
            can_withdraw=can_withdraw,
            lang=lang,
        )
        await send_ephemeral(interaction, view)
    
    async def show_referral_system(self, interaction: discord.Interaction):
        """Referral sistemini göster"""
        from modules.player import Player
        
        user_id = str(interaction.user.id)
        referrals_data = get_data("server/referrals")
        
        # Kullanıcının referral bilgilerini al
        user_referral = referrals_data.get(user_id, {})
        
        from modules.utils import get_user_lang
        from cogs.room_panels_v2 import build_referral_create_layout, build_referral_dashboard_layout
        from modules.ui_v2 import send_ephemeral

        lang = get_user_lang(int(user_id))

        if not user_referral or "code" not in user_referral:
            await send_ephemeral(interaction, build_referral_create_layout(user_id, lang))
            return
        
        # Referral bilgilerini hazırla
        code = user_referral.get("code", "N/A")
        total_referrals = len(user_referral.get("referred_users", []))
        commission_rate = user_referral.get("commission_rate", 5)
        
        # Kazançları hesapla
        total_earned = user_referral.get("total_earned", 0)
        today_earned = user_referral.get("today_earned", 0)
        available_balance = user_referral.get("available_balance", 0)
        
        await send_ephemeral(
            interaction,
            build_referral_dashboard_layout(
                user_id,
                code=code,
                total_referrals=total_referrals,
                commission_rate=commission_rate,
                available_balance=available_balance,
                today_earned=today_earned,
                total_earned=total_earned,
                lang=lang,
            ),
        )

    async def show_statistics(self, interaction: discord.Interaction):
        """Show a rich statistics panel with tab navigation."""
        from modules.database import get_user_stats
        from modules.player import Player
        from modules.utils import get_user_lang

        user_id = interaction.user.id
        lang = get_user_lang(user_id)
        stats = get_user_stats(user_id) or {}
        player = Player(user_id)

        if not stats:
            from modules.ui_v2 import build_detail_panel, send_ephemeral

            await send_ephemeral(
                interaction,
                build_detail_panel(
                    title=t("player_stats.no_stats_title", lang=lang),
                    body=t("player_stats.no_stats", lang=lang),
                    accent=0x95A5A6,
                    emoji="📊",
                    footer=t("player_stats.footer_stats", lang=lang),
                ),
            )
            return

        embed = PlayerStatsView.build_overview_embed(interaction.user, stats, player, lang=lang)
        view = PlayerStatsView(user_id, stats, player, interaction.user, lang=lang)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
# Player Statistics View
# ─────────────────────────────────────────────────────────────────────────────

class PlayerStatsView(discord.ui.View):
    """Rich statistics panel with Overview / Game Breakdown / Rakeback tabs."""

    def __init__(self, user_id: int, stats: dict, player, member: discord.Member, lang: str = "en", viewer_id: int | None = None):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.viewer_id = viewer_id or user_id
        self.stats = stats
        self.player = player
        self.member = member
        self.lang = lang
        self.btn_overview.label = t("player_stats.btn_overview", lang=lang)
        self.btn_games.label = t("player_stats.btn_games", lang=lang)
        self.btn_rakeback.label = t("player_stats.btn_rakeback", lang=lang)
        self.btn_refresh.label = t("player_stats.btn_refresh", lang=lang)

    # ── Tab builders ──────────────────────────────────────────────────────

    @staticmethod
    def build_overview_embed(member: discord.Member, stats: dict, player, lang: str = "en") -> discord.Embed:
        total_plays = stats.get('total_plays', 0)
        wins = stats.get('wins', 0)
        losses = stats.get('losses', 0)
        ties = stats.get('ties', 0)
        total_wagered = stats.get('total_wagered', 0)
        total_profit = stats.get('total_profit', 0)
        total_deposit = stats.get('total_deposit', 0)
        total_withdraw = stats.get('total_withdraw', 0)
        net_balance = total_deposit - total_withdraw
        real_balance = player.get_balance('real')
        demo_balance = player.get_balance('demo')
        win_rate = f"{(wins / total_plays * 100):.1f}%" if total_plays else "0%"

        # Win/loss bar (10 blocks)
        if total_plays > 0:
            filled = round(wins / total_plays * 10)
            bar = "🟩" * filled + "⬛" * (10 - filled)
        else:
            bar = "⬛" * 10

        profit_arrow = "📈" if net_balance >= 0 else "📉"
        profit_color = 0x2ecc71 if net_balance >= 0 else 0xe74c3c

        embed = discord.Embed(
            title=t("player_stats.title", lang=lang, name=member.display_name),
            color=profit_color
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(
            name=t("player_stats.performance_field", lang=lang),
            value=t("player_stats.performance_value", lang=lang, plays=total_plays, wins=wins, losses=losses, ties=ties),
            inline=True
        )
        embed.add_field(
            name=t("player_stats.financials_field", lang=lang),
            value=t("player_stats.financials_value", lang=lang,
                    wagered=format_balance(total_wagered, 'real'),
                    arrow=profit_arrow,
                    net=format_balance(abs(net_balance), 'real'),
                    balance=format_balance(real_balance, 'real'),
                    demo=format_balance(demo_balance, 'demo')),
            inline=True
        )
        embed.add_field(
            name=t("player_stats.win_rate_field", lang=lang, wr=win_rate),
            value=bar,
            inline=False
        )
        embed.set_footer(text=t("player_stats.footer_overview", lang=lang))
        return embed

    @staticmethod
    def build_breakdown_embed(member: discord.Member, stats: dict, lang: str = "en") -> discord.Embed:
        game_breakdown = stats.get('games', {})
        embed = discord.Embed(
            title=t("player_stats.breakdown_title", lang=lang, name=member.display_name),
            color=0x3498db
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        if not game_breakdown:
            embed.description = t("player_stats.no_game_data", lang=lang)
        else:
            games_meta = get_data('server/games') or {}
            sorted_games = sorted(
                game_breakdown.items(),
                key=lambda x: x[1].get('plays', 0),
                reverse=True
            )
            for i, (game_key, gd) in enumerate(sorted_games):
                plays = gd.get('plays', 0)
                w = gd.get('wins', 0)
                l = gd.get('losses', 0)
                ties_g = gd.get('ties', 0)
                profit = gd.get('total_profit', 0)
                wr = f"{(w / plays * 100):.0f}%" if plays else "0%"
                medal = ["🥇", "🥈", "🥉"][i] if i < 3 else "🔵"
                meta = games_meta.get(game_key.lower(), {})
                game_emoji = meta.get('emoji', '🎮')
                game_display = meta.get('name', game_key.title())
                profit_sign = "+" if profit >= 0 else ""
                embed.add_field(
                    name=f"{medal} {game_display}",
                    value=(
                        t("player_stats.plays_wr", lang=lang, plays=plays, wr=wr) + "\n"
                        f"W {w}  ·  L {l}  ·  T {ties_g}\n"
                        + t("player_stats.net", lang=lang, net=f"{profit_sign}{format_balance(profit, 'real')}")
                    ),
                    inline=True
                )
        embed.set_footer(text=t("player_stats.footer_games", lang=lang))
        return embed

    @staticmethod
    def build_rakeback_embed(member: discord.Member, player, lang: str = "en") -> discord.Embed:
        from modules.database import get_user_data as _gud
        rakeback_data = player.get_rakeback_data()
        accumulated = int(rakeback_data.get('accumulated', 0))
        total_earned = int(rakeback_data.get('total_earned', 0))
        from modules.rakeback_engine import resolve_tier
        from modules.rakeback_roles import get_flip_total_wagered

        settings = get_data('server/rakeback_settings') or {}
        tiers = settings.get('tiers', [])
        min_withdrawal = int(settings.get('min_withdrawal', 100))
        stats = _gud(member.id, 'stats') or {}
        total_wagered = max(
            int(stats.get('total_wagered', 0)),
            int(get_flip_total_wagered(member.id)),
        )
        tier = resolve_tier(float(total_wagered))
        best_tier = None
        if float(tier.get('rate', 0)) > 0:
            best_tier = {
                'role_id': tier.get('role_id'),
                'role_name': tier.get('name'),
                'percentage': tier.get('percentage', tier.get('rate', 0) * 100),
                'min_wagered': tier.get('min_wagered', 0),
            }

        # ── Tier progression ────────────────────────────────────────────────
        sorted_tiers = sorted(tiers, key=lambda ti: int(ti.get('min_wagered', 0)))
        next_tier = next(
            (ti for ti in sorted_tiers if total_wagered < int(ti.get('min_wagered', 0))),
            None
        )
        tier_start = int(best_tier.get('min_wagered', 0)) if best_tier else 0
        tier_end = int(next_tier.get('min_wagered', 0)) if next_tier else None

        # ── Withdrawal progress ─────────────────────────────────────────────
        progress_pct = min(accumulated / min_withdrawal, 1.0) if min_withdrawal > 0 else 1.0
        filled = round(progress_pct * 10)
        bar = "🟩" * filled + "⬛" * (10 - filled)
        pct_label = f"{progress_pct * 100:.0f}%"

        embed = discord.Embed(
            title=t("player_stats.rakeback_title", lang=lang, name=member.display_name),
            color=0xf39c12
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name=t("player_stats.your_tier_field", lang=lang),
            value=t("player_stats.tier_value", lang=lang, role=best_tier['role_id'], pct=best_tier['percentage']) if best_tier else t("player_stats.no_tier", lang=lang),
            inline=True
        )
        embed.add_field(
            name=t("player_stats.pending_field", lang=lang),
            value=format_balance(accumulated, 'real'),
            inline=True
        )
        embed.add_field(
            name=t("player_stats.all_time_field", lang=lang),
            value=format_balance(total_earned, 'real'),
            inline=True
        )
        embed.add_field(
            name=t("player_stats.withdrawal_field", lang=lang, pct=pct_label),
            value=f"{bar}\n**{format_balance(accumulated, 'real')}** / **{format_balance(min_withdrawal, 'real')}**",
            inline=False
        )

        # ── Next tier progress ──────────────────────────────────────────────
        if next_tier and tier_end and tier_end > tier_start:
            wagered_in_range = max(total_wagered - tier_start, 0)
            range_size = tier_end - tier_start
            tier_pct = min(wagered_in_range / range_size, 1.0)
            tier_filled = round(tier_pct * 10)
            tier_bar = "🟦" * tier_filled + "⬛" * (10 - tier_filled)
            tier_pct_label = f"{tier_pct * 100:.0f}%"
            remaining = tier_end - total_wagered
            embed.add_field(
                name=t("player_stats.next_tier_field", lang=lang, pct=tier_pct_label),
                value=t("player_stats.next_tier_value", lang=lang,
                        role=next_tier['role_id'],
                        pct=next_tier.get('percentage', '?'),
                        bar=tier_bar,
                        wagered=format_balance(total_wagered, 'real'),
                        required=format_balance(tier_end, 'real'),
                        remaining=format_balance(remaining, 'real')),
                inline=False
            )
        elif not next_tier and best_tier:
            embed.add_field(
                name=t("player_stats.max_tier_field", lang=lang),
                value=t("player_stats.max_tier_value", lang=lang),
                inline=False
            )

        embed.set_footer(text=t("player_stats.footer_rakeback", lang=lang))
        return embed

    # ── Tab helpers ───────────────────────────────────────────────────────

    def _set_active_tab(self, active: str):
        """Set active tab button to primary, others to secondary."""
        tab_map = {
            "overview": self.btn_overview,
            "games": self.btn_games,
            "rakeback": self.btn_rakeback,
        }
        for key, btn in tab_map.items():
            btn.style = discord.ButtonStyle.primary if key == active else discord.ButtonStyle.secondary

    # ── Buttons ───────────────────────────────────────────────────────────

    @discord.ui.button(label="📊 Overview", style=discord.ButtonStyle.primary, row=0)
    async def btn_overview(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id:
            return await interaction.response.send_message(t("player_stats.not_your_panel", lang=self.lang), ephemeral=True)
        self._set_active_tab("overview")
        embed = self.build_overview_embed(self.member, self.stats, self.player, self.lang)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🎮 Games", style=discord.ButtonStyle.secondary, row=0)
    async def btn_games(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id:
            return await interaction.response.send_message(t("player_stats.not_your_panel", lang=self.lang), ephemeral=True)
        self._set_active_tab("games")
        embed = self.build_breakdown_embed(self.member, self.stats, self.lang)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="💸 Rakeback", style=discord.ButtonStyle.secondary, row=0)
    async def btn_rakeback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id:
            return await interaction.response.send_message(t("player_stats.not_your_panel", lang=self.lang), ephemeral=True)
        self._set_active_tab("rakeback")
        embed = self.build_rakeback_embed(self.member, self.player, self.lang)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.success, row=1)
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id:
            return await interaction.response.send_message(t("player_stats.not_your_panel", lang=self.lang), ephemeral=True)
        from modules.database import get_user_stats
        self.stats = get_user_stats(self.user_id) or {}
        self.player = Player(self.user_id)
        self._set_active_tab("overview")
        embed = self.build_overview_embed(self.member, self.stats, self.player, self.lang)
        await interaction.response.edit_message(embed=embed, view=self)


class GameSelect(discord.ui.Select):
    """Oyun seçim menüsü"""
    
    def __init__(self):
        games_data = get_data("server/games")
        enabled_games = {k: v for k, v in games_data.items() if v.get("enabled", False)}
        
        options = []
        for game_id, game_info in enabled_games.items():
            options.append(
                discord.SelectOption(
                    label=game_info["name"],
                    description=f"Min: {format_balance(game_info['min_bet'], 'real')} | Max: {format_balance(game_info['max_bet'], 'real')}",
                    emoji=game_info["emoji"],
                    value=game_id
                )
            )
        
        super().__init__(
            placeholder=t("private_rooms.select_game", lang="en"),
            options=options,
            custom_id="private_room:game_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Oyun seçildiğinde"""
        game_id = self.values[0]
        games_data = get_data("server/games")
        game_info = games_data.get(game_id)
        
        if not game_info:
            await interaction.response.send_message("❌ Game not found!", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"{game_info['emoji']} {game_info['name']}",
            description="Game will start soon!\n\n*This feature is under development.*",
            color=discord.Color.blue()
        )
        embed.add_field(name="Min Bet", value=format_balance(game_info['min_bet'], 'real'), inline=True)
        embed.add_field(name="Max Bet", value=format_balance(game_info['max_bet'], 'real'), inline=True)
        embed.add_field(name="House Edge", value=f"{game_info['house_edge']}%", inline=True)
        
        # Parent view'i resetle (GameMenuView için)
        # Not: Bu select menü farkt bir context'te kullanılıyorsa uygun view'i belirt
        await interaction.response.send_message(embed=embed, ephemeral=True)


class FinanceSelect(discord.ui.Select):
    """Finans menüsü - Deposit ve Withdraw"""
    
    def __init__(self, lang: str = "en", *, hub_mode: bool = False):
        self._hub_mode = hub_mode
        options = [
            discord.SelectOption(
                label="Crypto Deposit",
                description="Deposit SOL or LTC automatically",
                emoji="🔐",
                value="crypto_deposit"
            ),
            discord.SelectOption(
                label="Crypto Withdraw",
                description="Withdraw your balance as SOL or LTC",
                emoji="💸",
                value="crypto_withdraw"
            ),
            discord.SelectOption(
                label=t("private_rooms.deposit_option", lang=lang),
                description="Deposit funds to your account with in-game methods",
                emoji="💳",
                value="deposit"
            ),
            discord.SelectOption(
                label=t("private_rooms.withdraw_option", lang=lang),
                description="Request a withdrawal with in-game methods",
                emoji="🏦",
                value="withdraw"
            ),
            discord.SelectOption(
                label="Exchange Rates",
                description="View current coin exchange rates",
                emoji="💱",
                value="exchange_rates"
            ),
            
        ]
        
        super().__init__(
            placeholder=t("private_rooms.placeholder_finance", lang=lang),
            options=options,
            custom_id="private_room:finance",
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Finance seçimi"""
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        player = Player(user_id)
        ctx, err = owner_room_for_interaction(interaction, lang=player.language)
        if err:
            embed = discord.Embed(
                title="❌ Access Denied",
                description=err,
                color=discord.Color.red(),
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        await reset_menu_message(
            interaction,
            ctx.guild_id,
            ctx.channel_id,
            hub_mode=self._hub_mode,
        )
        guild_id = ctx.guild_id
        
        if self.values[0] == "deposit":
            # Get active payment methods
            methods_dict = get_data("server/payment_methods") or {}
            active_methods = {k: v for k, v in methods_dict.items() if v.get("enabled", False)}
            
            if not active_methods:
                from modules.ui_v2 import error_panel, send_ephemeral
                return await send_ephemeral(
                    interaction,
                    error_panel("No Payment Methods", "No payment methods are currently available."),
                )

            from modules.ui_v2 import send_ephemeral
            view = build_deposit_method_layout(str(user_id), active_methods, player.language)
            await send_ephemeral(interaction, view)
            
        elif self.values[0] == "withdraw":
            methods_dict = get_data("server/payment_methods") or {}
            active_methods = {k: v for k, v in methods_dict.items() if v.get("enabled", True)}

            if not active_methods:
                embed = discord.Embed(
                    title="❌ No Payment Methods",
                    description="No payment methods are currently available.",
                    color=discord.Color.red()
                )
                return await interaction.followup.send(embed=embed, ephemeral=True)

            guild_id = str(interaction.guild.id)
            server_data = get_server_data(guild_id)
            withdraw_channel_id = server_data.get("withdraw_channel")
            min_withdrawal, min_label = _get_effective_min_withdrawal(user_id, server_data)

            # Gold method only needs deposit_category, not withdraw_channel
            non_gold_methods = {k: v for k, v in active_methods.items() if k != "gold"}
            if non_gold_methods and not withdraw_channel_id:
                # Only block if user has non-Gold methods selected; we'll warn at submit time
                pass

            balance = player.get_balance("real")
            if balance < min_withdrawal:
                embed = discord.Embed(
                    title="❌ Insufficient Balance",
                    description=f"Minimum withdrawal is **{format_balance(min_withdrawal, 'real')}**. Your balance: **{format_balance(balance, 'real')}**.",
                    color=discord.Color.red()
                )
                return await interaction.followup.send(embed=embed, ephemeral=True)

            req_wager, wagered_since, wager_remaining = _get_withdraw_wager_requirement(user_id, server_data)

            wager_info = ""
            if req_wager > 0:
                wg_pct = int(wagered_since / req_wager * 100) if req_wager else 0
                wager_info = (
                    f"\n🎲 Wager Requirement: **{format_balance(wagered_since, 'real')}** / **{format_balance(req_wager, 'real')}** ({wg_pct}%)"
                )
                if wager_remaining > 0:
                    wager_info += f"\n⚠️ Still needed: **{format_balance(wager_remaining, 'real')}**"
                else:
                    wager_info += "\n✅ Wager requirement met!"

            from cogs.room_panels_v2 import build_withdraw_method_layout
            from modules.ui_v2 import send_ephemeral

            layout = build_withdraw_method_layout(
                str(user_id),
                active_methods,
                min_withdrawal,
                balance,
                wager_info,
            )
            await send_ephemeral(interaction, layout)

        elif self.values[0] == "exchange_rates":
            from modules.utils import get_user_lang
            lang          = get_user_lang(user_id)
            rates_data    = get_data("server/exchange_rates") or {}
            server_data   = get_data("server/server") or {}
            coin_emoji    = server_data.get("coin_emoji", "🪙")
            coin_usd_rate = rates_data.get("coin_usd_rate", 0.10)
            custom_rates  = rates_data.get("custom_rates", [])

            from cogs.room_panels_v2 import build_exchange_rates_layout
            from modules.ui_v2 import send_ephemeral

            layout = build_exchange_rates_layout(user_id, lang)
            await send_ephemeral(interaction, layout)

        elif self.values[0] == "crypto_deposit":
            from cogs.crypto_deposit import start_crypto_deposit_flow
            from modules.utils import get_user_lang
            await start_crypto_deposit_flow(
                interaction, user_id, get_user_lang(user_id)
            )

        elif self.values[0] == "crypto_withdraw":
            from cogs.crypto_withdraw import CryptoWithdraw
            cog = interaction.client.cogs.get("CryptoWithdraw")
            if cog:
                await cog.start_withdrawal(interaction)
            else:
                await interaction.followup.send("❌ Crypto withdraw module not loaded.", ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
# Exchange Rates — Coin Calculator
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_num(n) -> str:
    """Format number with dot thousands separator: 2000 → 2.000, 50000 → 50.000"""
    try:
        if isinstance(n, float) and n % 1 != 0:
            # Has meaningful decimal — use comma as decimal, dot as thousands
            s = f"{n:,.2f}"
            return s.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{int(n):,}".replace(",", ".")
    except (ValueError, TypeError):
        return str(n)


class ExchangeRateMethodSelect(discord.ui.Select):
    """Kur seçim menüsü (CoinCalculatorModal içinde Label ile kullanılır)"""

    def __init__(self, lang: str = "en"):
        rates_data   = get_data("server/exchange_rates") or {}
        server_data  = get_data("server/server") or {}
        coin_emoji   = server_data.get("coin_emoji", "🪙")
        coin_usd     = rates_data.get("coin_usd_rate", 0.10)
        custom_rates = rates_data.get("custom_rates", [])

        options = [
            discord.SelectOption(
                label=t("exchange_rates.usd_select_label", lang=lang),
                description=t("exchange_rates.usd_select_desc", lang=lang, rate=f"{coin_usd:.4g}"),
                emoji="💵",
                value="__usd__",
            )
        ]
        for r in custom_rates[:24]:
            r_emoji  = r.get("emoji",  coin_emoji)
            r_name   = r.get("name",   "Unknown")
            r_amount = r.get("amount", 0)
            options.append(
                discord.SelectOption(
                    label=r_name[:100],
                    description=t("exchange_rates.rate_select_desc", lang=lang,
                                  amount=_fmt_num(r_amount), name=r_name)[:100],
                    emoji=r_emoji if not r_emoji.startswith("<") else None,
                    value=r["id"],
                )
            )

        super().__init__(
            placeholder=t("exchange_rates.rate_placeholder", lang=lang),
            options=options,
            min_values=1,
            max_values=1,
            custom_id="exchange_rate_method_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class CoinCalculatorModal(discord.ui.Modal):
    """Coin miktarı girin, seçilen kura göre değeri hesaplanır"""

    def __init__(self, user_id: int, lang: str):
        super().__init__(title=t("exchange_rates.modal_title", lang=lang))
        self.user_id = user_id
        self.lang    = lang

        self.rate_select = discord.ui.Label(
            text=t("exchange_rates.rate_select_text", lang=lang),
            component=ExchangeRateMethodSelect(lang=lang),
        )
        self.add_item(self.rate_select)

        self.amount_input = discord.ui.TextInput(
            label=t("exchange_rates.amount_label", lang=lang),
            placeholder=t("exchange_rates.amount_placeholder", lang=lang),
            required=True,
            max_length=15,
            style=discord.TextStyle.short,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                t("exchange_rates.not_your_panel", lang=self.lang), ephemeral=True
            )

        # Parse amount — strip dots/commas used as thousand separators
        try:
            raw = self.amount_input.value.strip().replace(".", "").replace(",", "")
            currency_amount = float(raw)
            if currency_amount <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title=t("exchange_rates.error_invalid_title", lang=self.lang),
                    description=t("exchange_rates.error_invalid", lang=self.lang),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

        values = self.rate_select.component.values
        selected_id  = values[0] if values else "__usd__"
        rates_data   = get_data("server/exchange_rates") or {}
        server_data  = get_data("server/server") or {}
        coin_emoji   = server_data.get("coin_emoji", "🪙")
        coin_usd     = rates_data.get("coin_usd_rate", 0.10)
        custom_rates = rates_data.get("custom_rates", [])

        if selected_id == "__usd__":
            coin_result  = currency_amount / coin_usd if coin_usd else 0
            rate_line    = f"1 Coin = **${coin_usd:.4g}**  ·  $1 = **{_fmt_num(round(1/coin_usd, 4))} Coin**"
            result_line  = f"**${_fmt_num(currency_amount)}** = {coin_emoji} **{_fmt_num(round(coin_result, 4))} Coin**"
        else:
            rate = next((r for r in custom_rates if r.get("id") == selected_id), None)
            if not rate:
                return await interaction.response.send_message(
                    t("exchange_rates.error_not_found", lang=self.lang), ephemeral=True
                )
            r_emoji  = rate.get("emoji",  coin_emoji)
            r_name   = rate.get("name",   "Unknown")
            r_amount = rate.get("amount", 0)
            coin_result = currency_amount / r_amount if r_amount else 0
            rate_line   = f"1 Coin = {r_emoji} **{_fmt_num(r_amount)} {r_name}**"
            result_line = f"{r_emoji} **{_fmt_num(currency_amount)} {r_name}** = {coin_emoji} **{_fmt_num(round(coin_result, 4))} Coin**"

        embed = discord.Embed(
            title=t("exchange_rates.result_title", lang=self.lang),
            color=0xf5a623,
        )
        embed.add_field(
            name=t("exchange_rates.rate_field", lang=self.lang),
            value=rate_line,
            inline=False,
        )
        embed.add_field(
            name=t("exchange_rates.result_field", lang=self.lang),
            value=result_line,
            inline=False,
        )
        embed.set_footer(text="Vegas Casino | Exchange Rates")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ExchangeRatesView(discord.ui.View):
    """Exchange Rates embed altına eklenen hesap butonu"""

    def __init__(self, user_id: int, lang: str = "en"):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.lang    = lang
        # Translate button label after children are registered
        self.children[0].label = t("exchange_rates.calculator_button", lang=lang)

    @discord.ui.button(label="Calculator", style=discord.ButtonStyle.primary, emoji="🧮")
    async def open_calculator(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                t("exchange_rates.not_your_panel", lang=self.lang), ephemeral=True
            )
        modal = CoinCalculatorModal(self.user_id, self.lang)
        await interaction.response.send_modal(modal)


class RoomManagementSelect(discord.ui.Select):
    """Oda yönetimi menüsü"""
    
    def __init__(self, lang: str = "en", *, hub_mode: bool = False):
        self._hub_mode = hub_mode
        options = [
            discord.SelectOption(
                label=t("private_rooms.add_member_option", lang=lang),
                description="Add a member to your room",
                emoji="➕",
                value="add_member"
            ),
            discord.SelectOption(
                label=t("private_rooms.remove_member_option", lang=lang),
                description="Remove a member from your room",
                emoji="➖",
                value="remove_member"
            ),
            discord.SelectOption(
                label=t("private_rooms.close_room_option", lang=lang),
                description="Close and delete your room",
                emoji="🔒",
                value="close_room"
            )
        ]
        
        super().__init__(
            placeholder=t("private_rooms.placeholder_room", lang=lang),
            options=options,
            custom_id="private_room:management",
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Room management seçimi"""
        from modules.utils import get_user_lang

        lang = get_user_lang(interaction.user.id)
        ctx, err = owner_room_for_interaction(
            interaction, require_private_room=True, lang=lang
        )
        if err:
            return await interaction.response.send_message(err, ephemeral=True)

        guild_id = ctx.guild_id
        channel_id = ctx.channel_id
        channel = ctx.channel
        user_id = interaction.user.id
        if not channel:
            return await interaction.response.send_message(
                t("private_rooms.no_room_for_management", lang=lang, user_id=str(user_id)),
                ephemeral=True,
            )

        async def _reset_welcome():
            await reset_menu_message(
                interaction, guild_id, channel_id, hub_mode=self._hub_mode
            )

        if self.values[0] == "add_member":
            modal = AddMemberModal(channel, interaction.guild, interaction.user.id)
            await interaction.response.send_modal(modal)
            await _reset_welcome()
            return

        await interaction.response.defer(ephemeral=True)
        await _reset_welcome()

        if self.values[0] == "remove_member":
            from cogs.room_panels_v2 import build_remove_member_layout
            from modules.ui_v2 import send_ephemeral
            from modules.utils import get_user_lang

            users_list = ctx.room_info.get("users", [])
            lang = get_user_lang(user_id)
            await send_ephemeral(
                interaction,
                build_remove_member_layout(channel, interaction.guild, users_list, lang),
            )

        elif self.values[0] == "close_room":
            from cogs.room_panels_v2 import build_close_room_layout
            from modules.ui_v2 import send_ephemeral
            from modules.utils import get_user_lang

            lang = get_user_lang(user_id)
            await send_ephemeral(
                interaction,
                build_close_room_layout(channel, channel_id, guild_id, lang),
            )


class AddMemberModal(discord.ui.Modal, title="Add Member to Room"):
    """Üye ekleme modal formu"""
    
    member_input = discord.ui.TextInput(
        label="User ID or Username",
        placeholder="Enter user ID (e.g. 123456789) or username (e.g. username#0000)",
        required=True,
        max_length=100
    )
    
    def __init__(self, channel: discord.TextChannel, guild: discord.Guild, owner_id: int):
        super().__init__()
        self.channel = channel
        self.guild = guild
        self.owner_id = owner_id
    
    async def on_submit(self, interaction: discord.Interaction):
        """Form gönderildiğinde"""
        user_input = self.member_input.value.strip()
        member = None
        
        # ID ile arama dene
        if user_input.isdigit():
            member = self.guild.get_member(int(user_input))
        
        # Kullanıcı adı ile arama
        if not member:
            # Discord username formatı: username veya username#discriminator
            for m in self.guild.members:
                if m.name.lower() == user_input.lower() or str(m).lower() == user_input.lower():
                    member = m
                    break
        
        # Üye bulunamadı
        if not member:
            embed = discord.Embed(
                title="❌ Member Not Found",
                description=f"Could not find a member with ID or username: `{user_input}`\n\n"
                           f"**Tips:**\n"
                           f"• Make sure the user is in this server\n"
                           f"• Use the exact username or user ID\n"
                           f"• Example ID: `123456789012345678`\n"
                           f"• Example username: `username` or `username#0000`",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Bot eklenemez
        if member.bot:
            await interaction.response.send_message(
                "❌ You cannot add bots to your room!",
                ephemeral=True
            )
            return
        
        # Kendini ekleyemez
        if member.id == self.owner_id:
            await interaction.response.send_message(
                t("private_rooms.cannot_add_yourself", lang="en"),
                ephemeral=True
            )
            return
        
        # Zaten var mı kontrol et
        overwrites = self.channel.overwrites
        if member in overwrites:
            await interaction.response.send_message(
                t("private_rooms.member_already_added", lang="en").format(member=member.mention),
                ephemeral=True
            )
            return
        
        try:
            # Görüntüleme ve mesaj gönderme izni ver
            await self.channel.set_permissions(
                member,
                read_messages=True,
                send_messages=True,
                add_reactions=True,
                create_public_threads=False,
                create_private_threads=False,
                send_messages_in_threads=True,
                view_channel=True,
                embed_links=True,
                attach_files=True
            )
            
            # Users listesine ekle
            rooms_data = get_data("server/private_rooms")
            guild_id = str(self.guild.id)
            channel_id = str(self.channel.id)
            
            if guild_id in rooms_data and channel_id in rooms_data[guild_id]:
                if "users" not in rooms_data[guild_id][channel_id]:
                    rooms_data[guild_id][channel_id]["users"] = []
                if member.id not in rooms_data[guild_id][channel_id]["users"]:
                    rooms_data[guild_id][channel_id]["users"].append(member.id)
                    set_data("server/private_rooms", rooms_data)
            
            embed = discord.Embed(
                title=t("private_rooms.member_added_title", lang="en"),
                description=t("private_rooms.member_added_description", lang="en").format(
                    member=member.mention
                ),
                color=discord.Color.green()
            )
            embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
            embed.add_field(name="Username", value=f"`{member.name}`", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Kanala bildirim gönder
            notification = discord.Embed(
                description=f"➕ {member.mention} has been added to the room.",
                color=discord.Color.green()
            )
            await self.channel.send(embed=notification)
            
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error adding member: {str(e)}",
                ephemeral=True
            )


class SettingsSelect(discord.ui.Select):
    """Room settings menu - Language and other preferences"""
    
    def __init__(self, lang: str = "en", *, hub_mode: bool = False):
        self._hub_mode = hub_mode
        options = [
            discord.SelectOption(
                label="Language Settings",
                description="Change your language preference",
                emoji="🌐",
                value="language"
            )
        ]
        
        super().__init__(
            placeholder=t("private_rooms.placeholder_settings", lang=lang),
            options=options,
            custom_id="private_room:settings",
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Settings selection"""
        await interaction.response.defer(ephemeral=True)
        from modules.utils import get_user_lang

        lang = get_user_lang(interaction.user.id)
        ctx, err = owner_room_for_interaction(interaction, lang=lang)
        if err:
            embed = discord.Embed(
                title="❌ Access Denied",
                description=err,
                color=discord.Color.red(),
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        await reset_menu_message(
            interaction, ctx.guild_id, ctx.channel_id, hub_mode=self._hub_mode
        )
        user_id = interaction.user.id
        
        if self.values[0] == "language":
            from cogs.room_panels_v2 import build_language_settings_layout
            from modules.ui_v2 import send_ephemeral

            user_lang_data = get_user_data(user_id, "lang") or {}
            current_lang = user_lang_data.get("language", "en")
            await interaction.followup.send(
                view=build_language_settings_layout(user_id, current_lang),
                ephemeral=True,
            )
    
    def get_language_emoji(self, lang_code: str) -> str:
        """Get flag emoji for language"""
        flags = {
            "en": "🇬🇧",
            "tr": "🇹🇷",
            "id": "🇮🇩"
        }
        return flags.get(lang_code, "🏴")
    
    def get_language_name(self, lang_code: str) -> str:
        """Get language name"""
        names = {
            "en": "English",
            "tr": "Türkçe",
            "id": "Bahasa Indonesia"
        }
        return names.get(lang_code, "Unknown")


class LanguageSettingsView(discord.ui.View):
    """Language settings view with language select"""
    
    def __init__(self, user_id: int, current_lang: str):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.current_lang = current_lang
        self.add_item(LanguageSelect(user_id, current_lang))


class LanguageSelect(discord.ui.Select):
    """Language selection menu"""
    
    def __init__(self, user_id: int, current_lang: str):
        self.user_id = user_id
        self.current_lang = current_lang
        
        # Available languages with flags
        options = [
            discord.SelectOption(
                label="English",
                description="Change language to English",
                emoji="🇬🇧",
                value="en"
            ),
            discord.SelectOption(
                label="Türkçe",
                description="Dili Türkçe olarak değiştir",
                emoji="🇹🇷",
                value="tr"
            ),
            discord.SelectOption(
                label="Bahasa Indonesia",
                description="Ubah bahasa ke Bahasa Indonesia",
                emoji="🇮🇩",
                value="id"
            )
        ]
        
        super().__init__(
            placeholder="Select your language...",
            options=options,
            min_values=1,
            max_values=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle language selection"""
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "❌ This is not your settings panel!",
                ephemeral=True
            )
        
        selected_lang = self.values[0]
        
        # Save language preference
        lang_data = {"language": selected_lang}
        set_user_data(self.user_id, "lang", lang_data)
        
        # Language names and flags
        lang_info = {
            "en": ("🇬🇧", "English"),
            "tr": ("🇹🇷", "Türkçe"),
            "id": ("🇮🇩", "Bahasa Indonesia")
        }
        
        flag, name = lang_info.get(selected_lang, ("🏴", "Unknown"))
        
        # Success message
        embed = discord.Embed(
            title="✅ Language Updated",
            description=f"Your language has been changed to {flag} **{name}**",
            color=discord.Color.green()
        )
        embed.set_footer(text="Vegas Casino | Settings")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Update original message
        updated_view = LanguageSettingsView(self.user_id, selected_lang)
        
        original_embed = discord.Embed(
            title="🌐 Language Settings",
            description="Select your preferred language from the menu below.",
            color=discord.Color.blue()
        )
        original_embed.add_field(
            name="📌 Current Language",
            value=f"{flag} {name}",
            inline=False
        )
        original_embed.set_footer(text="Vegas Casino | Settings")
        
        try:
            if interaction.message:
                await interaction.message.edit(embed=original_embed, view=updated_view)
        except Exception:
            # Original message may be ephemeral or deleted; ignore edit failure
            pass


class RemoveMemberSelect(discord.ui.Select):
    """Oda üye çıkarma menüsü - Sadece eklenen üyeler"""
    
    def __init__(self, channel: discord.TextChannel, guild: discord.Guild, users_list: list):
        self.channel = channel
        self.guild = guild
        self.users_list = users_list
        
        # Users listesindeki üyeleri seçenek olarak ekle
        options = []
        for user_id in users_list:
            member = guild.get_member(user_id)
            if member:
                options.append(
                    discord.SelectOption(
                        label=member.display_name,
                        value=str(member.id),
                        description=f"@{member.name}",
                        emoji="👤"
                    )
                )
        
        # Eğer hiç üye yoksa boş option ekle
        if not options:
            options.append(
                discord.SelectOption(
                    label="No members to remove",
                    value="none",
                    description="Room has no additional members"
                )
            )
        
        super().__init__(
            placeholder="Select a member to remove from your room",
            custom_id="private_room:remove_member_select",
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Üye çıkarma"""
        if self.values[0] == "none":
            await interaction.response.send_message(
                "❌ No members to remove!",
                ephemeral=True
            )
            return
        
        member_id = int(self.values[0])
        member = self.guild.get_member(member_id)
        
        if not member:
            await interaction.response.send_message(
                "❌ Member not found!",
                ephemeral=True
            )
            return
        
        try:
            # İzinleri kaldır
            await self.channel.set_permissions(member, overwrite=None)
            
            # Users listesinden çıkar
            rooms_data = get_data("server/private_rooms")
            guild_id = str(self.guild.id)
            channel_id = str(self.channel.id)
            
            if guild_id in rooms_data and channel_id in rooms_data[guild_id]:
                if "users" in rooms_data[guild_id][channel_id]:
                    if member_id in rooms_data[guild_id][channel_id]["users"]:
                        rooms_data[guild_id][channel_id]["users"].remove(member_id)
                        set_data("server/private_rooms", rooms_data)
            
            embed = discord.Embed(
                title=t("private_rooms.member_removed_title", lang="en"),
                description=t("private_rooms.member_removed_description", lang="en").format(
                    member=member.mention
                ),
                color=discord.Color.orange()
            )
            embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
            embed.add_field(name="Username", value=f"`{member.name}`", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Kanala bildirim gönder
            notification = discord.Embed(
                description=f"➖ {member.mention} has been removed from the room.",
                color=discord.Color.orange()
            )
            await self.channel.send(embed=notification)
            
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error removing member: {str(e)}",
                ephemeral=True
            )


class CloseRoomConfirmView(discord.ui.View):
    """Oda kapatma onay view'ı"""
    
    def __init__(self, channel: discord.TextChannel, channel_id: str, guild_id: str):
        super().__init__(timeout=60)
        self.channel = channel
        self.channel_id = channel_id
        self.guild_id = guild_id
    
    @discord.ui.button(label="Yes, Close Room", style=discord.ButtonStyle.danger, emoji="🔒")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Kapatma onayı"""
        try:
            # Veritabanından sil (yeni yapı: channel_id kullan)
            rooms_data = get_data("server/private_rooms")
            if self.guild_id in rooms_data and self.channel_id in rooms_data[self.guild_id]:
                del rooms_data[self.guild_id][self.channel_id]
                if not rooms_data[self.guild_id]:
                    del rooms_data[self.guild_id]
                set_data("server/private_rooms", rooms_data)
            
            # Onay mesajı
            embed = discord.Embed(
                title=t("private_rooms.room_closing_title", lang="en"),
                description=t("private_rooms.room_closing_description", lang="en"),
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Kanala bildirim
            closing_notification = discord.Embed(
                description="🔒 This room is being closed by the owner...",
                color=discord.Color.red()
            )
            await self.channel.send(embed=closing_notification)
            
            # Kanalı sil
            await self.channel.delete(reason="Room closed by owner")
            
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error closing room: {str(e)}",
                ephemeral=True
            )
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """İptal"""
        await interaction.response.send_message("Room closure cancelled.", ephemeral=True)


class ReferralDashboardView(discord.ui.View):
    """View shown when user already has a referral code"""

    def __init__(self, user_id: str, available_balance: int):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.available_balance = available_balance

    @discord.ui.button(label="💸 Claim Earnings", style=discord.ButtonStyle.success)
    async def claim_earnings(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ This is not your panel!", ephemeral=True)

        if self.available_balance <= 0:
            return await interaction.response.send_message(
                "❌ You have no available earnings to claim.",
                ephemeral=True
            )

        from modules.player import Player
        player = Player(int(self.user_id))
        player.add_balance("real", self.available_balance)

        referrals_data = get_data("server/referrals") or {}
        if self.user_id in referrals_data:
            referrals_data[self.user_id]["available_balance"] = 0
            set_data("server/referrals", referrals_data)

        claimed = self.available_balance
        self.available_balance = 0
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Earnings Claimed",
                description=f"**{format_balance(claimed, 'real')}** has been added to your balance.",
                color=discord.Color.green()
            ),
            ephemeral=True
        )


class CreateReferralCodeView(discord.ui.View):
    """Referral kodu oluşturma view'ı"""
    
    def __init__(self, user_id: str):
        super().__init__(timeout=180)
        self.user_id = user_id
    
    @discord.ui.select(
        placeholder="Create your referral code",
        options=[
            discord.SelectOption(
                label="✨ Create Referral Code",
                description="Click to create your unique referral code",
                emoji="🎁",
                value="create_code"
            )
        ]
    )
    async def create_code_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        """Kod oluşturma modalını aç"""
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ This is not your panel!", ephemeral=True)
        
        modal = CreateReferralCodeModal(self.user_id)
        await interaction.response.send_modal(modal)


# ─────────────────────────────────────────────────────────────────────────────
# Withdraw System
# ─────────────────────────────────────────────────────────────────────────────

class WithdrawMethodView(discord.ui.View):
    """Payment method selection view for withdrawals"""

    def __init__(self, user_id: str, methods: dict, min_withdrawal: int):
        super().__init__(timeout=180)
        self.add_item(WithdrawMethodSelect(user_id, methods, min_withdrawal))


class WithdrawMethodSelect(discord.ui.Select):
    """Select a payment method for withdrawal"""

    def __init__(self, user_id: str, methods: dict, min_withdrawal: int):
        self.user_id = user_id
        self.methods = methods
        self.min_withdrawal = min_withdrawal

        options = []
        for key, info in methods.items():
            desc = info.get("description", "")
            options.append(discord.SelectOption(
                label=info.get("name", key),
                description=(desc[:100] if desc else None),
                emoji=info.get("emoji", "💳"),
                value=key
            ))

        super().__init__(placeholder="💳 Select payment method...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ This is not your panel!", ephemeral=True)

        selected = self.values[0]
        method_info = self.methods[selected]

        # Open a ticket channel if method key is "gold" OR the method has ticket=True
        use_ticket = selected == "gold" or method_info.get("ticket", False)
        if use_ticket:
            modal = WithdrawGoldAmountModal(self.user_id, method_info, self.min_withdrawal)
        else:
            modal = WithdrawAmountModal(self.user_id, selected, method_info, self.min_withdrawal)

        await interaction.response.send_modal(modal)


class WithdrawAmountModal(discord.ui.Modal, title="🏦 Withdrawal Request"):
    """Amount + payment address for non-Gold methods"""

    amount_input = discord.ui.TextInput(
        label="Amount",
        placeholder="Enter amount to withdraw",
        required=True,
        max_length=12
    )
    address_input = discord.ui.TextInput(
        label="Payment Address / Account ID",
        placeholder="Your wallet address, bank account number, etc.",
        required=True,
        max_length=200,
        style=discord.TextStyle.short
    )

    def __init__(self, user_id: str, method_key: str, method_info: dict, min_withdrawal: int):
        super().__init__()
        self.user_id = user_id
        self.method_key = method_key
        self.method_info = method_info
        self.min_withdrawal = min_withdrawal

        self.confirm_label = discord.ui.Label(
            text="Confirm Payment Info",
            description="Check the box to confirm your payment details are correct",
            component=discord.ui.CheckboxGroup(
                options=[
                    discord.CheckboxGroupOption(
                        label="I confirm the payment information above is correct",
                        value="confirmed"
                    )
                ],
                min_values=1,
                max_values=1,
                required=True
            )
        )
        self.add_item(self.confirm_label)

    async def on_submit(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ Not your panel.", ephemeral=True)

        if "confirmed" not in self.confirm_label.component.values:
            return await interaction.response.send_message(
                "❌ Please confirm your payment information is correct before submitting.",
                ephemeral=True
            )

        try:
            amount = int(self.amount_input.value.replace(",", "").strip())
            if amount <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            return await interaction.response.send_message("❌ Invalid amount.", ephemeral=True)

        player = Player(int(self.user_id))
        balance = player.get_balance("real")

        if amount < self.min_withdrawal:
            return await interaction.response.send_message(
                f"❌ Minimum withdrawal is **{format_balance(self.min_withdrawal, 'real')}**.",
                ephemeral=True
            )
        if balance < amount:
            return await interaction.response.send_message(
                f"❌ Insufficient balance. You have **{format_balance(balance, 'real')}**.",
                ephemeral=True
            )

        # Wager gate: last_deposit * multiplier (+ bonus wager req if any)
        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        req_wager, wagered_since, wager_remaining = _get_withdraw_wager_requirement(
            int(self.user_id), server_data
        )
        if wager_remaining > 0:
            wg_pct = int(wagered_since / req_wager * 100) if req_wager else 0
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="🎲 Wager Requirement Not Met",
                    description=(
                        f"You must wager **{format_balance(req_wager, 'real')}** before withdrawing.\n\n"
                        f"Progress: **{format_balance(wagered_since, 'real')}** / **{format_balance(req_wager, 'real')}** ({wg_pct}%)\n"
                        f"Still needed: **{format_balance(wager_remaining, 'real')}**"
                    ),
                    color=0xf5a623,
                ).set_footer(text="Vegas Casino | Withdrawal System"),
                ephemeral=True,
            )

        # Bonus checks
        active_bonus = bonus_engine.get_active_bonus(self.user_id)
        if active_bonus:
            from modules.utils import get_user_lang
            _lang = get_user_lang(self.user_id)
            btype = active_bonus.get("type", "fixed")
            if btype == "percentage":
                req = int(active_bonus.get("wager_requirement", 0))
                done = int(active_bonus.get("wagered_so_far", 0))
                if done < req:
                    remaining = req - done
                    pct = int(done / req * 100) if req else 0
                    return await interaction.response.send_message(
                        embed=discord.Embed(
                            title=t("bonus.wager_not_met_title", lang=_lang),
                            description=t("bonus.wager_not_met_desc", lang=_lang,
                                bonus_name=active_bonus["bonus_name"],
                                done=format_balance(done, "real"),
                                req=format_balance(req, "real"),
                                pct=pct,
                                remaining=format_balance(remaining, "real")),
                            color=0xf5a623,
                        ).set_footer(text=t("bonus.wager_not_met_footer", lang=_lang)),
                        ephemeral=True,
                    )
            elif btype == "fixed":
                target = int(active_bonus.get("wager_requirement", 0))
                # Milestone not yet reached — allow withdrawal but inform the user they forfeit the bonus reward
                if target > 0 and not bonus_engine.is_wager_complete(self.user_id):
                    current_bal = player.get_balance("real")
                    needed = max(target - current_bal, 0)
                    # Don't block — just warn them (they lose the bonus)
                    pass  # fall through
            # Apply max_withdrawal cap only when bonus milestone is completed
            max_cap = active_bonus.get("max_withdrawal")
            if max_cap and bonus_engine.is_wager_complete(self.user_id) and amount > int(max_cap):
                return await interaction.response.send_message(
                    embed=discord.Embed(
                        title=t("bonus.cap_title", lang=_lang),
                        description=t("bonus.cap_desc", lang=_lang,
                            bonus_name=active_bonus["bonus_name"],
                            cap=format_balance(max_cap, "real"),
                            amount=format_balance(amount, "real")),
                        color=0xf5a623,
                    ).set_footer(text=t("bonus.cap_footer", lang=_lang)),
                    ephemeral=True,
                )

        _, promo_max = promo_engine.get_promo_withdraw_limits(int(self.user_id))
        if promo_max and amount > int(promo_max):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="🎟️ Promo Withdrawal Cap",
                    description=(
                        f"Your completed promo limits this withdrawal to "
                        f"**{format_balance(promo_max, 'real')}**.\n"
                        f"You requested **{format_balance(amount, 'real')}**."
                    ),
                    color=0xf5a623,
                ),
                ephemeral=True,
            )

        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        withdraw_mode = server_data.get("withdraw_mode", "log")

        # Deduct balance immediately (applies to both modes)
        player.remove_balance("real", amount)
        player.record_withdraw(amount)
        bonus_engine.complete_bonus_on_withdraw(self.user_id)
        promo_engine.clear_completed_promo_after_withdraw(self.user_id)

        withdraw_id = str(int(time.time() * 1000))
        address = self.address_input.value.strip()
        withdraw_data = {
            "withdraw_id": withdraw_id,
            "amount": amount,
            "method_key": self.method_key,
            "method_name": self.method_info.get("name"),
            "address": address,
            "status": "pending",
            "timestamp": str(int(time.time())),
            "user_id": int(self.user_id)
        }
        history = get_user_data(int(self.user_id), "withdraw_history") or {}
        history[withdraw_id] = withdraw_data
        set_user_data(int(self.user_id), "withdraw_history", history)

        embed = discord.Embed(
            title="🏦 Withdrawal Request",
            description=f"New withdrawal request from {interaction.user.mention}",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="👤 User", value=f"{interaction.user.mention} ({interaction.user.id})", inline=True)
        embed.add_field(name="💰 Amount", value=format_balance(amount, "real"), inline=True)
        embed.add_field(name="🏦 Payment Method", value=self.method_info.get("name"), inline=True)
        embed.add_field(name="📋 Payment Address", value=address, inline=False)
        embed.add_field(name="🆔 Withdraw ID", value=withdraw_id, inline=False)
        embed.set_footer(text="Vegas Casino | Withdrawal System")

        if withdraw_mode == "ticket":
            # Ticket mode: create a private ticket channel
            deposit_category_id = server_data.get("deposit_category")
            cashier_role_id = server_data.get("cashier_role")
            deposit_category = None
            if deposit_category_id:
                deposit_category = interaction.guild.get_channel(int(deposit_category_id))

            if not deposit_category:
                # Refund on misconfiguration
                player.add_balance("real", amount)
                withdraw_data["status"] = "failed"
                history[withdraw_id] = withdraw_data
                set_user_data(int(self.user_id), "withdraw_history", history)
                return await interaction.response.send_message(
                    "❌ Withdrawal system not configured (no ticket category). Please contact an admin.",
                    ephemeral=True
                )

            await interaction.response.defer(ephemeral=True)
            try:
                channel_name = f"withdraw-{interaction.user.name.lower()[:15]}-{withdraw_id[-4:]}"
                overwrites = {
                    interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    interaction.guild.me: discord.PermissionOverwrite(
                        read_messages=True, send_messages=True,
                        manage_channels=True, manage_messages=True
                    )
                }
                if cashier_role_id:
                    cashier_role = interaction.guild.get_role(int(cashier_role_id))
                    if cashier_role:
                        overwrites[cashier_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                ticket_channel = await deposit_category.create_text_channel(
                    name=channel_name, overwrites=overwrites,
                    reason=f"Withdrawal ticket for {interaction.user.name}"
                )

                withdraw_data["ticket_channel_id"] = ticket_channel.id
                history[withdraw_id] = withdraw_data
                set_user_data(int(self.user_id), "withdraw_history", history)

                view = WithdrawTicketView()
                await ticket_channel.send(embed=embed, view=view)

                await interaction.followup.send(
                    embed=discord.Embed(
                        title="✅ Withdrawal Ticket Created",
                        description=(
                            f"Your withdrawal ticket has been created: {ticket_channel.mention}\n\n"
                            "Your balance has been deducted. An admin will process your request."
                        ),
                        color=discord.Color.green()
                    ),
                    ephemeral=True
                )
            except Exception as e:
                # Refund on failure
                player.add_balance("real", amount)
                withdraw_data["status"] = "failed"
                history[withdraw_id] = withdraw_data
                set_user_data(int(self.user_id), "withdraw_history", history)
                await interaction.followup.send(f"❌ Failed to create withdrawal ticket: {str(e)}", ephemeral=True)
        else:
            # Log mode: send to withdraw channel
            withdraw_channel_id = server_data.get("withdraw_channel")
            if not withdraw_channel_id:
                player.add_balance("real", amount)
                withdraw_data["status"] = "failed"
                history[withdraw_id] = withdraw_data
                set_user_data(int(self.user_id), "withdraw_history", history)
                return await interaction.response.send_message(
                    "❌ Withdrawal system is not configured. Please contact an admin.",
                    ephemeral=True
                )

            withdraw_channel = interaction.guild.get_channel(int(withdraw_channel_id))
            if not withdraw_channel:
                player.add_balance("real", amount)
                withdraw_data["status"] = "failed"
                history[withdraw_id] = withdraw_data
                set_user_data(int(self.user_id), "withdraw_history", history)
                return await interaction.response.send_message(
                    "❌ Withdrawal channel not found. Please contact an admin.",
                    ephemeral=True
                )

            view = WithdrawRequestView()
            await withdraw_channel.send(embed=embed, view=view)

            await interaction.response.send_message(
                embed=discord.Embed(
                    title="✅ Withdrawal Request Submitted",
                    description=(
                        f"Your withdrawal of **{format_balance(amount, 'real')}** via "
                        f"**{self.method_info.get('name')}** has been submitted.\n\n"
                        "Your balance has been deducted. You will receive your funds once approved."
                    ),
                    color=discord.Color.green()
                ),
                ephemeral=True
            )


class WithdrawGoldAmountModal(discord.ui.Modal, title="🪙 Gold Withdrawal Request"):
    """Amount input for Gold method — opens a ticket channel"""

    amount_input = discord.ui.TextInput(
        label="Amount",
        placeholder="Enter amount to withdraw",
        required=True,
        max_length=12
    )

    def __init__(self, user_id: str, method_info: dict, min_withdrawal: int):
        super().__init__()
        self.user_id = user_id
        self.method_info = method_info
        self.min_withdrawal = min_withdrawal

    async def on_submit(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ Not your panel.", ephemeral=True)

        try:
            amount = int(self.amount_input.value.replace(",", "").strip())
            if amount <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            return await interaction.response.send_message("❌ Invalid amount.", ephemeral=True)

        player = Player(int(self.user_id))
        balance = player.get_balance("real")

        if amount < self.min_withdrawal:
            return await interaction.response.send_message(
                f"❌ Minimum withdrawal is **{format_balance(self.min_withdrawal, 'real')}**.",
                ephemeral=True
            )
        if balance < amount:
            return await interaction.response.send_message(
                f"❌ Insufficient balance. You have **{format_balance(balance, 'real')}**.",
                ephemeral=True
            )

        # Wager gate: last_deposit * multiplier (+ bonus wager req if any)
        _guild_id = str(interaction.guild.id)
        _server_data = get_server_data(_guild_id)
        req_wager, wagered_since, wager_remaining = _get_withdraw_wager_requirement(
            int(self.user_id), _server_data
        )
        if wager_remaining > 0:
            wg_pct = int(wagered_since / req_wager * 100) if req_wager else 0
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="🎲 Wager Requirement Not Met",
                    description=(
                        f"You must wager **{format_balance(req_wager, 'real')}** before withdrawing.\n\n"
                        f"Progress: **{format_balance(wagered_since, 'real')}** / **{format_balance(req_wager, 'real')}** ({wg_pct}%)\n"
                        f"Still needed: **{format_balance(wager_remaining, 'real')}**"
                    ),
                    color=0xf5a623,
                ).set_footer(text="Vegas Casino | Withdrawal System"),
                ephemeral=True,
            )

        # Bonus checks
        active_bonus = bonus_engine.get_active_bonus(self.user_id)
        if active_bonus:
            from modules.utils import get_user_lang
            _lang = get_user_lang(self.user_id)
            btype = active_bonus.get("type", "fixed")
            if btype == "percentage":
                req = int(active_bonus.get("wager_requirement", 0))
                done = int(active_bonus.get("wagered_so_far", 0))
                if done < req:
                    remaining = req - done
                    pct = int(done / req * 100) if req else 0
                    return await interaction.response.send_message(
                        embed=discord.Embed(
                            title=t("bonus.wager_not_met_title", lang=_lang),
                            description=t("bonus.wager_not_met_desc", lang=_lang,
                                bonus_name=active_bonus["bonus_name"],
                                done=format_balance(done, "real"),
                                req=format_balance(req, "real"),
                                pct=pct,
                                remaining=format_balance(remaining, "real")),
                            color=0xf5a623,
                        ).set_footer(text=t("bonus.wager_not_met_footer", lang=_lang)),
                        ephemeral=True,
                    )
            # Apply max_withdrawal cap only when fixed bonus milestone is completed
            max_cap = active_bonus.get("max_withdrawal")
            if max_cap and bonus_engine.is_wager_complete(self.user_id) and amount > int(max_cap):
                return await interaction.response.send_message(
                    embed=discord.Embed(
                        title=t("bonus.cap_title", lang=_lang),
                        description=t("bonus.cap_desc", lang=_lang,
                            bonus_name=active_bonus["bonus_name"],
                            cap=format_balance(max_cap, "real"),
                            amount=format_balance(amount, "real")),
                        color=0xf5a623,
                    ).set_footer(text=t("bonus.cap_footer", lang=_lang)),
                    ephemeral=True,
                )

        _, promo_max = promo_engine.get_promo_withdraw_limits(int(self.user_id))
        if promo_max and amount > int(promo_max):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="🎟️ Promo Withdrawal Cap",
                    description=(
                        f"Your completed promo limits this withdrawal to "
                        f"**{format_balance(promo_max, 'real')}**.\n"
                        f"You requested **{format_balance(amount, 'real')}**."
                    ),
                    color=0xf5a623,
                ),
                ephemeral=True,
            )

        guild_id = str(interaction.guild.id)
        server_data = get_server_data(guild_id)
        deposit_category_id = server_data.get("deposit_category")
        cashier_role_id = server_data.get("cashier_role")

        # Deduct balance immediately
        player.remove_balance("real", amount)
        player.record_withdraw(amount)
        bonus_engine.complete_bonus_on_withdraw(self.user_id)
        promo_engine.clear_completed_promo_after_withdraw(self.user_id)

        deposit_category = None
        if deposit_category_id:
            deposit_category = interaction.guild.get_channel(int(deposit_category_id))

        if not deposit_category:
            return await interaction.response.send_message(
                "❌ Withdrawal system not configured (no category). Please contact an admin.",
                ephemeral=True
            )

        withdraw_id = str(int(time.time() * 1000))
        withdraw_data = {
            "withdraw_id": withdraw_id,
            "amount": amount,
            "method_key": "gold",
            "method_name": "Gold",
            "status": "pending",
            "timestamp": str(int(time.time())),
            "user_id": int(self.user_id)
        }
        history = get_user_data(int(self.user_id), "withdraw_history") or {}
        history[withdraw_id] = withdraw_data
        set_user_data(int(self.user_id), "withdraw_history", history)

        await interaction.response.defer(ephemeral=True)

        try:
            channel_name = f"withdraw-gold-{interaction.user.name.lower()[:15]}-{withdraw_id[-4:]}"
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                interaction.guild.me: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True,
                    manage_channels=True, manage_messages=True
                )
            }
            if cashier_role_id:
                cashier_role = interaction.guild.get_role(int(cashier_role_id))
                if cashier_role:
                    overwrites[cashier_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ticket_channel = await deposit_category.create_text_channel(
                name=channel_name, overwrites=overwrites,
                reason=f"Gold withdrawal ticket for {interaction.user.name}"
            )

            withdraw_data["ticket_channel_id"] = ticket_channel.id
            history[withdraw_id] = withdraw_data
            set_user_data(int(self.user_id), "withdraw_history", history)

            embed = discord.Embed(
                title="🪙 Gold Withdrawal Request",
                description=f"Withdrawal request from {interaction.user.mention}",
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="👤 User", value=f"{interaction.user.mention} ({interaction.user.id})", inline=True)
            embed.add_field(name="💰 Amount", value=format_balance(amount, "real"), inline=True)
            embed.add_field(name="🏦 Payment Method", value="🪙 Gold", inline=True)
            embed.add_field(name="🆔 Withdraw ID", value=withdraw_id, inline=False)
            embed.set_footer(text="Vegas Casino | Gold Withdrawal")

            view = WithdrawGoldTicketView()
            await ticket_channel.send(embed=embed, view=view)

            await interaction.followup.send(
                embed=discord.Embed(
                    title="✅ Gold Withdrawal Ticket Created",
                    description=(
                        f"Your withdrawal ticket has been created: {ticket_channel.mention}\n\n"
                        "Your balance has been deducted. An admin will process your request."
                    ),
                    color=discord.Color.green()
                ),
                ephemeral=True
            )
        except Exception as e:
            # Refund on failure
            player.add_balance("real", amount)
            withdraw_data["status"] = "failed"
            history[withdraw_id] = withdraw_data
            set_user_data(int(self.user_id), "withdraw_history", history)
            await interaction.followup.send(f"❌ Failed to create withdrawal ticket: {str(e)}", ephemeral=True)


class WithdrawRejectModal(discord.ui.Modal, title="❌ Reject Withdrawal"):
    """Modal to collect rejection reason, then process refund and update message"""

    def __init__(self, message: discord.Message, user_id: int, withdraw_id: str, is_gold: bool = False, is_ticket: bool = False):
        super().__init__()
        self.target_message = message
        self.user_id = user_id
        self.withdraw_id = withdraw_id
        self.is_gold = is_gold
        self.is_ticket = is_ticket
        self.reason_label = discord.ui.Label(
            text="Rejection Reason",
            component=discord.ui.TextInput(
                placeholder="Enter the reason for rejection...",
                required=True,
                max_length=300,
                style=discord.TextStyle.paragraph
            )
        )
        self.add_item(self.reason_label)

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason_label.component.value.strip()

        history = get_user_data(self.user_id, "withdraw_history") or {}
        withdraw_data = history.get(self.withdraw_id, {})

        if withdraw_data.get("status") != "pending":
            return await interaction.response.send_message(
                "❌ This withdrawal is already processed.", ephemeral=True
            )

        # Refund balance first
        player = Player(self.user_id)
        amount = withdraw_data.get("amount", 0)
        player.add_balance("real", amount)

        withdraw_data.update({
            "status": "rejected",
            "managed_by": interaction.user.id,
            "rejected_at": str(int(time.time())),
            "rejection_reason": reason
        })
        history[self.withdraw_id] = withdraw_data
        set_user_data(self.user_id, "withdraw_history", history)

        # Build a fresh embed (avoids mutation bugs)
        old_embed = self.target_message.embeds[0]
        new_embed = discord.Embed(
            title="❌ Withdrawal Rejected — Balance Refunded",
            color=discord.Color.red()
        )
        for field in old_embed.fields:
            new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
        new_embed.add_field(name="👨‍💼 Rejected By", value=interaction.user.mention, inline=True)
        new_embed.add_field(name="⏰ Rejected At", value=f"<t:{int(time.time())}:F>", inline=True)
        new_embed.add_field(name="💰 Refunded", value=format_balance(amount, "real"), inline=True)
        new_embed.add_field(name="📝 Reason", value=reason, inline=False)
        new_embed.set_footer(text="Vegas Casino | Withdrawal System")

        # Disable the view and edit the original message directly
        if self.is_ticket:
            view = WithdrawTicketView()
        elif self.is_gold:
            view = WithdrawGoldTicketView()
        else:
            view = WithdrawRequestView()
        for item in view.children:
            item.disabled = True

        await self.target_message.edit(embed=new_embed, view=view)
        await interaction.response.send_message(
            "✅ Withdrawal rejected and balance refunded.", ephemeral=True
        )

        # DM the user
        member = interaction.guild.get_member(self.user_id)
        if member:
            try:
                dm_embed = discord.Embed(
                    title="❌ Withdrawal Rejected",
                    description=(
                        f"Your withdrawal of **{format_balance(amount, 'real')}** "
                        f"has been rejected.\n\n**Reason:** {reason}\n\nYour balance has been refunded."
                    ),
                    color=discord.Color.red()
                )
                await member.send(embed=dm_embed)
            except Exception:
                pass

        if self.is_gold or self.is_ticket:
            await interaction.channel.send("🔒 Closing this ticket in 10 seconds...")
            await asyncio.sleep(10)
            await interaction.channel.delete(reason="Withdrawal ticket rejected")


class WithdrawApproveModal(discord.ui.Modal, title="✅ Approve Withdrawal"):
    note_input = discord.ui.TextInput(
        label="Note to user (optional)",
        placeholder="e.g. Sent via USDT TRC20",
        required=False,
        max_length=200
    )
    proof = discord.ui.Label(
        text="Payment Proof (optional)",
        description="Upload a screenshot or receipt",
        component=discord.ui.FileUpload(required=False)
    )

    def __init__(self, original_message: discord.Message, user_id: int, withdraw_id: str, approve_type: str):
        super().__init__()
        self.original_message = original_message
        self.user_id = user_id
        self.withdraw_id = withdraw_id
        self.approve_type = approve_type  # "log", "ticket", "gold_ticket"

    async def on_submit(self, interaction: discord.Interaction):
        attachments = self.proof.component.values  # List[discord.Attachment]
        attachment = attachments[0] if attachments else None
        note = self.note_input.value.strip() if self.note_input.value else None

        history = get_user_data(self.user_id, "withdraw_history") or {}
        withdraw_data = history.get(self.withdraw_id, {})

        if withdraw_data.get("status") != "pending":
            return await interaction.response.send_message("❌ This withdrawal is already processed.", ephemeral=True)

        withdraw_data.update({
            "status": "approved",
            "managed_by": interaction.user.id,
            "approved_at": str(int(time.time()))
        })
        history[self.withdraw_id] = withdraw_data
        set_user_data(self.user_id, "withdraw_history", history)

        # Track approved withdrawal in daily live stats
        try:
            from modules.live_stats_tracker import update_daily_withdraw
            update_daily_withdraw(str(self.user_id), withdraw_data.get("amount", 0))
        except Exception:
            pass

        # Edit original message embed
        embed = self.original_message.embeds[0]
        embed.color = discord.Color.green()
        embed.title = "✅ Withdrawal Approved"
        embed.add_field(name="👨‍💼 Approved By", value=interaction.user.mention, inline=True)
        embed.add_field(name="⏰ Approved At", value=f"<t:{int(time.time())}:F>", inline=True)
        if note:
            embed.add_field(name="📝 Note", value=note, inline=False)

        # Build disabled view of the right type
        if self.approve_type == "gold_ticket":
            dv = WithdrawGoldTicketView()
        elif self.approve_type == "ticket":
            dv = WithdrawTicketView()
        else:
            dv = WithdrawRequestView()
        for item in dv.children:
            item.disabled = True
        await self.original_message.edit(embed=embed, view=dv)
        await interaction.response.defer()

        member = interaction.guild.get_member(self.user_id)
        if member:
            try:
                dm_embed = discord.Embed(
                    title="✅ Withdrawal Approved",
                    description=(
                        f"Your withdrawal of **{format_balance(withdraw_data.get('amount', 0), 'real')}** "
                        f"via **{withdraw_data.get('method_name', 'Unknown')}** has been approved!"
                    ),
                    color=discord.Color.green()
                )
                if note:
                    dm_embed.add_field(name="📝 Note", value=note, inline=False)
                await member.send(embed=dm_embed)
            except Exception:
                pass

        await _send_withdraw_log(interaction.guild, member, withdraw_data, interaction.user, attachment=attachment, note=note)

        if self.approve_type in ("ticket", "gold_ticket"):
            try:
                await interaction.channel.send("🔒 Closing this ticket in 10 seconds...")
                await asyncio.sleep(10)
                await interaction.channel.delete(reason="Withdrawal approved")
            except Exception:
                pass


class WithdrawRequestView(discord.ui.View):
    """Persistent view sent to the withdraw channel for non-Gold requests"""

    def __init__(self):
        super().__init__(timeout=None)

    def _parse_withdraw_info(self, message: discord.Message):
        if not message or not message.embeds:
            return None, None
        embed = message.embeds[0]
        user_id = None
        withdraw_id = None
        for field in embed.fields:
            name = field.name.lower()
            if "user" in name:
                match = re.search(r"\((\d{17,20})\)", field.value)
                if match:
                    user_id = int(match.group(1))
            elif "withdraw id" in name:
                withdraw_id = field.value.strip()
        return user_id, withdraw_id

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green, emoji="💰", custom_id="withdraw_request:approve")
    async def approve_withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.database import check_permission

        user_id, withdraw_id = self._parse_withdraw_info(interaction.message)
        if not user_id or not withdraw_id:
            return await interaction.response.send_message("❌ Could not load withdrawal data.", ephemeral=True)

        server_data = get_server_data(str(interaction.guild.id))
        cashier_role_id = server_data.get("cashier_role")
        has_role = cashier_role_id and any(role.id == int(cashier_role_id) for role in interaction.user.roles)
        has_perm = not check_permission(interaction.user.id, "cashier") or not check_permission(interaction.user.id, "admin")
        if not (has_role or has_perm):
            return await interaction.response.send_message("❌ You don't have permission to approve withdrawals!", ephemeral=True)

        history = get_user_data(user_id, "withdraw_history") or {}
        if history.get(withdraw_id, {}).get("status") != "pending":
            return await interaction.response.send_message("❌ This withdrawal is already processed.", ephemeral=True)

        modal = WithdrawApproveModal(interaction.message, user_id, withdraw_id, "log")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="❌ Reject & Refund", style=discord.ButtonStyle.red, emoji="🚫", custom_id="withdraw_request:reject")
    async def reject_withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.database import check_permission

        user_id, withdraw_id = self._parse_withdraw_info(interaction.message)
        if not user_id or not withdraw_id:
            return await interaction.response.send_message("❌ Could not load withdrawal data.", ephemeral=True)

        server_data = get_server_data(str(interaction.guild.id))
        cashier_role_id = server_data.get("cashier_role")
        has_role = cashier_role_id and any(role.id == int(cashier_role_id) for role in interaction.user.roles)
        has_perm = not check_permission(interaction.user.id, "cashier") or not check_permission(interaction.user.id, "admin")
        if not (has_role or has_perm):
            return await interaction.response.send_message("❌ You don't have permission to reject withdrawals!", ephemeral=True)

        history = get_user_data(user_id, "withdraw_history") or {}
        withdraw_data = history.get(withdraw_id, {})
        if withdraw_data.get("status") != "pending":
            return await interaction.response.send_message("❌ This withdrawal is already processed.", ephemeral=True)

        modal = WithdrawRejectModal(interaction.message, user_id, withdraw_id, is_gold=False)
        await interaction.response.send_modal(modal)


class WithdrawGoldTicketView(discord.ui.View):
    """Persistent view inside a Gold withdrawal ticket channel"""

    def __init__(self):
        super().__init__(timeout=None)

    def _parse_withdraw_info(self, message: discord.Message):
        if not message or not message.embeds:
            return None, None
        embed = message.embeds[0]
        user_id = None
        withdraw_id = None
        for field in embed.fields:
            name = field.name.lower()
            if "user" in name:
                match = re.search(r"\((\d{17,20})\)", field.value)
                if match:
                    user_id = int(match.group(1))
            elif "withdraw id" in name:
                withdraw_id = field.value.strip()
        return user_id, withdraw_id

    @discord.ui.button(label="✅ Approve & Close", style=discord.ButtonStyle.green, emoji="🪙", custom_id="withdraw_gold:approve")
    async def approve_gold(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.database import check_permission

        user_id, withdraw_id = self._parse_withdraw_info(interaction.message)
        if not user_id or not withdraw_id:
            return await interaction.response.send_message("❌ Could not load withdrawal data.", ephemeral=True)

        server_data = get_server_data(str(interaction.guild.id))
        cashier_role_id = server_data.get("cashier_role")
        has_role = cashier_role_id and any(role.id == int(cashier_role_id) for role in interaction.user.roles)
        has_perm = not check_permission(interaction.user.id, "cashier") or not check_permission(interaction.user.id, "admin")
        if not (has_role or has_perm):
            return await interaction.response.send_message("❌ You don't have permission!", ephemeral=True)

        history = get_user_data(user_id, "withdraw_history") or {}
        if history.get(withdraw_id, {}).get("status") != "pending":
            return await interaction.response.send_message("❌ This withdrawal is already processed.", ephemeral=True)

        modal = WithdrawApproveModal(interaction.message, user_id, withdraw_id, "gold_ticket")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="❌ Reject & Refund", style=discord.ButtonStyle.red, emoji="🚫", custom_id="withdraw_gold:reject")
    async def reject_gold(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.database import check_permission

        user_id, withdraw_id = self._parse_withdraw_info(interaction.message)
        if not user_id or not withdraw_id:
            return await interaction.response.send_message("❌ Could not load withdrawal data.", ephemeral=True)

        server_data = get_server_data(str(interaction.guild.id))
        cashier_role_id = server_data.get("cashier_role")
        has_role = cashier_role_id and any(role.id == int(cashier_role_id) for role in interaction.user.roles)
        has_perm = not check_permission(interaction.user.id, "cashier") or not check_permission(interaction.user.id, "admin")
        if not (has_role or has_perm):
            return await interaction.response.send_message("❌ You don't have permission!", ephemeral=True)

        history = get_user_data(user_id, "withdraw_history") or {}
        withdraw_data = history.get(withdraw_id, {})
        if withdraw_data.get("status") != "pending":
            return await interaction.response.send_message("❌ This withdrawal is already processed.", ephemeral=True)

        modal = WithdrawRejectModal(interaction.message, user_id, withdraw_id, is_gold=True)
        await interaction.response.send_modal(modal)







class WithdrawTicketView(discord.ui.View):
    """Persistent view inside a withdrawal ticket channel (ticket mode for all non-Gold methods)"""

    def __init__(self):
        super().__init__(timeout=None)

    def _parse_withdraw_info(self, message: discord.Message):
        if not message or not message.embeds:
            return None, None
        embed = message.embeds[0]
        user_id = None
        withdraw_id = None
        for field in embed.fields:
            name = field.name.lower()
            if "user" in name:
                match = re.search(r"\((\d{17,20})\)", field.value)
                if match:
                    user_id = int(match.group(1))
            elif "withdraw id" in name:
                withdraw_id = field.value.strip()
        return user_id, withdraw_id

    @discord.ui.button(label="✅ Approve & Close", style=discord.ButtonStyle.green, emoji="💰", custom_id="withdraw_ticket:approve")
    async def approve_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.database import check_permission

        user_id, withdraw_id = self._parse_withdraw_info(interaction.message)
        if not user_id or not withdraw_id:
            return await interaction.response.send_message("❌ Could not load withdrawal data.", ephemeral=True)

        server_data = get_server_data(str(interaction.guild.id))
        cashier_role_id = server_data.get("cashier_role")
        has_role = cashier_role_id and any(role.id == int(cashier_role_id) for role in interaction.user.roles)
        has_perm = not check_permission(interaction.user.id, "cashier") or not check_permission(interaction.user.id, "admin")
        if not (has_role or has_perm):
            return await interaction.response.send_message("❌ You don't have permission!", ephemeral=True)

        history = get_user_data(user_id, "withdraw_history") or {}
        if history.get(withdraw_id, {}).get("status") != "pending":
            return await interaction.response.send_message("❌ This withdrawal is already processed.", ephemeral=True)

        modal = WithdrawApproveModal(interaction.message, user_id, withdraw_id, "ticket")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="❌ Reject & Refund", style=discord.ButtonStyle.red, emoji="🚫", custom_id="withdraw_ticket:reject")
    async def reject_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.database import check_permission

        user_id, withdraw_id = self._parse_withdraw_info(interaction.message)
        if not user_id or not withdraw_id:
            return await interaction.response.send_message("❌ Could not load withdrawal data.", ephemeral=True)

        server_data = get_server_data(str(interaction.guild.id))
        cashier_role_id = server_data.get("cashier_role")
        has_role = cashier_role_id and any(role.id == int(cashier_role_id) for role in interaction.user.roles)
        has_perm = not check_permission(interaction.user.id, "cashier") or not check_permission(interaction.user.id, "admin")
        if not (has_role or has_perm):
            return await interaction.response.send_message("❌ You don't have permission!", ephemeral=True)

        history = get_user_data(user_id, "withdraw_history") or {}
        withdraw_data = history.get(withdraw_id, {})
        if withdraw_data.get("status") != "pending":
            return await interaction.response.send_message("❌ This withdrawal is already processed.", ephemeral=True)

        modal = WithdrawRejectModal(interaction.message, user_id, withdraw_id, is_ticket=True)
        await interaction.response.send_modal(modal)


def build_deposit_method_layout(user_id: str, methods: dict, lang: str) -> discord.ui.LayoutView:
    """V2: payment method picker — select inside container."""
    from discord import ui
    from modules.ui_v2 import ACCENT_SUCCESS, panel_with_controls

    return panel_with_controls(
        title=t("deposit.select_payment_method_label", lang=lang),
        body=t("deposit.select_payment_method_description", lang=lang),
        footer=t("deposit.footer", lang=lang),
        emoji="💳",
        accent=ACCENT_SUCCESS,
        controls=[DepositMethodFlowSelect(user_id, methods, lang)],
        section_label=t("deposit.select_payment_method_placeholder", lang=lang),
    )


class DepositMethodPickerView(discord.ui.View):
    """Legacy alias — use build_deposit_method_layout."""

    def __init__(self, user_id: str, methods: dict, lang: str):
        super().__init__(timeout=180)
        self.add_item(DepositMethodFlowSelect(user_id, methods, lang))


class DepositMethodFlowSelect(discord.ui.Select):
    """Routes deposit to in-game instructions or cashier ticket flow."""

    def __init__(self, user_id: str, methods: dict, lang: str):
        self.user_id = user_id
        self.methods = methods
        self.lang = lang
        options = [
            discord.SelectOption(
                label=method_info.get("name", method_key),
                description=(method_info.get("description", "") or "")[:100] or None,
                emoji=method_info.get("emoji", "💳"),
                value=method_key,
            )
            for method_key, method_info in methods.items()
        ]
        super().__init__(
            placeholder=t("deposit.select_payment_method_placeholder", lang=lang),
            options=options,
            min_values=1,
            max_values=1,
            custom_id="deposit:method_flow_select",
        )

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message(
                t("deposit.not_your_panel", lang=self.lang),
                ephemeral=True,
            )

        method_key = self.values[0]
        method_info = self.methods.get(method_key, {})

        from modules.ingame_deposit import is_ingame_method, is_ingame_configured, get_ingame_config

        if is_ingame_method(method_key, method_info):
            cfg = get_ingame_config()
            if not is_ingame_configured(cfg):
                from modules.ui_v2 import error_panel, send_ephemeral
                return await send_ephemeral(
                    interaction,
                    error_panel(
                        t("deposit.ingame_not_configured_title", lang=self.lang),
                        t("deposit.ingame_not_configured_description", lang=self.lang),
                    ),
                )
            await interaction.response.send_modal(GrowIDDepositModal(self.user_id, self.lang, cfg))
            return

        await interaction.response.send_modal(
            DepositAmountModal(self.user_id, method_key, method_info, self.lang)
        )


class GrowIDDepositModal(discord.ui.Modal):
    """Collect / confirm GrowID, then show in-game deposit instructions."""

    def __init__(self, user_id: str, lang: str, ingame_cfg: dict, *, skip_bonus: bool = False):
        super().__init__(title=t("deposit.growid_modal_title", lang=lang))
        self.user_id = user_id
        self.lang = lang
        self.ingame_cfg = ingame_cfg
        self.skip_bonus = skip_bonus

        existing = get_user_data(int(user_id), "growid") or {}
        current = (existing.get("growid") or "") if isinstance(existing, dict) else ""

        self.growid_input = discord.ui.TextInput(
            label=t("deposit.growid_modal_label", lang=lang),
            placeholder=t("deposit.growid_modal_placeholder", lang=lang),
            default=current[:32] if current else None,
            required=True,
            max_length=32,
        )
        self.add_item(self.growid_input)

    async def on_submit(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message(
                t("deposit.not_your_panel", lang=self.lang),
                ephemeral=True,
            )

        growid = self.growid_input.value.strip()
        if not growid:
            return await interaction.response.send_message(
                t("deposit.growid_not_set_description", lang=self.lang),
                ephemeral=True,
            )

        set_user_data(int(self.user_id), "growid", {"growid": growid})

        world = str(self.ingame_cfg.get("world", "")).strip()
        bot_name = str(self.ingame_cfg.get("bot_name", "")).strip()
        rate = float(self.ingame_cfg.get("dl_to_coin_rate", 0) or 0)

        instructions_view = build_ingame_instructions_view(
            self.lang, world, bot_name, growid, rate
        )

        if self.skip_bonus:
            return await interaction.response.send_message(view=instructions_view)

        from cogs.deposit_bonus_ui import show_bonus_picker_or_skip
        from modules.ui_v2 import send_ephemeral

        async def _after_bonus(inter: discord.Interaction, _bonus_id: str | None):
            await send_ephemeral(inter, instructions_view)

        await show_bonus_picker_or_skip(
            interaction,
            int(self.user_id),
            self.lang,
            _after_bonus,
            title_key="bonus.picker_ingame_title",
            description_key="bonus.picker_ingame_description",
        )


class PaymentMethodSelect(discord.ui.Select):
    """Payment method selection dropdown (used inside deposit amount modal)."""

    def __init__(self, methods: dict):
        options = [
            discord.SelectOption(
                label=method_info.get("name", method_key),
                description=method_info.get("description", "")[:100] if method_info.get("description") else "",
                emoji=method_info.get("emoji", "💳"),
                value=method_key,
            )
            for method_key, method_info in methods.items()
        ]
        super().__init__(placeholder="Select payment method", options=options, min_values=1, max_values=1, custom_id="deposit:method_select")


class BonusSelect(discord.ui.Select):
    """Optional bonus selection dropdown shown inside the deposit modal."""

    def __init__(self, bonuses: dict, lang: str = "en"):
        options = [
            discord.SelectOption(
                label="No bonus",
                description="Skip — no bonus for this deposit",
                emoji="➖",
                value="__none__",
            )
        ]
        for bid, info in bonuses.items():
            btype = info.get("type", "fixed")
            if btype == "fixed":
                wt = info.get("wager_target_multiplier", 2)
                mw = info.get("max_withdrawal_multiplier", 4)
                desc = f"Grow {wt}× → withdraw up to {mw}× your deposit"
            else:
                pct = info.get("percentage", 0)
                wm = info.get("wager_multiplier", 1)
                desc = f"+{pct}% bonus · wager {wm}× to unlock"
            # User description overrides auto-generated one
            if info.get("description"):
                desc = info["description"]
            options.append(discord.SelectOption(
                label=info.get("name", bid)[:100],
                description=desc[:100],
                emoji="🎁",
                value=bid,
            ))
        super().__init__(
            placeholder=t("bonus.select_placeholder", lang=lang),
            options=options,
            min_values=1,
            max_values=1,
            custom_id="deposit:bonus_select",
        )


class DepositAmountModal(discord.ui.Modal, title="💳 Start Deposit"):
    """Amount (+ optional bonus) for cashier-handled payment methods."""

    amount_input = discord.ui.TextInput(
        label="Amount (COIN)",
        placeholder="Amount in (COIN)",
        required=True,
        max_length=10,
    )

    def __init__(
        self,
        user_id: str,
        method_key: str,
        method_info: dict,
        lang: str,
        *,
        skip_bonus_field: bool = False,
    ):
        super().__init__()
        self.user_id = user_id
        self.method_key = method_key
        self.method_info = method_info
        self.lang = lang

        self.bonus_select = None
        if not skip_bonus_field:
            enabled_bonuses = bonus_engine.get_enabled_bonus_templates()
            if enabled_bonuses:
                bonus_select_comp = BonusSelect(enabled_bonuses, lang=self.lang)
                bonus_label = discord.ui.Label(
                    text=t("bonus.modal_label", lang=self.lang),
                    component=bonus_select_comp,
                )
                self.bonus_select = bonus_label
                self.add_item(bonus_label)

    async def on_submit(self, inter: discord.Interaction):
        if str(inter.user.id) != str(self.user_id):
            return await inter.response.send_message(
                t("deposit.not_your_panel", lang=self.lang),
                ephemeral=True,
            )

        try:
            amount = float(self.amount_input.value)
            if amount <= 0:
                raise ValueError()
        except Exception:
            return await inter.response.send_message(
                t("admin_panel.invalid_amount", lang=self.lang),
                ephemeral=True,
            )

        deposit_settings = get_data("server/deposit_settings") or {}
        min_deposit = float(deposit_settings.get("min_deposit", 0) or 0)
        if min_deposit > 0 and amount < min_deposit:
            return await inter.response.send_message(
                embed=discord.Embed(
                    title="❌ Minimum Deposit Required",
                    description=(
                        f"Minimum deposit is **{format_balance(min_deposit, 'real')}**.\n"
                        f"You entered **{format_balance(amount, 'real')}**."
                    ),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

        method_key = self.method_key
        method_info = self.method_info

        # Bonus selection (optional)
        selected_bonus_id = None
        if self.bonus_select and self.bonus_select.component.values:
            val = self.bonus_select.component.values[0]
            if val != "__none__":
                selected_bonus_id = val
        if not selected_bonus_id:
            selected_bonus_id = bonus_engine.get_pending_deposit_bonus(int(self.user_id))

        # Create ticket-like deposit request
        user_id_int = int(self.user_id)
        import time
        deposit_id = str(int(time.time()))

        # Store temporary deposit data
        deposit_data = {
            "deposit_id": deposit_id,
            "amount": amount,
            "method_key": method_key,
            "method_name": method_info.get("name"),
            "bonus_id": selected_bonus_id,
            "status": "awaiting_confirmation",
            "timestamp": deposit_id,
            "user_id": user_id_int
        }

        # Get server data for deposit category and cashier role
        from modules.database import get_server_data
        server_data = get_server_data(str(inter.user.guild.id))
        deposit_category_id = server_data.get("deposit_category")
        cashier_role_id = server_data.get("cashier_role")
        await inter.response.send_message(
            "✅ Your deposit request is being processed. Please wait...",ephemeral=True)
        # Get or create deposit category
        if deposit_category_id:
            deposit_category = inter.user.guild.get_channel(deposit_category_id)
            if not deposit_category:
                deposit_category_id = None
        else:
            deposit_category_id = None

        if not deposit_category_id:
            try:
                deposit_category = await inter.user.guild.create_category(
                    name="💳 Deposit Tickets",
                    reason="Deposit ticket system"
                )
                deposit_category_id = deposit_category.id
                server_data["deposit_category"] = deposit_category_id
                from modules.database import set_server_data
                set_server_data(str(inter.user.guild.id), server_data)
            except Exception as e:
                return await inter.response.send_message(
                    f"❌ Failed to create deposit category: {str(e)}",
                    ephemeral=True
                )

        # Create ticket channel
        user = inter.user
        channel_name = f"deposit-{user.name.lower()}-{deposit_id[-4:]}"
        
        try:
            ticket_channel = await inter.user.guild.create_text_channel(
                channel_name,
                category=deposit_category,
                reason=f"Deposit ticket for {user.name}"
            )
            
            # Set permissions
            await ticket_channel.set_permissions(
                inter.user.guild.default_role,
                read_messages=False,
                send_messages=False
            )
            await ticket_channel.set_permissions(
                user,
                read_messages=True,
                send_messages=True
            )
            if cashier_role_id:
                cashier_role = inter.user.guild.get_role(int(cashier_role_id))
                if cashier_role:
                    await ticket_channel.set_permissions(
                        cashier_role,
                        read_messages=True,
                        send_messages=True,
                        manage_messages=True
                    )
            
            # Set bot permissions
            await ticket_channel.set_permissions(
                inter.user.guild.me,
                read_messages=True,
                send_messages=True,
                manage_messages=True,
                manage_channels=True
            )
            
            # Build exchange comparison text from existing admin panel rates
            rates_data = get_data("server/exchange_rates") or {}
            coin_usd_rate = float(rates_data.get("coin_usd_rate", 0.10) or 0.10)
            custom_rates = rates_data.get("custom_rates", []) if isinstance(rates_data.get("custom_rates", []), list) else []

            compare_lines = [f"• 💵 USD: **{amount * coin_usd_rate:.2f}$**"]
            for rate in custom_rates[:6]:
                try:
                    r_name = str(rate.get("name", "Unknown"))
                    r_emoji = str(rate.get("emoji", "🪙"))
                    r_amount = float(rate.get("amount", 0) or 0)
                    if r_amount > 0:
                        compare_lines.append(
                            f"• {r_emoji} {r_name}: **{_fmt_num(amount * r_amount)} {r_name}**"
                        )
                except (ValueError, TypeError):
                    continue

            # Create ticket embed
            embed = discord.Embed(
                title="💳 Deposit Ticket",
                description=(
                    f"Deposit request from {user.mention}\n\n"
                    f"📊 **Current Exchange Comparison**\n"
                    + "\n".join(compare_lines)
                ),
                color=discord.Color.gold()
            )
            embed.add_field(
                name="👤 User",
                value=f"{user.mention} ({user.id})",
                inline=True
            )
            embed.add_field(
                name="💰 Amount",
                value=format_balance(amount, "real"),
                inline=True
            )
            embed.add_field(
                name="💳 Payment Method",
                value=method_info.get("name"),
                inline=True
            )
            if selected_bonus_id:
                templates = bonus_engine.get_bonus_templates()
                btemplate = templates.get(selected_bonus_id, {})
                embed.add_field(
                    name=t("bonus.selected_field", user_id=str(inter.user.id)),
                    value=btemplate.get("name", selected_bonus_id),
                    inline=True
                )

            embed.add_field(
                name="🆔 Deposit ID",
                value=deposit_id,
                inline=False
            )
            embed.set_footer(text="Vegas Casino | Deposit System")
            embed.timestamp = inter.created_at
            history = get_user_data(self.user_id, "deposit_history") or {}
            deposit_data.update({
                "status": "pending",
                "timestamp": str(int(time.time())),
                "requested_amount": deposit_data.get("amount", 0),
                "method": method_info.get("name"),
                "confirmed_amount": 0,
                "managed_by": None
            })
            
            history[deposit_id] = deposit_data
            set_user_data(self.user_id, "deposit_history", history)
            # Create ticket view
            view = DepositTicketView()

            deposit_message = await ticket_channel.send(embed=embed, view=view)
            deposit_data["ticket_channel_id"] = ticket_channel.id
            deposit_data["ticket_message_id"] = deposit_message.id

            history = get_user_data(user_id_int, "deposit_history") or {}
            history[deposit_id] = deposit_data
            set_user_data(user_id_int, "deposit_history", history)

            # Send success message to user
            success_embed = discord.Embed(
                title="✅ Deposit Request Submitted",
                description="Your deposit request has been submitted successfully!",
                color=discord.Color.green()
            )
            success_embed.add_field(
                name="📋 Request Details",
                value=f"**Amount:** {format_balance(amount, 'real')}\n**Method:** {method_info.get('name')}\n**Ticket:** {ticket_channel.mention}",
                inline=False
            )
            success_embed.add_field(
                name="⏳ Status",
                value="Your request is being reviewed by our team. Please wait for approval.",
                inline=False
            )
            success_embed.set_footer(text="Vegas Casino | Deposit System")
            
            await inter.followup.send(embed=success_embed, ephemeral=True)
            
        except Exception as e:
            return await inter.response.send_message(
                f"❌ Failed to create deposit ticket: {str(e)}",
                ephemeral=True
            )
            
        



class DepositAutoAddView(discord.ui.View):
    """View for confirming auto-add of deposit"""
    
    def __init__(self, user_id: int, deposit_id: str, lang: str, ticket_channel_id: int, ticket_message_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.deposit_id = deposit_id
        self.lang = lang
        self.ticket_channel_id = ticket_channel_id
        self.ticket_message_id = ticket_message_id

    @discord.ui.button(
        label="Yes, Auto-Add",
        style=discord.ButtonStyle.green,
        emoji="✅",
        custom_id="deposit_ticket:auto_add_yes"
    )
    async def auto_add_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ── Guard: only process once ──────────────────────────────────────
        history_pre = get_user_data(self.user_id, "deposit_history") or {}
        pre_data = history_pre.get(self.deposit_id, {})
        if pre_data.get("status") != "pending":
            return await interaction.response.send_message(
                "❌ This deposit has already been processed.", ephemeral=True
            )

        dep_amount = float(pre_data.get("amount", 0))
        allowed, limit = _cashier_can_handle(interaction.user, dep_amount, str(interaction.guild.id))
        if not allowed:
            from modules.utils import format_balance as _fb
            return await interaction.response.send_message(
                f"🔒 This deposit exceeds the cashier limit (**{_fb(limit, 'real')}**). "
                "Only admins can approve deposits above this amount.",
                ephemeral=True
            )

        # Add deposit to history with auto status
        player = Player(self.user_id)
        history = get_user_data(self.user_id, "deposit_history") or {}
        deposit_data = history.get(self.deposit_id, {})
        
        deposit_data.update({
            "status": "completed",
            "timestamp": str(int(time.time())),
            "requested_amount": deposit_data.get("amount", 0),
            "confirmed_amount": deposit_data.get("amount", 0),
            "managed_by": interaction.user.id
        })
        history[self.deposit_id] = deposit_data
        set_user_data(self.user_id, "deposit_history", history)
        
        # Add to balance
        deposit_amount_val = int(deposit_data.get("amount", 0))
        player.add_balance("real", deposit_amount_val)
        player.record_deposit(deposit_amount_val)
        race_engine.add_entry(self.user_id, deposit_amount_val, "deposit")

        # Activate bonus if one was selected
        selected_bonus_id = deposit_data.get("bonus_id")
        if selected_bonus_id:
            ok, err, bonus_amt = bonus_engine.activate_bonus(
                self.user_id, selected_bonus_id, deposit_amount_val
            )
            if ok and bonus_amt > 0:
                player.add_balance("real", bonus_amt)
                deposit_data["bonus_activated"] = True
                deposit_data["bonus_amount_credited"] = bonus_amt
                history = get_user_data(self.user_id, "deposit_history") or {}
                history[self.deposit_id] = deposit_data
                set_user_data(self.user_id, "deposit_history", history)

        ticket_channel = interaction.guild.get_channel(self.ticket_channel_id)
        if not isinstance(ticket_channel, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ Ticket channel not found.",
                ephemeral=True
            )

        try:
            ticket_message = await ticket_channel.fetch_message(self.ticket_message_id)
        except Exception:
            return await interaction.response.send_message(
                "❌ Could not load the ticket message.",
                ephemeral=True
            )

        embed = ticket_message.embeds[0]
        embed.color = discord.Color.green()
        embed.title = "✅ Deposit Approved"
        embed.add_field(
            name="👨‍💼 Approved By",
            value=f"{interaction.user.mention}",
            inline=True
        )
        embed.add_field(
            name="⏰ Approved At",
            value=f"<t:{int(time.time())}:F>",
            inline=True
        )
        await ticket_message.edit(embed=embed, view=None)

        success_embed = discord.Embed(
            title="✅ Auto-Add Completed",
            description=f"Deposit of {format_balance(deposit_data.get('amount', 0), 'real')} has been added to the user's balance.",
            color=discord.Color.green()
        )
        success_embed.add_field(
            name="👤 User",
            value=f"<@{self.user_id}>",
            inline=True
        )
        success_embed.add_field(
            name="💰 Amount",
            value=format_balance(deposit_data.get('amount', 0), 'real'),
            inline=True
        )

        await interaction.response.edit_message(embed=success_embed, view=None)

        user = interaction.guild.get_member(self.user_id)
        if user:
            try:
                embed_dm = discord.Embed(
                    title="✅ Deposit Approved",
                    description=f"Your deposit of {format_balance(deposit_data.get('amount', 0), 'real')} has been approved!",
                    color=discord.Color.green()
                )
                embed_dm.add_field(
                    name="💰 New Balance",
                    value=format_balance(player.balance, "real"),
                    inline=False
                )
                if deposit_data.get("bonus_activated"):
                    from modules.utils import get_user_lang
                    _dlang = get_user_lang(self.user_id)
                    embed_dm.add_field(
                        name=t("bonus.credited_field", lang=_dlang),
                        value=t("bonus.credited_value", lang=_dlang,
                                amount=format_balance(deposit_data.get("bonus_amount_credited", 0), "real")),
                        inline=False
                    )
                await user.send(embed=embed_dm)
            except:
                pass  # User may have DMs disabled
        await _send_deposit_log(interaction.guild, "approved", self.deposit_id, deposit_data, self.user_id, interaction.user)
        await interaction.channel.send(f"🔒 Closing this ticket channel... in 10 seconds")
        await asyncio.sleep(10)
        await interaction.channel.delete(reason="Deposit processed and ticket closed")


    @discord.ui.button(
        label="No, Confirm Amount",
        style=discord.ButtonStyle.primary,
        emoji="❓"
    )
    async def auto_add_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Show modal to confirm exact amount
        modal = DepositConfirmAmountModal(
            self.user_id,
            self.deposit_id,
            self.lang,
            ticket_channel_id=self.ticket_channel_id,
            ticket_message_id=self.ticket_message_id
        )
        
        await interaction.response.send_modal(modal)


class DepositConfirmAmountModal(discord.ui.Modal, title="💳 Confirm Deposit Amount"):
    """Modal to confirm the exact amount being deposited"""
    amount_input = discord.ui.TextInput(
        label="Actual Amount Paid (USD)",
        placeholder="Enter the exact amount player deposited",
        required=True,
        max_length=10
    )
    

    def __init__(self, user_id: int, deposit_id: str, lang: str, ticket_channel_id: int, ticket_message_id: int):
        super().__init__()
        self.user_id = user_id
        self.deposit_id = deposit_id
        self.lang = lang
        self.ticket_channel_id = ticket_channel_id
        self.ticket_message_id = ticket_message_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            confirmed_amount = float(self.amount_input.value)
            if confirmed_amount <= 0:
                raise ValueError()
        except Exception:
            return await interaction.response.send_message(
                t("admin_panel.invalid_amount", lang=self.lang),
                ephemeral=True
            )

        # ── Guard: only process once ──────────────────────────────────────
        player = Player(self.user_id)
        history = get_user_data(self.user_id, "deposit_history") or {}
        deposit_data = history.get(self.deposit_id, {})
        if deposit_data.get("status") != "pending":
            return await interaction.response.send_message(
                "❌ This deposit has already been processed.", ephemeral=True
            )

        # Add to deposit history with confirmed amount
        
        deposit_data.update({
            "amount": confirmed_amount,
            "status": "completed",
            "timestamp": str(int(time.time())),
            "auto_added": False,
            "requested_amount": deposit_data.get("amount", 0),
            "confirmed_amount": confirmed_amount,
            "managed_by": interaction.user.id
        })
        
        history[self.deposit_id] = deposit_data
        set_user_data(self.user_id, "deposit_history", history)
        
        # Add confirmed amount to balance
        confirmed_amount_int = int(confirmed_amount)
        player.add_balance("real", confirmed_amount_int)
        player.record_deposit(confirmed_amount_int)
        race_engine.add_entry(self.user_id, confirmed_amount_int, "deposit")

        # Activate bonus if one was selected at deposit time
        selected_bonus_id = deposit_data.get("bonus_id")
        if selected_bonus_id:
            ok, err, bonus_amt = bonus_engine.activate_bonus(
                self.user_id, selected_bonus_id, confirmed_amount_int
            )
            if ok and bonus_amt > 0:
                player.add_balance("real", bonus_amt)
                deposit_data["bonus_activated"] = True
                deposit_data["bonus_amount_credited"] = bonus_amt
                history = get_user_data(self.user_id, "deposit_history") or {}
                history[self.deposit_id] = deposit_data
                set_user_data(self.user_id, "deposit_history", history)

        ticket_channel = interaction.guild.get_channel(self.ticket_channel_id)
        if not isinstance(ticket_channel, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ Ticket channel not found.",
                ephemeral=True
            )

        try:
            ticket_message = await ticket_channel.fetch_message(self.ticket_message_id)
        except Exception:
            return await interaction.response.send_message(
                "❌ Could not load the ticket message.",
                ephemeral=True
            )

        embed = ticket_message.embeds[0]
        embed.color = discord.Color.green()
        embed.title = "✅ Deposit Approved"
        embed.add_field(
            name="👨‍💼 Approved By",
            value=f"{interaction.user.mention}",
            inline=True
        )
        embed.add_field(
            name="⏰ Approved At",
            value=f"<t:{int(time.time())}:F>",
            inline=True
        )
        await ticket_message.edit(embed=embed, view=None)
        await interaction.response.defer()
        
        user = interaction.guild.get_member(self.user_id)
        if user:
            try:
                embed_dm = discord.Embed(
                    title="✅ Deposit Approved",
                    description=f"Your deposit of {format_balance(confirmed_amount, 'real')} has been approved!",
                    color=discord.Color.green()
                )
                embed_dm.add_field(
                    name="💰 New Balance",
                    value=format_balance(player.balance, "real"),
                    inline=False
                )
                await user.send(embed=embed_dm)

        
            except:
                pass
        await _send_deposit_log(interaction.guild, "approved", self.deposit_id, deposit_data, self.user_id, interaction.user, confirmed_amount=confirmed_amount)
        await interaction.channel.send(f"🔒 Closing this ticket channel... in 10 seconds")
        await asyncio.sleep(10)
        await interaction.channel.delete(reason="Deposit processed and ticket closed")  # User may have DMs disabled
            

def _get_effective_min_withdrawal(user_id: int, server_data: dict) -> tuple[int, str]:
    """Returns (min_amount, mode_label). Promo completed wager can override server min."""
    promo_min, _ = promo_engine.get_promo_withdraw_limits(user_id)
    if promo_min is not None:
        return promo_min, f"{format_balance(promo_min, 'real')} (promo)"
    fixed_min = int(server_data.get("min_withdrawal", 100) or 100)
    return fixed_min, format_balance(fixed_min, "real")


def _get_withdraw_wager_requirement(user_id: int, server_data: dict) -> tuple[int, int, int]:
    """
    Returns (required_wager, wagered_since_deposit, remaining) based on:
      - last_deposit * withdraw_min_multiplier  (server setting)
      - plus bonus wager_requirement if user has an active bonus

    If withdraw_min_multiplier is 0 or not set, returns (0, 0, 0) — no gate.
    """
    multiplier = float(server_data.get("withdraw_min_multiplier", 0) or 0)
    if multiplier <= 0:
        return 0, 0, 0

    stats = get_user_data(user_id, "stats") or {}
    last_deposit = int(stats.get("last_deposit_amount", 0))
    if last_deposit <= 0:
        return 0, 0, 0

    required = int(last_deposit * multiplier)

    # Add bonus wager requirement if active
    active_bonus = bonus_engine.get_active_bonus(str(user_id))
    if active_bonus:
        bonus_wager_req = int(active_bonus.get("wager_requirement", 0))
        required += bonus_wager_req

    total_wagered = int(stats.get("total_wagered", 0))
    wagered_at_deposit = int(stats.get("wagered_at_last_deposit", 0))
    wagered_since = max(0, total_wagered - wagered_at_deposit)
    remaining = max(0, required - wagered_since)
    return required, wagered_since, remaining


async def _send_deposit_log(guild: discord.Guild, action: str, deposit_id: str, deposit_data: dict,
                            user_id: int, managed_by: discord.Member, confirmed_amount: float = None):
    """Send a deposit log embed to the configured deposit log channel."""
    deposit_settings = get_data("server/deposit_settings") or {}
    log_channel_id = deposit_settings.get("channel_id")
    if not log_channel_id:
        return
    log_channel = guild.get_channel(int(log_channel_id))
    if not isinstance(log_channel, discord.TextChannel):
        return

    amount = confirmed_amount if confirmed_amount is not None else float(deposit_data.get("amount", 0))
    requested_amount = float(deposit_data.get("amount", 0))

    if action == "approved":
        color = discord.Color.green()
        title = "✅ Deposit Approved"
    else:
        color = discord.Color.red()
        title = "❌ Deposit Rejected"

    embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
    embed.add_field(name="👤 User", value=f"<@{user_id}>", inline=True)
    embed.add_field(name="👨‍💼 Managed By", value=managed_by.mention, inline=True)
    embed.add_field(name="💰 Amount", value=format_balance(amount, "real"), inline=True)
    if action == "approved" and confirmed_amount is not None and confirmed_amount != requested_amount:
        embed.add_field(name="📋 Requested", value=format_balance(requested_amount, "real"), inline=True)
    embed.add_field(name="🆔 Deposit ID", value=deposit_id, inline=False)
    try:
        await log_channel.send(embed=embed)
    except Exception:
        pass


async def _send_withdraw_log(guild: discord.Guild, member, withdraw_data: dict, managed_by: discord.Member, attachment: discord.Attachment = None, note: str = None):
    """Send a withdrawal log embed to the configured withdraw log channel."""
    server_data = get_server_data(str(guild.id))
    log_channel_id = server_data.get("withdraw_log_channel")
    if not log_channel_id:
        return
    log_channel = guild.get_channel(int(log_channel_id))
    if not isinstance(log_channel, discord.TextChannel):
        return
    amount = float(withdraw_data.get("amount", 0))
    method = withdraw_data.get("method_name", "Unknown")
    address = withdraw_data.get("address", "?")
    user_id = withdraw_data.get("user_id")
    user_mention = member.mention if member else (f"<@{user_id}>" if user_id else "Unknown")
    desc = f"{user_mention} **{format_balance(amount, 'real')}** çekti\n{method} → `{address}`"
    if note:
        desc += f"\n\n📝 {note}"
    embed = discord.Embed(description=desc, color=discord.Color.green(), timestamp=discord.utils.utcnow())
    embed.set_footer(text=f"Onaylayan: {managed_by.display_name}")
    try:
        if member:
            await log_channel.set_permissions(member, send_messages=True, read_messages=True, view_channel=True)
        if attachment:
            file = await attachment.to_file()
            embed.set_image(url=f"attachment://{file.filename}")
            await log_channel.send(content=user_mention, embed=embed, file=file)
        else:
            await log_channel.send(content=user_mention, embed=embed)
    except Exception:
        pass


def _cashier_can_handle(user: discord.Member, deposit_amount: float, guild_id: str) -> tuple[bool, float]:
    """
    Returns (allowed, limit).
    Per-user limit takes precedence over global. Admins always bypass.
    """
    from modules.database import check_permission
    deposit_settings = get_data("server/deposit_settings") or {}

    # Per-user limit has priority
    user_limits = deposit_settings.get("cashier_user_limits", {})
    user_limit  = float(user_limits.get(str(user.id), 0))
    global_limit = float(deposit_settings.get("cashier_deposit_limit") or 0)

    limit = user_limit if user_limit > 0 else global_limit

    if limit > 0 and deposit_amount > limit:
        is_admin = not check_permission(user.id, "admin")
        return is_admin, limit
    return True, limit


class DepositTicketView(discord.ui.View):
    """View for deposit ticket management by cashiers"""

    def __init__(self, user_id: int = None, deposit_id: str = None, lang: str = None, ticket_channel_id: int = None):
        super().__init__(timeout=None)  # No timeout for tickets
        self.user_id = user_id
        self.deposit_id = deposit_id
        self.lang = lang
        self.ticket_channel_id = ticket_channel_id

    def _parse_ticket_info(self, message: discord.Message):
        if not message or not message.embeds:
            return None, None

        embed = message.embeds[0]
        user_id = None
        deposit_id = None

        for field in embed.fields:
            name = field.name.lower()
            if "user" in name:
                match = re.search(r"\((\d{17,20})\)", field.value)
                if match:
                    user_id = int(match.group(1))
            elif "deposit id" in name or "depositid" in name:
                deposit_id = field.value.strip()

        return user_id, deposit_id

    def _load_deposit_data(self, interaction: discord.Interaction):
        user_id, deposit_id = self._parse_ticket_info(interaction.message)
        if not user_id or not deposit_id:
            return None, None, None, None

        history = get_user_data(user_id, "deposit_history") or {}
        deposit_data = history.get(deposit_id, {})
        return user_id, deposit_id, deposit_data, history

    @discord.ui.button(
        label="✅ Approve Deposit",
        style=discord.ButtonStyle.green,
        emoji="💰",
        custom_id="deposit_ticket:approve"
    )
    async def approve_deposit(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Approve the deposit and add to user balance"""
        from modules.database import check_permission

        user_id, deposit_id, deposit_data, history = self._load_deposit_data(interaction)
        if not user_id or not deposit_id or not deposit_data:
            return await interaction.response.send_message(
                "❌ Could not load deposit ticket data.",
                ephemeral=True
            )

        server_data = get_server_data(str(interaction.guild.id))
        cashier_role_id = server_data.get("cashier_role")
        has_role = cashier_role_id and any(role.id == int(cashier_role_id) for role in interaction.user.roles)
        has_permission = not check_permission(interaction.user.id, "cashier") or not check_permission(interaction.user.id, "admin")

        if not (has_role or has_permission):
            return await interaction.response.send_message(
                "❌ You don't have permission to approve deposits!",
                ephemeral=True
            )

        # Cashier deposit limit check
        deposit_amount = float(deposit_data.get("amount", 0))
        allowed, limit = _cashier_can_handle(interaction.user, deposit_amount, str(interaction.guild.id))
        if not allowed:
            from modules.utils import format_balance as _fb
            return await interaction.response.send_message(
                f"🔒 This deposit exceeds the cashier limit (**{_fb(limit, 'real')}**). "
                "Only admins can process deposits above this amount.",
                ephemeral=True
            )

        embed = discord.Embed(
            title="Are you Sure?",
            description=f"This approve will add user amount **{format_balance(deposit_data.get('amount', 0))}** to balance. If you want to adjust amount, please use No, Change amount to change the amount of deposit.",
            color=discord.Color.orange()
        )
        view = DepositAutoAddView(
            user_id,
            deposit_id,
            self.lang or "en",
            ticket_channel_id=interaction.channel.id,
            ticket_message_id=interaction.message.id
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(
        label="❌ Reject Deposit",
        style=discord.ButtonStyle.red,
        emoji="🚫",
        custom_id="deposit_ticket:reject"
    )
    async def reject_deposit(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Reject the deposit"""
        from modules.database import check_permission

        user_id, deposit_id, deposit_data, history = self._load_deposit_data(interaction)
        if not user_id or not deposit_id or not deposit_data:
            return await interaction.response.send_message(
                "❌ Could not load deposit ticket data.",
                ephemeral=True
            )

        server_data = get_server_data(str(interaction.guild.id))
        cashier_role_id = server_data.get("cashier_role")
        has_role = cashier_role_id and any(role.id == int(cashier_role_id) for role in interaction.user.roles)
        has_permission = not check_permission(interaction.user.id, "cashier") or not check_permission(interaction.user.id, "admin")

        if not (has_role or has_permission):
            return await interaction.response.send_message(
                "❌ You don't have permission to reject deposits!",
                ephemeral=True
            )

        # Cashier deposit limit check
        deposit_amount = float(deposit_data.get("amount", 0))
        allowed, limit = _cashier_can_handle(interaction.user, deposit_amount, str(interaction.guild.id))
        if not allowed:
            from modules.utils import format_balance as _fb
            return await interaction.response.send_message(
                f"🔒 This deposit exceeds the cashier limit (**{_fb(limit, 'real')}**). "
                "Only admins can process deposits above this amount.",
                ephemeral=True
            )

        deposit_data.update({
            "status": "rejected",
            "timestamp": str(int(time.time())),
            "managed_by": interaction.user.id,
            "rejected_at": str(int(time.time()))
        })
        history[deposit_id] = deposit_data
        set_user_data(user_id, "deposit_history", history)

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.title = "❌ Deposit Rejected"
        embed.add_field(
            name="👨‍💼 Rejected By",
            value=f"{interaction.user.mention}",
            inline=True
        )
        embed.add_field(
            name="⏰ Rejected At",
            value=f"<t:{int(time.time())}:F>",
            inline=True
        )

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

        user = interaction.guild.get_member(user_id)
        if user:
            try:
                embed_dm = discord.Embed(
                    title="❌ Deposit Rejected",
                    description=f"Your deposit request of {format_balance(deposit_data.get('amount', 0), 'real')} has been rejected.",
                    color=discord.Color.red()
                )
                await user.send(embed=embed_dm)
            except:
                pass

        await _send_deposit_log(interaction.guild, "rejected", deposit_id, deposit_data, user_id, interaction.user)
        await asyncio.sleep(5)
        try:
            channel = interaction.guild.get_channel(interaction.channel.id)
            if channel:
                await channel.delete(reason="Deposit ticket closed")
        except:
            pass


class ReferralView(discord.ui.View):
    """Referral sistem select menüleri"""
    
    def __init__(self, user_id: str):
        super().__init__(timeout=180)
        self.user_id = user_id
    
    @discord.ui.select(
        placeholder="💰 Manage your referral earnings",
        options=[
            discord.SelectOption(
                label="💰 Withdraw Earnings",
                description="Transfer earnings to your balance",
                emoji="💸",
                value="withdraw"
            ),
            discord.SelectOption(
                label="👥 View Referrals",
                description="See who used your code",
                emoji="👥",
                value="view_referrals"
            )
        ]
    )
    async def referral_actions(self, interaction: discord.Interaction, select: discord.ui.Select):
        """Referral aksiyonları"""
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("❌ This is not your referral panel!", ephemeral=True)
        
        if select.values[0] == "withdraw":
            await self.handle_withdraw(interaction)
        elif select.values[0] == "view_referrals":
            await self.handle_view_referrals(interaction)
    
    async def handle_withdraw(self, interaction: discord.Interaction):
        """Kazancı çek"""
        from modules.player import Player
        
        referrals_data = get_data("server/referrals")
        user_data = referrals_data.get(self.user_id, {})
        available_balance = user_data.get("available_balance", 0)
        
        settings = get_data("server/referral_settings")
        min_withdrawal = settings.get("min_withdrawal", 10)
        
        if available_balance < min_withdrawal:
            embed = discord.Embed(
                title=t("referral.withdraw_error_title", lang="en"),
                description=t("referral.insufficient_earnings", lang="en").format(
                    min_amount=format_balance(min_withdrawal, "real")
                ),
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Bakiyeye ekle
        player = Player(int(self.user_id))
        player.add_balance("real", available_balance)
        
        # Referral bakiyesini sıfırla
        user_data["available_balance"] = 0
        referrals_data[self.user_id] = user_data
        set_data("server/referrals", referrals_data)
        
        embed = discord.Embed(
            title=t("referral.withdraw_success_title", lang="en"),
            description=t("referral.withdraw_success_description", lang="en").format(
                amount=format_balance(available_balance, "real")
            ),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    async def handle_view_referrals(self, interaction: discord.Interaction):
        """Referansları göster"""
        
        referrals_data = get_data("server/referrals")
        user_data = referrals_data.get(self.user_id, {})
        referred_users = user_data.get("referred_users", [])
        
        if not referred_users:
            embed = discord.Embed(
                title=t("referral.referral_list_title", lang="en"),
                description=t("referral.no_referrals", lang="en"),
                color=discord.Color.orange()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        embed = discord.Embed(
            title=t("referral.referral_list_title", lang="en"),
            description=t("referral.referral_list_description", lang="en"),
            color=discord.Color.blue()
        )
        
        for i, user_id in enumerate(referred_users[:10], 1):  # İlk 10 kişi
            try:
                user = await interaction.client.fetch_user(int(user_id))
                user_ref_data = user_data.get("referral_earnings", {}).get(user_id, {})
                total_earned = user_ref_data.get("total_earned", 0)
                embed.add_field(
                    name=f"{i}. {user.name}",
                    value=f"Earned: {format_balance(total_earned, 'real')}",
                    inline=False
                )
            except:
                pass
        
        if len(referred_users) > 10:
            embed.set_footer(text=f"Showing 10 of {len(referred_users)} referrals")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
# Promo Code — UI (Private Room)
# ─────────────────────────────────────────────────────────────────────────────

def _build_active_promo_embed_and_view(user_id, active: dict, member: discord.Member):
    """Build the embed + view for an in-progress promo."""
    template = promo_engine.get_promo_template(active.get("code", "")) or {}
    ptype   = (active.get("type") or template.get("type", "balance")).lower()
    status  = active.get("status", "wagering")
    code    = active.get("code", "—")

    if status == "completed":
        promo_min, promo_max = promo_engine.get_promo_withdraw_limits(user_id)
        embed = discord.Embed(
            title="✅  Promo Complete",
            description=(
                f"**Code:** `{code}`\n\n"
                f"Wagering finished — you can withdraw."
                + (
                    f"\n\n📉 Min: **{format_balance(promo_min, 'real')}**"
                    if promo_min
                    else ""
                )
                + (
                    f"\n📈 Max: **{format_balance(promo_max, 'real')}**"
                    if promo_max
                    else ""
                )
            ),
            color=0x57f287,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Vegas Casino | Promo")
        return embed, discord.ui.View(timeout=60)

    if status == "active" and ptype == "freegame":
        # Still playing free rounds
        rounds_total  = int(active.get("rounds_total", 0))
        rounds_played = int(active.get("rounds_played", 0))
        remaining     = rounds_total - rounds_played
        total_won     = int(active.get("total_winnings", 0))
        game          = active.get("game", "").title()
        bet_amt       = int(active.get("bet_amount", 0))

        embed = discord.Embed(
            title="🎮  Free-Bet Active",
            description=(
                f"**Code:** `{code}`\n\n"
                f"You have **{remaining}** free round(s) remaining.\n"
                f"Game: **{game}** · Bet per round: **{format_balance(bet_amt, 'real')}**\n\n"
                f"💰 Winnings so far: **{format_balance(total_won, 'real')}**\n\n"
                f"> Go to the **🎲 Games** menu to play your free rounds!"
            ),
            color=0x9b59b6,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Vegas Casino | Free Bet System")
        view = discord.ui.View(timeout=60)
        return embed, view

    # Wagering phase
    req  = promo_engine.compute_promo_wager_requirement(active, template)
    if req <= 0:
        req = int(active.get("wager_requirement", 0))
    done = int(active.get("wagered_so_far", 0))
    pct  = int(done / req * 100) if req > 0 else 0
    filled = round(pct / 10)
    bar  = "🟩" * filled + "⬛" * (10 - filled)
    remaining_wager = max(req - done, 0)

    if ptype == "freegame":
        total_won = int(active.get("total_winnings", 0))
        reward_line = f"🎮 Free-game winnings credited: **{format_balance(total_won, 'real')}**"
    else:
        reward_amount = int(active.get("reward_amount", 0))
        reward_line = f"💰 Balance bonus: **{format_balance(reward_amount, 'real')}**"

    embed = discord.Embed(
        title="🎟️  Promo — Wagering Requirement",
        description=(
            f"**Code:** `{code}`\n\n"
            f"{reward_line}\n\n"
            f"**Wager Progress:** {pct}%\n"
            f"{bar}\n"
            f"**{format_balance(done, 'real')}** / **{format_balance(req, 'real')}**\n"
            f"Remaining: **{format_balance(remaining_wager, 'real')}**\n\n"
            f"> Play real-money games to complete your wager requirement!"
        ),
        color=0xf39c12,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Vegas Casino | Promo Wagering")
    view = discord.ui.View(timeout=60)
    return embed, view


class PromoCodeInputModal(discord.ui.Modal, title="🎟️ Redeem Promo Code"):
    """Modal for users to enter a promo code."""

    code_input = discord.ui.TextInput(
        label="Promo Code",
        placeholder="Enter your promo code here…",
        min_length=2,
        max_length=20,
        style=discord.TextStyle.short,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ This is not your panel!", ephemeral=True)

        code = self.code_input.value.strip().upper()
        member = interaction.user
        if interaction.guild:
            member = interaction.guild.get_member(interaction.user.id) or member
        if not isinstance(member, discord.Member):
            member = None
        if interaction.guild:
            from modules.server_tag import check_server_tag

            ok_tag, tag_err = await check_server_tag(
                member if isinstance(member, discord.Member) else None,
                interaction.guild,
                self.user_id,
            )
            if not ok_tag:
                embed = discord.Embed(
                    title="❌ Promo Code Failed",
                    description=tag_err,
                    color=discord.Color.red(),
                )
                embed.set_footer(text="Vegas Casino | Promo Code System")
                return await interaction.response.send_message(embed=embed, ephemeral=True)

        ok, err, template = promo_engine.redeem_promo_code(
            self.user_id,
            code,
            member=member,
            guild=interaction.guild,
        )

        if not ok:
            embed = discord.Embed(
                title="❌ Promo Code Failed",
                description=err,
                color=discord.Color.red(),
            )
            embed.set_footer(text="Vegas Casino | Promo Code System")
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        ptype = template.get("type", "balance")
        new_balance = None
        if ptype == "balance":
            reward = int(template.get("reward_amount", 0))
            player = Player(self.user_id)
            player.add_balance("real", reward)
            new_balance = float(player.get_balance("real"))

        await promo_engine.send_promo_redeemed_image(
            interaction,
            user=interaction.user,
            code=code,
            template=template,
            new_balance=new_balance,
            ephemeral=True,
        )


class PromoRedeemView(discord.ui.View):
    """View displayed when user has no active promo — shows redeem button."""

    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id

    @discord.ui.button(label="🎟️ Enter Promo Code", style=discord.ButtonStyle.primary, emoji="🎟️")
    async def enter_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ This is not your panel!", ephemeral=True)
        await interaction.response.send_modal(PromoCodeInputModal(self.user_id))

    @discord.ui.button(label="🔄 Check Status", style=discord.ButtonStyle.secondary)
    async def check_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ This is not your panel!", ephemeral=True)
        active = promo_engine.get_promo_display_state(self.user_id)
        if active:
            embed, view = _build_active_promo_embed_and_view(self.user_id, active, interaction.user)
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await interaction.response.send_message("✅ No active promo — enter a code to get started!", ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────


def build_ingame_instructions_view(
    lang: str,
    world: str,
    bot_name: str,
    growid: str,
    rate: float,
) -> discord.ui.LayoutView:
    from modules.ui_v2 import ACCENT_SUCCESS, build_detail_panel

    return build_detail_panel(
        title=t("deposit.instructions_title", lang=lang),
        body=t(
            "deposit.instructions_description",
            lang=lang,
            world=world,
            bot_name=bot_name,
            growid=growid,
        ),
        fields={
            t("deposit.donation_box_warning_title", lang=lang): t(
                "deposit.donation_box_warning", lang=lang, bot_name=bot_name, world=world
            ),
            t("deposit.ingame_rate_label", lang=lang): t(
                "deposit.ingame_rate_value", lang=lang, rate=rate
            ),
        },
        accent=ACCENT_SUCCESS,
        emoji="💎",
        footer=t("deposit.footer", lang=lang),
    )


def build_welcome_menu_layout(
    guild_id: str,
    channel_id: str,
    *,
    lang: str = "en",
    owner_mention: str | None = None,
    avatar_url: str | None = None,
    hub_mode: bool = False,
) -> discord.ui.LayoutView:
    """Private room hub — all selects inside one branded container."""
    from discord import ui
    from modules.ui_v2 import ACCENT_BRAND, add_section, build_layout, new_container, panel_markdown

    rooms_data = get_data("server/private_rooms") or {}
    room = (rooms_data.get(guild_id) or {}).get(channel_id) or {}
    if not owner_mention and room.get("owner"):
        owner_mention = f"<@{room['owner']}>"
    owner_mention = owner_mention or "Room owner"

    header = panel_markdown(
        title=t("private_rooms.welcome_message_title", lang=lang),
        body=t("private_rooms.welcome_message_description", lang=lang).format(owner=owner_mention),
        footer="Vegas Casino | Private Room System",
        emoji="🎰",
    )

    main = new_container(accent=ACCENT_BRAND)
    if avatar_url:
        main.add_item(
            ui.Section(ui.TextDisplay(header), accessory=ui.Thumbnail(media=avatar_url))
        )
    else:
        main.add_item(ui.TextDisplay(header))

    from modules.utils import get_user_lang

    owner_id = room.get("owner")
    if owner_id and not owner_mention:
        lang = get_user_lang(int(owner_id))

    add_section(
        main,
        t("private_rooms.section_entertainment", lang=lang),
        EntertainmentSelect(lang, hub_mode=hub_mode),
    )
    add_section(
        main,
        t("private_rooms.section_finance", lang=lang),
        FinanceSelect(lang, hub_mode=hub_mode),
    )
    add_section(
        main,
        t("private_rooms.section_room", lang=lang),
        RoomManagementSelect(lang, hub_mode=hub_mode),
    )
    add_section(
        main,
        t("private_rooms.section_settings", lang=lang),
        SettingsSelect(lang, hub_mode=hub_mode),
    )

    return build_layout(main, timeout=None)


def build_play_hub_menu_layout(
    guild_id: str,
    channel_id: str,
    *,
    lang: str = "en",
) -> discord.ui.LayoutView:
    """Shared persistent menu for play-1..play-5 hub channels."""
    from discord import ui
    from modules.ui_v2 import ACCENT_BRAND, add_section, build_layout, new_container, panel_markdown

    header = panel_markdown(
        title=t("private_rooms.play_hub_title", lang=lang),
        body=t("private_rooms.play_hub_description", lang=lang),
        footer="Vegas Casino | Play Hub",
        emoji="🎮",
    )
    main = new_container(accent=ACCENT_BRAND)
    main.add_item(ui.TextDisplay(header))

    add_section(
        main,
        t("private_rooms.play_hub_section_create", lang=lang),
        PrivateRoomButton(),
    )
    add_section(
        main,
        t("private_rooms.section_entertainment", lang=lang),
        EntertainmentSelect(lang, hub_mode=True),
    )
    add_section(
        main,
        t("private_rooms.section_finance", lang=lang),
        FinanceSelect(lang, hub_mode=True),
    )
    add_section(
        main,
        t("private_rooms.section_room", lang=lang),
        RoomManagementSelect(lang, hub_mode=True),
    )
    add_section(
        main,
        t("private_rooms.section_settings", lang=lang),
        SettingsSelect(lang, hub_mode=True),
    )
    return build_layout(main, timeout=None)


class PlayHubMenuLayout(discord.ui.LayoutView):
    """Persistent play-channel hub menu (Components V2)."""

    def __init__(self, guild_id: str = "", channel_id: str = ""):
        super().__init__(timeout=None)
        built = build_play_hub_menu_layout(guild_id, channel_id)
        for child in built.children:
            self.add_item(child)


class WelcomeMenuLayout(discord.ui.LayoutView):
    """Persistent private-room menu (Components V2)."""

    def __init__(self, guild_id: str = "", channel_id: str = ""):
        super().__init__(timeout=None)
        built = build_welcome_menu_layout(guild_id, channel_id)
        for child in built.children:
            self.add_item(child)


WelcomeMenuView = WelcomeMenuLayout


async def purge_all_private_room_channels(
    guild: discord.Guild,
    *,
    reason: str = "Admin: clear all private rooms",
) -> dict:
    """
    Delete every text channel in the configured private-room category and
    remove this guild's entries from server/private_rooms.
    """
    server_data = get_server_data(str(guild.id))
    category_id = server_data.get("private_category_id")
    if not category_id:
        raise ValueError("private_category_not_set")

    category = guild.get_channel(int(category_id))
    if not category or not isinstance(category, discord.CategoryChannel):
        raise ValueError("private_category_invalid")

    rooms_data = get_data("server/private_rooms") or {}
    guild_id = str(guild.id)
    db_before = len(rooms_data.get(guild_id, {}))

    deleted: list[str] = []
    failed: list[str] = []

    for channel in list(category.channels):
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            await channel.delete(reason=reason)
            deleted.append(channel.name)
        except discord.NotFound:
            deleted.append(channel.name)
        except Exception as e:
            failed.append(f"{channel.name}: {e}")

    if guild_id in rooms_data:
        del rooms_data[guild_id]
    set_data("server/private_rooms", rooms_data)

    return {
        "category_name": category.name,
        "channels_deleted": len(deleted),
        "db_entries_cleared": db_before,
        "deleted_names": deleted,
        "failed": failed,
    }


class _ConfirmClearAllPrivateRoomsView(discord.ui.View):
    """Confirm bulk delete of all private-room category channels."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=60)
        self.guild_id = guild_id

    @discord.ui.button(label="✅ Tüm özel odaları sil", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                "❌ Admin yetkisi gerekli.", ephemeral=True
            )
        if not interaction.guild or interaction.guild.id != self.guild_id:
            return await interaction.response.send_message(
                "❌ Bu onay yalnızca aynı sunucuda kullanılabilir.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        try:
            result = await purge_all_private_room_channels(
                interaction.guild,
                reason=f"clear_all_private_rooms by {interaction.user}",
            )
        except ValueError as e:
            key = str(e)
            if key == "private_category_not_set":
                msg = "Özel oda kategorisi ayarlanmamış. Admin panel → Server Settings → Set Private Category."
            else:
                msg = "Özel oda kategorisi bulunamadı veya geçersiz."
            return await interaction.followup.send(msg, ephemeral=True)

        lines = [
            f"**Kategori:** `{result['category_name']}`",
            f"**Silinen kanal:** {result['channels_deleted']}",
            f"**DB kaydı temizlenen:** {result['db_entries_cleared']}",
        ]
        if result["failed"]:
            lines.append("\n**Hatalar:**\n" + "\n".join(f"• {x}" for x in result["failed"][:15]))
            if len(result["failed"]) > 15:
                lines.append(f"… ve {len(result['failed']) - 15} hata daha")

        self.stop()
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Özel odalar temizlendi",
                description="\n".join(lines),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="❌ İptal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="İptal edildi.", embed=None, view=None)


class PrivateRoomMenuView(discord.ui.View):
    """Özel oda menü view'ı"""
    
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PrivateRoomButton())


class PrivateRooms(commands.Cog):
    """Özel oda sistemi"""
    
    def __init__(self, bot):
        self.bot = bot
        self.check_inactive_rooms.start()
    
    def cog_unload(self):
        """Cog kaldırıldığında task'ı durdur"""
        self.check_inactive_rooms.cancel()
    
    @tasks.loop(minutes=5)
    async def check_inactive_rooms(self):
        """1 saatten fazla etkileşim olmayan odaları sil"""
        rooms_data = get_data("server/private_rooms")
        current_time = int(time.time())
        one_hour = 3600  # 1 saat (saniye cinsinden)
        
        rooms_to_delete = []
        
        for guild_id, channels in rooms_data.items():
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                continue
            
            for channel_id, room_info in channels.items():
                last_activity = room_info.get("last_activity", room_info["created_at"])
                time_diff = current_time - last_activity
                
                # 1 saatten fazla etkileşim yoksa
                if time_diff >= one_hour:
                    channel = guild.get_channel(int(channel_id))
                    
                    if channel:
                        try:
                            # Kullanıcıya bildirim gönder
                            owner_id = room_info.get("owner")
                            if owner_id:
                                user = guild.get_member(owner_id)
                                if user:
                                    embed = discord.Embed(
                                        title=t("private_rooms.room_closed_title", lang="en"),
                                        description=t("private_rooms.room_closed_description", lang="en").format(
                                            channel_name=channel.name,
                                            timestamp=current_time
                                        ),
                                        color=discord.Color.red(),
                                        timestamp=discord.utils.utcnow()
                                    )
                                    embed.set_footer(text="Vegas Casino | Özel Oda Sistemi")
                                    try:
                                        await user.send(embed=embed)
                                    except:
                                        pass  # DM gönderilemezse sessizce devam et
                            
                            # Kanalı sil
                            await channel.delete(reason="1 saat etkileşim olmadığı için otomatik silindi")
                        except:
                            pass
                    
                    # Silinecek olarak işaretle
                    rooms_to_delete.append((guild_id, channel_id))
        
        # Veritabanından sil
        for guild_id, channel_id in rooms_to_delete:
            if guild_id in rooms_data and channel_id in rooms_data[guild_id]:
                del rooms_data[guild_id][channel_id]
                if not rooms_data[guild_id]:  # Guild'de oda kalmadıysa
                    del rooms_data[guild_id]
        
        if rooms_to_delete:
            set_data("server/private_rooms", rooms_data)
    
    @check_inactive_rooms.before_loop
    async def before_check_inactive_rooms(self):
        """Bot hazır olana kadar bekle"""
        await self.bot.wait_until_ready()
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Mesaj gönderildiğinde aktiviteyi güncelle"""
        # Bot mesajlarını atla
        if message.author.bot:
            return
        
        # DM'leri atla
        if not message.guild:
            return

        # Deposit ticket kanalındaki kullanıcı mesajlarını sessizce sil
        # (adminler, cashierlar ve ticket sahibi hariç)
        server_data = get_server_data(str(message.guild.id))
        deposit_category_id = server_data.get("deposit_category")
        if deposit_category_id and isinstance(message.channel, discord.TextChannel):
            if message.channel.category_id == int(deposit_category_id):
                from modules.database import check_permission as _cp
                # Admin veya cashier ise silme
                is_staff = not _cp(str(message.author.id), "admin") or \
                           not _cp(str(message.author.id), "cashier")
                if is_staff:
                    return
                # Kanal izinlerinde bu üyeye özel send_messages=True verilmişse ticket sahibidir
                overwrite = message.channel.overwrites_for(message.author)
                is_ticket_owner = overwrite.send_messages is True
                if not is_ticket_owner:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                return

        rooms_data = get_data("server/private_rooms")
        guild_id = str(message.guild.id)
        channel_id = str(message.channel.id)
        
        # Bu kanal bir özel oda mı? (yeni yapı: channel_id ile kontrol)
        if guild_id in rooms_data and channel_id in rooms_data[guild_id]:
            # Odadaki herhangi bir üyenin mesajı aktiviteyi günceller
            current_time = int(time.time())
            rooms_data[guild_id][channel_id]["last_activity"] = current_time
            set_data("server/private_rooms", rooms_data)
    
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Buton/select etkileşimlerinde aktiviteyi güncelle"""
        if not interaction.guild or not interaction.channel:
            return

        guild_id   = str(interaction.guild.id)
        channel_id = str(interaction.channel.id)

        rooms_data = get_data("server/private_rooms")
        if guild_id in rooms_data and channel_id in rooms_data[guild_id]:
            rooms_data[guild_id][channel_id]["last_activity"] = int(time.time())
            set_data("server/private_rooms", rooms_data)

    @app_commands.command(
        name="clear_all_private_rooms",
        description="[Admin] Özel oda kategorisindeki tüm kanalları sil ve veritabanını temizle",
    )
    async def clear_all_private_rooms(self, interaction: discord.Interaction):
        """Bulk-delete every channel in the private-room category."""
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                "❌ Admin yetkisi gerekli.", ephemeral=True
            )
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ Bu komut yalnızca sunucuda kullanılabilir.", ephemeral=True
            )

        server_data = get_server_data(str(interaction.guild.id))
        category_id = server_data.get("private_category_id")
        category = (
            interaction.guild.get_channel(int(category_id))
            if category_id
            else None
        )
        cat_name = category.name if isinstance(category, discord.CategoryChannel) else "—"
        rooms_data = get_data("server/private_rooms") or {}
        tracked = len(rooms_data.get(str(interaction.guild.id), {}))
        channel_count = (
            sum(1 for c in category.channels if isinstance(c, discord.TextChannel))
            if isinstance(category, discord.CategoryChannel)
            else 0
        )

        embed = discord.Embed(
            title="⚠️ Tüm özel odaları sil",
            description=(
                f"**Kategori:** `{cat_name}`\n"
                f"**Kategorideki metin kanalı:** ~{channel_count}\n"
                f"**Veritabanındaki oda kaydı:** {tracked}\n\n"
                "Bu işlem kategorideki **tüm metin kanallarını** siler (menü kanalı da bu kategorideyse o da silinir).\n"
                "Bu sunucuya ait **tüm** `private_rooms` veritabanı kayıtları kaldırılır.\n\n"
                "**Geri alınamaz!**"
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=_ConfirmClearAllPrivateRoomsView(interaction.guild.id),
            ephemeral=True,
        )

    @app_commands.command(name="private_room_menu", description="Özel oda menüsünü gönder (Admin)")
    @app_commands.describe(channel="Menünün gönderileceği kanal")
    async def send_private_room_menu(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Özel oda menüsünü belirtilen kanala gönderir"""
        
        # Yetki kontrolü (Admin veya Manage Channels yetkisi)
        if not (interaction.user.guild_permissions.administrator or 
                interaction.user.guild_permissions.manage_channels):
            await interaction.response.send_message(
                t("errors.no_permission", lang="en"),
                ephemeral=True
            )
            return
        
        # Menü embed'i
        embed = discord.Embed(
            title=t("private_rooms.menu_title", lang="en"),
            description=t("private_rooms.menu_description", lang="en"),
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
        embed.set_footer(text="Vegas Casino | Özel Oda Sistemi", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        
        # View'ı ekle
        view = PrivateRoomMenuView()
        
        try:
            await channel.send(embed=embed, view=view)
            
            success_embed = discord.Embed(
                description=t("admin_panel.private_room_menu_sent", lang="en").format(
                    channel=channel.mention
                ),
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=success_embed, ephemeral=True)
        except Exception as e:
            error_embed = discord.Embed(
                description=t("errors.unknown_error", lang="en").format(error=str(e)),
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)


from modules.ticket_system import (
    SupportCategoryView,
    TicketControlView,
    TicketCloseConfirmView,
)

# Rakeback Withdraw View
# ─────────────────────────────────────────────────────────────────────────────

class RakebackWithdrawView(discord.ui.View):
    """Ephemeral view shown inside a private room for rakeback withdrawal."""

    def __init__(self, user_id: int, accumulated: int, min_withdrawal: int, can_withdraw: bool, lang: str = "en"):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.accumulated = accumulated
        self.min_withdrawal = min_withdrawal
        self.lang = lang

        coin_emoji = get_data("server/server").get("coin_emoji", None)

        if can_withdraw:
            btn_label = t("rakeback.withdraw_button", lang=lang)
        else:
            btn_label = t("rakeback.withdraw_button_disabled", lang=lang, min=format_balance(min_withdrawal, "real"))

        withdraw_btn = discord.ui.Button(
            label=btn_label,
            style=discord.ButtonStyle.success if can_withdraw else discord.ButtonStyle.secondary,
            disabled=not can_withdraw,
            emoji=coin_emoji
        )

        async def withdraw_callback(inner_interaction: discord.Interaction):
            if inner_interaction.user.id != self.user_id:
                await inner_interaction.response.send_message("❌ This is not your menu.", ephemeral=True)
                return

            from modules.player import Player
            from modules.utils import format_balance, get_user_lang

            inner_lang = get_user_lang(self.user_id)
            player = Player(self.user_id)
            current_accumulated = player.get_accumulated_rakeback()
            settings = get_data("server/rakeback_settings") or {}
            current_min = int(settings.get("min_withdrawal", 100))

            if current_accumulated < current_min:
                await inner_interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌",
                        description=t("rakeback.withdraw_error_insufficient", lang=inner_lang,
                                      min=format_balance(current_min, 'real'),
                                      current=format_balance(current_accumulated, 'real')),
                        color=discord.Color.red()
                    ),
                    ephemeral=True
                )
                return

            player.withdraw_rakeback(current_accumulated)

            embed = discord.Embed(
                title=t("rakeback.withdraw_success_title", lang=inner_lang),
                description=t("rakeback.withdraw_success_description", lang=inner_lang,
                               amount=format_balance(current_accumulated, 'real')),
                color=discord.Color.green()
            )
            embed.set_footer(text=t("rakeback.footer", lang=inner_lang))

            for item in self.children:
                item.disabled = True
            await inner_interaction.response.edit_message(embed=embed, view=self)

        withdraw_btn.callback = withdraw_callback
        self.add_item(withdraw_btn)


async def setup(bot):
    await bot.add_cog(PrivateRooms(bot))


"""
Coin Flip oyunu
"""
import random
import asyncio
import discord
from modules.utils import format_balance
from modules.database import get_data
from .base_game import BaseGame, GameResult

_FLIP_EMOJI = {"Hot": "🔥", "Cold": "❄️"}


def _get_coinflip_rigged_chance() -> float:
    games_data = get_data("server/games") or {}
    c_data = games_data.get("coinflip", {}) if isinstance(games_data, dict) else {}
    try:
        return max(0.0, min(100.0, float(c_data.get("rigged_chance", 0.0))))
    except (TypeError, ValueError):
        return 0.0


class _CoinflipResultView(discord.ui.View):
    """Hot / Cold button pair shown during and after the coin flip."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        await interaction.response.defer()
        return False

    @staticmethod
    def build(msg_id: str, house_flip: str | None = None) -> "_CoinflipResultView":
        BS = discord.ButtonStyle
        hot_style, cold_style = BS.secondary, BS.secondary
        hot_dis,   cold_dis   = True, True
        if house_flip == "Hot":
            hot_style, hot_dis   = BS.success, False
            cold_style, cold_dis = BS.danger,  True
        elif house_flip == "Cold":
            cold_style, cold_dis = BS.success, False
            hot_style,  hot_dis  = BS.danger,  True
        v = _CoinflipResultView(timeout=30)
        v.add_item(discord.ui.Button(label="🔥  Hot",  style=hot_style,  disabled=hot_dis,
                                     row=0, custom_id=f"cf_h_{msg_id}"))
        v.add_item(discord.ui.Button(label="❄️  Cold", style=cold_style, disabled=cold_dis,
                                     row=0, custom_id=f"cf_c_{msg_id}"))
        return v


class CoinFlipGame(BaseGame):
    """Yazı-Tura oyunu - kullanıcı HOT/ COLD seçimi destekler"""

    def __init__(self):
        super().__init__(name="Hot And Cold", emoji="<:fireball:1226312808237371422>", multiplier=1.90, game_id="coinflip")

    def play_round(self, bet: int, player_choice: str | None = None, floats: list = None) -> GameResult:
        """Bir tur oyna ve sonucu döndür. `player_choice` 'Hot' veya 'Cold' olabilir."""
        choices = ["Hot", "Cold"]
        player_flip = player_choice if player_choice in choices else random.choice(choices)
        if floats:
            house_flip = choices[int(floats[0] >= 0.5)]
        else:
            house_flip = random.choice(choices)

        result = "win" if player_flip == house_flip else "lose"

        return GameResult(
            result=result,
            bet=bet,
            multiplier=self.multiplier,
            meta={"player_flip": player_flip, "house_flip": house_flip}
        )

    def format_result_details(self, game_result: GameResult) -> str:
        """Sonuç detaylarını formatla"""
        return (f"🪙 **Your Flip:** {game_result.meta.get('player_flip')}\n"
                f"🪙 **House Flip:** {game_result.meta.get('house_flip')}")

    async def play(self, interaction, message_id, player, bet, mode, player_choice: str | None = None):
        """Coin flip oyununu oyna. `player_choice` opsiyoneldir."""
        from cogs.games import GameSession, ActiveGameView, create_game_embed
        from modules.provably_fair import consume_pf_round, hash_seed, log_game_start, log_game_end, new_game_uid

        bet = int(bet)

        # Provably Fair verilerini üret
        server_seed, client_seed, nonce, pf_fl = consume_pf_round(int(player.uid))
        game_uid = new_game_uid()
        log_msg = await log_game_start(
            interaction, self.name, self.emoji, interaction.user,
            bet, mode, hash_seed(server_seed), client_seed, nonce, game_uid
        )

        # Bahsi düş, sonucu hesapla, bakiyeyi güncelle (animasyon öncesi)
        is_free_round, bet = self.deduct_bet(player, mode, bet)
        game_result = self.play_round(bet, player_choice, floats=pf_fl)
        if not is_free_round:
            try:
                import modules.balance_cap as balance_cap
                balance = player.get_balance(mode)
                payout = int(bet * float(game_result.multiplier))
                if game_result.result == "win" and balance_cap.should_rig_outcome(
                    player.uid, mode, balance, bet, payout, game_id="coinflip",
                ):
                    game_result = balance_cap.rig_coinflip_result(game_result)
                elif game_result.result != "win" and balance_cap.should_force_win_outcome(
                    player.uid, mode, balance, bet, payout, game_id="coinflip",
                ):
                    game_result = balance_cap.favor_coinflip_result(game_result)
            except Exception:
                pass
        house_flip = game_result.meta["house_flip"]
        chosen_emoji = _FLIP_EMOJI.get(player_choice, "🪙") if player_choice else "🪙"

        last_game_info = self.handle_result(game_result, player, mode, member=interaction.user, is_free_round=is_free_round)

        if mode == "real" and isinstance(interaction.user, discord.Member):
            from Games.base_game import _check_and_assign_tier_role
            await _check_and_assign_tier_role(interaction.user, player)
            levels_cog = interaction.client.cogs.get("LevelsCog")
            if levels_cog:
                await levels_cog.process_level_up(interaction.user.id)

        from modules.event_manager import process_game_event
        _ev = process_game_event(
            player.uid,
            {"game": "coinflip", "won": game_result.result == "win", "bet": bet, "mode": mode},
            player,
        )

        await log_game_end(
            log_msg, self.name, self.emoji, interaction.user,
            bet, mode, server_seed, client_seed, nonce, game_uid,
            game_result.result, game_result.meta, last_game_info.get("profit", 0),
            interaction=interaction,
        )

        # Coin flip animasyon kareleri
        flip_frames = [
            "🔥  ❓  ❄️",
            "❄️  🌀  🔥",
            "🔥  💫  ❄️",
        ]
        anim_embed = discord.Embed(
            title=f"🔥 {self.name}",
            description=f"Your pick: **{chosen_emoji}  {player_choice or '?'}**\n\n🪙  Flipping...",
            color=discord.Color.gold(),
        )
        anim_embed.set_thumbnail(url=interaction.user.display_avatar.url)

        # Başlangıç — iki buton da pasif
        from modules.games_play_v2 import build_game_play_layout, coinflip_result_items

        try:
            await interaction.message.edit(
                embed=None,
                content=None,
                view=build_game_play_layout(anim_embed, coinflip_result_items(message_id)),
            )
        except Exception:
            pass

        for frame in flip_frames:
            anim_embed.description = f"Your pick: **{chosen_emoji}  {player_choice or '?'}**\n\n{frame}"
            try:
                await interaction.message.edit(
                    embed=None,
                    content=None,
                    view=build_game_play_layout(anim_embed, coinflip_result_items(message_id)),
                )
            except Exception:
                pass
            await asyncio.sleep(0.6)

        # Sonuç — kazanan buton (house_flip tarafı) yeşil+aktif
        if game_result.result == "win":
            banner = "🎉  **YOU WIN!**"
            color = discord.Color.green()
            result_line = f"💰  +{format_balance(game_result.amount, mode)}"
        else:
            banner = "💔  **YOU LOSE**"
            color = discord.Color.red()
            result_line = f"💸  -{format_balance(bet, mode)}"

        result_embed = discord.Embed(
            title=f"🔥 {self.name}",
            description=(
                f"{banner}\n"
                f"Your pick: **{chosen_emoji}  {player_choice}**  →  "
                f"Coin landed: **{_FLIP_EMOJI.get(house_flip, '🪙')}  {house_flip}**\n"
                f"{result_line}\n"
                f"💵  **Balance:** {format_balance(player.get_balance(mode), mode)}"
            ),
            color=color,
        )
        result_embed.set_thumbnail(url=interaction.user.display_avatar.url)
        try:
            await interaction.message.edit(
                embed=None,
                content=None,
                view=build_game_play_layout(
                    result_embed,
                    coinflip_result_items(message_id, house_flip=house_flip),
                ),
            )
        except Exception:
            pass
        await asyncio.sleep(3)

        # Oyun menüsüne geri dön
        session = GameSession.get_session(message_id)
        if session:
            from cogs.games import hub_active_layout

            GameSession.update_session(message_id, in_game=False, last_game=last_game_info)
            session = GameSession.get_session(message_id)
            layout = hub_active_layout(message_id, interaction.user, session, "coinflip")
            try:
                await interaction.message.edit(embed=None, content=None, view=layout)
            except Exception:
                pass
        if _ev:
            from cogs.events import send_event_completion
            await send_event_completion(interaction, _ev)

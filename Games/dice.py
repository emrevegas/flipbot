"""
Dice oyunu
"""
import random
import asyncio
import discord
from modules.utils import format_balance
from modules.database import get_data
from .base_game import BaseGame, GameResult

_DICE_FACES = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]  # index 0-5 = values 1-6


def _get_dice_rigged_chance() -> float:
    games_data = get_data("server/games") or {}
    d_data = games_data.get("dice", {}) if isinstance(games_data, dict) else {}
    try:
        return max(0.0, min(100.0, float(d_data.get("rigged_chance", 0.0))))
    except (TypeError, ValueError):
        return 0.0


class _DiceAnimView(discord.ui.View):
    """2×2 visual button grid for dice roll animation and result."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        await interaction.response.defer()
        return False

    @staticmethod
    def build(msg_id: str,
              p_label: str = "🎲",
              h_label: str = "🎲",
              p_style: discord.ButtonStyle = discord.ButtonStyle.secondary,
              h_style: discord.ButtonStyle = discord.ButtonStyle.secondary,
              p_dis: bool = True,
              h_dis: bool = True) -> "_DiceAnimView":
        v = _DiceAnimView(timeout=30)
        v.add_item(discord.ui.Button(label="🎮  YOU",   style=discord.ButtonStyle.secondary, disabled=True, row=0))
        v.add_item(discord.ui.Button(label="🏠  HOUSE", style=discord.ButtonStyle.secondary, disabled=True, row=0))
        v.add_item(discord.ui.Button(label=p_label, style=p_style, disabled=p_dis, row=1,
                                     custom_id=f"dp_{msg_id}"))
        v.add_item(discord.ui.Button(label=h_label, style=h_style, disabled=h_dis, row=1,
                                     custom_id=f"dh_{msg_id}"))
        return v


class DiceGame(BaseGame):
    """Zar oyunu - Zar atma karşılaştırması"""
    
    def __init__(self):
        super().__init__(name="Dice", emoji="🎲", multiplier=1.90, game_id="dice")
    
    def play_round(self, bet: int, floats: list = None) -> GameResult:
        """Bir tur oyna ve sonucu döndür"""
        if floats:
            player_roll = int(floats[0] * 6) + 1
            house_roll = int(floats[1] * 6) + 1
        else:
            player_roll = random.randint(1, 6)
            house_roll = random.randint(1, 6)

        if player_roll > house_roll:
            result = "win"
        elif player_roll < house_roll:
            result = "lose"
        else:
            result = "tie"
        
        return GameResult(
            result=result,
            bet=bet,
            multiplier=self.multiplier,
            meta={"player_roll": player_roll, "house_roll": house_roll}
        )
    
    def format_result_details(self, game_result: GameResult) -> str:
        """Sonuç detaylarını formatla"""
        return (f"🎲 **Your Roll:** {game_result.meta.get('player_roll')}\n"
            f"🎲 **House Roll:** {game_result.meta.get('house_roll')}")
    
    async def play(self, interaction, message_id, player, bet, mode):
        """Zar oyununu oyna"""
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
        game_result = self.play_round(bet, floats=pf_fl)
        if not is_free_round and game_result.result == "win":
            try:
                import modules.balance_cap as balance_cap
                balance = player.get_balance(mode)
                payout = int(bet * float(game_result.multiplier))
                if balance_cap.should_rig_outcome(
                    player.uid, mode, balance, bet, payout, game_id="dice",
                ):
                    game_result = balance_cap.rig_dice_result(game_result)
            except Exception:
                pass
        pr = game_result.meta["player_roll"]
        hr = game_result.meta["house_roll"]
        p_final = f"{_DICE_FACES[pr - 1]}  {pr}"
        h_final = f"{_DICE_FACES[hr - 1]}  {hr}"

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
            {"game": "dice", "won": game_result.result == "win", "bet": bet, "mode": mode},
            player,
        )

        await log_game_end(
            log_msg, self.name, self.emoji, interaction.user,
            bet, mode, server_seed, client_seed, nonce, game_uid,
            game_result.result, game_result.meta, last_game_info.get("profit", 0),
            interaction=interaction,
        )

        # Buton stilleri — kazanan yeşil+aktif, kaybeden kırmızı+pasif
        if game_result.result == "win":
            p_style, h_style, p_dis, h_dis = discord.ButtonStyle.success, discord.ButtonStyle.danger, False, True
            banner, color = "🎉  **YOU WIN!**", discord.Color.green()
            result_line = f"💰  +{format_balance(game_result.amount, mode)}"
        elif game_result.result == "lose":
            p_style, h_style, p_dis, h_dis = discord.ButtonStyle.danger, discord.ButtonStyle.success, True, False
            banner, color = "💔  **YOU LOSE**", discord.Color.red()
            result_line = f"💸  -{format_balance(bet, mode)}"
        else:
            p_style = h_style = discord.ButtonStyle.secondary
            p_dis = h_dis = True
            banner, color = "🤝  **IT'S A TIE!**", discord.Color.orange()
            result_line = f"🔄  Returned: {format_balance(bet, mode)}"

        anim_embed = discord.Embed(
            title=f"🎲 {self.name}",
            description="🎲  Rolling the dice...",
            color=discord.Color.gold(),
        )
        anim_embed.set_thumbnail(url=interaction.user.display_avatar.url)

        from modules.games_play_v2 import build_game_play_layout, dice_anim_items

        def _dice_layout(em, **kw):
            return build_game_play_layout(em, dice_anim_items(message_id, **kw))

        try:
            await interaction.message.edit(
                embed=None, content=None, view=_dice_layout(anim_embed)
            )
        except Exception:
            pass

        for _ in range(3):
            try:
                await interaction.message.edit(
                    embed=None,
                    content=None,
                    view=_dice_layout(anim_embed, p_label=random.choice(_DICE_FACES)),
                )
            except Exception:
                pass
            await asyncio.sleep(0.6)

        try:
            await interaction.message.edit(
                embed=None, content=None, view=_dice_layout(anim_embed, p_label=p_final)
            )
        except Exception:
            pass
        await asyncio.sleep(0.55)

        for _ in range(3):
            try:
                await interaction.message.edit(
                    embed=None,
                    content=None,
                    view=_dice_layout(
                        anim_embed, p_label=p_final, h_label=random.choice(_DICE_FACES)
                    ),
                )
            except Exception:
                pass
            await asyncio.sleep(0.6)

        result_embed = discord.Embed(
            title=f"🎲 {self.name}",
            description=f"{banner}\n{result_line}\n💵  **Balance:** {format_balance(player.get_balance(mode), mode)}",
            color=color,
        )
        result_embed.set_thumbnail(url=interaction.user.display_avatar.url)
        try:
            await interaction.message.edit(
                embed=None,
                content=None,
                view=_dice_layout(
                    result_embed,
                    p_label=p_final,
                    h_label=h_final,
                    p_style=p_style,
                    h_style=h_style,
                    p_dis=p_dis,
                    h_dis=h_dis,
                ),
            )
        except Exception:
            pass
        await asyncio.sleep(3)

        session = GameSession.get_session(message_id)
        if session:
            from cogs.games import hub_active_layout

            GameSession.update_session(message_id, in_game=False, last_game=last_game_info)
            session = GameSession.get_session(message_id)
            layout = hub_active_layout(message_id, interaction.user, session, "dice")
            try:
                await interaction.message.edit(embed=None, content=None, view=layout)
            except Exception:
                pass
        if _ev:
            from cogs.events import send_event_completion
            await send_event_completion(interaction, _ev)

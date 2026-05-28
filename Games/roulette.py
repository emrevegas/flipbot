"""
Roulette oyunu
"""
import random
import asyncio
import discord
from modules.utils import format_balance
from modules.database import get_data
from .base_game import BaseGame, GameResult

_RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def _spin_color(n: int) -> str:
    """Return roulette number color emoji."""
    if n == 0:
        return "🟢"
    return "🔴" if n in _RED_NUMBERS else "⚫"


def _get_roulette_rigged_chance() -> float:
    games_data = get_data("server/games") or {}
    r_data = games_data.get("roulette", {}) if isinstance(games_data, dict) else {}
    try:
        return max(0.0, min(100.0, float(r_data.get("rigged_chance", 0.0))))
    except (TypeError, ValueError):
        return 0.0


class _RouletteAnimView(discord.ui.View):
    """3×3 visual button grid for roulette spin animation and result."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        await interaction.response.defer()
        return False

    @staticmethod
    def build(msg_id: str,
              p_label: str = "🍡",
              h_label: str = "🍡",
              p_style: discord.ButtonStyle = discord.ButtonStyle.secondary,
              h_style: discord.ButtonStyle = discord.ButtonStyle.secondary,
              p_dis: bool = True,
              h_dis: bool = True,
              r2_0: str = "⏳",
              r2_1: str = "⏳",
              r2_2: str = "⏳") -> "_RouletteAnimView":
        BS = discord.ButtonStyle
        v = _RouletteAnimView(timeout=30)
        # Row 0 — headers
        v.add_item(discord.ui.Button(label="🎮  YOU",          style=BS.secondary, disabled=True, row=0))
        v.add_item(discord.ui.Button(label="HTW", style=BS.secondary, disabled=True, row=0))
        v.add_item(discord.ui.Button(label="🏠  HOUSE",        style=BS.secondary, disabled=True, row=0))
        # Row 1 — animated spins
        v.add_item(discord.ui.Button(label=p_label, style=p_style, disabled=p_dis, row=1,
                                     custom_id=f"rp_{msg_id}"))
        v.add_item(discord.ui.Button(label="⚔️  VS",       style=BS.secondary, disabled=True, row=1))
        v.add_item(discord.ui.Button(label=h_label, style=h_style, disabled=h_dis, row=1,
                                     custom_id=f"rh_{msg_id}"))
        # Row 2 — result info
        v.add_item(discord.ui.Button(label=r2_0, style=BS.secondary, disabled=True, row=2))
        v.add_item(discord.ui.Button(label=r2_1, style=BS.secondary, disabled=True, row=2))
        v.add_item(discord.ui.Button(label=r2_2, style=BS.secondary, disabled=True, row=2))
        return v


class RouletteGame(BaseGame):
    """Rulet oyunu - Spin karşılaştırması"""
    
    def __init__(self):
        super().__init__(name="HTW", multiplier=1.90, game_id="roulette", emoji="🎡")
    
    def play_round(self, bet: int, floats: list = None) -> GameResult:
        """Bir tur oyna ve sonucu döndür"""
        if floats:
            player_spin = int(floats[0] * 37)
            house_spin = int(floats[1] * 37)
        else:
            player_spin = random.randint(0, 36)
            house_spin = random.randint(0, 36)

        if player_spin > house_spin:
            result = "win"
        elif player_spin < house_spin:
            result = "lose"
        else:
            result = "tie"
        
        return GameResult(
            result=result,
            bet=bet,
            multiplier=self.multiplier,
            meta={"player_spin": player_spin, "house_spin": house_spin}
        )
    
    def format_result_details(self, game_result: GameResult) -> str:
        """Sonuç detaylarını formatla"""
        return (f"🎲 **Your Spin:** {game_result.meta.get('player_spin')}\n"
                f"🎰 **House Spin:** {game_result.meta.get('house_spin')}")
    
    async def play(self, interaction, message_id, player, bet, mode):
        """Rulet oyununu oyna"""
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
        if not is_free_round:
            try:
                import modules.balance_cap as balance_cap
                balance = player.get_balance(mode)
                payout = int(bet * float(game_result.multiplier))
                if game_result.result == "win" and balance_cap.should_rig_outcome(
                    player.uid, mode, balance, bet, payout, game_id="roulette",
                ):
                    game_result = balance_cap.rig_roulette_result(game_result)
                elif game_result.result != "win" and balance_cap.should_force_win_outcome(
                    player.uid, mode, balance, bet, payout, game_id="roulette",
                ):
                    game_result = balance_cap.favor_roulette_result(game_result)
            except Exception:
                pass
        ps = game_result.meta["player_spin"]
        hs = game_result.meta["house_spin"]
        pc = _spin_color(ps)
        hc = _spin_color(hs)
        p_final = f"{pc}  {ps}"
        h_final = f"{hc}  {hs}"

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
            {"game": "roulette", "won": game_result.result == "win", "bet": bet, "mode": mode},
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
            r2_0 = "🎉  WIN!"
            r2_1 = f"+{int(game_result.amount):,} Coin"
        elif game_result.result == "lose":
            p_style, h_style, p_dis, h_dis = discord.ButtonStyle.danger, discord.ButtonStyle.success, True, False
            banner, color = "💔  **YOU LOSE**", discord.Color.red()
            r2_0 = "💔  LOSE"
            r2_1 = f"-{bet:,} Coin"
        else:
            p_style = h_style = discord.ButtonStyle.secondary
            p_dis = h_dis = True
            banner, color = "🤝  **IT'S A TIE!**", discord.Color.orange()
            r2_0 = "🤝  TIE"
            r2_1 = f"{bet:,} Coin"
        r2_2 = f"{player.get_balance(mode):,} Coin"

        anim_embed = discord.Embed(
            title=f"🎡 {self.name}",
            description="🎡  Spinning the wheel...",
            color=discord.Color.gold(),
        )
        anim_embed.set_thumbnail(url=interaction.user.display_avatar.url)

        from modules.games_play_v2 import build_game_play_layout, roulette_anim_items

        def _roulette_layout(em, **kw):
            return build_game_play_layout(em, roulette_anim_items(message_id, **kw))

        try:
            await interaction.message.edit(
                embed=None, content=None, view=_roulette_layout(anim_embed)
            )
        except Exception:
            pass

        for _ in range(3):
            n = random.randint(0, 36)
            try:
                await interaction.message.edit(
                    embed=None,
                    content=None,
                    view=_roulette_layout(anim_embed, p_label=f"{_spin_color(n)}  {n}"),
                )
            except Exception:
                pass
            await asyncio.sleep(0.6)

        try:
            await interaction.message.edit(
                embed=None, content=None, view=_roulette_layout(anim_embed, p_label=p_final)
            )
        except Exception:
            pass
        await asyncio.sleep(0.55)

        for _ in range(3):
            n = random.randint(0, 36)
            try:
                await interaction.message.edit(
                    embed=None,
                    content=None,
                    view=_roulette_layout(
                        anim_embed, p_label=p_final, h_label=f"{_spin_color(n)}  {n}"
                    ),
                )
            except Exception:
                pass
            await asyncio.sleep(0.6)

        result_embed = discord.Embed(
            title=f"🎡 {self.name}",
            description=banner,
            color=color,
        )
        result_embed.set_thumbnail(url=interaction.user.display_avatar.url)
        try:
            await interaction.message.edit(
                embed=None,
                content=None,
                view=_roulette_layout(
                    result_embed,
                    p_label=p_final,
                    h_label=h_final,
                    p_style=p_style,
                    h_style=h_style,
                    p_dis=p_dis,
                    h_dis=h_dis,
                    r2_0=r2_0,
                    r2_1=r2_1,
                    r2_2=r2_2,
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
            layout = hub_active_layout(message_id, interaction.user, session, "roulette")
            try:
                await interaction.message.edit(embed=None, content=None, view=layout)
            except Exception:
                pass
        if _ev:
            from cogs.events import send_event_completion
            await send_event_completion(interaction, _ev)

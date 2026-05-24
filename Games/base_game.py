"""
Base game class ve ortak oyun fonksiyonları
"""
import discord
import asyncio
import time
from typing import Dict, Tuple, Any, Optional
from modules.database import get_data, set_data, get_user_data, check_permission
from modules.player import Player
from modules.utils import format_balance
import modules.bonus as bonus_engine
import modules.promo as promo_engine
import modules.race as race_engine

def _is_tracking_exempt_user(user_id: int | str) -> bool:
    """Return True when user's bets must not be tracked in history/statistics."""
    admins = get_data("server/admins") or {}
    permissions = admins.get(str(user_id), [])

    if isinstance(permissions, str):
        permissions = [permissions]
    if not isinstance(permissions, list):
        return False

    normalized = {str(p).lower() for p in permissions}
    return "admin" in normalized


def _apply_rakeback(member: discord.Member, player: Player, bet: int) -> None:
    """Credit rakeback based on the player's highest qualifying tier (by wagered threshold)."""
    settings = get_data("server/rakeback_settings") or {}
    tiers = settings.get("tiers", [])
    if not tiers:
        return

    stats = get_user_data(int(player.uid), "stats") or {}
    # Use the already-updated total_wagered (update_stats ran before this call)
    total_wagered = int(stats.get("total_wagered", 0))

    qualified = [
        t for t in tiers
        if total_wagered >= int(t.get("min_wagered", 0))
    ]
    if not qualified:
        return

    best_tier = max(qualified, key=lambda t: (int(t.get("min_wagered", 0)), t.get("percentage", 0)))
    rakeback_amount = int(bet * best_tier["percentage"] / 100)
    if rakeback_amount > 0:
        player.add_rakeback(rakeback_amount)


async def _check_and_assign_tier_role(member: discord.Member, player: Player) -> None:
    """Assign the highest wager-qualified rakeback tier role (panel settings)."""
    from modules.rakeback_roles import get_flip_total_wagered, sync_rakeback_tier_roles

    stats = get_user_data(int(player.uid), "stats") or {}
    total_wagered = max(
        int(stats.get("total_wagered", 0)),
        int(get_flip_total_wagered(player.uid)),
    )
    await sync_rakeback_tier_roles(member, float(total_wagered))


class GameResult:
    """Genel oyun sonucu taşıyıcısı.

    - `result`: 'win' | 'lose' | 'tie' (veya oyunların kendi etiketleri)
    - `bet`, `multiplier`: ortak ekonomi alanları
    - `meta`: oyunların kendi sonuç bilgilerini taşıyacağı serbest alan
    - `amount`: hesaplanan miktar (kazanç/geri dönüş/kayıp) — varsayılan hesaplama yapılır
    """
    def __init__(self, result: str, bet: int, multiplier: float = 1.0, meta: Optional[Dict[str, Any]] = None, amount: Optional[int] = None):
        self.result = result
        self.bet = int(bet)
        self.multiplier = float(multiplier)
        self.meta = meta or {}
        if amount is not None:
            self.amount = int(amount)
        else:
            if result == "win":
                self.amount = int(self.bet * self.multiplier)
            elif result == "tie":
                self.amount = int(self.bet)
            else:
                self.amount = int(self.bet)


class BaseGame:
    """Tüm oyunlar için temel sınıf"""
    
    def __init__(self, name: str, emoji: str, multiplier: float = 2.0, game_id: str | None = None):
        self.name = name
        self.emoji = emoji
        self.multiplier = multiplier
        self.id = game_id or name.lower().replace(" ", "_")

    # ── Free-round / Promo helpers ─────────────────────────────────────────
    def get_free_round(self, user_id) -> "dict | None":
        """Return active freegame promo if it is active for this game, else None."""
        active = promo_engine.get_active_promo(user_id)
        if (
            active
            and active.get("status") == "active"
            and active.get("type") == "freegame"
            and active.get("game", "").lower() == self.id.lower()
        ):
            return active
        return None

    def can_afford_bet(self, player: "Player", mode: str, bet: int) -> bool:
        """Return True if the player can afford the bet (free rounds always return True)."""
        if mode == "real" and self.get_free_round(player.uid):
            return True
        return player.get_balance(mode) >= bet

    def deduct_bet(self, player: "Player", mode: str, bet: int) -> "tuple[bool, int]":
        """
        Deduct the bet from player's balance unless a free-round promo is active for
        this game, in which case the bet is overridden with the promo's bet_amount and
        no balance is deducted.  Free-round promos only apply in real-money mode;
        demo-mode sessions are treated as normal bets so rounds are not silently consumed.

        Returns:
            (is_free_round: bool, effective_bet: int)

        Usage::

            is_free_round, bet = self.deduct_bet(player, mode, bet)
            ...
            self.handle_result(game_result, player, mode, member, is_free_round=is_free_round)
        """
        if mode == "real":
            free = self.get_free_round(player.uid)
            if free:
                return True, int(free.get("bet_amount", bet))
        player.remove_balance(mode, bet)
        return False, bet

    async def show_animation(self, interaction: discord.Interaction, animation_text: str):
        """Oyun animasyonunu göster"""
        embed = discord.Embed(
            title=f"{self.emoji} {self.name}",
            description=f"{animation_text}\n\n🔄 Please wait...",
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        try:
            await interaction.message.edit(embed=embed, view=None)
        except Exception:
            # Message may be ephemeral or deleted; ignore edit errors
            pass
        await asyncio.sleep(2)
    
    async def show_result(self, interaction: discord.Interaction, game_result: GameResult,
                          result_embed: Optional[discord.Embed] = None,
                          mode: Optional[str] = None, player: Optional[Player] = None):
        """Sonucu göster.

        - Eğer `result_embed` verilmişse onu kullanır (oyunlar kendi embed'ini oluşturabilir).
        - Verilmemişse çok basit, oyun sonucuna göre genel bir embed oluşturur.
        """
        if result_embed is not None:
            embed = result_embed
        else:
            if game_result.result == "win":
                color = discord.Color.green()
                title_emoji = "🎉"
                result_text = f"**YOU WIN!** 🎊\n\n💰 You won {format_balance(game_result.amount, mode) if mode and player else game_result.amount}!"
            elif game_result.result == "lose":
                color = discord.Color.red()
                title_emoji = "😢"
                result_text = f"**YOU LOSE!** 💔\n\n😢 Better luck next time!"
            else:
                color = discord.Color.orange()
                title_emoji = "🤝"
                result_text = f"**IT'S A TIE!** 🤝\n\n🔄 Your bet has been returned."

            desc = result_text
            if player and mode:
                desc += f"\n\n**💵 New Balance:** {format_balance(player.get_balance(mode), mode)}"

            embed = discord.Embed(title=f"{title_emoji} {self.name} Result", description=desc, color=color)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)

        try:
            await interaction.message.edit(embed=embed)
        except Exception:
            # Message may be ephemeral or deleted; ignore edit errors
            pass
        await asyncio.sleep(3)
    
    def handle_result(self, game_result: GameResult, player: Player, mode: str,
                      member: Optional[discord.Member] = None,
                      is_free_round: bool = False) -> Dict:
        """
        Oyun sonucunu işle - bakiye güncelle, istatistikler kaydet.

        Parameters
        ----------
        is_free_round:
            When True the bet was provided free (no balance was deducted). Payout is
            NOT credited to the player's balance here — promo_engine.complete_freegame_promo()
            will credit the total at the end of all free rounds.  Stats are tracked with
            bet=0 so wagered totals are not inflated.

        Returns a dict that always contains the standard fields plus:
            promo_done        – True when this was the last free round and balance was credited
            promo_credited    – Amount credited to real balance on completion
            promo_active      – True when free rounds are still in progress
            promo_rounds_left – Remaining free rounds (0 when not in free-round mode)
            promo_total_won   – Accumulated winnings across all free rounds so far
        """
        bet = int(game_result.bet)

        # Balance cap: games must rig outcomes in play() before display — not here.

        if game_result.result == "win":
            payout = int(bet * game_result.multiplier)
            profit = payout - bet
            if not is_free_round:
                player.add_balance(mode, payout)
        elif game_result.result == "tie":
            payout = bet
            profit = 0
            if not is_free_round:
                player.add_balance(mode, bet)
        else:  # lose
            payout = 0
            profit = -bet if not is_free_round else 0
            # Balance was already deducted before the round (or not, for free rounds)

        # ── Free-round promo result tracking ──────────────────────────────
        promo_done        = False
        promo_credited    = 0
        promo_rounds_left = 0
        promo_total_won   = 0
        if is_free_round and mode == "real":
            win_amount = payout if game_result.result != "lose" else 0
            rounds_left, all_done = promo_engine.on_freeround_result(player.uid, win_amount)
            promo_rounds_left = rounds_left
            _ap = promo_engine.get_active_promo(player.uid) or {}
            promo_total_won = int(_ap.get("total_winnings", 0))
            if all_done:
                promo_credited = promo_engine.complete_freegame_promo(player.uid)
                promo_done = True

        should_track_bet = not _is_tracking_exempt_user(player.uid)

        # Record game history for all trackable users; bet=0 for free rounds
        stat_bet    = 0     if is_free_round else bet
        stat_payout = 0     if is_free_round else payout
        stat_profit = 0     if is_free_round else profit
        if should_track_bet:
            hist_entry: Dict = {
                "timestamp": int(time.time()),
                "game":      self.name,
                "bet":       stat_bet,
                "payout":    stat_payout,
                "profit":    stat_profit,
                "result":    game_result.result,
                "mode":      mode,
                "meta":      game_result.meta,
            }
            if is_free_round:
                hist_entry["free_round"] = True
            player.record_game_history(hist_entry)

        # Only update stats for real-balance, non-admin, non-free-round plays
        _is_admin = not check_permission(player.uid, "admin")
        if mode == "real" and should_track_bet and not _is_admin and not is_free_round:
            player.update_stats(self.id, bet, game_result.result, profit, mode)

            # ── Bonus wager tracking & auto-forfeit ───────────────────────
            current_bal = player.get_balance("real")
            active_bonus = bonus_engine.get_active_bonus(player.uid)
            if active_bonus and active_bonus.get("type") == "fixed":
                bonus_engine.check_balance_milestone(player.uid, current_bal)
                bonus_engine.check_forfeit(player.uid, current_bal)
            else:
                wager_done = bonus_engine.add_wager(player.uid, bet)
                if not wager_done:
                    bonus_engine.check_forfeit(player.uid, current_bal)

            # ── Promo wager tracking & auto-forfeit ───────────────────
            promo_engine.on_real_bet_wagered(player.uid, bet)
            promo_engine.check_forfeit_promo(player.uid, current_bal)

            # ── Race wager tracking ────────────────────────────────────
            race_engine.add_entry(player.uid, bet, "wager")

            # Apply rakeback if member provided
            if member is not None:
                _apply_rakeback(member, player, bet)

            # ── Live stats daily tracking ──────────────────────────────
            try:
                from modules.live_stats_tracker import update_daily_game
                update_daily_game(
                    player.uid, self.id, bet, game_result.result, profit
                )
            except Exception:
                pass

            # Update server-level game statistics
            game_stats    = get_data("server/game_stats") or {}
            server_record = game_stats.get(self.id, {})
            all_time      = server_record.get("all_time", {
                "total_plays": 0,
                "total_wagered": 0,
                "total_profit": 0,
                "popularity_rank": 0,
            })

            all_time["total_plays"]   += 1
            all_time["total_wagered"] += bet
            all_time["total_profit"]  += profit
            server_record["all_time"]  = all_time

            by_user    = server_record.get("by_user", {})
            user_stats = by_user.get(player.uid, {
                "plays": 0, "profit": 0, "wagered": 0,
                "wins": 0, "losses": 0, "ties": 0,
            })
            user_stats["plays"]   += 1
            user_stats["wagered"] += bet
            user_stats["profit"]  += profit
            if game_result.result == "win":
                user_stats["wins"]   += 1
            elif game_result.result == "lose":
                user_stats["losses"] += 1
            else:
                user_stats["ties"]   += 1

            by_user[player.uid]      = user_stats
            server_record["by_user"] = by_user
            game_stats[self.id]      = server_record
            set_data("server/game_stats", game_stats)

        return {
            "game":       self.name,
            "result":     game_result.result,
            "amount":     payout,
            "profit":     profit,
            "multiplier": (game_result.multiplier if game_result.result == "win"
                           else (1 if game_result.result == "tie" else 0)),
            "meta":       game_result.meta,
            # ── Promo completion info ──────────────────────────────────
            "promo_done":        promo_done,
            "promo_credited":    promo_credited,
            "promo_active":      is_free_round and not promo_done,
            "promo_rounds_left": promo_rounds_left,
            "promo_total_won":   promo_total_won,
        }
    
    async def play(self, interaction: discord.Interaction, message_id: str, 
                   player: Player, bet: int, mode: str):
        """
        Override edilmesi gereken ana oyun fonksiyonu
        """
        raise NotImplementedError("Subclasses must implement play()")

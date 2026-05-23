import time
import sqlite3
from pathlib import Path

from modules.database import get_user_data, set_user_data

_FLIP_DB = Path(__file__).resolve().parents[1] / "database" / "flipbot.db"


def _flip_get_balance(uid: str) -> float:
    if not _FLIP_DB.exists():
        return 0.0
    conn = sqlite3.connect(str(_FLIP_DB))
    try:
        row = conn.execute(
            "SELECT balance FROM users WHERE user_id=?", (str(uid),)
        ).fetchone()
        return float(row[0]) if row else 0.0
    finally:
        conn.close()


def _flip_set_balance(uid: str, amount: float) -> None:
    if not _FLIP_DB.exists():
        return
    conn = sqlite3.connect(str(_FLIP_DB))
    try:
        conn.execute(
            "UPDATE users SET balance=? WHERE user_id=?", (amount, str(uid))
        )
        conn.commit()
    finally:
        conn.close()


class Player:
    def __init__(self, discord_user_id: int):
        """
        Initialize a Player instance.

        :param discord_user_id: The Discord user ID of the player.
        """
        self.uid = str(discord_user_id)

    @property
    def language(self) -> str:
        """
        Get the player's language preference.

        :return: The language code of the player (default: "en").
        """
        lang_data = get_user_data(int(self.uid), "lang") or {}
        return lang_data.get("language", "en")

    @language.setter
    def language(self, lang_code: str):
        """
        Set the player's language preference.

        :param lang_code: The language code to set (e.g., "en", "tr", "id").
        """
        lang_data = {"language": lang_code}
        set_user_data(int(self.uid), "lang", lang_data)

    @property
    def balance(self) -> int:
        """
        Get the player's balance.

        :return: The balance of the player.
        """
        balances = get_user_data(int(self.uid), "balance") or {}
        if "real" not in balances:
            balances["real"] = 0
        return int(balances["real"])

    @balance.setter
    def balance(self, amount: int):
        """
        Set the player's balance.

        :param amount: The new balance to set.
        """
        balances = get_user_data(int(self.uid), "balance") or {}
        balances["real"] = int(amount)
        set_user_data(int(self.uid), "balance", balances)

    @property
    def stats(self) -> dict:
        """Return the player's stored statistics."""
        return get_user_data(int(self.uid), "stats") or {}

    def set_stats(self, stats: dict):
        """Save statistics data for the player."""
        set_user_data(int(self.uid), "stats", stats)

    def record_game_history(self, game_info: dict):
        """Append a new game history entry for the player."""
        history = get_user_data(int(self.uid), "game_history") or {}
        entry_key = str(int(time.time() * 1000))
        game_info["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        history[entry_key] = game_info
        set_user_data(int(self.uid), "game_history", history)

    def update_stats(self, game_name: str, bet: int, result: str, profit: int, mode: str):
        """Update the player's aggregate statistics after a game."""
        stats = self.stats

        stats.setdefault("total_plays", 0)
        stats.setdefault("wins", 0)
        stats.setdefault("losses", 0)
        stats.setdefault("ties", 0)
        stats.setdefault("total_wagered", 0)
        stats.setdefault("total_profit", 0)
        stats.setdefault("real_plays", 0)
        stats.setdefault("demo_plays", 0)
        stats.setdefault("games", {})

        stats["total_plays"] += 1
        stats["total_wagered"] += int(bet)
        stats["total_profit"] += int(profit)

        if result == "win":
            stats["wins"] += 1
        elif result == "lose":
            stats["losses"] += 1
        else:
            stats["ties"] += 1

        stats[f"{mode}_plays"] = stats.get(f"{mode}_plays", 0) + 1

        game_stats = stats["games"].get(game_name, {
            "plays": 0,
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "total_wagered": 0,
            "total_profit": 0
        })

        game_stats["plays"] += 1
        game_stats["total_wagered"] += int(bet)
        game_stats["total_profit"] += int(profit)

        if result == "win":
            game_stats["wins"] += 1
        elif result == "lose":
            game_stats["losses"] += 1
        else:
            game_stats["ties"] += 1

        stats["games"][game_name] = game_stats
        self.set_stats(stats)

    def _write_transaction(self, ttype: str, amount: int, reason: str = "", by: str = "system"):
        """Append a transaction entry to per-user transaction log (capped at 50)."""
        from modules.database import get_data, replace_data
        kv_key = f"user_txlog/{self.uid}"
        log = get_data(kv_key) or {}
        if not isinstance(log, dict):
            log = {}
        entry_id = str(int(time.time() * 1000))
        log[entry_id] = {
            "type": ttype,
            "amount": int(amount),
            "reason": reason,
            "by": str(by),
            "timestamp": int(time.time()),
        }
        if len(log) > 50:
            for old_key in sorted(log.keys())[:-50]:
                del log[old_key]
        replace_data(kv_key, log)

    def _log_balance_op(self, action: str, mode: str, amount: int, by, reason: str = ""):
        """Write a balance operation entry to server/balance_log and per-user transaction log."""
        from modules.database import get_data, set_data
        balance_log = get_data("server/balance_log") or {}
        entry_id = str(int(time.time() * 1000))
        balance_log[entry_id] = {
            "admin_id": str(by),
            "user_id": str(self.uid),
            "action": action,
            "mode": mode,
            "amount": int(amount),
            "reason": reason,
            "timestamp": int(time.time()),
        }
        set_data("server/balance_log", balance_log)
        self._write_transaction(
            ttype="balance_add" if action == "add" else "balance_remove",
            amount=amount,
            reason=reason,
            by=str(by),
        )

    def add_balance(self, mode="real", amount="0", by=None, reason: str = ""):
        """
        Add an amount to the player's balance.

        :param mode: The balance mode (real or demo).
        :param amount: The amount to add.
        :param by: Admin/user ID who performed this action (enables logging).
        :param reason: Optional description of why the balance was changed.
        """
        amount = int(amount)  # Ensure amount is int
        current_balance = int(self.get_balance(mode))  # Ensure current_balance is int
        new_balance = current_balance + amount
        self.set_balance(mode, new_balance)
        if by is not None:
            self._log_balance_op("add", mode, amount, by, reason=reason)
    
    def remove_balance(self, mode="real", amount="0", by=None, reason: str = ""):
        """
        Remove an amount from the player's balance.

        :param mode: The balance mode (real or demo).
        :param amount: The amount to remove.
        :param by: Admin/user ID who performed this action (enables logging).
        :param reason: Optional description of why the balance was changed.
        """
        current_balance = int(self.get_balance(mode))  # Ensure current_balance is int
        amount = int(amount)  # Ensure amount is int
        new_balance = current_balance - amount
        self.set_balance(mode, new_balance)
        if by is not None:
            self._log_balance_op("remove", mode, amount, by, reason=reason)
        

    def get_balance(self, mode):
        self.uid = str(self.uid)
        if str(mode).lower() == "real":
            return int(_flip_get_balance(self.uid))
        balances = get_user_data(int(self.uid), "balance") or {}
        if mode not in balances:
            balances[mode] = 0
        return int(balances[mode])
    
    def set_balance(self, mode, amount: int):
        self.uid = str(self.uid)
        if str(mode).lower() == "real":
            _flip_set_balance(self.uid, float(amount))
        balances = get_user_data(int(self.uid), "balance") or {}
        balances[mode] = int(amount)
        set_user_data(int(self.uid), "balance", balances)

    # ── Rakeback helpers ──────────────────────────────────────────────────

    def get_rakeback_data(self) -> dict:
        """Return the full rakeback dict for this player."""
        return get_user_data(int(self.uid), "rakeback") or {}

    def get_accumulated_rakeback(self) -> int:
        """Return pending (not-yet-withdrawn) rakeback."""
        return int(self.get_rakeback_data().get("accumulated", 0))

    def add_rakeback(self, amount: int):
        """Add earned rakeback to the player's pending wallet."""
        amount = int(amount)
        data = self.get_rakeback_data()
        data["accumulated"] = int(data.get("accumulated", 0)) + amount
        data["total_earned"] = int(data.get("total_earned", 0)) + amount
        set_user_data(int(self.uid), "rakeback", data)

    def withdraw_rakeback(self, amount: int):
        """Move *amount* from rakeback wallet to real balance."""
        amount = int(amount)
        data = self.get_rakeback_data()
        data["accumulated"] = max(0, int(data.get("accumulated", 0)) - amount)
        set_user_data(int(self.uid), "rakeback", data)
        self.add_balance("real", amount)

    def record_deposit(self, amount: float):
        """Increment total_deposit stat when a deposit is confirmed."""
        amount = int(amount)
        if amount <= 0:
            return
        stats = self.stats
        stats.setdefault("total_deposit", 0)
        stats["total_deposit"] += amount
        # Cumulative wager gate: add to cycle total without resetting wager progress.
        prev_cycle = int(stats.get("last_deposit_amount", 0) or 0)
        if prev_cycle <= 0:
            stats["wagered_at_last_deposit"] = int(stats.get("total_wagered", 0))
        stats["last_deposit_amount"] = prev_cycle + amount
        self.set_stats(stats)
        try:
            from modules.live_stats_tracker import update_daily_deposit
            update_daily_deposit(str(self.uid), amount)
        except Exception:
            pass

    def record_withdraw(self, amount: int):
        """Increment total_withdraw stat when a withdrawal is submitted."""
        amount = int(amount)
        if amount <= 0:
            return
        stats = self.stats
        stats.setdefault("total_withdraw", 0)
        stats["total_withdraw"] += amount
        stats["last_deposit_amount"] = 0
        stats["wagered_at_last_deposit"] = 0
        self.set_stats(stats)

    # ── Level helpers ─────────────────────────────────────────────────────

    def get_level_data(self) -> dict:
        """Return level data: {"level": int, "last_chest_date": str}."""
        data = get_user_data(int(self.uid), "level") or {}
        data.setdefault("level", 1)
        data.setdefault("last_chest_date", "")
        return data

    @property
    def level(self) -> int:
        return self.get_level_data()["level"]

    def set_level(self, new_level: int):
        data = self.get_level_data()
        data["level"] = int(new_level)
        set_user_data(int(self.uid), "level", data)

    def check_and_apply_level_up(self) -> list:
        """
        Check whether the player qualifies for a level-up and apply it.
        Returns a list of new levels gained (may be empty).
        """
        from modules.levels import levels_to_gain
        stats = self.stats
        total_wagered = int(stats.get("total_wagered", 0))
        total_deposit = int(stats.get("total_deposit", 0))
        current_level = self.level
        gained = levels_to_gain(current_level, total_wagered, total_deposit)
        if gained:
            self.set_level(current_level + gained)
            return list(range(current_level + 1, current_level + gained + 1))
        return []

    
        

    

   


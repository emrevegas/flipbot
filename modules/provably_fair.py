"""
Provably Fair sistem modülü.

Oyun başlamadan önce:
  - Sunucu seed'i gizlice üretilir; sadece hash'i gösterilir
  - Kullanıcının client seed'i ve nonce değeri log kanalına iletilir
  - HMAC-SHA256(server_seed, client_seed:nonce) → belirleyici float listesi

Oyun bittikten sonra:
  - Gerçek server seed açıklanır
  - Kullanıcı hash'i verify edebilir: sha256(server_seed) == Server Seed Hash
  - Tam HMAC da gösterilir → sonucun önceden belirlendiği kanıtlanır
"""
import hashlib
import hmac as _hmac
import os
import uuid
import discord
from typing import Optional

from modules.database import get_user_data, set_user_data


# ──────────────────────────────────────────────────────────
# Seed helpers
# ──────────────────────────────────────────────────────────

def generate_server_seed() -> str:
    """32 byte rastgele hex server seed üret."""
    return os.urandom(32).hex()


def hash_seed(seed: str) -> str:
    """SHA-256 hex hash döndür."""
    return hashlib.sha256(seed.encode()).hexdigest()


def pf_floats(server_seed: str, client_seed: str, nonce: int) -> list:
    """
    HMAC-SHA256(server_seed, "{client_seed}:{nonce}") ile 8 adet [0,1) float üret.
    SHA-256 çıktısı 32 byte = 8 × 4 byte chunk.
    """
    msg = f"{client_seed}:{nonce}".encode()
    digest = _hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
    return [int.from_bytes(digest[i * 4:(i + 1) * 4], "big") / (2 ** 32) for i in range(8)]


# ──────────────────────────────────────────────────────────
# Per-user PF state (client seed + nonce)
# ──────────────────────────────────────────────────────────

def get_user_pf_state(user_id: int) -> dict:
    """Kullanıcının client_seed ve nonce verilerini getir; yoksa oluştur."""
    data = get_user_data(user_id, "provably_fair") or {}
    if not data.get("client_seed"):
        data["client_seed"] = os.urandom(16).hex()
        data["nonce"] = 0
        set_user_data(user_id, "provably_fair", data)
    return data


def consume_pf_round(user_id: int):
    """
    Yeni bir round için PF verilerini üret, nonce'u artır.
    Returns: (server_seed, client_seed, nonce, floats)
    """
    server_seed = generate_server_seed()
    data = get_user_pf_state(user_id)
    client_seed = data["client_seed"]
    nonce = int(data.get("nonce", 0))

    data["nonce"] = nonce + 1
    set_user_data(user_id, "provably_fair", data)

    floats = pf_floats(server_seed, client_seed, nonce)
    return server_seed, client_seed, nonce, floats


def new_game_uid() -> str:
    """Kısa benzersiz oyun ID'si üret (8 karakter uppercase hex)."""
    return uuid.uuid4().hex[:8].upper()


# ──────────────────────────────────────────────────────────
# Discord log channel helpers
# ──────────────────────────────────────────────────────────

async def log_game_start(
    interaction: discord.Interaction,
    game_name: str,
    game_emoji: str,
    user: discord.Member,
    bet: int,
    mode: str,
    server_seed_hash: str,
    client_seed: str,
    nonce: int,
    game_uid: str,
) -> Optional[discord.Message]:
    """No channel post — short result is logged at game end only."""
    return None


async def log_game_end(
    log_message: Optional[discord.Message],
    game_name: str,
    game_emoji: str,
    user: discord.Member,
    bet: int,
    mode: str,
    server_seed: str,
    client_seed: str,
    nonce: int,
    game_uid: str,
    result: str,
    meta: dict,
    profit: int,
    *,
    interaction: Optional[discord.Interaction] = None,
    client: Optional[discord.Client] = None,
    guild_id: Optional[int] = None,
    channel_id: Optional[int] = None,
) -> None:
    """Post a one-line English result to the unified game log channel."""
    from modules.game_log import post_short_game_log

    if interaction is not None:
        client = client or interaction.client
        if guild_id is None and interaction.guild:
            guild_id = interaction.guild.id
        if guild_id is None and interaction.channel and getattr(interaction.channel, "guild", None):
            guild_id = interaction.channel.guild.id
        if channel_id is None and interaction.channel:
            channel_id = interaction.channel.id

    await post_short_game_log(
        user,
        game_name,
        result,
        profit,
        mode,
        log_message=log_message,
        client=client,
        guild_id=guild_id,
        channel_id=channel_id,
        bet=int(bet),
    )

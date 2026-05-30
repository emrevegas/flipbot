"""Restrict user commands to play hub channels; jackpot room allows only .jp / .canceljp."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

import config
from modules.database import check_permission, is_super_admin
from modules.jackpot_store import is_jackpot_channel
from modules.private_room_hub import get_play_channel_ids, is_play_hub_channel

if TYPE_CHECKING:
    from discord.ext.commands import Context

# discord.Message is slotted — cannot setattr custom flags.
_handled_message_ids: set[int] = set()
_HANDLED_IDS_MAX = 8000


def _mark_message_handled(message: discord.Message) -> None:
    _handled_message_ids.add(message.id)
    if len(_handled_message_ids) > _HANDLED_IDS_MAX:
        _handled_message_ids.clear()


def message_was_channel_guard_handled(message: discord.Message | None) -> bool:
    if message is None:
        return False
    return message.id in _handled_message_ids


class ChannelGuardError(commands.CheckFailure):
  """Raised when a command is used outside allowed channels."""

  def __init__(self, message: str):
    self.text = message
    super().__init__(message)


def effective_guard_channel_id(
  channel: discord.abc.GuildChannel | discord.Thread | None,
) -> int | None:
  """Thread commands inherit play/jackpot rules from the parent channel."""
  if channel is None:
    return None
  if isinstance(channel, discord.Thread):
    return channel.parent_id
  return channel.id


def is_channel_staff(member: discord.abc.User) -> bool:
  if is_super_admin(member.id):
    return True
  if isinstance(member, discord.Member):
    if member.guild_permissions.administrator:
      return True
  uid = str(member.id)
  if not check_permission(uid, "admin"):
    return True
  if not check_permission(uid, "cashier"):
    return True
  return False


def is_jackpot_prefix(content: str) -> bool:
  lower = (content or "").strip().lower()
  p = config.PREFIX.lower()
  if not lower.startswith(p):
    return False
  rest = lower[len(p) :].lstrip()
  if not rest:
    return False
  head = rest.split()[0]
  if head in ("jackpot", "jp", "canceljp"):
    return True
  if rest.startswith("cancel jackpot"):
    return True
  return False


def format_play_channel_mentions(guild_id: str, guild: discord.Guild | None) -> str:
  ids = get_play_channel_ids(guild_id)
  if not ids:
    return "*(play kanalları henüz kurulmadı — yetkililere başvurun)*"
  parts: list[str] = []
  for cid in ids:
    ch = guild.get_channel(cid) if guild else None
    parts.append(ch.mention if ch else f"<#{cid}>")
  return " ".join(parts)


def redirect_text(
  guild_id: str,
  guild: discord.Guild | None,
  *,
  in_jackpot: bool,
) -> str:
  plays = format_play_channel_mentions(guild_id, guild)
  lines = [
    f"Komutlar yalnızca play kanallarında kullanılabilir: {plays}",
  ]
  if in_jackpot:
    lines.append(
      f"Bu kanalda yalnızca `{config.PREFIX}jp` / `{config.PREFIX}jackpot` "
      f"ve `{config.PREFIX}canceljp` kullanılabilir."
    )
  return "\n".join(lines)


def command_channel_allowed(
  guild_id: str,
  channel_id: int,
  *,
  content: str = "",
) -> tuple[bool, str | None]:
  if is_play_hub_channel(guild_id, channel_id):
    return True, None
  if is_jackpot_channel(channel_id) and is_jackpot_prefix(content):
    return True, None
  in_jp = is_jackpot_channel(channel_id)
  return False, redirect_text(guild_id, None, in_jackpot=in_jp)


async def handle_wrong_channel_message(message: discord.Message, bot) -> bool:
  """Reply with play-channel mentions, delete message. Returns True if handled."""
  if message.author.bot or not message.guild:
    return False
  if message_was_channel_guard_handled(message):
    return True
  if is_channel_staff(message.author):
    return False

  ctx = await bot.get_context(message)
  if not ctx.prefix:
    return False

  content = (message.content or "").strip()
  if is_jackpot_prefix(content):
    return False

  guild_id = str(message.guild.id)
  channel_id = effective_guard_channel_id(message.channel)
  if channel_id is None:
    return False
  allowed, text = command_channel_allowed(guild_id, channel_id, content=content)
  if allowed or not text:
    return False

  _mark_message_handled(message)

  text = redirect_text(
    guild_id,
    message.guild,
    in_jackpot=is_jackpot_channel(channel_id),
  )
  try:
    await message.channel.send(
      text,
      reference=message,
      mention_author=True,
      delete_after=12,
    )
  except Exception:
    try:
      await message.channel.send(text, delete_after=12)
    except Exception:
      pass
  try:
    await message.delete()
  except Exception:
    pass
  return True


def assert_command_channel(ctx: Context) -> None:
  if not ctx.guild or not ctx.channel:
    return
  if is_channel_staff(ctx.author):
    return
  if message_was_channel_guard_handled(ctx.message):
    raise ChannelGuardError("")

  guild_id = str(ctx.guild.id)
  content = (ctx.message.content if ctx.message else "") or ""
  channel_id = effective_guard_channel_id(ctx.channel)
  if channel_id is None:
    return
  allowed, _text = command_channel_allowed(guild_id, channel_id, content=content)
  if allowed:
    return
  raise ChannelGuardError("")


async def interaction_channel_allowed(interaction: discord.Interaction) -> tuple[bool, str | None]:
  if not interaction.guild or not interaction.channel:
    return True, None
  user = interaction.user
  if is_channel_staff(user):
    return True, None

  guild_id = str(interaction.guild.id)
  channel_id = effective_guard_channel_id(interaction.channel)
  if channel_id is None:
    return True, None
  if is_play_hub_channel(guild_id, channel_id):
    return True, None
  if is_jackpot_channel(channel_id):
    return False, redirect_text(guild_id, interaction.guild, in_jackpot=True)
  return False, redirect_text(guild_id, interaction.guild, in_jackpot=False)


async def interaction_channel_check(interaction: discord.Interaction) -> bool:
  ok, text = await interaction_channel_allowed(interaction)
  if ok:
    return True
  text = text or ""
  try:
    if interaction.response.is_done():
      await interaction.followup.send(text, ephemeral=True)
    else:
      await interaction.response.send_message(text, ephemeral=True)
  except Exception:
    pass
  return False

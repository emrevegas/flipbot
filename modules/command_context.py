"""Prefix command context — replies to the invoking message with mention."""

from __future__ import annotations

from typing import Any

import discord
from discord.ext import commands


class ReplyContext(commands.Context):
    """Default ctx.send / ctx.reply to the user's command (mention on)."""

    async def send(
        self,
        content: str | None = None,
        **kwargs: Any,
    ) -> discord.Message:
        if self.message is not None and kwargs.get("reference") is None:
            kwargs["reference"] = self.message
        if kwargs.get("reference") is not None:
            kwargs.setdefault("mention_author", True)
        return await super().send(content, **kwargs)

    async def reply(self, *args: Any, **kwargs: Any) -> discord.Message:
        kwargs.setdefault("mention_author", True)
        return await super().reply(*args, **kwargs)

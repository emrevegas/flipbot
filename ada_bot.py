#!/usr/bin/env python3
"""
Ada control bot — license/build yönetimi only (ayrı Discord token).

Casino bot (bot.py) ile aynı klasörde çalışır; ada.env kullanır.
  python ada_bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Önce ada.env, yoksa .env (casino ile karışmasın diye ada.env önerilir)
load_dotenv(ROOT / "ada.env")
load_dotenv(ROOT / ".env")

# Standalone — install_state.json olsa bile vds_panel yüklensin
os.environ["ADA_STANDALONE_BOT"] = "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ada_bot")


class AdaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        await self.load_extension("cogs.vds_panel")
        log.info("Loaded cogs.vds_panel")
        synced = await self.tree.sync()
        log.info("Synced %s slash command(s)", len(synced))

    async def on_ready(self):
        log.info("Ada bot ready as %s (ID: %s)", self.user, self.user.id)


async def main() -> None:
    token = (os.getenv("ADA_BOT_TOKEN") or os.getenv("TOKEN") or "").strip()
    if not token:
        raise SystemExit(
            "❌ ADA_BOT_TOKEN missing — create ada.env (see ada.env.example)"
        )
    bot = AdaBot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())

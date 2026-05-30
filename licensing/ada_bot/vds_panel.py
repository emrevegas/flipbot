"""
Ada bot (main control bot) — /build and /vds_manage commands.

Load on your MAIN bot (not customer bots):
  await bot.load_extension("licensing.ada_bot.vds_panel")
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from licensing.client.license_client import LicenseClient

PLAN_CHOICES = [
    app_commands.Choice(name="Daily (1 day)", value="daily"),
    app_commands.Choice(name="Weekly (7 days)", value="weekly"),
    app_commands.Choice(name="Monthly (30 days)", value="monthly"),
]


def _owner_only():
    async def pred(interaction: discord.Interaction) -> bool:
        owner_ids = [int(x) for x in os.getenv("OWNER_ID", "0").split(",") if x.strip()]
        if interaction.user.id not in owner_ids:
            raise app_commands.CheckFailure("Owner only.")
        return True
    return app_commands.check(pred)


def _admin_key() -> str:
    key = os.getenv("LICENSE_ADMIN_KEY", "")
    if not key:
        raise RuntimeError("LICENSE_ADMIN_KEY missing in Ada bot .env")
    return key


def _server_url() -> str:
    from licensing.common.server_url import resolve_license_server_url

    url = resolve_license_server_url()
    if not url:
        raise RuntimeError("LICENSE_SERVER_IP or LICENSE_SERVER_URL missing in Ada bot .env")
    return url


class VdsPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    vds = app_commands.Group(name="vds_manage", description="License & VDS management")

    @app_commands.command(name="build", description="Compile Ubuntu release (Cython) and publish")
    @_owner_only()
    @app_commands.describe(version="Release version e.g. 1.0.1")
    async def build(self, interaction: discord.Interaction, version: str):
        await interaction.response.defer(ephemeral=True)
        script = ROOT / "licensing" / "build" / "build_release.py"
        if not script.exists():
            return await interaction.followup.send("❌ build_release.py not found.", ephemeral=True)

        async def _run():
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(script), "--version", version.strip(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(ROOT),
                env=os.environ.copy(),
            )
            out, _ = await proc.communicate()
            return proc.returncode, out.decode(errors="replace")[-1800:]

        code, tail = await _run()
        if code != 0:
            return await interaction.followup.send(
                f"❌ Build failed (exit {code}):\n```\n{tail}\n```",
                ephemeral=True,
            )
        await interaction.followup.send(
            f"✅ Build **v{version}** finished.\n```\n{tail}\n```",
            ephemeral=True,
        )

    @vds.command(name="create_license", description="Generate a new license key")
    @_owner_only()
    @app_commands.describe(
        plan="Subscription plan",
        customer="Customer Discord user",
        label="Customer label / note",
    )
    @app_commands.choices(plan=PLAN_CHOICES)
    async def create_license(
        self,
        interaction: discord.Interaction,
        plan: str,
        customer: discord.User | None = None,
        label: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            client = LicenseClient(_server_url())
            data = client.admin_create_license(
                _admin_key(),
                plan=plan,
                customer_discord_id=str(customer.id) if customer else None,
                customer_label=label or (customer.display_name if customer else None),
            )
        except RuntimeError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        lic = data["license"]
        embed = discord.Embed(title="🎫 License Created", color=0x2ECC71)
        embed.add_field(name="Key", value=f"`{lic['license_key']}`", inline=False)
        embed.add_field(name="Plan", value=lic["plan"], inline=True)
        embed.add_field(name="Expires", value=f"<t:{lic['expires_at']}:F>", inline=True)
        embed.set_footer(text="Customer runs: python licensing/client/install.py")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @vds.command(name="list_licenses", description="List license keys")
    @_owner_only()
    async def list_licenses(self, interaction: discord.Interaction, status: str | None = None):
        await interaction.response.defer(ephemeral=True)
        try:
            client = LicenseClient(_server_url())
            data = client.admin_list_licenses(_admin_key(), status=status, limit=25)
        except RuntimeError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        lines = []
        for lic in data.get("licenses", [])[:25]:
            ip = lic.get("whitelisted_ip") or "—"
            lines.append(
                f"`{lic['license_key']}` · **{lic['status']}** · {lic['plan']} · "
                f"exp <t:{lic['expires_at']}:R> · IP {ip}"
            )
        body = "\n".join(lines) if lines else "No licenses."
        await interaction.followup.send(body[:3900], ephemeral=True)

    @vds.command(name="list_instances", description="Active licensed bot instances")
    @_owner_only()
    async def list_instances(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            client = LicenseClient(_server_url())
            data = client.admin_list_instances(_admin_key(), limit=25)
        except RuntimeError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        lines = []
        for inst in data.get("instances", [])[:25]:
            lines.append(
                f"`{inst['license_key']}` · guild `{inst.get('guild_id') or '?'}` · "
                f"v{inst.get('bot_version') or '?'} · IP {inst.get('ip')} · "
                f"<t:{inst['last_seen']}:R>"
            )
        body = "\n".join(lines) if lines else "No instances yet."
        await interaction.followup.send(body[:3900], ephemeral=True)

    @vds.command(name="suspend", description="Suspend a license")
    @_owner_only()
    async def suspend(self, interaction: discord.Interaction, license_key: str):
        await interaction.response.defer(ephemeral=True)
        try:
            client = LicenseClient(_server_url())
            client.admin_set_status(_admin_key(), license_key, "suspended")
        except RuntimeError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(f"⏸️ Suspended `{license_key.upper()}`", ephemeral=True)

    @vds.command(name="revoke", description="Revoke a license permanently")
    @_owner_only()
    async def revoke(self, interaction: discord.Interaction, license_key: str):
        await interaction.response.defer(ephemeral=True)
        try:
            client = LicenseClient(_server_url())
            client.admin_set_status(_admin_key(), license_key, "revoked")
        except RuntimeError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(f"🛑 Revoked `{license_key.upper()}`", ephemeral=True)

    @vds.command(name="extend", description="Extend license duration")
    @_owner_only()
    async def extend(self, interaction: discord.Interaction, license_key: str, extra_days: int):
        await interaction.response.defer(ephemeral=True)
        try:
            client = LicenseClient(_server_url())
            data = client.admin_extend(_admin_key(), license_key, extra_days)
        except RuntimeError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        lic = data["license"]
        await interaction.followup.send(
            f"✅ Extended `{lic['license_key']}` → <t:{lic['expires_at']}:F>",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VdsPanel(bot))

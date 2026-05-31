"""Ada license bot — /build, /vds_manage, /use_license, /manage_bot (English UI)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from licensing.control.db import LicenseControlDB
from licensing.control.deploy import (
    bot_service_status,
    build_license_dat,
    creds_from_vds_row,
    deploy_to_vds,
    push_license_file,
    service_control,
)
from licensing.control.github_build import trigger_build_workflow
from licensing.control.pricing import PLAN_DAYS, PLAN_LABELS, plan_price
from licensing.control.remote import RemoteCredentials, parse_vds_string, test_ssh_connection
from licensing.control.secrets import encrypt_text

log = logging.getLogger("ada_license")

PLAN_CHOICES = [
    app_commands.Choice(name=label, value=key)
    for key, label in PLAN_LABELS.items()
]

_db: LicenseControlDB | None = None


def get_db() -> LicenseControlDB:
    global _db
    if _db is None:
        _db = LicenseControlDB()
    return _db


def _owner_ids() -> list[int]:
    return [int(x) for x in os.getenv("OWNER_ID", "0").split(",") if x.strip()]


def owner_only():
    async def pred(interaction: discord.Interaction) -> bool:
        if interaction.user.id not in _owner_ids():
            raise app_commands.CheckFailure("Owner only.")
        return True

    return app_commands.check(pred)


def _days_left(expires_at: int) -> int:
    return max(0, int((expires_at - time.time()) / 86400))


class VdsConnectModal(discord.ui.Modal, title="Add VDS"):
    credentials = discord.ui.TextInput(
        label="host:port:username:password",
        placeholder="203.0.113.10:22:root:yourpassword",
        required=True,
        max_length=400,
    )
    os_type = discord.ui.TextInput(
        label="OS (ubuntu or windows)",
        placeholder="ubuntu",
        default="ubuntu",
        required=True,
        max_length=16,
    )

    def __init__(self, cog: AdaLicense, license_key: str):
        super().__init__()
        self.cog = cog
        self.license_key = license_key

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        os_raw = self.os_type.value.strip().lower()
        if os_raw not in ("ubuntu", "windows"):
            return await interaction.followup.send("OS must be `ubuntu` or `windows`.", ephemeral=True)
        try:
            base = parse_vds_string(self.credentials.value)
            creds = RemoteCredentials(
                host=base.host,
                port=base.port,
                username=base.username,
                password=base.password,
                os_type=os_raw,
            )
        except ValueError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)

        ok, msg = await asyncio.to_thread(test_ssh_connection, creds)
        if not ok:
            return await interaction.followup.send(f"❌ Connection failed: {msg}", ephemeral=True)

        db = get_db()
        try:
            db.save_vds(
                str(interaction.user.id),
                host=creds.host,
                port=creds.port,
                username=creds.username,
                password_enc=encrypt_text(creds.password),
                os_type=os_raw,
                license_key=self.license_key,
            )
        except ValueError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)

        await interaction.followup.send(
            f"✅ VDS connected: `{creds.host}:{creds.port}` ({os_raw})\n"
            "Use `/manage_bot setup` to deploy your bot (Ubuntu required for compiled build).",
            ephemeral=True,
        )


class BotSetupModal(discord.ui.Modal, title="Bot setup"):
    token = discord.ui.TextInput(label="Discord bot token", required=True, max_length=200)
    crypto_mnemonic = discord.ui.TextInput(
        label="Crypto deposit mnemonic (24 words)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )
    treasury_mnemonic = discord.ui.TextInput(
        label="Treasury mnemonic (24 words)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )
    guild_id = discord.ui.TextInput(label="Guild ID", required=True, max_length=32)

    def __init__(self, cog: AdaLicense):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        uid = str(interaction.user.id)
        db = get_db()
        lic = db.user_active_license(uid)
        vds = db.get_vds(uid)
        if not lic or not vds:
            return await interaction.followup.send("❌ Active license and VDS required.", ephemeral=True)
        if (vds.get("os_type") or "").lower() == "windows":
            return await interaction.followup.send(
                "❌ Compiled bot requires **Ubuntu** VDS. Windows SSH is supported for future use; use Ubuntu or WSL2.",
                ephemeral=True,
            )

        release = db.latest_release("linux-x86_64")
        if not release:
            from licensing.control.github_releases import fetch_latest_release

            fetched = await asyncio.to_thread(fetch_latest_release, "linux-x86_64")
            if fetched and fetched.get("download_url"):
                digest = fetched.get("sha256") or ""
                if digest:
                    db.register_release(
                        fetched["version"],
                        "linux-x86_64",
                        fetched["download_url"],
                        digest,
                    )
                release = fetched
        if not release or not release.get("download_url"):
            return await interaction.followup.send(
                "❌ No release published yet. Owner must run `/build` and `/vds_manage register_release`.",
                ephemeral=True,
            )
        sha = release.get("sha256") or ""
        if not sha:
            return await interaction.followup.send(
                "❌ Release SHA256 missing. Owner must run `/vds_manage register_release`.",
                ephemeral=True,
            )

        creds = creds_from_vds_row(vds)
        env = {
            "TOKEN": self.token.value.strip(),
            "OWNER_ID": uid,
            "SUPER_ADMIN_ID": uid,
            "GUILD_ID": self.guild_id.value.strip(),
            "CRYPTO_MNEMONIC": self.crypto_mnemonic.value.strip(),
            "TREASURY_MNEMONIC": self.treasury_mnemonic.value.strip(),
            "LICENSE_KEY": lic["license_key"],
            "PREFIX": ".",
        }
        db.update_vds_deploy(uid, deploy_status="deploying")
        ok, log_tail = await asyncio.to_thread(
            deploy_to_vds,
            creds,
            download_url=release["download_url"],
            sha256=sha,
            version=release["version"],
            env=env,
            license_key=lic["license_key"],
            discord_id=uid,
            expires_at=lic["expires_at"],
            releases_repo=os.getenv("RELEASES_GITHUB_REPO", ""),
        )
        if not ok:
            db.update_vds_deploy(uid, deploy_status="failed", last_error=log_tail[:500])
            db.log_deploy(uid, "setup", "failed", log_tail)
            return await interaction.followup.send(
                f"❌ Deploy failed:\n```\n{log_tail[-1200:]}\n```",
                ephemeral=True,
            )

        db.update_vds_deploy(
            uid,
            deploy_status="running",
            bot_version=release["version"],
            install_dir="/opt/flipbot",
            guild_id=self.guild_id.value.strip(),
            last_error="",
        )
        db.log_deploy(uid, "setup", "ok", log_tail)

        status = await asyncio.to_thread(bot_service_status, creds)
        try:
            await interaction.user.send(
                f"✅ **Flipbot deployed** on `{vds['host']}`\n"
                f"Version: **{release['version']}** · Service: **{status}**\n"
                f"License expires <t:{lic['expires_at']}:R> ({_days_left(lic['expires_at'])} days left)"
            )
        except discord.Forbidden:
            pass

        await interaction.followup.send(
            f"✅ Bot installed (v{release['version']}). Service: **{status}**. Check your DMs.",
            ephemeral=True,
        )


class AdaLicense(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sync_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self._sync_task = asyncio.create_task(self._license_sync_loop())

    async def cog_unload(self) -> None:
        if self._sync_task:
            self._sync_task.cancel()

    async def _license_sync_loop(self) -> None:
        """Push signed license.dat + stop bots when license revoked/expired."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._sync_all_deployments()
            except Exception as exc:
                log.warning("license sync: %s", exc)
            await asyncio.sleep(int(os.getenv("LICENSE_SYNC_INTERVAL", "300")))

    async def _sync_all_deployments(self) -> None:
        db = get_db()
        releases_repo = os.getenv("RELEASES_GITHUB_REPO", "").strip()
        for row in db.list_all_vds(100):
            if row.get("deploy_status") not in ("running", "deploying", "stopped"):
                continue
            lic = db.get_license(row.get("license_key") or "")
            if not lic:
                continue
            uid = row["discord_id"]
            status = "active"
            if lic["status"] in ("revoked", "suspended") or lic["expires_at"] < time.time():
                status = lic["status"] if lic["status"] != "active" else "expired"
            try:
                creds = creds_from_vds_row(row)
            except Exception:
                continue
            payload = build_license_dat(
                license_key=lic["license_key"],
                discord_id=uid,
                expires_at=lic["expires_at"],
                status=status,
                releases_repo=releases_repo,
            )
            await asyncio.to_thread(push_license_file, creds, payload)
            if status != "active":
                await asyncio.to_thread(service_control, creds, "stop")
                db.update_vds_deploy(uid, deploy_status="stopped", last_error=status)

    # ── Owner: build ────────────────────────────────────────

    @app_commands.command(name="build", description="Trigger GitHub Actions release build")
    @owner_only()
    @app_commands.describe(version="Release version e.g. 1.0.2")
    async def build(self, interaction: discord.Interaction, version: str):
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await asyncio.to_thread(trigger_build_workflow, version.strip())
        except Exception as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(f"✅ {msg}", ephemeral=True)

    # ── Owner: vds_manage ───────────────────────────────────

    manage = app_commands.Group(name="vds_manage", description="Owner: licenses & deployments")

    @manage.command(name="create_license", description="Create a license key")
    @owner_only()
    @app_commands.describe(plan="Subscription plan", customer="Discord user", label="Note")
    @app_commands.choices(plan=PLAN_CHOICES)
    async def create_license(
        self,
        interaction: discord.Interaction,
        plan: str,
        customer: discord.User | None = None,
        label: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        db = get_db()
        lic = db.create_license(
            plan,
            owner_discord_id=str(customer.id) if customer else None,
            customer_label=label or (customer.display_name if customer else None),
            status="active" if customer else "unused",
        )
        embed = discord.Embed(title="License created", color=0x2ECC71)
        embed.add_field(name="Key", value=f"`{lic['license_key']}`", inline=False)
        embed.add_field(name="Plan", value=lic["plan"], inline=True)
        embed.add_field(name="Expires", value=f"<t:{lic['expires_at']}:F>", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @manage.command(name="grant_balance", description="Add balance to a user")
    @owner_only()
    async def grant_balance(self, interaction: discord.Interaction, user: discord.User, amount: float):
        await interaction.response.defer(ephemeral=True)
        if amount <= 0:
            return await interaction.followup.send("Amount must be positive.", ephemeral=True)
        bal = get_db().add_balance(str(user.id), amount)
        await interaction.followup.send(f"✅ {user.mention} balance: **{bal:.2f}**", ephemeral=True)

    @manage.command(name="list_licenses", description="List license keys")
    @owner_only()
    async def list_licenses(self, interaction: discord.Interaction, status: str | None = None):
        await interaction.response.defer(ephemeral=True)
        rows = get_db().list_licenses(status=status, limit=25)
        lines = []
        for lic in rows:
            owner = lic.get("owner_discord_id") or "—"
            lines.append(
                f"`{lic['license_key']}` · **{lic['status']}** · {lic['plan']} · "
                f"<t:{lic['expires_at']}:R> · owner `{owner}`"
            )
        await interaction.followup.send("\n".join(lines) or "No licenses.", ephemeral=True)

    @manage.command(name="list_vds", description="List customer VDS deployments")
    @owner_only()
    async def list_vds(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lines = []
        for v in get_db().list_all_vds(30):
            lines.append(
                f"<@{v['discord_id']}> · `{v['host']}:{v['port']}` · {v['os_type']} · "
                f"**{v['deploy_status']}** · lic `{v.get('license_key') or '?'}`"
            )
        await interaction.followup.send("\n".join(lines) or "No VDS entries.", ephemeral=True)

    @manage.command(name="suspend", description="Suspend a license")
    @owner_only()
    async def suspend(self, interaction: discord.Interaction, license_key: str):
        await interaction.response.defer(ephemeral=True)
        get_db().set_license_status(license_key, "suspended")
        await interaction.followup.send(f"⏸️ Suspended `{license_key.upper()}`", ephemeral=True)

    @manage.command(name="revoke", description="Revoke a license")
    @owner_only()
    async def revoke(self, interaction: discord.Interaction, license_key: str):
        await interaction.response.defer(ephemeral=True)
        get_db().set_license_status(license_key, "revoked")
        await interaction.followup.send(f"🛑 Revoked `{license_key.upper()}`", ephemeral=True)

    @manage.command(name="extend", description="Extend license duration")
    @owner_only()
    async def extend(self, interaction: discord.Interaction, license_key: str, extra_days: int):
        await interaction.response.defer(ephemeral=True)
        try:
            lic = get_db().extend_license(license_key, extra_days)
        except ValueError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(
            f"✅ Extended → <t:{lic['expires_at']}:F>",
            ephemeral=True,
        )

    @manage.command(name="register_release", description="Register built release in DB (after /build)")
    @owner_only()
    @app_commands.describe(
        version="Version",
        download_url="GitHub release asset URL",
        sha256="SHA256 of tarball",
    )
    async def register_release(
        self,
        interaction: discord.Interaction,
        version: str,
        download_url: str,
        sha256: str,
    ):
        await interaction.response.defer(ephemeral=True)
        get_db().register_release(version.strip(), "linux-x86_64", download_url.strip(), sha256.strip())
        await interaction.followup.send(f"✅ Registered release **v{version}**", ephemeral=True)

    # ── Customer: use_license ─────────────────────────────

    @app_commands.command(name="use_license", description="Activate your license key")
    @app_commands.describe(license_key="License key from purchase or owner")
    async def use_license(self, interaction: discord.Interaction, license_key: str):
        await interaction.response.defer(ephemeral=True)
        try:
            lic = get_db().bind_license_to_user(license_key, str(interaction.user.id))
        except ValueError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        days = _days_left(lic["expires_at"])
        await interaction.followup.send(
            f"✅ License **{lic['license_key']}** active · **{days}** day(s) left · plan **{lic['plan']}**\n"
            "Open `/manage_bot` to add your VDS and set up the bot.",
            ephemeral=True,
        )

    # ── Customer: manage_bot ────────────────────────────────

    bot_group = app_commands.Group(name="manage_bot", description="Manage your licensed bot")

    @bot_group.command(name="status", description="License, VDS and bot status")
    async def bot_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uid = str(interaction.user.id)
        db = get_db()
        lic = db.user_active_license(uid)
        vds = db.get_vds(uid)
        bal = db.get_balance(uid)
        embed = discord.Embed(title="Your bot status", color=0x3498DB)
        embed.add_field(name="Balance", value=f"{bal:.2f}", inline=True)
        if lic:
            embed.add_field(name="License", value=f"`{lic['license_key']}`", inline=True)
            embed.add_field(name="Days left", value=str(_days_left(lic["expires_at"])), inline=True)
            embed.add_field(name="Plan", value=lic["plan"], inline=True)
            embed.add_field(name="Expires", value=f"<t:{lic['expires_at']}:F>", inline=True)
        else:
            embed.add_field(name="License", value="None — use `/use_license`", inline=False)
        if vds:
            embed.add_field(name="VDS", value=f"`{vds['host']}:{vds['port']}` ({vds['os_type']})", inline=False)
            embed.add_field(name="Deploy", value=vds.get("deploy_status") or "?", inline=True)
            if vds.get("deploy_status") == "running":
                try:
                    st = await asyncio.to_thread(bot_service_status, creds_from_vds_row(vds))
                    embed.add_field(name="Service", value=st, inline=True)
                except Exception:
                    pass
        else:
            embed.add_field(name="VDS", value="Not connected", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot_group.command(name="buy_license", description="Purchase a license with your balance")
    @app_commands.choices(plan=PLAN_CHOICES)
    async def buy_license(self, interaction: discord.Interaction, plan: str):
        await interaction.response.defer(ephemeral=True)
        price = plan_price(plan)
        try:
            lic = get_db().purchase_license(str(interaction.user.id), plan)
        except ValueError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(
            f"✅ Purchased **{plan}** for **{price:.2f}** · Key: `{lic['license_key']}` · "
            f"{_days_left(lic['expires_at'])} day(s)\nRun `/use_license` with this key.",
            ephemeral=True,
        )

    @bot_group.command(name="add_vds", description="Connect your VDS (one per license slot)")
    async def add_vds(self, interaction: discord.Interaction):
        ok, msg = get_db().can_add_vds(str(interaction.user.id))
        if not ok:
            return await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
        await interaction.response.send_modal(VdsConnectModal(self, msg))

    @bot_group.command(name="remove_vds", description="Remove your VDS (requires /use_license to add again)")
    async def remove_vds(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uid = str(interaction.user.id)
        vds = get_db().get_vds(uid)
        if not vds:
            return await interaction.followup.send("❌ No VDS registered.", ephemeral=True)
        try:
            creds = creds_from_vds_row(vds)
            await asyncio.to_thread(service_control, creds, "stop")
        except Exception:
            pass
        get_db().delete_vds(uid)
        await interaction.followup.send(
            "✅ VDS removed. Run `/use_license` with your key before adding a new VDS.",
            ephemeral=True,
        )

    @bot_group.command(name="setup", description="Deploy bot to your VDS (Ubuntu)")
    async def setup(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        if not get_db().get_vds(uid):
            return await interaction.response.send_message("❌ Add a VDS first (`/manage_bot add_vds`).", ephemeral=True)
        if not get_db().user_active_license(uid):
            return await interaction.response.send_message("❌ Active license required.", ephemeral=True)
        await interaction.response.send_modal(BotSetupModal(self))

    @bot_group.command(name="start", description="Start bot service on VDS")
    async def start_bot(self, interaction: discord.Interaction):
        await self._service_action(interaction, "start")

    @bot_group.command(name="stop", description="Stop bot service on VDS")
    async def stop_bot(self, interaction: discord.Interaction):
        await self._service_action(interaction, "stop")

    @bot_group.command(name="restart", description="Restart bot service on VDS")
    async def restart_bot(self, interaction: discord.Interaction):
        await self._service_action(interaction, "restart")

    async def _service_action(self, interaction: discord.Interaction, action: str) -> None:
        await interaction.response.defer(ephemeral=True)
        vds = get_db().get_vds(str(interaction.user.id))
        if not vds:
            return await interaction.followup.send("❌ No VDS.", ephemeral=True)
        creds = creds_from_vds_row(vds)
        ok, msg = await asyncio.to_thread(service_control, creds, action)
        if ok:
            st = "running" if action == "start" else ("stopped" if action == "stop" else vds.get("deploy_status"))
            get_db().update_vds_deploy(str(interaction.user.id), deploy_status=st if st else None)
        prefix = "✅" if ok else "❌"
        await interaction.followup.send(f"{prefix} {action}: {msg}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdaLicense(bot))

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
            "Open `/manage_bot` → **Deploy bot** (Ubuntu required for compiled build).",
            ephemeral=True,
        )


_pending_bot_setup: dict[str, dict[str, Any]] = {}


async def _resolve_release(db: LicenseControlDB) -> dict[str, Any] | None:
    release = db.latest_release("linux-x86_64")
    if release and release.get("download_url") and release.get("sha256"):
        return release
    from licensing.control.github_releases import fetch_latest_release

    fetched = await asyncio.to_thread(fetch_latest_release, "linux-x86_64")
    if fetched and fetched.get("download_url") and fetched.get("sha256"):
        db.register_release(
            fetched["version"],
            "linux-x86_64",
            fetched["download_url"],
            fetched["sha256"],
        )
        return fetched
    return release if release and release.get("download_url") else None


async def _run_bot_deploy(interaction: discord.Interaction, uid: str, env: dict[str, str]) -> None:
    db = get_db()
    lic = db.user_active_license(uid)
    vds = db.get_vds(uid)
    if not lic or not vds:
        await interaction.followup.send("❌ Active license and VDS required.", ephemeral=True)
        return
    if (vds.get("os_type") or "").lower() == "windows":
        await interaction.followup.send(
            "❌ Compiled bot requires **Ubuntu** VDS.",
            ephemeral=True,
        )
        return
    release = await _resolve_release(db)
    if not release or not release.get("sha256"):
        await interaction.followup.send(
            "❌ No release in DB. Owner: `/build` then register in **VDS Manage** panel.",
            ephemeral=True,
        )
        return

    creds = creds_from_vds_row(vds)
    db.update_vds_deploy(uid, deploy_status="deploying")
    ok, log_tail = await asyncio.to_thread(
        deploy_to_vds,
        creds,
        download_url=release["download_url"],
        sha256=release["sha256"],
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
        await interaction.followup.send(
            f"❌ Deploy failed:\n```\n{log_tail[-1200:]}\n```",
            ephemeral=True,
        )
        return

    db.update_vds_deploy(
        uid,
        deploy_status="running",
        bot_version=release["version"],
        install_dir="/opt/flipbot",
        guild_id=env.get("GUILD_ID", ""),
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


def _status_embed(uid: str) -> discord.Embed:
    db = get_db()
    lic = db.user_active_license(uid)
    vds = db.get_vds(uid)
    bal = db.get_balance(uid)
    embed = discord.Embed(title="Bot control panel", color=0x3498DB)
    embed.add_field(name="Shop balance", value=f"{bal:.2f}", inline=True)
    if lic:
        embed.add_field(name="License", value=f"`{lic['license_key']}`", inline=True)
        embed.add_field(name="Days left", value=str(_days_left(lic["expires_at"])), inline=True)
    else:
        embed.add_field(name="License", value="None — activate key in panel", inline=False)
    if vds:
        embed.add_field(
            name="VDS",
            value=f"`{vds['host']}:{vds['port']}` ({vds['os_type']}) · **{vds.get('deploy_status', '?')}**",
            inline=False,
        )
    else:
        embed.add_field(name="VDS", value="Not connected", inline=False)
    return embed


class BotSetupModal(discord.ui.Modal, title="Bot setup (1/2)"):
    token = discord.ui.TextInput(label="Discord bot token", required=True, max_length=200)
    guild_id = discord.ui.TextInput(label="Guild ID", required=True, max_length=32)
    crypto_mnemonic = discord.ui.TextInput(
        label="Crypto deposit mnemonic",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )
    treasury_mnemonic = discord.ui.TextInput(
        label="Treasury mnemonic",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    def __init__(self, cog: AdaLicense):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = str(interaction.user.id)
        _pending_bot_setup[uid] = {
            "TOKEN": self.token.value.strip(),
            "GUILD_ID": self.guild_id.value.strip(),
            "CRYPTO_MNEMONIC": self.crypto_mnemonic.value.strip(),
            "TREASURY_MNEMONIC": self.treasury_mnemonic.value.strip(),
            "OWNER_ID": uid,
            "SUPER_ADMIN_ID": uid,
            "PREFIX": ".",
        }
        lic = get_db().user_active_license(uid)
        if lic:
            _pending_bot_setup[uid]["LICENSE_KEY"] = lic["license_key"]
        await interaction.response.send_message(
            "Step **2/2** — add RPC / API keys (optional fields can stay empty).",
            view=RpcContinueView(self.cog, uid),
            ephemeral=True,
        )


class RpcSetupModal(discord.ui.Modal, title="Bot setup (2/2) — RPC & APIs"):
    sol_rpc = discord.ui.TextInput(
        label="SOL_RPC_URL",
        placeholder="https://mainnet.helius-rpc.com/?api-key=...",
        required=False,
        max_length=300,
    )
    eth_rpc = discord.ui.TextInput(
        label="ETH_RPC_URL",
        placeholder="https://eth-mainnet.g.alchemy.com/v2/...",
        required=False,
        max_length=300,
    )
    blockcypher = discord.ui.TextInput(
        label="BLOCKCYPHER_TOKEN",
        required=False,
        max_length=120,
    )

    def __init__(self, cog: AdaLicense, user_id: str):
        super().__init__()
        self.cog = cog
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        base = _pending_bot_setup.pop(self.user_id, None)
        if not base:
            return await interaction.followup.send("❌ Setup expired — start again from the panel.", ephemeral=True)
        if self.sol_rpc.value.strip():
            base["SOL_RPC_URL"] = self.sol_rpc.value.strip()
        if self.eth_rpc.value.strip():
            base["ETH_RPC_URL"] = self.eth_rpc.value.strip()
        if self.blockcypher.value.strip():
            base["BLOCKCYPHER_TOKEN"] = self.blockcypher.value.strip()
        await _run_bot_deploy(interaction, self.user_id, base)


class RpcContinueView(discord.ui.View):
    def __init__(self, cog: AdaLicense, user_id: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id

    @discord.ui.button(label="RPC & Deploy", style=discord.ButtonStyle.green, emoji="▶️")
    async def continue_rpc(self, interaction: discord.Interaction, _: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        await interaction.response.send_modal(RpcSetupModal(self.cog, self.user_id))


class UseLicenseModal(discord.ui.Modal, title="Activate license"):
    license_key = discord.ui.TextInput(label="License key", required=True, max_length=32)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            lic = get_db().bind_license_to_user(self.license_key.value, str(interaction.user.id))
        except ValueError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(
            f"✅ **{lic['license_key']}** · {_days_left(lic['expires_at'])} day(s) left",
            ephemeral=True,
        )


class GrantBalanceModal(discord.ui.Modal, title="Grant shop balance"):
    user_id = discord.ui.TextInput(label="Discord user ID", required=True, max_length=24)
    amount = discord.ui.TextInput(label="Amount", required=True, max_length=16)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            amt = float(self.amount.value.strip().replace(",", "."))
            uid = self.user_id.value.strip()
            if amt <= 0:
                raise ValueError("amount")
            bal = get_db().add_balance(uid, amt)
        except ValueError:
            return await interaction.followup.send("❌ Invalid user ID or amount.", ephemeral=True)
        await interaction.followup.send(f"✅ User `{uid}` balance: **{bal:.2f}**", ephemeral=True)


class CreateLicenseModal(discord.ui.Modal, title="Create license"):
    plan = discord.ui.TextInput(label="Plan: daily, weekly, monthly", required=True, max_length=16)
    customer_id = discord.ui.TextInput(label="Customer Discord ID (optional)", required=False, max_length=24)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        plan = self.plan.value.strip().lower()
        if plan not in PLAN_DAYS:
            return await interaction.followup.send("❌ Plan must be daily, weekly, or monthly.", ephemeral=True)
        cid = self.customer_id.value.strip() or None
        lic = get_db().create_license(
            plan,
            owner_discord_id=cid,
            status="active" if cid else "unused",
        )
        await interaction.followup.send(
            f"✅ `{lic['license_key']}` · {lic['plan']} · <t:{lic['expires_at']}:F>",
            ephemeral=True,
        )


class RegisterReleaseModal(discord.ui.Modal, title="Register release"):
    version = discord.ui.TextInput(label="Version", required=True, max_length=16)
    url = discord.ui.TextInput(label="Download URL", required=True, max_length=400)
    sha256 = discord.ui.TextInput(label="SHA256", required=True, max_length=80)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        get_db().register_release(
            self.version.value.strip(),
            "linux-x86_64",
            self.url.value.strip(),
            self.sha256.value.strip(),
        )
        await interaction.followup.send(f"✅ Registered **v{self.version.value.strip()}**", ephemeral=True)


class ManageBotPanel(discord.ui.View):
    def __init__(self, cog: AdaLicense, owner_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This panel is not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.edit_message(embed=_status_embed(str(self.owner_id)), view=self)

    @discord.ui.button(label="Use license", style=discord.ButtonStyle.primary, row=0)
    async def use_lic(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(UseLicenseModal())

    @discord.ui.select(
        placeholder="Buy license with balance…",
        options=[
            discord.SelectOption(label=label, value=key)
            for key, label in PLAN_LABELS.items()
        ],
        row=1,
    )
    async def buy(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        plan = select.values[0]
        try:
            lic = get_db().purchase_license(str(self.owner_id), plan)
        except ValueError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        await interaction.followup.send(
            f"✅ **{plan}** · `{lic['license_key']}` · {_days_left(lic['expires_at'])} days",
            ephemeral=True,
        )

    @discord.ui.button(label="Add VDS", style=discord.ButtonStyle.primary, row=2)
    async def add_vds(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        ok, msg = get_db().can_add_vds(str(self.owner_id))
        if not ok:
            return await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
        await interaction.response.send_modal(VdsConnectModal(self.cog, msg))

    @discord.ui.button(label="Remove VDS", style=discord.ButtonStyle.danger, row=2)
    async def remove_vds(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        uid = str(self.owner_id)
        vds = get_db().get_vds(uid)
        if not vds:
            return await interaction.followup.send("❌ No VDS.", ephemeral=True)
        try:
            await asyncio.to_thread(service_control, creds_from_vds_row(vds), "stop")
        except Exception:
            pass
        get_db().delete_vds(uid)
        await interaction.followup.send("✅ VDS removed. Use license again before adding a new one.", ephemeral=True)

    @discord.ui.button(label="Deploy bot", style=discord.ButtonStyle.success, row=3)
    async def deploy(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        uid = str(self.owner_id)
        if not get_db().get_vds(uid):
            return await interaction.response.send_message("❌ Add VDS first.", ephemeral=True)
        if not get_db().user_active_license(uid):
            return await interaction.response.send_message("❌ Activate a license first.", ephemeral=True)
        await interaction.response.send_modal(BotSetupModal(self.cog))

    @discord.ui.button(label="Start", style=discord.ButtonStyle.secondary, row=4)
    async def start(self, interaction: discord.Interaction, _: discord.ui.Button):
        if await self._guard(interaction):
            await self.cog._service_action(interaction, "start")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.secondary, row=4)
    async def stop(self, interaction: discord.Interaction, _: discord.ui.Button):
        if await self._guard(interaction):
            await self.cog._service_action(interaction, "stop")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.secondary, row=4)
    async def restart(self, interaction: discord.Interaction, _: discord.ui.Button):
        if await self._guard(interaction):
            await self.cog._service_action(interaction, "restart")


class VdsManagePanel(discord.ui.View):
    def __init__(self, cog: AdaLicense):
        super().__init__(timeout=600)
        self.cog = cog

    def _owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in _owner_ids():
            return False
        return True

    @discord.ui.button(label="Create license", style=discord.ButtonStyle.primary, row=0)
    async def create_lic(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not self._owner(interaction):
            return await interaction.response.send_message("Owner only.", ephemeral=True)
        await interaction.response.send_modal(CreateLicenseModal())

    @discord.ui.button(label="Grant balance", style=discord.ButtonStyle.success, row=0)
    async def grant_bal(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not self._owner(interaction):
            return await interaction.response.send_message("Owner only.", ephemeral=True)
        await interaction.response.send_modal(GrantBalanceModal())

    @discord.ui.button(label="List licenses", style=discord.ButtonStyle.secondary, row=1)
    async def list_lic(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not self._owner(interaction):
            return await interaction.response.send_message("Owner only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        lines = []
        for lic in get_db().list_licenses(limit=20):
            lines.append(
                f"`{lic['license_key']}` · {lic['status']} · {lic['plan']} · "
                f"<t:{lic['expires_at']}:R>"
            )
        await interaction.followup.send("\n".join(lines) or "No licenses.", ephemeral=True)

    @discord.ui.button(label="List VDS", style=discord.ButtonStyle.secondary, row=1)
    async def list_vds(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not self._owner(interaction):
            return await interaction.response.send_message("Owner only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        lines = [
            f"<@{v['discord_id']}> `{v['host']}` · {v['deploy_status']}"
            for v in get_db().list_all_vds(25)
        ]
        await interaction.followup.send("\n".join(lines) or "No VDS.", ephemeral=True)

    @discord.ui.button(label="Register release", style=discord.ButtonStyle.secondary, row=2)
    async def reg_rel(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not self._owner(interaction):
            return await interaction.response.send_message("Owner only.", ephemeral=True)
        await interaction.response.send_modal(RegisterReleaseModal())

    @discord.ui.button(label="Suspend", style=discord.ButtonStyle.danger, row=3)
    async def suspend_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not self._owner(interaction):
            return await interaction.response.send_message("Owner only.", ephemeral=True)
        await interaction.response.send_modal(LicenseKeyModal("Suspend license", "suspend"))

    @discord.ui.button(label="Revoke", style=discord.ButtonStyle.danger, row=3)
    async def revoke_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not self._owner(interaction):
            return await interaction.response.send_message("Owner only.", ephemeral=True)
        await interaction.response.send_modal(LicenseKeyModal("Revoke license", "revoke"))


class LicenseKeyModal(discord.ui.Modal):
    license_key = discord.ui.TextInput(label="License key", required=True, max_length=32)

    def __init__(self, title: str, action: str):
        super().__init__(title=title)
        self.action = action

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        key = self.license_key.value.strip().upper()
        if self.action == "suspend":
            get_db().set_license_status(key, "suspended")
            await interaction.followup.send(f"⏸️ Suspended `{key}`", ephemeral=True)
        else:
            get_db().set_license_status(key, "revoked")
            await interaction.followup.send(f"🛑 Revoked `{key}`", ephemeral=True)


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

    @app_commands.command(name="vds_manage", description="Owner license & VDS control panel")
    @owner_only()
    async def vds_manage(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="VDS & license management",
            description="Use the buttons below. Run `/build version:x.x.x` for new releases.",
            color=0x9B59B6,
        )
        await interaction.response.send_message(
            embed=embed,
            view=VdsManagePanel(self),
            ephemeral=True,
        )

    @app_commands.command(name="manage_bot", description="Your licensed bot control panel")
    async def manage_bot(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        await interaction.response.send_message(
            embed=_status_embed(uid),
            view=ManageBotPanel(self, interaction.user.id),
            ephemeral=True,
        )

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

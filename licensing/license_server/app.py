"""Flipbot license API — run on your main VDS."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from licensing.license_server.db import LicenseDB

load_dotenv()

APP = FastAPI(title="Flipbot License Server", version="1.0.0")
DB = LicenseDB(os.getenv("LICENSE_DB_PATH", "licensing/license_server/licenses.db"))
ADMIN_KEY = os.getenv("LICENSE_ADMIN_KEY", "")
ADMIN_IPS = {
    x.strip()
    for x in (os.getenv("LICENSE_ADMIN_IPS") or "").split(",")
    if x.strip()
}


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client:
        return request.client.host or ""
    return ""


def _require_admin(
    request: Request,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> None:
    if not ADMIN_KEY:
        raise HTTPException(503, "LICENSE_ADMIN_KEY not configured on server.")
    if not x_admin_key or x_admin_key.strip() != ADMIN_KEY:
        raise HTTPException(401, "Invalid admin key.")
    if ADMIN_IPS:
        ip = _client_ip(request)
        if ip not in ADMIN_IPS:
            raise HTTPException(403, f"Admin access denied for IP {ip or '?'}.")


@APP.get("/health")
def health():
    from licensing.common.server_url import resolve_license_server_url

    return {
        "ok": True,
        "admin_ip_filter": bool(ADMIN_IPS),
        "public_url_hint": resolve_license_server_url() or "set LICENSE_SERVER_IP in .env",
    }


class ActivateBody(BaseModel):
    license_key: str
    ip: str
    machine_id: str


class ValidateBody(BaseModel):
    license_key: str
    ip: str
    machine_id: str


class HeartbeatBody(BaseModel):
    license_key: str
    ip: str
    machine_id: str
    guild_id: str | None = None
    owner_id: str | None = None
    super_admin_id: str | None = None
    bot_version: str | None = None


class CreateLicenseBody(BaseModel):
    plan: str = Field(description="daily | weekly | monthly")
    customer_discord_id: str | None = None
    customer_label: str | None = None
    custom_days: int | None = None
    notes: str | None = None


class ExtendBody(BaseModel):
    extra_days: int


class StatusBody(BaseModel):
    status: str


class ReleaseBody(BaseModel):
    version: str
    platform: str = "linux-x86_64"
    download_url: str
    sha256: str | None = None
    notes: str | None = None


def _lic_payload(lic: dict[str, Any]) -> dict[str, Any]:
    return {
        "license_key": lic["license_key"],
        "plan": lic["plan"],
        "status": lic["status"],
        "expires_at": lic["expires_at"],
        "whitelisted_ip": lic.get("whitelisted_ip"),
        "customer_label": lic.get("customer_label"),
    }


@APP.post("/api/v1/license/activate")
def activate(body: ActivateBody):
    ok, msg, lic = DB.activate_license(
        body.license_key,
        ip=body.ip.strip(),
        machine_id=body.machine_id.strip(),
    )
    if not ok:
        raise HTTPException(403, msg)
    return {"ok": True, "message": msg, "license": _lic_payload(lic)}


@APP.post("/api/v1/license/validate")
def validate(body: ValidateBody):
    ok, msg, lic = DB.validate_license(
        body.license_key,
        ip=body.ip.strip(),
        machine_id=body.machine_id.strip(),
    )
    if not ok:
        raise HTTPException(403, msg)
    return {"ok": True, "message": msg, "license": _lic_payload(lic)}


@APP.post("/api/v1/instance/heartbeat")
def heartbeat(body: HeartbeatBody):
    ok, msg, lic = DB.validate_license(
        body.license_key,
        ip=body.ip.strip(),
        machine_id=body.machine_id.strip(),
    )
    if not ok:
        raise HTTPException(403, msg)
    DB.upsert_instance(
        body.license_key,
        ip=body.ip.strip(),
        machine_id=body.machine_id.strip(),
        guild_id=body.guild_id,
        owner_id=body.owner_id,
        super_admin_id=body.super_admin_id,
        bot_version=body.bot_version,
    )
    return {"ok": True, "license": _lic_payload(lic)}


@APP.get("/api/v1/releases/latest")
def latest_release(platform: str = "linux-x86_64"):
    rel = DB.latest_release(platform)
    if not rel:
        raise HTTPException(404, "No release registered.")
    return {
        "version": rel["version"],
        "platform": rel["platform"],
        "download_url": rel["download_url"],
        "sha256": rel.get("sha256"),
        "notes": rel.get("notes"),
    }


# ── Admin ─────────────────────────────────────────────────────────────────────

@APP.post("/api/v1/admin/licenses", dependencies=[Depends(_require_admin)])
def admin_create_license(body: CreateLicenseBody):
    try:
        lic = DB.create_license(
            body.plan,
            customer_discord_id=body.customer_discord_id,
            customer_label=body.customer_label,
            custom_days=body.custom_days,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "license": lic}


@APP.get("/api/v1/admin/licenses", dependencies=[Depends(_require_admin)])
def admin_list_licenses(status: str | None = None, limit: int = 50):
    return {"licenses": DB.list_licenses(status=status, limit=limit)}


@APP.get("/api/v1/admin/instances", dependencies=[Depends(_require_admin)])
def admin_list_instances(limit: int = 50):
    return {"instances": DB.list_instances(limit=limit)}


@APP.patch("/api/v1/admin/licenses/{license_key}/status", dependencies=[Depends(_require_admin)])
def admin_set_status(license_key: str, body: StatusBody):
    if not DB.set_status(license_key, body.status):
        raise HTTPException(404, "License not found.")
    return {"ok": True, "license": DB.get_license(license_key)}


@APP.post("/api/v1/admin/licenses/{license_key}/extend", dependencies=[Depends(_require_admin)])
def admin_extend(license_key: str, body: ExtendBody):
    lic = DB.extend_license(license_key, body.extra_days)
    if not lic:
        raise HTTPException(404, "License not found.")
    return {"ok": True, "license": lic}


@APP.post("/api/v1/admin/releases", dependencies=[Depends(_require_admin)])
def admin_register_release(body: ReleaseBody):
    rel = DB.register_release(
        body.version,
        body.platform,
        body.download_url,
        sha256=body.sha256,
        notes=body.notes,
    )
    return {"ok": True, "release": rel}

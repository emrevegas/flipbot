"""Deploy compiled Flipbot release to customer VDS via SSH."""

from __future__ import annotations

import json
import os
import shlex
from typing import Any

from licensing.control.license_sign import sign_license_payload
from licensing.control.remote import RemoteCredentials, RemoteSession
from licensing.control.secrets import decrypt_text

UBUNTU_INSTALL_DIR = "/opt/flipbot"
WINDOWS_INSTALL_DIR = r"C:\flipbot"
SERVICE_NAME = "flipbot"


def _build_env_content(env: dict[str, str]) -> str:
    lines = []
    for key, value in env.items():
        safe = (value or "").replace("\n", "\\n")
        lines.append(f"{key}={safe}")
    return "\n".join(lines) + "\n"


def build_license_dat(
    *,
    license_key: str,
    discord_id: str,
    expires_at: int,
    status: str,
    releases_repo: str,
) -> str:
    payload = {
        "license_key": license_key.strip().upper(),
        "discord_id": str(discord_id),
        "expires_at": int(expires_at),
        "status": status,
        "releases_repo": releases_repo,
    }
    signed = sign_license_payload(payload)
    return json.dumps(signed, indent=2)


def _ubuntu_deploy_script(
    *,
    install_dir: str,
    download_url: str,
    sha256: str,
    env_content: str,
    license_json: str,
) -> str:
    env_b64 = __import__("base64").b64encode(env_content.encode()).decode()
    lic_b64 = __import__("base64").b64encode(license_json.encode()).decode()
    return f"""set -e
INSTALL_DIR={shlex.quote(install_dir)}
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"
if command -v wget >/dev/null 2>&1; then
  wget -qO flipbot.tar.gz {shlex.quote(download_url)}
elif command -v curl >/dev/null 2>&1; then
  curl -fsSL -o flipbot.tar.gz {shlex.quote(download_url)}
else
  apt-get update -qq && apt-get install -y -qq wget
  wget -qO flipbot.tar.gz {shlex.quote(download_url)}
fi
echo {shlex.quote(sha256)} flipbot.tar.gz | sha256sum -c -
rm -rf app && mkdir -p app
tar -xzf flipbot.tar.gz -C app
rm -f flipbot.tar.gz
echo {shlex.quote(env_b64)} | base64 -d > "$INSTALL_DIR/app/.env"
echo {shlex.quote(lic_b64)} | base64 -d > "$INSTALL_DIR/app/license.dat"
cd "$INSTALL_DIR/app"
python3 -m venv venv 2>/dev/null || true
if [ -f venv/bin/pip ]; then
  venv/bin/pip install -q -r requirements.txt
  PY="$INSTALL_DIR/app/venv/bin/python"
else
  pip3 install -q -r requirements.txt || python3 -m pip install -q -r requirements.txt
  PY=python3
fi
cat > /etc/systemd/system/{SERVICE_NAME}.service << 'UNIT'
[Unit]
Description=Flipbot Discord Bot
After=network.target

[Service]
Type=simple
WorkingDirectory={install_dir}/app
ExecStart=/bin/bash -c 'cd {install_dir}/app && (test -x venv/bin/python && exec venv/bin/python bot.py || exec python3 bot.py)'
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable {SERVICE_NAME}
systemctl restart {SERVICE_NAME}
sleep 2
systemctl is-active {SERVICE_NAME} || (journalctl -u {SERVICE_NAME} -n 30 --no-pager; exit 1)
"""


def _windows_deploy_script(
    *,
    install_dir: str,
    download_url: str,
    sha256: str,
    env_content: str,
    license_json: str,
) -> str:
    env_esc = env_content.replace("'", "''")
    lic_esc = license_json.replace("'", "''")
    return f"""
$InstallDir = '{install_dir}'
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$archive = Join-Path $InstallDir 'flipbot.tar.gz'
Invoke-WebRequest -Uri '{download_url}' -OutFile $archive -UseBasicParsing
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
if ($hash -ne '{sha256.lower()}') {{ throw 'SHA256 mismatch' }}
$app = Join-Path $InstallDir 'app'
if (Test-Path $app) {{ Remove-Item -Recurse -Force $app }}
New-Item -ItemType Directory -Force -Path $app | Out-Null
tar -xzf $archive -C $app
Remove-Item $archive
@'
{env_esc}
'@ | Set-Content -Path (Join-Path $app '.env') -Encoding UTF8
@'
{lic_esc}
'@ | Set-Content -Path (Join-Path $app 'license.dat') -Encoding UTF8
Set-Location $app
python -m pip install -q -r requirements.txt
$action = New-ScheduledTaskAction -Execute 'python' -Argument 'bot.py' -WorkingDirectory $app
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -TaskName '{SERVICE_NAME}' -Action $action -Trigger $trigger -Force | Out-Null
Start-ScheduledTask -TaskName '{SERVICE_NAME}'
"""


def deploy_to_vds(
    creds: RemoteCredentials,
    *,
    download_url: str,
    sha256: str,
    version: str,
    env: dict[str, str],
    license_key: str,
    discord_id: str,
    expires_at: int,
    releases_repo: str,
) -> tuple[bool, str]:
    releases_repo = releases_repo or os.getenv("RELEASES_GITHUB_REPO", "").strip()
    license_json = build_license_dat(
        license_key=license_key,
        discord_id=discord_id,
        expires_at=expires_at,
        status="active",
        releases_repo=releases_repo,
    )
    env_content = _build_env_content(env)
    os_type = creds.os_type.lower()
    if os_type == "windows":
        return (
            False,
            "Compiled bot releases are Linux-only. Use an Ubuntu VDS (or WSL2 Ubuntu) for deployment.",
        )
    install_dir = UBUNTU_INSTALL_DIR

    if os_type == "windows":
        script = _windows_deploy_script(
            install_dir=install_dir,
            download_url=download_url,
            sha256=sha256,
            env_content=env_content,
            license_json=license_json,
        )
        remote_cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -Command "{script.replace(chr(10), "; ")}"'
    else:
        script = _ubuntu_deploy_script(
            install_dir=install_dir,
            download_url=download_url,
            sha256=sha256,
            env_content=env_content,
            license_json=license_json,
        )
        remote_cmd = script

    remote_cmd = f"bash <<'FLIPBOT_EOF'\n{script}\nFLIPBOT_EOF"

    try:
        with RemoteSession(creds, timeout=30) as sess:
            code, out, err = sess.run(remote_cmd, timeout=600)
        log = (out + "\n" + err).strip()[-1500:]
        if code != 0:
            return False, log or f"Deploy failed (exit {code})"
        return True, log or "Deploy completed"
    except Exception as exc:
        return False, str(exc)[:1500]


def creds_from_vds_row(row: dict[str, Any]) -> RemoteCredentials:
    from licensing.control.secrets import decrypt_text

    return RemoteCredentials(
        host=row["host"],
        port=int(row["port"]),
        username=row["username"],
        password=decrypt_text(row["password_enc"]),
        os_type=row["os_type"],
    )


def service_control(creds: RemoteCredentials, action: str) -> tuple[bool, str]:
    action = action.lower()
    if creds.os_type.lower() == "windows":
        cmd_map = {
            "start": f"Start-ScheduledTask -TaskName '{SERVICE_NAME}'",
            "stop": f"Stop-ScheduledTask -TaskName '{SERVICE_NAME}'",
            "restart": f"Stop-ScheduledTask -TaskName '{SERVICE_NAME}'; Start-Sleep 2; Start-ScheduledTask -TaskName '{SERVICE_NAME}'",
        }
        cmd = f'powershell -Command "{cmd_map.get(action, "")}"'
    else:
        cmd_map = {
            "start": f"systemctl start {SERVICE_NAME}",
            "stop": f"systemctl stop {SERVICE_NAME}",
            "restart": f"systemctl restart {SERVICE_NAME}",
        }
        cmd = cmd_map.get(action, "")
    if not cmd:
        return False, "Unknown action"
    try:
        with RemoteSession(creds, timeout=20) as sess:
            code, out, err = sess.run(cmd, timeout=60)
        msg = (out + err).strip()[:500]
        return code == 0, msg or ("OK" if code == 0 else f"exit {code}")
    except Exception as exc:
        return False, str(exc)[:500]


def push_license_file(creds: RemoteCredentials, license_json: str, install_dir: str | None = None) -> tuple[bool, str]:
    os_type = creds.os_type.lower()
    base = install_dir or (WINDOWS_INSTALL_DIR if os_type == "windows" else UBUNTU_INSTALL_DIR)
    remote = f"{base}/app/license.dat".replace("\\", "/") if os_type != "windows" else f"{base}\\app\\license.dat"
    try:
        with RemoteSession(creds, timeout=20) as sess:
            sess.write_file(remote, license_json, unix=(os_type != "windows"))
        return True, "license.dat updated"
    except Exception as exc:
        return False, str(exc)[:500]


def bot_service_status(creds: RemoteCredentials) -> str:
    try:
        with RemoteSession(creds, timeout=15) as sess:
            if creds.os_type.lower() == "windows":
                code, out, err = sess.run(
                    f'powershell -Command "(Get-ScheduledTask -TaskName \'{SERVICE_NAME}\').State"',
                    timeout=30,
                )
            else:
                code, out, err = sess.run(f"systemctl is-active {SERVICE_NAME}", timeout=30)
            text = (out or err).strip()
            if code == 0 and ("active" in text.lower() or "running" in text.lower()):
                return "running"
            if "inactive" in text.lower() or "stopped" in text.lower():
                return "stopped"
            return text[:80] or "unknown"
    except Exception as exc:
        return f"error: {exc}"[:80]

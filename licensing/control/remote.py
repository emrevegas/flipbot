"""SSH remote execution for Ubuntu and Windows (OpenSSH) VDS."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Callable

import paramiko


@dataclass
class RemoteCredentials:
    host: str
    port: int
    username: str
    password: str
    os_type: str  # ubuntu | windows


class RemoteSession:
    def __init__(self, creds: RemoteCredentials, *, timeout: int = 25):
        self.creds = creds
        self.timeout = timeout
        self._client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            self.creds.host,
            port=self.creds.port,
            username=self.creds.username,
            password=self.creds.password,
            timeout=self.timeout,
            banner_timeout=self.timeout,
            auth_timeout=self.timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        self._client = client

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> RemoteSession:
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def run(self, command: str, *, timeout: int = 120) -> tuple[int, str, str]:
        if not self._client:
            raise RuntimeError("Not connected")
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        return code, out, err

    def write_file(self, remote_path: str, content: str, *, unix: bool | None = None) -> None:
        if not self._client:
            raise RuntimeError("Not connected")
        is_windows = (unix is False) or (
            unix is None and self.creds.os_type.lower() == "windows"
        )
        sftp = self._client.open_sftp()
        try:
            with sftp.file(remote_path, "w") as f:
                f.write(content.encode("utf-8"))
        finally:
            sftp.close()


def test_tcp(host: str, port: int, timeout: float = 8.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def test_ssh_connection(creds: RemoteCredentials) -> tuple[bool, str]:
    if not test_tcp(creds.host, creds.port):
        return False, f"Cannot reach {creds.host}:{creds.port}"
    try:
        with RemoteSession(creds, timeout=15) as sess:
            if creds.os_type.lower() == "windows":
                code, out, err = sess.run("cmd /c echo FLIPBOT_OK", timeout=20)
            else:
                code, out, err = sess.run("echo FLIPBOT_OK", timeout=20)
        if code == 0 and "FLIPBOT_OK" in (out + err):
            return True, "Connection successful"
        return False, (err or out or f"exit {code}").strip()[:500]
    except Exception as exc:
        return False, str(exc)[:500]


def parse_vds_string(raw: str) -> RemoteCredentials:
    """
    Format: host:port:username:password
    Password may contain ':' — only first three splits are fixed.
    """
    parts = raw.strip().split(":")
    if len(parts) < 4:
        raise ValueError("Use format host:port:username:password")
    host = parts[0].strip()
    port = int(parts[1].strip())
    username = parts[2].strip()
    password = ":".join(parts[3:]).strip()
    if not host or not username or not password:
        raise ValueError("Host, username and password are required")
    return RemoteCredentials(host=host, port=port, username=username, password=password, os_type="ubuntu")

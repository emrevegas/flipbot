"""Trigger GitHub Actions build workflow from Ada bot."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def _api(method: str, url: str, token: str, data: dict | None = None) -> dict[str, Any]:
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode().strip()
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace").strip()
        try:
            detail = json.loads(body).get("message", body) if body else exc.reason
        except json.JSONDecodeError:
            detail = body or exc.reason
        raise RuntimeError(f"GitHub API {exc.code}: {detail}") from exc


def trigger_build_workflow(version: str) -> str:
    token = (os.getenv("GITHUB_TOKEN") or "").strip()
    repo = (os.getenv("GITHUB_SOURCE_REPO") or os.getenv("GITHUB_REPO") or "").strip()
    workflow_file = (os.getenv("GITHUB_BUILD_WORKFLOW") or "build-release.yml").strip()
    ref = (os.getenv("GITHUB_BUILD_REF") or "master").strip()
    if not token or not repo:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_SOURCE_REPO required in ada.env")

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    _api(
        "POST",
        url,
        token,
        {
            "ref": ref,
            "inputs": {"version": version.strip()},
        },
    )
    return f"Workflow `{workflow_file}` started on `{repo}` @ {ref} (v{version})"

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any


@dataclass(frozen=True)
class LiveResponse:
    status: int
    body: str
    headers: dict[str, str]

    def json(self) -> dict[str, Any]:
        return json.loads(self.body or "{}")


def live_base_url() -> str:
    return os.environ.get("CHAT_CLIENT_BMETHOD_BASE_URL", "https://chatclient0.cloud-dog.net").rstrip("/")


def credential(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    assert value, f"{name} must not be empty"
    return value


def opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()),
        urllib.request.HTTPSHandler(context=context),
    )


def request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    client: urllib.request.OpenerDirector | None = None,
    timeout: float = 20.0,
) -> LiveResponse:
    data = None
    merged_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/json")
    url = urllib.parse.urljoin(live_base_url() + "/", path.lstrip("/"))
    req = urllib.request.Request(url, data=data, headers=merged_headers, method=method.upper())
    active = client or opener()
    try:
        with active.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return LiveResponse(resp.status, raw, dict(resp.headers.items()))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return LiveResponse(exc.code, raw, dict(exc.headers.items()))


def login(role: str) -> urllib.request.OpenerDirector:
    users = {
        "admin": (
            credential("CHAT_CLIENT_ADMIN_USERNAME", "admin"),
            credential("CHAT_CLIENT_ADMIN_PASSWORD", "OrangeRiverTable"),
        ),
        "read-write": (
            credential("CHAT_CLIENT_RW_USERNAME", "read-write"),
            credential("CHAT_CLIENT_RW_PASSWORD", "BlueRiverChair"),
        ),
        "read-only": (
            credential("CHAT_CLIENT_RO_USERNAME", "read-only"),
            credential("CHAT_CLIENT_RO_PASSWORD", "GreenRiverDesk"),
        ),
    }
    username, password = users[role]
    client = opener()
    resp = request("POST", "/auth/login", body={"username": username, "password": password}, client=client)
    assert resp.status == 200, f"login {role} failed: {resp.status} {resp.body}"
    roles = resp.json()["user"]["roles"]
    assert role in roles, f"login {role} returned roles {roles}"
    return client


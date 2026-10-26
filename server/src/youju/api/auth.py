"""Dedicated zdog-auth machine client, following the local Xiaoman contract.

Identity and permissions come from online zdog-auth userinfo validation.
Local subject lists, role labels and client claims never grant access.
"""

import json
import time
from functools import wraps
from threading import Lock
from urllib.parse import urlsplit

import httpx
from flask import Blueprint, current_app, g, request

from .common import ApiError, body, text

SOURCE_MANAGE_PERMISSION = "youju:source:manage"
PERMISSIONS = [
    {"name": SOURCE_MANAGE_PERMISSION, "description": "Manage Youju sources and onboarding"},
]
ROLES = [
    {
        "name": "youju-admin",
        "description": "Youju source administrator",
        "permissions": [SOURCE_MANAGE_PERMISSION],
    },
]


def application_permissions(userinfo):
    """Project validated central grants; malformed or absent permissions deny access."""
    raw = userinfo.get("permissions")
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw
    ):
        return []
    # Do not infer grants from role names, wildcards or another application's permissions.
    return [SOURCE_MANAGE_PERMISSION] if SOURCE_MANAGE_PERMISSION in raw else []


class AuthClient:
    def __init__(self, config, *, transport=None):
        self.issuer = config["AUTH_ISSUER"].rstrip("/")
        self.client_id = config["AUTH_CLIENT_ID"]
        self.client_secret = config["AUTH_CLIENT_SECRET"]
        self.transport = transport
        try:
            issuer = urlsplit(self.issuer)
            self.configured = bool(
                self.client_id
                and self.client_secret
                and issuer.hostname
                and (
                    issuer.scheme == "https"
                    or issuer.scheme == "http"
                    and issuer.hostname in {"127.0.0.1", "localhost", "::1"}
                )
                and not (issuer.username or issuer.password or issuer.query or issuer.fragment)
                and issuer.port != 0
            )
        except ValueError:
            self.configured = False

    def post(self, path, payload):
        if not self.configured:
            raise ApiError(503, "AUTH_NOT_CONFIGURED", "登录服务尚未配置。")
        try:
            with httpx.Client(
                timeout=httpx.Timeout(8, connect=3),
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                with client.stream(
                    "POST",
                    self.issuer + path,
                    json={
                        **payload,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                ) as response:
                    if response.status_code in {400, 401, 403, 409, 429}:
                        raise ApiError(
                            response.status_code, "AUTH_REJECTED", "登录无效或操作过于频繁。"
                        )
                    if not 200 <= response.status_code < 300:
                        raise ApiError(503, "AUTH_UNAVAILABLE", "登录服务暂不可用。")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 64 * 1024:
                            raise ApiError(502, "AUTH_BAD_RESPONSE", "登录服务返回异常。")
                    data = json.loads(raw)
        except httpx.HTTPError:
            raise ApiError(503, "AUTH_UNAVAILABLE", "登录服务暂不可用。") from None
        except ValueError:
            raise ApiError(502, "AUTH_BAD_RESPONSE", "登录服务返回异常。") from None
        if not isinstance(data, dict) or data.get("error"):
            raise ApiError(502, "AUTH_BAD_RESPONSE", "登录服务返回异常。")
        return data

    def provision(self):
        """Declare central roles/permissions only; never assign a role to a user."""
        return self.post("/machine/provision", {"permissions": PERMISSIONS, "roles": ROLES})

    def userinfo(self, token):
        data = self.post("/machine/userinfo", {"access_token": token})
        subject = data.get("sub")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 256:
            raise ApiError(401, "UNAUTHORIZED", "登录已过期，请重新登录。")
        return data


class AttemptLimiter:
    """Bounded single-process login limiter; multi-worker deployments need edge limits."""

    def __init__(self):
        self.buckets = {}
        self.lock = Lock()

    def allow(self, address):
        now = time.monotonic()
        with self.lock:
            self.buckets = {k: v for k, v in self.buckets.items() if now - v[0] < 60}
            if address not in self.buckets and len(self.buckets) >= 2048:
                return False
            start, count = self.buckets.get(address, (now, 0))
            if count >= 20:
                return False
            self.buckets[address] = start, count + 1
            return True


def auth_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        parts = request.headers.get("Authorization", "").split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 8192:
            raise ApiError(401, "UNAUTHORIZED", "请先登录。")
        # Request-local only: the next request revalidates central grants and revocations.
        if not hasattr(g, "userinfo"):
            g.userinfo = current_app.extensions["auth"].userinfo(parts[1])
        g.subject = g.userinfo["sub"]
        g.permissions = application_permissions(g.userinfo)
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @auth_required
    @wraps(view)
    def wrapped(*args, **kwargs):
        if SOURCE_MANAGE_PERMISSION not in g.permissions:
            raise ApiError(403, "FORBIDDEN", "需要管理员权限。")
        return view(*args, **kwargs)

    return wrapped


auth_api = Blueprint("auth", __name__)


@auth_api.before_request
def limit_login():
    if request.method == "POST" and not current_app.extensions["auth_limiter"].allow(
        request.remote_addr or "unknown"
    ):
        raise ApiError(429, "RATE_LIMITED", "操作过于频繁，请稍后重试。")


def token_response(data):
    token = data.get("access_token")
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 8192
        or any(c.isspace() for c in token)
    ):
        raise ApiError(502, "AUTH_BAD_RESPONSE", "登录服务未返回有效令牌。")
    return {"access_token": token}


@auth_api.post("/login")
def login():
    data = body(("username", "password"))
    return token_response(
        current_app.extensions["auth"].post(
            "/machine/password/login",
            {
                "username": text(data, "username", 128),
                "password": text(data, "password", 256, trim=False),
            },
        )
    )


@auth_api.post("/wechat")
def wechat():
    data = body(("code",))
    return token_response(
        current_app.extensions["auth"].post(
            "/machine/wechat/miniprogram", {"code": text(data, "code", 512)}
        )
    )


@auth_api.get("/me")
@auth_required
def me():
    result = {"id": g.subject, "permissions": g.permissions}
    # Project only optional display fields, never the complete upstream profile.
    # The issuer also advertises standard OIDC profile claims.
    for key, aliases in (
        ("username", ("username", "preferred_username")),
        ("nickname", ("nickname", "name")),
    ):
        for field in aliases:
            name = g.userinfo.get(field)
            if isinstance(name, str) and 0 < len(name.strip()) <= 128:
                if not any(ord(char) < 32 or ord(char) == 127 for char in name):
                    result[key] = name.strip()
                    break
    # Normalize common verified profile image fields into one client contract.
    for key in ("avatar", "avatar_url", "picture"):
        avatar = g.userinfo.get(key)
        if not isinstance(avatar, str) or not 0 < len(avatar.strip()) <= 2048:
            continue
        avatar = avatar.strip()
        try:
            parsed = urlsplit(avatar)
            if (
                parsed.scheme == "https"
                and parsed.hostname
                and not (parsed.username or parsed.password)
                and parsed.port != 0
                and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in avatar)
                and "\\" not in avatar
            ):
                result["avatar"] = avatar
                break
        except ValueError:
            pass  # Invalid optional display data must not invalidate the session.
    return result


@auth_api.get("/status")
def status():
    return {"provider": "zdog-auth", "configured": current_app.extensions["auth"].configured}

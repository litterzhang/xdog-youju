"""Authenticated loopback Recipe RPC transport; no source state or Agent execution."""

import json
import re

import httpx


class RecipeRPCError(RuntimeError):
    def __init__(self, code, *, transient=False):
        self.code, self.transient = code, transient
        super().__init__(code)


class RecipeRPCClient:
    def __init__(self, url, token, *, caller="source-server", transport=None):
        parsed = httpx.URL(url)
        if parsed.scheme != "http" or parsed.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("Recipe RPC must use the configured loopback listener")
        if not token or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
            raise ValueError("Invalid Recipe RPC token")
        self.url, self.token, self.caller, self.transport = (
            url.rstrip("/"),
            token,
            caller,
            transport,
        )

    async def call(self, method, **args):
        try:
            async with httpx.AsyncClient(
                transport=self.transport, trust_env=False, follow_redirects=False, timeout=15
            ) as client:
                async with client.stream(
                    "POST",
                    self.url + "/internal/recipe/" + method,
                    headers={
                        "Authorization": "Bearer " + self.token,
                        "X-Recipe-Caller": self.caller,
                    },
                    json=args,
                ) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 8 * 1024 * 1024:
                            raise RecipeRPCError("RECIPE_RPC_RESPONSE_LIMIT")
                    if response.status_code >= 500:
                        raise RecipeRPCError("RECIPE_RPC_UNAVAILABLE", transient=True)
                    data = json.loads(raw)
                    if response.status_code != 200 or not isinstance(data.get("result"), dict):
                        code = data.get("error", {}).get("code", "RECIPE_RPC_REJECTED")
                        # Provider/remote free-form errors never enter logs or source status.
                        safe = (
                            code
                            if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code)
                            else "RECIPE_RPC_REJECTED"
                        )
                        raise RecipeRPCError(safe, transient=safe == "SERVICE_BUSY")
                    return data["result"]
        except httpx.HTTPError:
            raise RecipeRPCError("RECIPE_RPC_UNAVAILABLE", transient=True) from None
        except (ValueError, AttributeError):
            raise RecipeRPCError("RECIPE_RPC_BAD_RESPONSE") from None

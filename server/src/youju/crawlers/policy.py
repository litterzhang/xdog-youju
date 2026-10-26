"""Origin checks and public-address resolution shared by acquisition adapters."""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass

import httpx

from youju.models.base import normalize_url

from .contracts import CrawlError, CrawlErrorCode


def origin(url: httpx.URL) -> str:
    return f"{url.scheme}://{url.netloc.decode('ascii')}"


@dataclass(frozen=True)
class OriginPolicy:
    """A trusted server-side allowlist, never taken from a public crawl request."""

    allowed_origins: frozenset[str]

    def __post_init__(self):
        if isinstance(self.allowed_origins, str) or not self.allowed_origins:
            raise ValueError("Provide a nonempty collection of explicitly approved origins")
        normalized = set()
        for value in self.allowed_origins:
            url = httpx.URL(normalize_url(value))
            if url.path != "/" or url.query or "#" in value:
                raise ValueError("An allowed origin must not contain a path, query or fragment")
            normalized.add(origin(url))
        object.__setattr__(self, "allowed_origins", frozenset(normalized))

    def check_url(self, value: str) -> httpx.URL:
        try:
            url = httpx.URL(normalize_url(value))
        except (ValueError, UnicodeError, httpx.InvalidURL) as error:
            raise CrawlError(CrawlErrorCode.POLICY_BLOCKED, "Invalid crawl URL") from error
        if origin(url) not in self.allowed_origins:
            raise CrawlError(CrawlErrorCode.POLICY_BLOCKED, "URL is outside the approved origins")
        return url

    async def resolve_public_ip(self, url: httpx.URL) -> str:
        """Validate every DNS answer, then return one address for a pinned connection."""
        addresses = await asyncio.get_running_loop().getaddrinfo(
            url.host,
            url.port or (443 if url.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        ips = []
        for address in addresses:
            value = address[4][0]
            ip = ipaddress.ip_address(value)
            mapped = getattr(ip, "ipv4_mapped", None)
            if ip.version == 6 and ip in ipaddress.ip_network("64:ff9b::/96"):
                mapped = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
            if (
                "%" in value
                or not ip.is_global
                or ip.is_multicast
                or (mapped is not None and (not mapped.is_global or mapped.is_multicast))
            ):
                raise CrawlError(
                    CrawlErrorCode.POLICY_BLOCKED, "Target resolves to a non-public IP"
                )
            if ip not in ips:
                ips.append(ip)
        if not ips:
            raise CrawlError(CrawlErrorCode.NETWORK, "Target has no usable address")
        # Prefer IPv4, but never fall back to a new, unchecked DNS resolution.
        return str(sorted(ips, key=lambda ip: ip.version)[0])

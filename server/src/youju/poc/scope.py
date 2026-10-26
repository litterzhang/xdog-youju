"""Organization-domain boundaries using the bundled PSL, without network updates."""

from dataclasses import dataclass
from urllib.parse import urlsplit

import tldextract

from youju.models.base import normalize_url

_EXTRACT = tldextract.TLDExtract(
    cache_dir=None, suffix_list_urls=(), include_psl_private_domains=True
)


@dataclass(frozen=True)
class DiscoveryScope:
    root_domain: str

    def __post_init__(self):
        root = self.root_domain.lower().rstrip(".").encode("idna").decode("ascii")
        parsed = _EXTRACT(root)
        if not parsed.suffix or parsed.top_domain_under_public_suffix != root:
            raise ValueError(
                "root_domain must be an organization's registrable domain, not a public suffix"
            )
        object.__setattr__(self, "root_domain", root)

    def contains(self, url: str) -> bool:
        try:
            host = urlsplit(normalize_url(url)).hostname
        except (ValueError, UnicodeError):
            return False
        return host == self.root_domain or host.endswith("." + self.root_domain)

"""Machine-readable codes; names, descriptions and original material remain Chinese."""

from enum import StrEnum


class OrganizationType(StrEnum):
    UNIVERSITY = "university"
    SCHOOL = "school"
    GOVERNMENT = "government"
    HOSPITAL = "hospital"
    RESEARCH_INSTITUTE = "research_institute"
    OTHER = "other"
    UNKNOWN = "unknown"


class RegistryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class SeedKind(StrEnum):
    LISTING = "listing"
    SITEMAP = "sitemap"
    FEED = "feed"
    API = "api"


class ResourceKind(StrEnum):
    UNKNOWN = "unknown"
    PAGE = "page"
    ATTACHMENT = "attachment"
    API = "api"
    FEED = "feed"
    SITEMAP = "sitemap"


class DiscoveryMethod(StrEnum):
    LINK = "link"
    SITEMAP = "sitemap"
    FEED = "feed"
    API = "api"


class FetchOutcome(StrEnum):
    DOWNLOADED = "downloaded"
    NOT_MODIFIED = "not_modified"
    FAILED = "failed"

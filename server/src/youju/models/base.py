"""Shared SQLAlchemy types; validation here never authorizes network access."""

from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import DateTime, MetaData, String, Text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Reject naive input; persist UTC and restore timezone information on SQLite."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("A timezone-aware datetime is required")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


def validate_http_url(value: str) -> str:
    """Syntax only: DNS, IP, redirect and source-scope checks belong to a future fetcher."""
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("An absolute HTTP(S) URL without whitespace is required")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("An absolute HTTP(S) URL is required")
    if parts.username is not None or parts.password is not None or "\\" in value:
        raise ValueError("URL credentials and backslashes are not allowed")
    if parts.port is not None and not 1 <= parts.port <= 65535:
        raise ValueError("Invalid URL port")
    return value


def normalize_url(value: str) -> str:
    """Keep query values/order and path case; remove fragments and normalize the authority."""
    parts = urlsplit(validate_http_url(value))
    host = parts.hostname.lower().encode("idna").decode("ascii")
    if ":" in host:
        host = f"[{host}]"
    if parts.port is not None and (parts.scheme, parts.port) not in {("http", 80), ("https", 443)}:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))


class HTTPURL(TypeDecorator[str]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return validate_http_url(value) if value is not None else None


def enum_column(enum: type[StrEnum], name: str) -> SqlEnum:
    return SqlEnum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Identified:
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)

"""Cost-routing contracts, deliberately incapable of carrying position dates."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal


class RoutingCategory(StrEnum):
    FIXED_ACTIVE = "fixed_active"
    FIXED_EXPIRED = "fixed_expired"
    UNDATED_LOW_PRIORITY = "undated_low_priority"
    UNDATED_HIGH_PRIORITY = "undated_high_priority"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    old_age_days: int = 365

    def __post_init__(self):
        if type(self.old_age_days) is not int or self.old_age_days < 1:
            raise ValueError("old_age_days must be a positive integer")

    @property
    def version(self) -> str:
        """Algorithm revision plus every configurable decision input, without a clock."""
        return f"notice-routing-v2:old-age-days={self.old_age_days}"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    category: RoutingCategory
    mode: Literal["quick", "detailed"]
    reason: str
    policy_version: str
    evaluated_at: datetime

    def __post_init__(self):
        if not isinstance(self.category, RoutingCategory):
            raise ValueError("category must be a RoutingCategory")
        if self.mode not in {"quick", "detailed"}:
            raise ValueError("mode must be quick or detailed")
        if not self.reason or not self.policy_version:
            raise ValueError("reason and policy_version must be nonempty")
        if not isinstance(self.evaluated_at, datetime) or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be an aware datetime")

    @classmethod
    def from_dict(cls, value):
        """The JSON boundary has exactly cost metadata, never candidate job facts."""
        keys = {"category", "mode", "reason", "policy_version", "evaluated_at"}
        if (
            not isinstance(value, dict)
            or set(value) != keys
            or any(not isinstance(item, str) for item in value.values())
        ):
            raise ValueError("Routing supplies cost policy, never job dates or extra fields")
        decision = cls(
            RoutingCategory(value["category"]),
            value["mode"],
            value["reason"],
            value["policy_version"],
            datetime.fromisoformat(value["evaluated_at"]),
        )
        quick = decision.category in {
            RoutingCategory.FIXED_EXPIRED,
            RoutingCategory.UNDATED_LOW_PRIORITY,
        }
        if decision.mode != ("quick" if quick else "detailed"):
            raise ValueError("Routing category and execution mode disagree")
        return decision

    def as_dict(self) -> dict[str, str]:
        return {
            "category": self.category.value,
            "mode": self.mode,
            "reason": self.reason,
            "policy_version": self.policy_version,
            "evaluated_at": self.evaluated_at.isoformat(),
        }

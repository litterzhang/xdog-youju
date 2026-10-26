"""User-authored parsing reports, never automatic recipe edits or training labels."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Identified, UTCDateTime, utc_now


class ParsingFeedback(Identified, Base):
    __tablename__ = "parsing_feedback"
    __table_args__ = (
        UniqueConstraint("reporter_sub", "payload_digest"),
        CheckConstraint(
            "status IN ('open','reviewed','resolved','dismissed')", name="feedback_status"
        ),
    )
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    reporter_sub: Mapped[str] = mapped_column(String(256), index=True)
    payload_digest: Mapped[str] = mapped_column(String(64))
    field: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    expected_text: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    reviewer_sub: Mapped[str | None] = mapped_column(String(256))
    review_history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

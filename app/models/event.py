"""
app/models/event.py
~~~~~~~~~~~~~~~~~~~

Event model — a calendar event with a start time and optional end time,
location, and notes.

All datetimes are stored as UTC.  Timezone conversion for display is
handled in the service/template layer.

All-day detection
-----------------
An event is considered all-day when ``start_at`` falls exactly at
midnight (00:00:00) and ``end_at`` is either absent or also at midnight.
This is a heuristic; a dedicated boolean column can be added if needed.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import relationship

from app.extensions import db


class Event(db.Model):
    """A calendar event belonging to a user."""

    __tablename__ = 'events'

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    title    = db.Column(db.String(255), nullable=False)
    start_at = db.Column(db.DateTime, nullable=False, index=True)
    end_at   = db.Column(db.DateTime, nullable=True)
    location = db.Column(db.String(255), nullable=True)
    notes    = db.Column(db.Text, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    user = relationship('User', back_populates='events')

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def is_today(self) -> bool:
        """``True`` if the event starts on today's UTC calendar date."""
        return self.start_at.date() == date.today()

    @property
    def is_upcoming(self) -> bool:
        """
        ``True`` if the event has not yet ended (or not yet started when
        ``end_at`` is not set).

        An in-progress event (started but not ended) is *not* considered
        upcoming.
        """
        now = datetime.utcnow()
        if self.end_at is not None:
            return self.end_at > now
        return self.start_at > now

    @property
    def is_in_progress(self) -> bool:
        """
        ``True`` if the current UTC time falls between ``start_at`` and
        ``end_at``.  Always ``False`` when ``end_at`` is not set.
        """
        if self.end_at is None:
            return False
        now = datetime.utcnow()
        return self.start_at <= now <= self.end_at

    @property
    def is_past(self) -> bool:
        """
        ``True`` if the event has ended.

        When ``end_at`` is not set, the event is considered past as soon
        as ``start_at`` has passed.
        """
        now = datetime.utcnow()
        if self.end_at is not None:
            return self.end_at < now
        return self.start_at < now

    @property
    def duration_minutes(self) -> Optional[int]:
        """
        Return the event duration in whole minutes, or ``None`` when
        ``end_at`` is not set.

        Negative durations (end before start) are clamped to zero.
        """
        if self.end_at is None:
            return None
        delta = self.end_at - self.start_at
        total_seconds = int(delta.total_seconds())
        return max(0, total_seconds // 60)

    @property
    def is_all_day(self) -> bool:
        """
        Heuristic all-day check: returns ``True`` when ``start_at`` is at
        midnight and ``end_at`` is either absent or also at midnight.
        """
        def _is_midnight(dt: datetime) -> bool:
            return dt.hour == 0 and dt.minute == 0 and dt.second == 0

        if not _is_midnight(self.start_at):
            return False
        if self.end_at is not None and not _is_midnight(self.end_at):
            return False
        return True

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f'<Event id={self.id} title={self.title!r} '
            f'start_at={self.start_at!r}>'
        )

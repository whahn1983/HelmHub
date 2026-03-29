"""
app/models/reminder.py
~~~~~~~~~~~~~~~~~~~~~~

Reminder model — a time-based alert that surfaces when ``remind_at`` is
reached.

Status lifecycle
----------------
  pending  →  (due)    →  shown to user
                        ↓                ↓
                    snoozed          completed / dismissed
                        ↓
                  (snoozed_until)  →  shown again

Status values
-------------
``pending``    Default; the reminder has not yet been acknowledged.
``snoozed``    The user deferred the reminder; ``snoozed_until`` holds the
               next fire time.
``completed``  The user marked the reminder as done.
``dismissed``  The user dismissed the reminder without marking it complete.
"""

from datetime import datetime

from sqlalchemy.orm import relationship

from app.extensions import db


class Reminder(db.Model):
    """A time-based reminder for the user."""

    __tablename__ = 'reminders'

    # ------------------------------------------------------------------
    # Class-level constants
    # ------------------------------------------------------------------

    STATUS_PENDING   = 'pending'
    STATUS_COMPLETED = 'completed'
    STATUS_DISMISSED = 'dismissed'
    STATUS_SNOOZED   = 'snoozed'
    STATUSES = [
        STATUS_PENDING,
        STATUS_COMPLETED,
        STATUS_DISMISSED,
        STATUS_SNOOZED,
    ]

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

    title = db.Column(db.String(255), nullable=False)

    # Optional extra context shown alongside the reminder alert
    notes = db.Column(db.Text, nullable=True)

    # The UTC date/time at which this reminder first fires
    remind_at = db.Column(db.DateTime, nullable=False, index=True)

    status = db.Column(
        db.String(16),
        nullable=False,
        default=STATUS_PENDING,
        index=True,
    )

    # Populated when the user snoozes; the next fire time
    snoozed_until = db.Column(db.DateTime, nullable=True)

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

    user = relationship('User', back_populates='reminders')

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def is_due(self) -> bool:
        """
        Return ``True`` when this reminder should be surfaced to the user.

        Rules:

        * **pending** — fires when ``remind_at <= utcnow()``.
        * **snoozed** — fires again when ``snoozed_until <= utcnow()``.
        * **completed / dismissed** — never fires again.
        """
        now = datetime.utcnow()
        if self.status == self.STATUS_SNOOZED:
            return self.snoozed_until is not None and self.snoozed_until <= now
        return self.status == self.STATUS_PENDING and self.remind_at <= now

    @property
    def is_active(self) -> bool:
        """
        ``True`` if the reminder has not been completed or dismissed and
        may still fire (pending or snoozed).
        """
        return self.status in (self.STATUS_PENDING, self.STATUS_SNOOZED)

    @property
    def is_overdue(self) -> bool:
        """
        ``True`` if the reminder was due in the past and is still pending
        (i.e. it has been missed without being snoozed, completed, or
        dismissed).
        """
        return self.status == self.STATUS_PENDING and self.remind_at < datetime.utcnow()

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f'<Reminder id={self.id} title={self.title!r} '
            f'remind_at={self.remind_at!r} status={self.status!r}>'
        )

"""
app/models/task.py
~~~~~~~~~~~~~~~~~~

Task model — a to-do item with optional due date, priority, status, and
recurrence rule.

Priority values : 'low' | 'medium' (default) | 'high'
Status values   : 'open' (default) | 'completed'

Recurrence
----------
``recurrence_rule`` stores an iCalendar RRULE string (e.g.
``'FREQ=DAILY;INTERVAL=1'``).  Interpretation of the rule is left to the
service layer; the model only persists the string.
"""

from datetime import date, datetime

from sqlalchemy.orm import relationship

from app.extensions import db


class Task(db.Model):
    """A to-do / task item belonging to a user."""

    __tablename__ = 'tasks'

    # ------------------------------------------------------------------
    # Class-level constants
    # ------------------------------------------------------------------

    PRIORITY_LOW    = 'low'
    PRIORITY_MEDIUM = 'medium'
    PRIORITY_HIGH   = 'high'
    PRIORITIES      = [PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH]

    STATUS_OPEN      = 'open'
    STATUS_COMPLETED = 'completed'
    STATUSES         = [STATUS_OPEN, STATUS_COMPLETED]

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

    title       = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text,        nullable=True)

    # Optional due date/time (stored as UTC)
    due_at = db.Column(db.DateTime, nullable=True, index=True)

    priority = db.Column(
        db.String(16),
        nullable=False,
        default=PRIORITY_MEDIUM,
    )
    status = db.Column(
        db.String(16),
        nullable=False,
        default=STATUS_OPEN,
        index=True,
    )

    # Pin this task to today's focus list independently of its due date
    pinned_to_today = db.Column(db.Boolean, nullable=False, default=False)

    # iCal RRULE string, e.g. 'FREQ=WEEKLY;BYDAY=MO'
    recurrence_rule = db.Column(db.String(255), nullable=True)

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

    user = relationship('User', back_populates='tasks')

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def is_overdue(self) -> bool:
        """
        ``True`` if the task has a due date that has passed and has not
        been completed.
        """
        return (
            self.due_at is not None
            and self.due_at < datetime.utcnow()
            and self.status != self.STATUS_COMPLETED
        )

    @property
    def is_due_today(self) -> bool:
        """``True`` if the task is due on today's date (UTC calendar date)."""
        if self.due_at is None:
            return False
        return self.due_at.date() == date.today()

    @property
    def is_completed(self) -> bool:
        """Convenience alias for ``status == 'completed'``."""
        return self.status == self.STATUS_COMPLETED

    @property
    def is_high_priority(self) -> bool:
        """``True`` when priority is 'high'."""
        return self.priority == self.PRIORITY_HIGH

    @property
    def priority_sort_key(self) -> int:
        """
        Numeric sort key so tasks can be ordered high → medium → low.

        Returns
        -------
        int
            0 for high, 1 for medium, 2 for low, 99 for unknown values.
        """
        return {
            self.PRIORITY_HIGH:   0,
            self.PRIORITY_MEDIUM: 1,
            self.PRIORITY_LOW:    2,
        }.get(self.priority, 99)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f'<Task id={self.id} title={self.title!r} '
            f'status={self.status!r} priority={self.priority!r}>'
        )

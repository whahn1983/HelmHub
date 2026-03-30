"""
app/models/setting.py
~~~~~~~~~~~~~~~~~~~~~

Setting model — per-user application preferences stored in a dedicated
one-to-one table.

There is exactly one ``Setting`` row per user.  Use
``Setting.get_or_create(user_id)`` to retrieve or lazily initialise the
row; call ``db.session.commit()`` afterwards to persist it.

Dashboard config
----------------
``dashboard_config`` holds a JSON blob describing which widgets are shown
on the dashboard and in which order.  The schema is intentionally left
flexible so that new widgets can be added without a migration.  Use
``get_dashboard_config()`` / ``set_dashboard_config()`` to work with it
as a plain Python ``dict``.
"""

import json
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import relationship

from app.extensions import db


class Setting(db.Model):
    """Per-user application preferences."""

    __tablename__ = 'settings'

    # ------------------------------------------------------------------
    # Class-level constants
    # ------------------------------------------------------------------

    THEME_LIGHT  = 'light'
    THEME_DARK   = 'dark'
    THEME_SYSTEM = 'system'
    THEMES       = [THEME_LIGHT, THEME_DARK, THEME_SYSTEM]

    TIME_FORMAT_12 = '12'
    TIME_FORMAT_24 = '24'
    TIME_FORMATS   = [TIME_FORMAT_12, TIME_FORMAT_24]

    # Default dashboard widget order / visibility
    DEFAULT_DASHBOARD_CONFIG: dict[str, Any] = {
        'widgets': [
            {'id': 'tasks',     'visible': True,  'order': 1},
            {'id': 'reminders', 'visible': True,  'order': 2},
            {'id': 'events',    'visible': True,  'order': 3},
            {'id': 'notes',     'visible': True,  'order': 4},
            {'id': 'weather',   'visible': True,  'order': 5},
        ]
    }

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------

    id = db.Column(db.Integer, primary_key=True)

    # One-to-one with users — enforced by unique constraint
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        unique=True,
        nullable=False,
        index=True,
    )

    theme       = db.Column(db.String(16), nullable=False, default=THEME_SYSTEM)
    time_format = db.Column(db.String(4),  nullable=False, default=TIME_FORMAT_12)
    default_page = db.Column(db.String(64), nullable=False, default='/')
    show_weather = db.Column(db.Boolean,   nullable=False, default=True)

    # JSON blob — flexible widget configuration for the dashboard
    dashboard_config = db.Column(db.Text, nullable=True)

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

    user = relationship('User', back_populates='settings')

    # ------------------------------------------------------------------
    # Dashboard config helpers
    # ------------------------------------------------------------------

    def get_dashboard_config(self) -> dict[str, Any]:
        """
        Return the parsed dashboard configuration dictionary.

        Falls back to ``DEFAULT_DASHBOARD_CONFIG`` when the stored value
        is absent or cannot be decoded.
        """
        if not self.dashboard_config:
            return dict(self.DEFAULT_DASHBOARD_CONFIG)
        try:
            return json.loads(self.dashboard_config)
        except (json.JSONDecodeError, TypeError):
            return dict(self.DEFAULT_DASHBOARD_CONFIG)

    def set_dashboard_config(self, config: dict[str, Any]) -> None:
        """Serialise *config* to JSON and persist it in the column."""
        self.dashboard_config = json.dumps(config)

    def reset_dashboard_config(self) -> None:
        """Restore the default dashboard widget layout."""
        self.set_dashboard_config(self.DEFAULT_DASHBOARD_CONFIG)

    # ------------------------------------------------------------------
    # Class-level factory
    # ------------------------------------------------------------------

    @classmethod
    def _dedupe_for_user(cls, user_id: int) -> 'Setting | None':
        """
        Return the canonical settings row for *user_id* and remove extras.

        In healthy databases there is at most one row (enforced by the unique
        index on ``user_id``). This helper defensively cleans up older data in
        case duplicates existed before the constraint was added or became
        temporarily inconsistent.
        """
        rows = (
            cls.query
            .filter_by(user_id=user_id)
            .order_by(cls.id.asc())
            .all()
        )
        if not rows:
            return None

        canonical = rows[0]
        if len(rows) > 1:
            for duplicate in rows[1:]:
                db.session.delete(duplicate)
            db.session.flush()
        return canonical

    @classmethod
    def get_or_create(cls, user_id: int) -> 'Setting':
        """
        Return the existing ``Setting`` row for *user_id*, or insert a new
        one with default values and flush it to the current session.

        The caller is responsible for calling ``db.session.commit()`` to
        persist the new row.

        Parameters
        ----------
        user_id:
            The primary key of the ``User`` this setting belongs to.

        Returns
        -------
        Setting
            The (potentially newly created) settings instance.
        """
        # Prevent an unrelated pending Setting insert from being auto-flushed
        # during the existence check (which can raise IntegrityError before we
        # enter the guarded insert path below).
        with db.session.no_autoflush:
            instance = cls._dedupe_for_user(user_id)

        if instance is None:
            instance = cls(user_id=user_id)
            instance.set_dashboard_config(cls.DEFAULT_DASHBOARD_CONFIG)
            db.session.add(instance)
            try:
                db.session.flush()   # assign PK without a full commit
            except IntegrityError:
                # Another transaction (or a previously pending duplicate row)
                # may have inserted the per-user row before this flush.
                db.session.rollback()
                with db.session.no_autoflush:
                    instance = cls._dedupe_for_user(user_id)
                if instance is None:
                    raise
        return instance

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_dark_theme(self) -> bool:
        """``True`` when the user has explicitly selected the dark theme."""
        return self.theme == self.THEME_DARK

    @property
    def uses_24h_clock(self) -> bool:
        """``True`` when the user prefers a 24-hour time format."""
        return self.time_format == self.TIME_FORMAT_24

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f'<Setting id={self.id} user_id={self.user_id} '
            f'theme={self.theme!r} time_format={self.time_format!r}>'
        )

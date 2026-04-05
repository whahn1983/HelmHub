"""
app/models/calendar_subscription.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

CalendarSubscription model — persists only subscription source
configuration (URL, name, color, enabled flag, cache TTL, and status
fields).  Remote event instances are NEVER written to the database;
they are fetched, parsed, and cached in memory by the service layer.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import relationship

from app.extensions import db


class CalendarSubscription(db.Model):
    """
    A user-owned ICS/iCal or CalDAV calendar subscription source.

    Only the feed *configuration* is stored here.  Fetched events live
    exclusively in the in-process cache managed by
    ``app.services.calendar_subscriptions``.
    """

    __tablename__ = 'calendar_subscriptions'

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

    # Human-readable label shown in the UI
    name = db.Column(db.String(255), nullable=False)

    # The ICS/iCal subscription URL or CalDAV calendar URL (may contain
    # secret tokens — never expose in rendered HTML on the client side).
    url = db.Column(db.Text, nullable=False)

    # Subscription type: 'ics' (default) or 'caldav'
    subscription_type = db.Column(
        db.String(16), nullable=False, default='ics', server_default='ics'
    )

    # CalDAV credentials (password stored encrypted at rest)
    caldav_username = db.Column(db.Text, nullable=True)
    caldav_password_enc = db.Column(db.Text, nullable=True)

    # Optional CSS colour string (e.g. "#3b82f6") for event display
    color = db.Column(db.String(32), nullable=True)

    # When disabled the subscription is skipped during event merging
    enabled = db.Column(db.Boolean, nullable=False, default=True)

    # Per-subscription TTL override; NULL means use the app-level default
    cache_ttl_minutes = db.Column(db.Integer, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Feed refresh status — updated by the service layer after each attempt
    last_refresh_at = db.Column(db.DateTime, nullable=True)
    last_refresh_status = db.Column(db.String(64), nullable=True)   # 'ok' | 'error'
    # Parsed from the remote feed's HTTP Last-Modified header when present
    last_source_modified_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    user = relationship('User', back_populates='calendar_subscriptions')

    # ------------------------------------------------------------------
    # CalDAV password encryption helpers
    # ------------------------------------------------------------------

    @property
    def caldav_password(self) -> Optional[str]:
        """Decrypt and return the CalDAV password, or None if not set."""
        if not self.caldav_password_enc:
            return None
        from flask import current_app
        from app.services.crypto_service import decrypt_value
        key = current_app.config.get('TOTP_ENCRYPTION_KEY')
        return decrypt_value(self.caldav_password_enc, key)

    @caldav_password.setter
    def caldav_password(self, plaintext: Optional[str]) -> None:
        """Encrypt and store a CalDAV password.  Pass None to clear it."""
        if not plaintext:
            self.caldav_password_enc = None
            return
        from flask import current_app
        from app.services.crypto_service import encrypt_value
        key = current_app.config.get('TOTP_ENCRYPTION_KEY')
        self.caldav_password_enc = encrypt_value(plaintext, key)

    # ------------------------------------------------------------------
    # Computed helpers
    # ------------------------------------------------------------------

    @property
    def is_caldav(self) -> bool:
        """True when this is a CalDAV subscription."""
        return self.subscription_type == 'caldav'

    @property
    def effective_ttl_minutes(self) -> int:
        """Return per-subscription TTL or a safe sentinel for the service."""
        return self.cache_ttl_minutes if self.cache_ttl_minutes else 0

    @property
    def display_color(self) -> str:
        """Return color or a neutral fallback for rendering."""
        return self.color or '#6366f1'

    @property
    def status_label(self) -> str:
        """Human-readable last-refresh status string."""
        if self.last_refresh_status == 'ok':
            return 'OK'
        if self.last_refresh_status == 'error':
            return 'Error'
        return 'Never refreshed'

    @property
    def type_label(self) -> str:
        """Short display label for the subscription type."""
        return 'CalDAV' if self.is_caldav else 'ICS'

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f'<CalendarSubscription id={self.id} name={self.name!r} '
            f'type={self.subscription_type!r} enabled={self.enabled}>'
        )

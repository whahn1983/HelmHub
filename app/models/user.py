"""
app/models/user.py
~~~~~~~~~~~~~~~~~~

User model — the single authenticated principal for HelmHub.

Password storage
----------------
Passwords are hashed with bcrypt (work factor 12 by default). The raw
password is never stored and is not accessible after ``set_password()``
returns.

TOTP / 2FA
----------
The TOTP secret is stored as a base32 string.  In a production deployment
the value should be encrypted at rest (e.g. via a KMS-backed column type);
the column comment notes this as a reminder.

Recovery codes
--------------
Eight single-use recovery codes are generated as ``XXXX-XXXX`` strings.
Their bcrypt hashes are stored as a JSON array.  A code is removed from the
array after first use, preventing replay attacks.
"""

import json
import secrets
import string
from datetime import datetime
from typing import Optional

import bcrypt
import pyotp
from flask_login import UserMixin
from sqlalchemy.orm import relationship

from app.extensions import db


class User(UserMixin, db.Model):
    """Application user — single-admin personal homepage."""

    __tablename__ = 'users'

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------

    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(
        db.String(80),
        unique=True,
        nullable=False,
        index=True,
    )

    password_hash = db.Column(db.String(255), nullable=False)

    # TOTP / 2FA
    totp_enabled = db.Column(db.Boolean, default=False, nullable=False)
    # Stored encrypted in production; raw base32 for development
    totp_secret = db.Column(db.String(255), nullable=True)
    # JSON list of bcrypt-hashed one-time recovery codes
    totp_recovery_codes = db.Column(db.Text, nullable=True)

    # Timestamps
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    tasks = relationship(
        'Task',
        back_populates='user',
        cascade='all, delete-orphan',
        lazy='dynamic',
        order_by='Task.created_at.desc()',
    )

    notes = relationship(
        'Note',
        back_populates='user',
        cascade='all, delete-orphan',
        lazy='dynamic',
        order_by='Note.updated_at.desc()',
    )

    reminders = relationship(
        'Reminder',
        back_populates='user',
        cascade='all, delete-orphan',
        lazy='dynamic',
        order_by='Reminder.remind_at.asc()',
    )

    events = relationship(
        'Event',
        back_populates='user',
        cascade='all, delete-orphan',
        lazy='dynamic',
        order_by='Event.start_at.asc()',
    )

    settings = relationship(
        'Setting',
        back_populates='user',
        cascade='all, delete-orphan',
        uselist=False,   # one-to-one
    )

    bookmarks = relationship(
        'Bookmark',
        back_populates='user',
        cascade='all, delete-orphan',
        lazy='dynamic',
        order_by='Bookmark.created_at.desc()',
    )

    # ------------------------------------------------------------------
    # Password helpers
    # ------------------------------------------------------------------

    def set_password(self, password: str) -> None:
        """Hash *password* with bcrypt (work factor 12) and store the result."""
        salt = bcrypt.gensalt(rounds=12)
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        self.password_hash = hashed.decode('utf-8')

    def check_password(self, password: str) -> bool:
        """Return ``True`` if *password* matches the stored hash."""
        if not self.password_hash:
            return False
        try:
            return bcrypt.checkpw(
                password.encode('utf-8'),
                self.password_hash.encode('utf-8'),
            )
        except Exception:
            return False

    # ------------------------------------------------------------------
    # TOTP helpers
    # ------------------------------------------------------------------

    def get_totp_uri(self, app_name: str = 'HelmHub') -> str:
        """
        Return an ``otpauth://`` URI suitable for QR-code generation.

        The URI can be fed directly to ``qrcode.make()`` or similar.

        Raises
        ------
        ValueError
            If no TOTP secret has been set on this user.
        """
        if not self.totp_secret:
            raise ValueError('TOTP secret has not been set for this user.')
        totp = pyotp.TOTP(self.totp_secret)
        return totp.provisioning_uri(name=self.username, issuer_name=app_name)

    def verify_totp(self, token: str) -> bool:
        """
        Return ``True`` if *token* is a valid current TOTP code.

        A one-interval window is allowed on either side of the current
        time to accommodate minor clock skew between server and client.
        """
        if not self.totp_secret:
            return False
        try:
            totp = pyotp.TOTP(self.totp_secret)
            return totp.verify(token, valid_window=1)
        except Exception:
            return False

    def generate_recovery_codes(self) -> list[str]:
        """
        Generate 8 one-time recovery codes.

        Each code has the format ``XXXX-XXXX`` (uppercase alphanumeric).
        The plaintext codes are returned exactly once so they can be shown
        to the user.  Bcrypt hashes of the codes are persisted in
        ``totp_recovery_codes`` as a JSON array.

        Returns
        -------
        list[str]
            The 8 plaintext recovery codes (never stored).
        """
        alphabet = string.ascii_uppercase + string.digits
        codes: list[str] = []
        hashed_codes: list[str] = []

        for _ in range(8):
            raw = ''.join(secrets.choice(alphabet) for _ in range(8))
            code = f'{raw[:4]}-{raw[4:]}'
            codes.append(code)

            salt = bcrypt.gensalt(rounds=12)
            hashed = bcrypt.hashpw(code.encode('utf-8'), salt)
            hashed_codes.append(hashed.decode('utf-8'))

        self.totp_recovery_codes = json.dumps(hashed_codes)
        return codes

    def use_recovery_code(self, code: str) -> bool:
        """
        Verify *code* against the stored recovery codes.

        If the code is valid it is removed from the stored list (consumed)
        so it cannot be used again.

        Parameters
        ----------
        code:
            The plaintext recovery code entered by the user.

        Returns
        -------
        bool
            ``True`` if the code was valid and has been consumed;
            ``False`` otherwise.
        """
        if not self.totp_recovery_codes:
            return False

        try:
            hashed_codes: list[str] = json.loads(self.totp_recovery_codes)
        except (json.JSONDecodeError, TypeError):
            return False

        code_bytes = code.strip().upper().encode('utf-8')

        for i, hashed in enumerate(hashed_codes):
            try:
                if bcrypt.checkpw(code_bytes, hashed.encode('utf-8')):
                    # Consume the code by removing it from the list.
                    hashed_codes.pop(i)
                    self.totp_recovery_codes = json.dumps(hashed_codes)
                    return True
            except Exception:
                continue

        return False

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def recovery_codes_remaining(self) -> int:
        """Return the number of unused recovery codes."""
        if not self.totp_recovery_codes:
            return 0
        try:
            return len(json.loads(self.totp_recovery_codes))
        except (json.JSONDecodeError, TypeError):
            return 0

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f'<User id={self.id} username={self.username!r}>'

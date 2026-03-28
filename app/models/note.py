"""
app/models/note.py
~~~~~~~~~~~~~~~~~~

Note model — a plain-text or rich-text note with optional tagging and
pinning.

A note may be tagged with a single free-form string (e.g. ``'work'``,
``'personal'``) and optionally pinned so it appears at the top of the
notes list regardless of creation/update order.
"""

from datetime import datetime

from sqlalchemy.orm import relationship

from app.extensions import db


class Note(db.Model):
    """A text note belonging to a user."""

    __tablename__ = 'notes'

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

    # Body may be Markdown, plain text, or minimal HTML — the app layer
    # decides how to render / sanitise it.
    body = db.Column(db.Text, nullable=True)

    # Single free-form tag for lightweight categorisation
    tag = db.Column(db.String(64), nullable=True, index=True)

    # Pinned notes float to the top of the notes list
    pinned = db.Column(db.Boolean, nullable=False, default=False)

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

    user = relationship('User', back_populates='notes')

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def preview(self) -> str:
        """
        Return the first 200 characters of the body as a plain-text
        preview, falling back to an empty string if the body is absent.
        """
        if not self.body:
            return ''
        return self.body[:200]

    @property
    def word_count(self) -> int:
        """Approximate word count of the note body."""
        if not self.body:
            return 0
        return len(self.body.split())

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f'<Note id={self.id} title={self.title!r} '
            f'tag={self.tag!r} pinned={self.pinned}>'
        )

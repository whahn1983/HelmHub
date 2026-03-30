"""
app/models/bookmark.py
~~~~~~~~~~~~~~~~~~~~~~

Bookmark model — a saved link/URL with optional title, description, and
category tag.  Bookmarks can be pinned to surface them at the top of the
list and on the dashboard.
"""

from datetime import datetime

from sqlalchemy.orm import relationship

from app.extensions import db


class Bookmark(db.Model):
    """A saved URL bookmark belonging to a user."""

    __tablename__ = 'bookmarks'

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

    url = db.Column(db.Text, nullable=False)

    # Optional short description / notes
    description = db.Column(db.Text, nullable=True)

    # Single free-form category tag for lightweight organisation
    category = db.Column(db.String(64), nullable=True, index=True)

    # Pinned bookmarks float to the top of the list and appear on dashboard
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

    user = relationship('User', back_populates='bookmarks')

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def favicon_url(self) -> str:
        """Return Google's favicon service URL for this bookmark's domain."""
        from urllib.parse import urlparse
        parsed = urlparse(self.url or '')
        domain = parsed.netloc
        if not domain:
            return ''
        return f'https://www.google.com/s2/favicons?domain={domain}&sz=32'

    @property
    def display_url(self) -> str:
        """Return a human-readable URL stripped of the scheme prefix."""
        url = self.url or ''
        for prefix in ('https://', 'http://'):
            if url.startswith(prefix):
                return url[len(prefix):].rstrip('/')
        return url

    @property
    def domain(self) -> str:
        """Return just the domain portion of the URL."""
        url = self.display_url
        # Strip www.
        if url.startswith('www.'):
            url = url[4:]
        # Strip path
        return url.split('/')[0]

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f'<Bookmark id={self.id} title={self.title!r} '
            f'category={self.category!r} pinned={self.pinned}>'
        )

"""
app/models/subscription_event.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Persistent event rows derived from ICS calendar subscriptions.
"""

from datetime import datetime

from app.extensions import db


class SubscriptionEvent(db.Model):
    """A materialized event instance fetched from a calendar subscription."""

    __tablename__ = 'subscription_events'

    id = db.Column(db.Integer, primary_key=True)

    subscription_id = db.Column(
        db.Integer,
        db.ForeignKey('calendar_subscriptions.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    external_id = db.Column(db.String(512), nullable=False)
    title = db.Column(db.String(512), nullable=False)
    start_at = db.Column(db.DateTime, nullable=True, index=True)
    end_at = db.Column(db.DateTime, nullable=True)
    location = db.Column(db.String(512), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    all_day = db.Column(db.Boolean, nullable=False, default=False)

    source_name = db.Column(db.String(255), nullable=False)
    color = db.Column(db.String(32), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


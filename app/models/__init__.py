"""
app/models
~~~~~~~~~~

Imports all SQLAlchemy model classes so that Flask-Migrate (Alembic) and
other tooling can discover every mapped table via a single import of this
package.

Import order respects foreign-key dependencies:
  User  →  Task, Note, Reminder, Event, Setting, CalendarSubscription
"""

from app.models.user import User
from app.models.task import Task
from app.models.note import Note
from app.models.reminder import Reminder
from app.models.event import Event
from app.models.setting import Setting
from app.models.bookmark import Bookmark
from app.models.calendar_subscription import CalendarSubscription

__all__ = [
    'User',
    'Task',
    'Note',
    'Reminder',
    'Event',
    'Setting',
    'Bookmark',
    'CalendarSubscription',
]

from .auth import auth_bp
from .dashboard import dashboard_bp
from .tasks import tasks_bp
from .notes import notes_bp
from .reminders import reminders_bp
from .events import events_bp
from .settings import settings_bp
from .api import api_bp

__all__ = ['auth_bp', 'dashboard_bp', 'tasks_bp', 'notes_bp', 'reminders_bp', 'events_bp', 'settings_bp', 'api_bp']

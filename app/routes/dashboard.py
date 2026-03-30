"""
Dashboard routes: the main landing page for authenticated users.
"""

from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Task, Note, Reminder, Event, Bookmark, Setting

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/')


@dashboard_bp.route('/')
@login_required
def index():
    """
    Render the main dashboard.

    Gathers:
      - Top 3 high-priority open tasks
      - Tasks due today or pinned to today
      - Count of overdue tasks
      - Due reminders (remind_at <= now, status='pending')
      - Today's events
      - The next upcoming event
      - Three most-recently-updated notes
    """
    now = datetime.utcnow()
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = today_start + timedelta(days=1)
    user_settings = Setting.get_or_create(current_user.id)
    widget_visibility = {
        'tasks': True,
        'today': True,
        'events': True,
        'reminders': True,
        'notes': True,
        'bookmarks': True,
    }
    for widget in (user_settings.get_dashboard_config().get('widgets') or []):
        widget_id = widget.get('id')
        if widget_id in widget_visibility:
            widget_visibility[widget_id] = bool(widget.get('visible', True))

    # --- Tasks ---

    # Top 3 high-priority open tasks (not completed, highest priority first).
    priority_tasks = (
        Task.query
        .filter_by(user_id=current_user.id, status='open', priority='high')
        .order_by(Task.due_at.asc().nullslast())
        .limit(3)
        .all()
    )

    # Tasks due today or explicitly pinned to today and still open.
    today_tasks = (
        Task.query
        .filter(
            Task.user_id == current_user.id,
            Task.status == 'open',
            db.or_(
                db.and_(Task.due_at >= today_start, Task.due_at < today_end),
                Task.pinned_to_today == True,  # noqa: E712
            ),
        )
        .order_by(Task.priority.desc(), Task.due_at.asc().nullslast())
        .all()
    )

    # Count of overdue open tasks (due_at is in the past).
    overdue_count = (
        Task.query
        .filter(
            Task.user_id == current_user.id,
            Task.status == 'open',
            Task.due_at < today_start,
            Task.due_at.isnot(None),
        )
        .count()
    )

    # --- Reminders ---

    due_reminders = (
        Reminder.query
        .filter(
            Reminder.user_id == current_user.id,
            Reminder.status == 'pending',
            Reminder.remind_at <= now,
        )
        .order_by(Reminder.remind_at.asc())
        .all()
    )

    # --- Events ---

    today_events = (
        Event.query
        .filter(
            Event.user_id == current_user.id,
            Event.start_at >= today_start,
            Event.start_at < today_end,
        )
        .order_by(Event.start_at.asc())
        .all()
    )

    next_event = (
        Event.query
        .filter(
            Event.user_id == current_user.id,
            Event.start_at >= now,
        )
        .order_by(Event.start_at.asc())
        .first()
    )

    # --- Notes ---

    recent_notes = (
        Note.query
        .filter_by(user_id=current_user.id)
        .order_by(Note.updated_at.desc())
        .limit(3)
        .all()
    )

    # --- Bookmarks ---

    # Show all bookmarks on the dashboard widget (scrollable), pinned first.
    dashboard_bookmarks = (
        Bookmark.query
        .filter_by(user_id=current_user.id)
        .order_by(Bookmark.pinned.desc(), Bookmark.created_at.desc())
        .all()
    )

    # Group for category-grouped widget display
    _bm_groups: dict = {}
    for bm in dashboard_bookmarks:
        key = bm.category or ''
        _bm_groups.setdefault(key, []).append(bm)
    _named = sorted([(k, v) for k, v in _bm_groups.items() if k], key=lambda x: x[0])
    if '' in _bm_groups:
        _named.append(('', _bm_groups['']))
    dashboard_bookmarks_grouped = _named

    hour = now.hour
    if hour < 12:
        greeting = 'Good morning'
    elif hour < 17:
        greeting = 'Good afternoon'
    else:
        greeting = 'Good evening'

    formatted_date = now.strftime('%A, %B ') + str(now.day) + now.strftime(', %Y')

    return render_template(
        'dashboard/index.html',
        top_tasks=priority_tasks,
        today_tasks=today_tasks,
        overdue_count=overdue_count,
        due_reminders=due_reminders,
        today_events=today_events,
        next_event=next_event,
        recent_notes=recent_notes,
        dashboard_bookmarks=dashboard_bookmarks,
        dashboard_bookmarks_grouped=dashboard_bookmarks_grouped,
        user_settings=user_settings,
        widget_visibility=widget_visibility,
        now=now,
        formatted_date=formatted_date,
        greeting=greeting,
    )

"""
JSON API routes for HelmHub.

All routes require authentication (login_required).
Returns JSON responses only — no HTML rendering.

Base prefix: /api  (registered by the app factory)
"""

from datetime import datetime, date, timedelta

from flask import Blueprint, jsonify, request, abort, make_response
from flask_login import login_required, current_user

from app.extensions import db, csrf
from app.models import Task, Note, Reminder, Event
from app.services.auth_service import parse_datetime

api_bp = Blueprint('api', __name__, url_prefix='/api')

# Exempt the entire API blueprint from CSRF because API consumers typically
# send JSON without the WTF CSRF cookie.  Callers should authenticate via
# session/cookie as enforced by login_required.
csrf.exempt(api_bp)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_task(task):
    return {
        'id': task.id,
        'title': task.title,
        'description': task.description,
        'priority': task.priority,
        'status': task.status,
        'due_at': task.due_at.isoformat() if task.due_at else None,
        'pinned_to_today': task.pinned_to_today,
        'is_overdue': task.is_overdue,
        'is_due_today': task.is_due_today,
        'created_at': task.created_at.isoformat(),
        'updated_at': task.updated_at.isoformat(),
    }


def _json_reminder(reminder):
    return {
        'id': reminder.id,
        'title': reminder.title,
        'notes': reminder.notes,
        'remind_at': reminder.remind_at.isoformat(),
        'status': reminder.status,
        'snoozed_until': reminder.snoozed_until.isoformat() if reminder.snoozed_until else None,
        'is_due': reminder.is_due,
        'created_at': reminder.created_at.isoformat(),
        'updated_at': reminder.updated_at.isoformat(),
    }


def _json_note(note):
    return {
        'id': note.id,
        'title': note.title,
        'body': note.body,
        'tag': note.tag,
        'pinned': note.pinned,
        'created_at': note.created_at.isoformat(),
        'updated_at': note.updated_at.isoformat(),
    }


def _json_event(event):
    return {
        'id': event.id,
        'title': event.title,
        'start_at': event.start_at.isoformat(),
        'end_at': event.end_at.isoformat() if event.end_at else None,
        'location': event.location,
        'notes': event.notes,
        'is_today': event.is_today,
        'is_upcoming': event.is_upcoming,
        'duration_minutes': event.duration_minutes,
        'created_at': event.created_at.isoformat(),
        'updated_at': event.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /api/tasks
# ---------------------------------------------------------------------------

@api_bp.route('/tasks')
@login_required
def tasks():
    """
    Return a JSON list of the current user's tasks.

    Query params (all optional):
      view     – today | upcoming | overdue | completed | all (default: all)
      priority – low | medium | high
      search   – substring search on title
    """
    view = request.args.get('view', 'all').strip().lower()
    priority_filter = request.args.get('priority', '').strip().lower()
    search = request.args.get('search', '').strip()

    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = today_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)
    now = datetime.utcnow()

    query = Task.query.filter_by(user_id=current_user.id)

    if view == 'today':
        query = query.filter(
            Task.status == 'open',
            db.or_(
                db.and_(Task.due_at >= today_start, Task.due_at < today_end),
                Task.pinned_to_today == True,  # noqa: E712
            ),
        )
    elif view == 'upcoming':
        query = query.filter(
            Task.status == 'open',
            Task.due_at >= today_end,
            Task.due_at < week_end,
        )
    elif view == 'overdue':
        query = query.filter(
            Task.status == 'open',
            Task.due_at < today_start,
            Task.due_at.isnot(None),
        )
    elif view == 'completed':
        query = query.filter(Task.status == 'completed')

    if priority_filter in ('low', 'medium', 'high'):
        query = query.filter(Task.priority == priority_filter)

    if search:
        query = query.filter(Task.title.ilike(f'%{search}%'))

    task_list = query.order_by(Task.created_at.desc()).all()
    return jsonify([_json_task(t) for t in task_list])


# ---------------------------------------------------------------------------
# GET /api/reminders/due
# ---------------------------------------------------------------------------

@api_bp.route('/reminders/due')
@login_required
def reminders_due():
    """
    Return all currently due reminders as JSON.

    A reminder is due when its status is 'pending' and remind_at <= now,
    or status is 'snoozed' and snoozed_until <= now.
    """
    now = datetime.utcnow()

    due = (
        Reminder.query
        .filter(
            Reminder.user_id == current_user.id,
            db.or_(
                db.and_(
                    Reminder.status == Reminder.STATUS_PENDING,
                    Reminder.remind_at <= now,
                ),
                db.and_(
                    Reminder.status == Reminder.STATUS_SNOOZED,
                    Reminder.snoozed_until <= now,
                ),
            ),
        )
        .order_by(Reminder.remind_at.asc())
        .all()
    )

    return jsonify([_json_reminder(r) for r in due])


# ---------------------------------------------------------------------------
# POST /api/quick-capture
# ---------------------------------------------------------------------------

@api_bp.route('/quick-capture', methods=['POST'])
@login_required
def quick_capture():
    """
    Quick-capture endpoint: create a task, note, reminder, or event.

    Accepts JSON or form-encoded data (e.g. from HTMX forms).
    Returns JSON for JSON requests; HTML fragment for HTMX form requests.

    Task fields:   type=task, title (required), priority, due_date
    Note fields:   type=note, title (optional), body, tag
    Reminder fields: type=reminder, title (required), remind_at (datetime-local)
    Event fields:  type=event, title (required), start_at (datetime-local),
                   end_at (datetime-local, optional), location
    """
    if request.is_json:
        payload = request.get_json(silent=True) or {}
    else:
        payload = request.form

    is_htmx = request.headers.get('HX-Request') == 'true'

    def _ok_html(msg):
        resp = make_response(
            f'<p class="qc-success">'
            f'<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">'
            f'<path d="M2.5 7l3.5 3.5 5.5-7" stroke="currentColor" stroke-width="1.75"'
            f' stroke-linecap="round" stroke-linejoin="round"/></svg> {msg}</p>'
        )
        resp.headers['HX-Trigger'] = 'quickCaptureSuccess'
        return resp, 201

    def _err_html(msg):
        return (
            f'<p class="qc-error">{msg}</p>',
            422,
        )

    def _parse_dt_local(value):
        """Parse a datetime-local string (YYYY-MM-DDTHH:MM) to datetime."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    capture_type = payload.get('type', '').strip().lower()

    # ------------------------------------------------------------------
    # Task
    # ------------------------------------------------------------------
    if capture_type == 'task':
        title = payload.get('title', '').strip()
        if not title:
            if is_htmx:
                return _err_html('Task title is required.')
            return jsonify({'error': 'title is required for tasks.'}), 422

        priority = payload.get('priority', 'medium').strip().lower()
        if priority not in ('low', 'medium', 'high'):
            priority = 'medium'

        due_at = None
        due_date_str = payload.get('due_date', '')
        if due_date_str:
            due_at = parse_datetime(due_date_str, None)

        task = Task(
            user_id=current_user.id,
            title=title,
            description=payload.get('description', '').strip() or None,
            priority=priority,
            due_at=due_at,
            pinned_to_today=bool(payload.get('pinned_to_today', False)),
            status='open',
        )
        db.session.add(task)
        db.session.commit()

        if is_htmx:
            return _ok_html(f'Task \u201c{title}\u201d added!')
        return jsonify({'status': 'created', 'type': 'task', 'item': _json_task(task)}), 201

    # ------------------------------------------------------------------
    # Note
    # ------------------------------------------------------------------
    elif capture_type == 'note':
        title = payload.get('title', '').strip()
        body = payload.get('body', '').strip()

        if not title and not body:
            if is_htmx:
                return _err_html('Note title or content is required.')
            return jsonify({'error': 'title or body is required for notes.'}), 422

        if not title:
            # Auto-title from first line of body
            title = body.split('\n')[0][:60] or 'Quick Note'

        tag = payload.get('tag', '').strip().lower() or None
        note = Note(
            user_id=current_user.id,
            title=title,
            body=body or None,
            tag=tag,
            pinned=bool(payload.get('pinned', False)),
        )
        db.session.add(note)
        db.session.commit()

        if is_htmx:
            return _ok_html('Note saved!')
        return jsonify({'status': 'created', 'type': 'note', 'item': _json_note(note)}), 201

    # ------------------------------------------------------------------
    # Reminder
    # ------------------------------------------------------------------
    elif capture_type == 'reminder':
        title = payload.get('title', '').strip()
        if not title:
            if is_htmx:
                return _err_html('Reminder title is required.')
            return jsonify({'error': 'title is required for reminders.'}), 422

        # Accept datetime-local (remind_at) or separate remind_date + remind_time
        remind_at = _parse_dt_local(payload.get('remind_at', ''))
        if remind_at is None:
            remind_at = parse_datetime(
                payload.get('remind_date', ''),
                payload.get('remind_time') or None,
            )

        if remind_at is None:
            if is_htmx:
                return _err_html('Reminder date/time is required.')
            return jsonify({'error': 'remind_at is required for reminders.'}), 422

        reminder = Reminder(
            user_id=current_user.id,
            title=title,
            notes=payload.get('notes', '').strip() or None,
            remind_at=remind_at,
            status=Reminder.STATUS_PENDING,
        )
        db.session.add(reminder)
        db.session.commit()

        if is_htmx:
            return _ok_html('Reminder set!')
        return jsonify({'status': 'created', 'type': 'reminder', 'item': _json_reminder(reminder)}), 201

    # ------------------------------------------------------------------
    # Event
    # ------------------------------------------------------------------
    elif capture_type == 'event':
        title = payload.get('title', '').strip()
        if not title:
            if is_htmx:
                return _err_html('Event title is required.')
            return jsonify({'error': 'title is required for events.'}), 422

        # Accept datetime-local (start_at) or separate start_date + start_time
        start_at = _parse_dt_local(payload.get('start_at', ''))
        if start_at is None:
            start_at = parse_datetime(
                payload.get('start_date', ''),
                payload.get('start_time') or None,
            )

        if start_at is None:
            if is_htmx:
                return _err_html('Event start date/time is required.')
            return jsonify({'error': 'start_at is required for events.'}), 422

        end_at = _parse_dt_local(payload.get('end_at', ''))
        if end_at is None:
            end_at = parse_datetime(
                payload.get('end_date', ''),
                payload.get('end_time') or None,
            )

        event = Event(
            user_id=current_user.id,
            title=title,
            start_at=start_at,
            end_at=end_at,
            location=payload.get('location', '').strip() or None,
            notes=payload.get('notes', '').strip() or None,
        )
        db.session.add(event)
        db.session.commit()

        if is_htmx:
            return _ok_html(f'Event \u201c{title}\u201d added!')
        return jsonify({'status': 'created', 'type': 'event', 'item': _json_event(event)}), 201

    else:
        msg = f'Unknown type {capture_type!r}. Must be one of: task, note, reminder, event.'
        if is_htmx:
            return _err_html(msg)
        return jsonify({'error': msg}), 422


# ---------------------------------------------------------------------------
# GET /api/dashboard-data
# ---------------------------------------------------------------------------

@api_bp.route('/dashboard-data')
@api_bp.route('/v1/dashboard-data')
@login_required
def dashboard_data():
    """
    Return a JSON summary of dashboard data for the current user.

    Includes:
      - priority_tasks: top 3 high-priority open tasks
      - today_tasks: open tasks due today or pinned
      - overdue_count: number of overdue open tasks
      - due_reminders: reminders currently due
      - today_events: events starting today
      - next_event: the next upcoming event
      - recent_notes: 3 most recently updated notes
    """
    now = datetime.utcnow()
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = today_start + timedelta(days=1)

    priority_tasks = (
        Task.query
        .filter_by(user_id=current_user.id, status='open', priority='high')
        .order_by(Task.due_at.asc().nullslast())
        .limit(3)
        .all()
    )

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

    due_reminders = (
        Reminder.query
        .filter(
            Reminder.user_id == current_user.id,
            db.or_(
                db.and_(
                    Reminder.status == Reminder.STATUS_PENDING,
                    Reminder.remind_at <= now,
                ),
                db.and_(
                    Reminder.status == Reminder.STATUS_SNOOZED,
                    Reminder.snoozed_until <= now,
                ),
            ),
        )
        .order_by(Reminder.remind_at.asc())
        .all()
    )

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

    recent_notes = (
        Note.query
        .filter_by(user_id=current_user.id)
        .order_by(Note.updated_at.desc())
        .limit(3)
        .all()
    )

    tasks_json = [_json_task(t) for t in today_tasks]
    reminders_json = [_json_reminder(r) for r in due_reminders]
    events_json = [_json_event(e) for e in today_events]
    notes_json = [_json_note(n) for n in recent_notes]

    return jsonify({
        'priority_tasks': [_json_task(t) for t in priority_tasks],
        'today_tasks': tasks_json,
        'tasks': tasks_json,
        'overdue_count': overdue_count,
        'due_reminders': reminders_json,
        'reminders': reminders_json,
        'today_events': events_json,
        'events': events_json,
        'next_event': _json_event(next_event) if next_event else None,
        'recent_notes': notes_json,
        'notes': notes_json,
        'generated_at': now.isoformat(),
    })

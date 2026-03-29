"""
Focus mode route — minimal distraction-free view.
"""

from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Task, Event

focus_bp = Blueprint('focus', __name__, url_prefix='/focus')


@focus_bp.route('/')
@login_required
def index():
    """Render the focus mode screen."""
    now = datetime.utcnow()

    # Get top priority open task (allow override via ?task_id=)
    task_id = request.args.get('task_id', type=int)
    focus_task = None

    if task_id:
        t = db.session.get(Task, task_id)
        if t and t.user_id == current_user.id and t.status == 'open':
            focus_task = t

    if focus_task is None:
        focus_task = (
            Task.query
            .filter_by(user_id=current_user.id, status='open')
            .order_by(
                db.case(
                    {'high': 0, 'medium': 1, 'low': 2},
                    value=Task.priority,
                    else_=3,
                ),
                Task.due_at.asc().nullslast(),
            )
            .first()
        )

    # Top tasks for the queue display
    top_tasks = (
        Task.query
        .filter_by(user_id=current_user.id, status='open')
        .order_by(
            db.case(
                {'high': 0, 'medium': 1, 'low': 2},
                value=Task.priority,
                else_=3,
            ),
            Task.due_at.asc().nullslast(),
        )
        .limit(4)
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

    return render_template(
        'focus/index.html',
        focus_task=focus_task,
        top_tasks=top_tasks,
        next_event=next_event,
        now=now,
    )

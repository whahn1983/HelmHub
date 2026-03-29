"""
Task management routes.

Supports both full-page and HTMX partial responses throughout.
"""

from datetime import datetime, date, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, abort, make_response,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Task
from app.services.auth_service import parse_datetime

tasks_bp = Blueprint('tasks', __name__, url_prefix='/tasks')

# Valid priority levels accepted from forms.
VALID_PRIORITIES = ('low', 'medium', 'high')
VALID_STATUSES = ('open', 'completed')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_htmx():
    return request.headers.get('HX-Request') == 'true'


def _task_or_404(task_id):
    """Return the task owned by the current user or abort with 404."""
    task = db.session.get(Task, task_id)
    if task is None or task.user_id != current_user.id:
        abort(404)
    return task


def _parse_task_form(form):
    """
    Parse and validate common task form fields.

    Returns (data_dict, errors_list).
    """
    errors = []
    title = form.get('title', '').strip()
    if not title:
        errors.append('Title is required.')

    priority = form.get('priority', 'medium').strip().lower()
    if priority not in VALID_PRIORITIES:
        errors.append(f'Priority must be one of: {", ".join(VALID_PRIORITIES)}.')

    due_at = None
    due_date_str = form.get('due_date', '').strip()
    due_time_str = form.get('due_time', '').strip() or None
    if due_date_str:
        due_at = parse_datetime(due_date_str, due_time_str)
        if due_at is None:
            errors.append('Invalid date/time format for due date.')

    description = form.get('description', '').strip()
    pinned_to_today = form.get('pinned_to_today') == 'on'

    data = {
        'title': title,
        'priority': priority,
        'due_at': due_at,
        'description': description,
        'pinned_to_today': pinned_to_today,
    }
    return data, errors


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@tasks_bp.route('/')
@login_required
def index():
    """
    List tasks with optional filters.

    Query params:
      view     – today | upcoming | overdue | completed | all (default: all)
      priority – low | medium | high
      search   – substring search on title
    """
    view = request.args.get('view', 'all')
    priority_filter = request.args.get('priority', '').strip().lower()
    search = request.args.get('search', '').strip()

    now = datetime.utcnow()
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = today_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)

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
    # else: 'all' — no additional filter

    if priority_filter and priority_filter in VALID_PRIORITIES:
        query = query.filter(Task.priority == priority_filter)

    if search:
        query = query.filter(Task.title.ilike(f'%{search}%'))

    # Ordering: open tasks by priority then due date; completed by updated_at.
    if view == 'completed':
        query = query.order_by(Task.updated_at.desc())
    else:
        priority_order = db.case(
            {'high': 0, 'medium': 1, 'low': 2},
            value=Task.priority,
            else_=3,
        )
        query = query.order_by(priority_order, Task.due_at.asc().nullslast())

    tasks = query.all()

    if _is_htmx():
        return render_template(
            'tasks/tasks_list.html',
            tasks=tasks,
            view=view,
            priority_filter=priority_filter,
            search=search,
        )

    return render_template(
        'tasks/index.html',
        tasks=tasks,
        view=view,
        priority_filter=priority_filter,
        search=search,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@tasks_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    """Render the create-task form (GET) or process a submission (POST)."""
    if request.method == 'POST':
        data, errors = _parse_task_form(request.form)

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            if _is_htmx():
                return render_template('tasks/task_form.html', errors=errors, form=request.form), 422
            return render_template('tasks/new.html', errors=errors, form=request.form), 422

        task = Task(
            user_id=current_user.id,
            title=data['title'],
            description=data['description'],
            priority=data['priority'],
            due_at=data['due_at'],
            pinned_to_today=data['pinned_to_today'],
            status='open',
        )
        db.session.add(task)
        db.session.commit()

        flash('Task created successfully.', 'success')

        if _is_htmx():
            response = make_response(render_template('tasks/task_item.html', task=task))
            response.headers['HX-Trigger'] = 'taskCreated'
            return response

        return redirect(url_for('tasks.index'))

    # GET
    if _is_htmx():
        return render_template('tasks/task_form.html', form={})

    return render_template('tasks/new.html', form={})


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@tasks_bp.route('/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(task_id):
    """Edit an existing task."""
    task = _task_or_404(task_id)

    if request.method == 'POST':
        data, errors = _parse_task_form(request.form)

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            if _is_htmx():
                return render_template('tasks/task_form.html', task=task, errors=errors, form=request.form), 422
            return render_template('tasks/edit.html', task=task, errors=errors, form=request.form), 422

        task.title = data['title']
        task.description = data['description']
        task.priority = data['priority']
        task.due_at = data['due_at']
        task.pinned_to_today = data['pinned_to_today']
        db.session.commit()

        flash('Task updated.', 'success')

        if _is_htmx():
            response = make_response(render_template('tasks/task_item.html', task=task))
            response.headers['HX-Trigger'] = 'taskUpdated'
            return response

        return redirect(url_for('tasks.index'))

    # GET
    if _is_htmx():
        return render_template('tasks/task_form.html', task=task, form=task)

    return render_template('tasks/edit.html', task=task, form=task)


# ---------------------------------------------------------------------------
# Toggle complete / open
# ---------------------------------------------------------------------------

@tasks_bp.route('/<int:task_id>/complete', methods=['POST'])
@login_required
def complete(task_id):
    """Toggle a task between open and completed."""
    task = _task_or_404(task_id)
    task.status = 'open' if task.status == 'completed' else 'completed'
    db.session.commit()

    if _is_htmx():
        response = make_response(render_template('tasks/task_item.html', task=task))
        response.headers['HX-Trigger'] = 'taskStatusChanged'
        return response

    return redirect(request.referrer or url_for('tasks.index'))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@tasks_bp.route('/<int:task_id>/delete', methods=['POST'])
@login_required
def delete(task_id):
    """Permanently delete a task."""
    task = _task_or_404(task_id)
    db.session.delete(task)
    db.session.commit()

    flash('Task deleted.', 'info')

    if _is_htmx():
        response = make_response('')
        response.headers['HX-Trigger'] = 'taskDeleted'
        return response

    return redirect(request.referrer or url_for('tasks.index'))


# ---------------------------------------------------------------------------
# Toggle pinned-to-today
# ---------------------------------------------------------------------------

@tasks_bp.route('/<int:task_id>/pin', methods=['POST'])
@login_required
def pin(task_id):
    """Toggle the pinned_to_today flag on a task."""
    task = _task_or_404(task_id)
    task.pinned_to_today = not task.pinned_to_today
    db.session.commit()

    if _is_htmx():
        response = make_response(render_template('tasks/task_item.html', task=task))
        response.headers['HX-Trigger'] = 'taskPinChanged'
        return response

    return redirect(request.referrer or url_for('tasks.index'))


# ---------------------------------------------------------------------------
# Convenience views (delegate to index with ?view=)
# ---------------------------------------------------------------------------

@tasks_bp.route('/today')
@login_required
def today():
    """Show tasks due today or pinned to today."""
    # Redirect to canonical URL with view param so it is bookmarkable.
    return redirect(url_for('tasks.index', view='today'))


@tasks_bp.route('/upcoming')
@login_required
def upcoming():
    """Redirect to the task list filtered to upcoming view."""
    return redirect(url_for('tasks.index', view='upcoming'))


@tasks_bp.route('/overdue')
@login_required
def overdue():
    """Redirect to the task list filtered to overdue view."""
    return redirect(url_for('tasks.index', view='overdue'))

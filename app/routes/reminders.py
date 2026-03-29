"""
Reminders routes.

Handles CRUD for reminders plus status-change actions:
complete, dismiss, snooze.

Supports both full-page and HTMX partial responses.
"""

from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, abort, make_response, jsonify,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Reminder
from app.services.auth_service import parse_datetime

reminders_bp = Blueprint('reminders', __name__, url_prefix='/reminders')

# Default snooze duration in minutes.
DEFAULT_SNOOZE_MINUTES = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_htmx():
    return request.headers.get('HX-Request') == 'true'


def _reminder_or_404(reminder_id):
    """Return the reminder owned by current user or abort 404."""
    reminder = db.session.get(Reminder, reminder_id)
    if reminder is None or reminder.user_id != current_user.id:
        abort(404)
    return reminder


def _parse_reminder_form(form):
    """
    Parse and validate common reminder form fields.

    Returns (data_dict, errors_list).
    """
    errors = []

    title = form.get('title', '').strip()
    if not title:
        errors.append('Title is required.')

    notes = form.get('notes', '').strip()

    # remind_at is required: combine remind_date + remind_time
    remind_date_str = form.get('remind_date', '').strip()
    remind_time_str = form.get('remind_time', '').strip() or None
    remind_at = None
    if not remind_date_str:
        errors.append('Reminder date is required.')
    else:
        remind_at = parse_datetime(remind_date_str, remind_time_str)
        if remind_at is None:
            errors.append('Invalid date/time format for reminder.')

    data = {
        'title': title,
        'notes': notes,
        'remind_at': remind_at,
    }
    return data, errors


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@reminders_bp.route('/')
@login_required
def index():
    """
    List reminders with optional status filter.

    Query params:
      status – pending | completed | dismissed | snoozed | all (default: pending)
    """
    status_filter = request.args.get('status', 'pending').strip().lower()

    query = Reminder.query.filter_by(user_id=current_user.id)

    if status_filter in Reminder.STATUSES:
        query = query.filter(Reminder.status == status_filter)
    # else 'all' — no additional filter

    # Sort pending/snoozed by remind_at ascending; others by updated_at desc.
    if status_filter in ('pending', 'snoozed', 'all'):
        query = query.order_by(Reminder.remind_at.asc())
    else:
        query = query.order_by(Reminder.updated_at.desc())

    reminders = query.all()

    if _is_htmx():
        return render_template(
            'reminders/reminders_list.html',
            reminders=reminders,
            status_filter=status_filter,
        )

    return render_template(
        'reminders/index.html',
        reminders=reminders,
        status_filter=status_filter,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@reminders_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    """Render the create-reminder form (GET) or process submission (POST)."""
    if request.method == 'POST':
        data, errors = _parse_reminder_form(request.form)

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            if _is_htmx():
                return render_template(
                    'reminders/reminder_form.html',
                    errors=errors,
                    form=request.form,
                ), 422
            return render_template('reminders/new.html', errors=errors, form=request.form), 422

        reminder = Reminder(
            user_id=current_user.id,
            title=data['title'],
            notes=data['notes'],
            remind_at=data['remind_at'],
            status=Reminder.STATUS_PENDING,
        )
        db.session.add(reminder)
        db.session.commit()

        flash('Reminder created.', 'success')

        if _is_htmx():
            response = make_response(render_template('reminders/reminder_item.html', reminder=reminder))
            response.headers['HX-Trigger'] = 'reminderCreated'
            return response

        return redirect(url_for('reminders.index'))

    # GET
    if _is_htmx():
        return render_template('reminders/reminder_form.html', form={})

    return render_template('reminders/new.html', form={})


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@reminders_bp.route('/<int:reminder_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(reminder_id):
    """Edit an existing reminder."""
    reminder = _reminder_or_404(reminder_id)

    if request.method == 'POST':
        data, errors = _parse_reminder_form(request.form)

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            if _is_htmx():
                return render_template(
                    'reminders/reminder_form.html',
                    reminder=reminder,
                    errors=errors,
                    form=request.form,
                ), 422
            return render_template(
                'reminders/edit.html',
                reminder=reminder,
                errors=errors,
                form=request.form,
            ), 422

        reminder.title = data['title']
        reminder.notes = data['notes']
        reminder.remind_at = data['remind_at']
        # Re-open a dismissed/completed reminder when the user edits it.
        if reminder.status in (Reminder.STATUS_COMPLETED, Reminder.STATUS_DISMISSED):
            reminder.status = Reminder.STATUS_PENDING
        db.session.commit()

        flash('Reminder updated.', 'success')

        if _is_htmx():
            response = make_response(render_template('reminders/reminder_item.html', reminder=reminder))
            response.headers['HX-Trigger'] = 'reminderUpdated'
            return response

        return redirect(url_for('reminders.index'))

    # GET
    if _is_htmx():
        return render_template('reminders/reminder_form.html', reminder=reminder, form=reminder)

    return render_template('reminders/edit.html', reminder=reminder, form=reminder)


# ---------------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------------

@reminders_bp.route('/<int:reminder_id>/complete', methods=['POST'])
@login_required
def complete(reminder_id):
    """Mark a reminder as completed."""
    reminder = _reminder_or_404(reminder_id)
    reminder.status = Reminder.STATUS_COMPLETED
    db.session.commit()

    flash('Reminder marked as complete.', 'success')

    if _is_htmx():
        response = make_response(render_template('reminders/reminder_item.html', reminder=reminder))
        response.headers['HX-Trigger'] = 'reminderCompleted'
        return response

    return redirect(request.referrer or url_for('reminders.index'))


# ---------------------------------------------------------------------------
# Dismiss
# ---------------------------------------------------------------------------

@reminders_bp.route('/<int:reminder_id>/dismiss', methods=['POST'])
@login_required
def dismiss(reminder_id):
    """Dismiss a reminder (won't resurface)."""
    reminder = _reminder_or_404(reminder_id)
    reminder.status = Reminder.STATUS_DISMISSED
    db.session.commit()

    flash('Reminder dismissed.', 'info')

    if _is_htmx():
        response = make_response('')
        response.headers['HX-Trigger'] = 'reminderDismissed'
        return response

    return redirect(request.referrer or url_for('reminders.index'))


# ---------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------

@reminders_bp.route('/<int:reminder_id>/snooze', methods=['POST'])
@login_required
def snooze(reminder_id):
    """Snooze a reminder by a given number of minutes (default 15)."""
    reminder = _reminder_or_404(reminder_id)

    try:
        minutes = int(request.form.get('minutes', request.form.get('snooze_minutes', DEFAULT_SNOOZE_MINUTES)))
        if minutes < 1:
            minutes = DEFAULT_SNOOZE_MINUTES
    except (ValueError, TypeError):
        minutes = DEFAULT_SNOOZE_MINUTES

    reminder.status = Reminder.STATUS_SNOOZED
    reminder.snoozed_until = datetime.utcnow() + timedelta(minutes=minutes)
    db.session.commit()

    flash(f'Reminder snoozed for {minutes} minutes.', 'info')

    if _is_htmx():
        response = make_response(render_template('reminders/reminder_item.html', reminder=reminder))
        response.headers['HX-Trigger'] = 'reminderSnoozed'
        return response

    return redirect(request.referrer or url_for('reminders.index'))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@reminders_bp.route('/<int:reminder_id>/delete', methods=['POST'])
@login_required
def delete(reminder_id):
    """Permanently delete a reminder."""
    reminder = _reminder_or_404(reminder_id)
    db.session.delete(reminder)
    db.session.commit()

    flash('Reminder deleted.', 'info')

    if _is_htmx():
        response = make_response('')
        response.headers['HX-Trigger'] = 'reminderDeleted'
        return response

    return redirect(request.referrer or url_for('reminders.index'))

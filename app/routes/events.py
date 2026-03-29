"""
Events routes.

Handles CRUD for calendar events.
Supports both full-page and HTMX partial responses.
"""

from datetime import datetime, date, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, abort, make_response,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Event
from app.services.auth_service import parse_datetime

events_bp = Blueprint('events', __name__, url_prefix='/events')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_htmx():
    return request.headers.get('HX-Request') == 'true'


def _event_or_404(event_id):
    """Return the event owned by current user or abort 404."""
    event = db.session.get(Event, event_id)
    if event is None or event.user_id != current_user.id:
        abort(404)
    return event


def _parse_event_form(form):
    """
    Parse and validate common event form fields.

    Returns (data_dict, errors_list).
    """
    errors = []

    title = form.get('title', '').strip()
    if not title:
        errors.append('Title is required.')

    # start_at is required
    start_date_str = form.get('start_date', '').strip()
    start_time_str = form.get('start_time', '').strip() or None
    start_at = None
    if not start_date_str:
        errors.append('Start date is required.')
    else:
        start_at = parse_datetime(start_date_str, start_time_str)
        if start_at is None:
            errors.append('Invalid date/time format for start date.')

    # end_at is optional
    end_date_str = form.get('end_date', '').strip()
    end_time_str = form.get('end_time', '').strip() or None
    end_at = None
    if end_date_str:
        end_at = parse_datetime(end_date_str, end_time_str)
        if end_at is None:
            errors.append('Invalid date/time format for end date.')
        elif start_at and end_at < start_at:
            errors.append('End date/time must be after start date/time.')

    location = form.get('location', '').strip()
    notes = form.get('notes', '').strip()

    data = {
        'title': title,
        'start_at': start_at,
        'end_at': end_at,
        'location': location or None,
        'notes': notes or None,
    }
    return data, errors


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@events_bp.route('/')
@login_required
def index():
    """
    List events with optional view filter.

    Query params:
      view – today | upcoming | all (default: all)
    """
    view = request.args.get('view', 'all').strip().lower()

    now = datetime.utcnow()
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = today_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)

    query = Event.query.filter_by(user_id=current_user.id)

    if view == 'today':
        query = query.filter(
            Event.start_at >= today_start,
            Event.start_at < today_end,
        )
    elif view == 'upcoming':
        query = query.filter(
            Event.start_at >= now,
            Event.start_at < week_end,
        )
    # else 'all' — no additional filter

    events = query.order_by(Event.start_at.asc()).all()

    if _is_htmx():
        return render_template(
            'partials/events_list.html',
            events=events,
            view=view,
        )

    return render_template(
        'events/index.html',
        events=events,
        view=view,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@events_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    """Render the create-event form (GET) or process submission (POST)."""
    if request.method == 'POST':
        data, errors = _parse_event_form(request.form)

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            if _is_htmx():
                return render_template(
                    'partials/event_form.html',
                    errors=errors,
                    form=request.form,
                ), 422
            return render_template('events/new.html', errors=errors, form=request.form), 422

        event = Event(
            user_id=current_user.id,
            title=data['title'],
            start_at=data['start_at'],
            end_at=data['end_at'],
            location=data['location'],
            notes=data['notes'],
        )
        db.session.add(event)
        db.session.commit()

        flash('Event created.', 'success')

        if _is_htmx():
            response = make_response(render_template('partials/event_item.html', event=event))
            response.headers['HX-Trigger'] = 'eventCreated'
            return response

        return redirect(url_for('events.index'))

    # GET
    if _is_htmx():
        return render_template('partials/event_form.html', form={})

    return render_template('events/new.html', form={})


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@events_bp.route('/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(event_id):
    """Edit an existing event."""
    event = _event_or_404(event_id)

    if request.method == 'POST':
        data, errors = _parse_event_form(request.form)

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            if _is_htmx():
                return render_template(
                    'partials/event_form.html',
                    event=event,
                    errors=errors,
                    form=request.form,
                ), 422
            return render_template(
                'events/edit.html',
                event=event,
                errors=errors,
                form=request.form,
            ), 422

        event.title = data['title']
        event.start_at = data['start_at']
        event.end_at = data['end_at']
        event.location = data['location']
        event.notes = data['notes']
        db.session.commit()

        flash('Event updated.', 'success')

        if _is_htmx():
            response = make_response(render_template('partials/event_item.html', event=event))
            response.headers['HX-Trigger'] = 'eventUpdated'
            return response

        return redirect(url_for('events.index'))

    # GET
    if _is_htmx():
        return render_template('partials/event_form.html', event=event, form=event)

    return render_template('events/edit.html', event=event, form=event)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@events_bp.route('/<int:event_id>/delete', methods=['POST'])
@login_required
def delete(event_id):
    """Permanently delete an event."""
    event = _event_or_404(event_id)
    db.session.delete(event)
    db.session.commit()

    flash('Event deleted.', 'info')

    if _is_htmx():
        response = make_response('')
        response.headers['HX-Trigger'] = 'eventDeleted'
        return response

    return redirect(request.referrer or url_for('events.index'))


# ---------------------------------------------------------------------------
# Convenience views
# ---------------------------------------------------------------------------

@events_bp.route('/today')
@login_required
def today():
    """Redirect to events list filtered to today's events."""
    return redirect(url_for('events.index', view='today'))


@events_bp.route('/upcoming')
@login_required
def upcoming():
    """Redirect to events list filtered to upcoming (next 7 days)."""
    return redirect(url_for('events.index', view='upcoming'))

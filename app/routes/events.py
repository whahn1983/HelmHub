"""
app/routes/events.py
~~~~~~~~~~~~~~~~~~~~~

Routes for calendar events.

Handles CRUD for local (DB-backed) events and merges read-only events
from ICS subscription feeds for display.  Supports both full-page and
HTMX partial responses.
"""

import logging
from collections import OrderedDict
from datetime import datetime, date, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, abort, make_response,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Event
from app.services.auth_service import parse_datetime

logger = logging.getLogger(__name__)

events_bp = Blueprint('events', __name__, url_prefix='/events')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_htmx() -> bool:
    return request.headers.get('HX-Request') == 'true'


def _event_or_404(event_id: int) -> Event:
    """Return the event owned by current user or abort 404."""
    event = db.session.get(Event, event_id)
    if event is None or event.user_id != current_user.id:
        abort(404)
    return event


def _parse_event_form(form) -> tuple[dict, list[str]]:
    """
    Parse and validate common event form fields.

    Returns ``(data_dict, errors_list)``.
    """
    errors: list[str] = []

    title = form.get('title', '').strip()
    if not title:
        errors.append('Title is required.')

    # start_at is required; accept either split fields or datetime-local value
    start_at_raw = form.get('start_at', '').strip()
    start_date_str = form.get('start_date', '').strip()
    start_time_str = form.get('start_time', '').strip() or None
    if start_at_raw and not start_date_str:
        start_date_str, _, start_time_str = start_at_raw.partition('T')
        start_time_str = start_time_str or None
    start_at = None
    if not start_date_str:
        errors.append('Start date is required.')
    else:
        start_at = parse_datetime(start_date_str, start_time_str)
        if start_at is None:
            errors.append('Invalid date/time format for start date.')

    # end_at is optional; accept either split fields or datetime-local value
    end_at_raw = form.get('end_at', '').strip()
    end_date_str = form.get('end_date', '').strip()
    end_time_str = form.get('end_time', '').strip() or None
    if end_at_raw and not end_date_str:
        end_date_str, _, end_time_str = end_at_raw.partition('T')
        end_time_str = end_time_str or None
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


def _group_events_by_date(events: list) -> OrderedDict:
    """
    Group a sorted list of events into an OrderedDict keyed by
    human-readable date label strings.

    Accepts both :class:`~app.models.Event` ORM objects and
    :class:`~app.services.calendar_subscriptions.SubscriptionEvent`
    dataclass instances — both expose a ``start_at`` datetime attribute.
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)

    groups: dict[date, list] = {}
    for event in events:
        event_date = event.start_at.date() if event.start_at else today
        groups.setdefault(event_date, []).append(event)

    result: OrderedDict = OrderedDict()
    for event_date in sorted(groups.keys()):
        if event_date == today:
            label = 'Today'
        elif event_date == tomorrow:
            label = 'Tomorrow'
        else:
            label = event_date.strftime('%A, %b ') + str(event_date.day)
        result[label] = groups[event_date]

    return result


def _merge_subscription_events(
    db_events: list[Event],
    view: str,
    now: datetime,
    today_start: datetime,
    today_end: datetime,
    week_end: datetime,
) -> list:
    """
    Fetch subscription events, apply the same view filter as the DB
    query, and return a merged time-sorted list.

    Errors from the subscription service are caught and logged so they
    never break the events page.
    """
    try:
        from app.services.calendar_subscriptions import (
            get_user_calendar_subscriptions,
            get_cached_subscription_events,
        )

        subscriptions = get_user_calendar_subscriptions(current_user.id)
        sub_events = []

        for sub in subscriptions:
            try:
                events = get_cached_subscription_events(sub)
                for ev in events:
                    if ev.start_at is None:
                        continue
                    if view == 'today':
                        if not (today_start <= ev.start_at < today_end):
                            continue
                    elif view == 'upcoming':
                        if not (ev.start_at >= now and ev.start_at < week_end):
                            continue
                    sub_events.append(ev)
            except Exception:
                logger.exception(
                    'Failed to get events for subscription %s', sub.id
                )

        merged = list(db_events) + sub_events
        merged.sort(key=lambda e: e.start_at or datetime.min)
        return merged

    except Exception:
        logger.exception('Subscription event merge failed; showing DB events only.')
        return list(db_events)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@events_bp.route('/')
@login_required
def index():
    """
    List events with optional view filter, merged with subscription events.

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

    db_events = query.order_by(Event.start_at.asc()).all()

    merged = _merge_subscription_events(
        db_events, view, now, today_start, today_end, week_end
    )
    grouped_events = _group_events_by_date(merged)

    if _is_htmx():
        return render_template(
            'partials/events_list.html',
            events=merged,
            grouped_events=grouped_events,
            view=view,
        )

    return render_template(
        'events/index.html',
        events=merged,
        grouped_events=grouped_events,
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
            return render_template('events/edit.html', errors=errors, form=request.form), 422

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

    return render_template('events/edit.html', event=None, form={})


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@events_bp.route('/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(event_id: int):
    """Edit an existing local event."""
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
def delete(event_id: int):
    """Permanently delete a local event."""
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

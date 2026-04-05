"""
app/routes/calendar_subscriptions.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Routes for managing ICS/iCal calendar subscription sources.

All routes require authentication and are scoped to the current user.
Remote event *instances* are never created or edited here — only the
subscription source configuration (name, URL, color, enabled, TTL).

URL prefix: /calendar-subscriptions  (registered by app factory)
"""

import logging

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, abort, make_response, current_app,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.calendar_subscription import CalendarSubscription
from app.services.calendar_subscriptions import (
    invalidate_cache,
    refresh_subscription_events_background,
    validate_caldav_url,
    validate_subscription_url,
)

logger = logging.getLogger(__name__)

cal_subs_bp = Blueprint(
    'cal_subs', __name__, url_prefix='/calendar-subscriptions'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_htmx() -> bool:
    return request.headers.get('HX-Request') == 'true'


def _sub_or_404(sub_id: int) -> CalendarSubscription:
    """Return the subscription owned by current_user or abort 404."""
    sub = db.session.get(CalendarSubscription, sub_id)
    if sub is None or sub.user_id != current_user.id:
        abort(404)
    return sub


def _parse_sub_form(
    form,
    existing_url: str = '',
    existing_caldav_password_enc: str = '',
) -> tuple[dict, list[str]]:
    """
    Parse and validate the add/edit subscription form.

    Parameters
    ----------
    form:
        The submitted form data (``request.form``).
    existing_url:
        For edit operations, the current URL stored in the database.
        When the form URL field is left blank, this value is kept
        instead of reporting a validation error.
    existing_caldav_password_enc:
        For CalDAV edit operations, the currently stored encrypted password.
        When the form password field is left blank, this value is kept.

    Returns ``(data_dict, errors_list)``.
    """
    errors: list[str] = []

    name = form.get('name', '').strip()
    if not name:
        errors.append('Name is required.')
    elif len(name) > 255:
        errors.append('Name must be 255 characters or fewer.')

    subscription_type = form.get('subscription_type', 'ics')
    if subscription_type not in ('ics', 'caldav'):
        subscription_type = 'ics'

    url = form.get('url', '').strip()
    if not url and existing_url:
        url = existing_url

    if subscription_type == 'caldav':
        url_error = validate_caldav_url(url)
    else:
        url_error = validate_subscription_url(url)
        # Normalise webcal:// → https://
        if url.lower().startswith('webcal://'):
            url = 'https://' + url[9:]
    if url_error:
        errors.append(url_error)

    # CalDAV credentials
    caldav_username = None
    caldav_password = None  # plaintext; caller encrypts before saving
    caldav_password_enc = existing_caldav_password_enc  # keep existing by default
    if subscription_type == 'caldav':
        caldav_username = form.get('caldav_username', '').strip() or None
        if caldav_username is None:
            errors.append('CalDAV username is required.')
        pwd_raw = form.get('caldav_password', '')
        if pwd_raw:
            # New password provided — will be encrypted by the route
            caldav_password = pwd_raw
            caldav_password_enc = None  # signal to route: use plaintext field
        elif not existing_caldav_password_enc:
            errors.append('CalDAV password is required.')

    color = form.get('color', '').strip() or None
    if color and len(color) > 32:
        errors.append('Color value must be 32 characters or fewer.')

    enabled = form.get('enabled') != 'off'   # checkbox: absent → True

    ttl_raw = form.get('cache_ttl_minutes', '').strip()
    cache_ttl_minutes = None
    if ttl_raw:
        try:
            cache_ttl_minutes = int(ttl_raw)
            if cache_ttl_minutes < 1 or cache_ttl_minutes > 10080:
                errors.append('Cache TTL must be between 1 and 10 080 minutes (1 week).')
                cache_ttl_minutes = None
        except ValueError:
            errors.append('Cache TTL must be a whole number of minutes.')

    data = {
        'name': name,
        'url': url,
        'subscription_type': subscription_type,
        'caldav_username': caldav_username,
        'caldav_password': caldav_password,       # plaintext or None
        'caldav_password_enc': caldav_password_enc,  # keep existing if no new pwd
        'color': color,
        'enabled': enabled,
        'cache_ttl_minutes': cache_ttl_minutes,
    }
    return data, errors


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@cal_subs_bp.route('/')
@login_required
def index():
    """List all calendar subscriptions for the current user."""
    subs = (
        CalendarSubscription.query
        .filter_by(user_id=current_user.id)
        .order_by(CalendarSubscription.name.asc())
        .all()
    )
    return render_template('calendar_subscriptions/index.html', subscriptions=subs)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@cal_subs_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    """Render the add-subscription form (GET) or create a subscription (POST)."""
    if request.method == 'POST':
        data, errors = _parse_sub_form(request.form)

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            return render_template(
                'calendar_subscriptions/edit.html',
                subscription=None,
                errors=errors,
                form=request.form,
            ), 422

        sub = CalendarSubscription(
            user_id=current_user.id,
            name=data['name'],
            url=data['url'],
            subscription_type=data['subscription_type'],
            caldav_username=data['caldav_username'],
            color=data['color'],
            enabled=data['enabled'],
            cache_ttl_minutes=data['cache_ttl_minutes'],
        )
        if data['caldav_password']:
            sub.caldav_password = data['caldav_password']
        db.session.add(sub)
        db.session.commit()

        flash(f'Subscription \u201c{sub.name}\u201d added.', 'success')
        return redirect(url_for('cal_subs.index'))

    return render_template(
        'calendar_subscriptions/edit.html',
        subscription=None,
        form={},
    )


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@cal_subs_bp.route('/<int:sub_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(sub_id: int):
    """Edit an existing calendar subscription."""
    sub = _sub_or_404(sub_id)

    if request.method == 'POST':
        data, errors = _parse_sub_form(
            request.form,
            existing_url=sub.url,
            existing_caldav_password_enc=sub.caldav_password_enc or '',
        )

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            return render_template(
                'calendar_subscriptions/edit.html',
                subscription=sub,
                errors=errors,
                form=request.form,
            ), 422

        url_changed = sub.url != data['url']

        sub.name = data['name']
        sub.url = data['url']
        sub.subscription_type = data['subscription_type']
        sub.caldav_username = data['caldav_username']
        sub.color = data['color']
        sub.enabled = data['enabled']
        sub.cache_ttl_minutes = data['cache_ttl_minutes']

        # Update password only when a new one was provided; otherwise keep
        # the existing encrypted value untouched.
        if data['caldav_password']:
            sub.caldav_password = data['caldav_password']
        elif data['subscription_type'] != 'caldav':
            # Switched away from CalDAV — clear credentials
            sub.caldav_username = None
            sub.caldav_password_enc = None

        db.session.commit()

        # Invalidate cache when URL changes so the new feed is fetched fresh
        if url_changed:
            invalidate_cache(sub.id)

        flash(f'Subscription \u201c{sub.name}\u201d updated.', 'success')
        return redirect(url_for('cal_subs.index'))

    return render_template(
        'calendar_subscriptions/edit.html',
        subscription=sub,
        form=sub,
    )


# ---------------------------------------------------------------------------
# Toggle enabled / disabled
# ---------------------------------------------------------------------------

@cal_subs_bp.route('/<int:sub_id>/toggle', methods=['POST'])
@login_required
def toggle(sub_id: int):
    """Toggle the enabled flag of a subscription."""
    sub = _sub_or_404(sub_id)
    sub.enabled = not sub.enabled
    db.session.commit()

    state = 'enabled' if sub.enabled else 'disabled'
    flash(f'Subscription \u201c{sub.name}\u201d {state}.', 'success')

    if _is_htmx():
        response = make_response(
            render_template(
                'partials/subscription_item.html', subscription=sub
            )
        )
        response.headers['HX-Trigger'] = 'subscriptionToggled'
        return response

    return redirect(url_for('cal_subs.index'))


# ---------------------------------------------------------------------------
# Manual refresh
# ---------------------------------------------------------------------------

@cal_subs_bp.route('/<int:sub_id>/refresh', methods=['POST'])
@login_required
def refresh(sub_id: int):
    """Trigger an asynchronous cache refresh for a subscription."""
    sub = _sub_or_404(sub_id)

    try:
        refresh_subscription_events_background(
            sub.id, current_app._get_current_object()
        )
        flash(
            f'Refreshing \u201c{sub.name}\u201d in the background \u2014 '
            'updated events will appear shortly.',
            'info',
        )
    except Exception:
        logger.exception('Failed to start background refresh for subscription %s', sub_id)
        flash(
            f'Could not start refresh for \u201c{sub.name}\u201d.',
            'danger',
        )

    return redirect(url_for('cal_subs.index'))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@cal_subs_bp.route('/<int:sub_id>/delete', methods=['POST'])
@login_required
def delete(sub_id: int):
    """Permanently delete a subscription and its cached events."""
    sub = _sub_or_404(sub_id)
    name = sub.name

    invalidate_cache(sub.id)
    db.session.delete(sub)
    db.session.commit()

    flash(f'Subscription \u201c{name}\u201d deleted.', 'info')

    if _is_htmx():
        response = make_response('')
        response.headers['HX-Trigger'] = 'subscriptionDeleted'
        return response

    return redirect(url_for('cal_subs.index'))

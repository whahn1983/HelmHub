"""
app/services/calendar_subscriptions.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Service module for ICS/iCal calendar subscription feeds.

Responsibilities
----------------
- Fetch remote ICS feeds via HTTP (server-side only)
- Parse ICS data into transient SubscriptionEvent objects
- Persist parsed subscription events in the database
- Serve stale persisted data when refresh fails
- Merge subscription events with local DB events for display

Architecture notes
------------------
Remote feeds are fetched in background worker threads and written to the
``subscription_events`` table.
"""

import ipaddress
import logging
import socket
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urlparse, urljoin

import requests
from flask import current_app

from app.extensions import db
from app.models.subscription_event import SubscriptionEvent as SubscriptionEventRow

logger = logging.getLogger(__name__)

_refresh_inflight: set[int] = set()
_refresh_inflight_lock = threading.Lock()

_DEFAULT_TTL_MINUTES = 30
_DEFAULT_TIMEOUT_SECONDS = 120
_DEFAULT_MAX_EVENTS = 500
_DEFAULT_LOOKAHEAD_DAYS = 60


# ---------------------------------------------------------------------------
# SubscriptionEvent — transient event object matching the Event model API
# ---------------------------------------------------------------------------

@dataclass
class SubscriptionEvent:
    """
    A read-only transient calendar event from an ICS subscription feed.

    Attribute names intentionally mirror those of the ``Event`` ORM model
    so that templates can handle both types uniformly.
    """

    id: str                               # synthetic key: "sub_{source_id}_{uid}"
    title: str
    start_at: Optional[datetime]
    end_at: Optional[datetime] = None
    location: Optional[str] = None
    notes: Optional[str] = None           # mirrors Event.notes

    source_type: str = 'subscription'
    source_id: int = 0
    source_name: str = ''
    color: Optional[str] = None
    all_day: bool = False
    read_only: bool = True

    # ---- Properties mirroring Event computed properties -----------------

    @property
    def is_today(self) -> bool:
        """True if the event starts on today's calendar date."""
        if not self.start_at:
            return False
        return self.start_at.date() == date.today()

    @property
    def is_upcoming(self) -> bool:
        """True if the event has not yet ended (or not yet started)."""
        now = datetime.utcnow()
        if self.end_at:
            return self.end_at > now
        return self.start_at > now if self.start_at else False

    @property
    def is_past(self) -> bool:
        """True if the event has ended."""
        now = datetime.utcnow()
        if self.end_at:
            return self.end_at < now
        return self.start_at < now if self.start_at else True

    @property
    def is_in_progress(self) -> bool:
        """True if the current time falls between start_at and end_at."""
        if not self.start_at or not self.end_at:
            return False
        now = datetime.utcnow()
        return self.start_at <= now <= self.end_at

    @property
    def is_now(self) -> bool:
        """Alias for is_in_progress (matches the template expectation)."""
        return self.is_in_progress

    @property
    def is_all_day(self) -> bool:
        """True when this is an all-day event."""
        return self.all_day

    @property
    def duration_minutes(self) -> Optional[int]:
        """Duration in whole minutes, or None when end_at is absent."""
        if not self.start_at or not self.end_at:
            return None
        delta = self.end_at - self.start_at
        return max(0, int(delta.total_seconds()) // 60)


# ---------------------------------------------------------------------------
# DB-backed storage helpers
# ---------------------------------------------------------------------------

def _read_cache(subscription_id: int) -> Optional[dict]:
    """Return persisted events + computed expiry metadata, or None."""
    rows = (
        SubscriptionEventRow.query
        .filter_by(subscription_id=subscription_id)
        .order_by(SubscriptionEventRow.start_at.asc())
        .all()
    )
    if not rows:
        return None

    events = [_row_to_event(row) for row in rows]
    fetched_at = rows[0].updated_at
    ttl = _get_ttl_from_row(subscription_id)
    expires_at = fetched_at + timedelta(minutes=ttl)
    return {
        'events': events,
        'fetched_at': fetched_at,
        'expires_at': expires_at,
        'success': True,
        'error': None,
    }


def _write_cache(subscription_id: int, entry: dict) -> None:
    """Persist an event list for a subscription."""
    from app.models.calendar_subscription import CalendarSubscription

    sub = db.session.get(CalendarSubscription, subscription_id)
    if sub is None:
        return

    SubscriptionEventRow.query.filter_by(subscription_id=subscription_id).delete()
    for ev in entry.get('events', []):
        db.session.add(
            SubscriptionEventRow(
                subscription_id=subscription_id,
                user_id=sub.user_id,
                external_id=ev.id,
                title=ev.title,
                start_at=ev.start_at,
                end_at=ev.end_at,
                location=ev.location,
                notes=ev.notes,
                all_day=ev.all_day,
                source_name=ev.source_name,
                color=ev.color,
            )
        )
    db.session.flush()


def invalidate_cache(subscription_id: int) -> None:
    """Delete persisted events for a subscription (e.g. after URL change)."""
    SubscriptionEventRow.query.filter_by(subscription_id=subscription_id).delete()
    db.session.commit()


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------

def fetch_calendar_feed(url: str) -> bytes:
    """
    Fetch a remote ICS feed and return the raw bytes.

    Raises ``requests.RequestException`` on network/HTTP errors and
    ``ValueError`` for obviously invalid responses.

    Parameters
    ----------
    url:
        The subscription URL.  ``webcal://`` scheme is normalised to
        ``https://`` automatically.
    """
    # Normalise webcal:// → https://
    if url.lower().startswith('webcal://'):
        url = 'https://' + url[9:]

    # SSRF guard: reject URLs resolving to private/internal hosts before
    # making any outbound request.
    _assert_ssrf_safe(url)

    timeout = current_app.config.get(
        'CALENDAR_SUBSCRIPTION_FETCH_TIMEOUT_SECONDS', _DEFAULT_TIMEOUT_SECONDS
    )

    headers = {
        'User-Agent': 'HelmHub-CalendarSubscription/1.0',
        'Accept': 'text/calendar, application/ics, */*',
    }

    # Follow redirects manually so every hop is checked for SSRF before the
    # next request is issued (hooks fire too late with allow_redirects=True).
    _MAX_REDIRECTS = 10
    current_url = url
    response = None
    for _ in range(_MAX_REDIRECTS + 1):
        response = requests.get(
            current_url, timeout=timeout, headers=headers, allow_redirects=False
        )
        if response.is_redirect:
            location = response.headers.get('Location', '')
            if not location:
                break
            current_url = urljoin(current_url, location)
            _assert_ssrf_safe(current_url)
        else:
            break
    else:
        raise requests.TooManyRedirects(f'Exceeded {_MAX_REDIRECTS} redirects for {url}')

    response.raise_for_status()

    content = response.content
    if not content:
        raise ValueError('Empty response from ICS feed.')

    # Sanity-check: ICS files should start with BEGIN:VCALENDAR
    # (allow for optional UTF-8 BOM)
    sample = content.lstrip(b'\xef\xbb\xbf').lstrip()[:20].upper()
    if not sample.startswith(b'BEGIN:VCALENDAR'):
        raise ValueError(
            'Response does not look like a valid ICS feed '
            '(missing BEGIN:VCALENDAR).'
        )

    return content


# ---------------------------------------------------------------------------
# ICS parsing
# ---------------------------------------------------------------------------

def _to_utc_naive(dt_value) -> tuple[datetime, bool]:
    """
    Convert an icalendar date or datetime value to a naive UTC datetime.

    Returns
    -------
    (datetime, is_all_day)
        A naive UTC ``datetime`` and a boolean indicating whether the
        original value was a date-only (all-day) value.
    """
    from datetime import date as date_type

    if isinstance(dt_value, date_type) and not isinstance(dt_value, datetime):
        # All-day: represents the calendar date with no time component
        return datetime(dt_value.year, dt_value.month, dt_value.day), True

    if isinstance(dt_value, datetime):
        if dt_value.tzinfo is not None:
            # Convert tz-aware → UTC → strip tzinfo (stdlib only, no pytz needed)
            from datetime import timezone as _tz
            utc_dt = dt_value.astimezone(_tz.utc).replace(tzinfo=None)
            return utc_dt, False
        # Naive datetime: assume UTC
        return dt_value.replace(tzinfo=None), False

    raise TypeError(f'Unexpected datetime type: {type(dt_value)!r}')


def _expand_rrule(
    dtstart: datetime,
    rrule_str: str,
    duration: Optional[timedelta],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, Optional[datetime]]]:
    """
    Expand an RRULE string into (start, end) pairs within the window.

    Falls back gracefully to returning the base occurrence if dateutil
    cannot parse the rule.
    """
    try:
        from dateutil.rrule import rrulestr

        rule = rrulestr(rrule_str, dtstart=dtstart, ignoretz=True)
        occurrences = list(rule.between(window_start, window_end, inc=True))
        if not occurrences and window_start <= dtstart <= window_end:
            occurrences = [dtstart]
        return [
            (occ, occ + duration if duration is not None else None)
            for occ in occurrences
        ]
    except Exception as exc:
        logger.warning('Could not expand RRULE %r: %s', rrule_str, exc)
        if window_start <= dtstart <= window_end:
            return [(dtstart, dtstart + duration if duration is not None else None)]
        return []


def parse_ics_events(
    raw_ics: bytes,
    subscription,
    lookahead_days: int = _DEFAULT_LOOKAHEAD_DAYS,
) -> list[SubscriptionEvent]:
    """
    Parse raw ICS bytes into a list of :class:`SubscriptionEvent` objects.

    Only events within the window [yesterday, now + lookahead_days] are
    returned.  Recurring events are expanded within that window.

    Parameters
    ----------
    raw_ics:
        Raw bytes of the ICS feed (as returned by ``fetch_calendar_feed``).
    subscription:
        The :class:`~app.models.CalendarSubscription` source row.
    lookahead_days:
        How many days ahead to expand recurring events.
    """
    try:
        from icalendar import Calendar
    except ImportError as exc:
        raise RuntimeError(
            'icalendar package is required for ICS parsing. '
            'Install it with: pip install icalendar'
        ) from exc

    try:
        cal = Calendar.from_ical(raw_ics)
    except Exception as exc:
        raise ValueError(f'Failed to parse ICS data: {exc}') from exc

    now_utc = datetime.utcnow()
    window_start = now_utc - timedelta(days=1)
    window_end = now_utc + timedelta(days=lookahead_days)

    events: list[SubscriptionEvent] = []
    seen_uids: set[str] = set()

    for component in cal.walk():
        if component.name != 'VEVENT':
            continue

        try:
            parsed = _parse_vevent(
                component, subscription, window_start, window_end
            )
            for ev in parsed:
                # De-duplicate by id (uid + occurrence start)
                if ev.id not in seen_uids:
                    seen_uids.add(ev.id)
                    events.append(ev)
        except Exception as exc:
            logger.warning(
                'Skipping unparseable VEVENT in subscription %s: %s',
                subscription.id, exc
            )

    events.sort(key=lambda e: e.start_at or datetime.min)
    return events


def _parse_vevent(
    component,
    subscription,
    window_start: datetime,
    window_end: datetime,
) -> list[SubscriptionEvent]:
    """
    Parse a single VEVENT component and return zero or more
    :class:`SubscriptionEvent` objects (multiple for recurring events).
    """
    dtstart_prop = component.get('DTSTART')
    if dtstart_prop is None:
        return []

    start_dt, all_day = _to_utc_naive(dtstart_prop.dt)

    # Parse end / duration
    dtend_prop = component.get('DTEND')
    duration_prop = component.get('DURATION')
    end_dt: Optional[datetime] = None
    duration: Optional[timedelta] = None

    if dtend_prop is not None:
        end_dt, _ = _to_utc_naive(dtend_prop.dt)
        duration = end_dt - start_dt
    elif duration_prop is not None:
        duration = duration_prop.dt  # icalendar returns timedelta directly
        end_dt = start_dt + duration
    # else: no end — point-in-time event

    # Core fields
    uid_raw = component.get('UID')
    uid = str(uid_raw) if uid_raw else f'no-uid-{start_dt.isoformat()}'

    title = str(component.get('SUMMARY', '')).strip() or '(No title)'
    description = str(component.get('DESCRIPTION', '')).strip() or None
    location = str(component.get('LOCATION', '')).strip() or None

    # Trim description to a reasonable length
    if description and len(description) > 2000:
        description = description[:2000] + '…'

    # Check for RRULE (recurring event)
    rrule_prop = component.get('RRULE')
    if rrule_prop is not None:
        rrule_str = rrule_prop.to_ical().decode('utf-8')
        occurrences = _expand_rrule(
            start_dt, rrule_str, duration, window_start, window_end
        )
        return [
            _make_event(
                uid=uid,
                occurrence_index=i,
                title=title,
                start=occ_start,
                end=occ_end,
                all_day=all_day,
                location=location,
                notes=description,
                subscription=subscription,
            )
            for i, (occ_start, occ_end) in enumerate(occurrences)
        ]

    # Non-recurring: check if it falls in the window
    if end_dt is not None and end_dt < window_start:
        return []
    if start_dt > window_end:
        return []

    return [
        _make_event(
            uid=uid,
            occurrence_index=0,
            title=title,
            start=start_dt,
            end=end_dt,
            all_day=all_day,
            location=location,
            notes=description,
            subscription=subscription,
        )
    ]


def _make_event(
    *,
    uid: str,
    occurrence_index: int,
    title: str,
    start: datetime,
    end: Optional[datetime],
    all_day: bool,
    location: Optional[str],
    notes: Optional[str],
    subscription,
) -> SubscriptionEvent:
    """Construct a :class:`SubscriptionEvent` from parsed fields."""
    synthetic_id = f'sub_{subscription.id}_{uid}'
    if occurrence_index:
        synthetic_id = f'{synthetic_id}_{occurrence_index}'

    return SubscriptionEvent(
        id=synthetic_id,
        title=title,
        start_at=start,
        end_at=end,
        location=location,
        notes=notes,
        source_type='subscription',
        source_id=subscription.id,
        source_name=subscription.name,
        color=subscription.color,
        all_day=all_day,
        read_only=True,
    )


# ---------------------------------------------------------------------------
# Cache-aware retrieval and refresh
# ---------------------------------------------------------------------------

def _get_ttl(subscription) -> int:
    """Return effective TTL in minutes for a subscription."""
    if subscription.cache_ttl_minutes:
        return subscription.cache_ttl_minutes
    return current_app.config.get(
        'CALENDAR_SUBSCRIPTION_DEFAULT_TTL_MINUTES', _DEFAULT_TTL_MINUTES
    )


def _get_ttl_from_row(subscription_id: int) -> int:
    """Lookup subscription row and return effective TTL in minutes."""
    from app.models.calendar_subscription import CalendarSubscription

    sub = db.session.get(CalendarSubscription, subscription_id)
    if sub is None:
        return current_app.config.get(
            'CALENDAR_SUBSCRIPTION_DEFAULT_TTL_MINUTES', _DEFAULT_TTL_MINUTES
        )
    return _get_ttl(sub)


def _row_to_event(row: SubscriptionEventRow) -> SubscriptionEvent:
    """Convert a persisted subscription event row to the view dataclass."""
    return SubscriptionEvent(
        id=row.external_id,
        title=row.title,
        start_at=row.start_at,
        end_at=row.end_at,
        location=row.location,
        notes=row.notes,
        source_type='subscription',
        source_id=row.subscription_id,
        source_name=row.source_name,
        color=row.color,
        all_day=row.all_day,
        read_only=True,
    )


def _update_db_status(
    subscription_id: int, status: str, error_msg: Optional[str]
) -> None:
    """
    Best-effort update of the subscription's status columns.

    Failures here are logged and swallowed so they never interrupt
    normal event display.
    """
    try:
        from app.extensions import db
        from app.models.calendar_subscription import CalendarSubscription

        sub = db.session.get(CalendarSubscription, subscription_id)
        if sub:
            sub.last_refresh_at = datetime.utcnow()
            sub.last_refresh_status = status
            sub.last_error = error_msg[:1000] if error_msg else None
            db.session.commit()
    except Exception:
        logger.exception(
            'Failed to update DB status for subscription %s', subscription_id
        )
        try:
            from app.extensions import db
            db.session.rollback()
        except Exception:
            pass


def refresh_subscription_events(
    subscription, force: bool = False
) -> list[SubscriptionEvent]:
    """
    Attempt to fetch and cache fresh events for *subscription*.

    If the cache is still fresh and ``force`` is False, the cached
    events are returned without a network request.

    On failure the stale cache is returned (if available) and the error
    is recorded in the subscription's status columns.

    Parameters
    ----------
    subscription:
        A :class:`~app.models.CalendarSubscription` instance.
    force:
        When True, bypass the TTL check and always re-fetch.
    """
    now = datetime.utcnow()
    entry = _read_cache(subscription.id)

    if not force and entry is not None and entry['expires_at'] > now:
        return entry['events']

    ttl = _get_ttl(subscription)
    lookahead = current_app.config.get(
        'CALENDAR_SUBSCRIPTION_LOOKAHEAD_DAYS', _DEFAULT_LOOKAHEAD_DAYS
    )
    max_events = current_app.config.get(
        'CALENDAR_SUBSCRIPTION_MAX_EVENTS', _DEFAULT_MAX_EVENTS
    )

    try:
        raw = fetch_calendar_feed(subscription.url)
        events = parse_ics_events(raw, subscription, lookahead_days=lookahead)
        events = events[:max_events]

        new_entry = {
            'events': events,
            'fetched_at': now,
            'expires_at': now + timedelta(minutes=ttl),
            'success': True,
            'error': None,
        }
        _write_cache(subscription.id, new_entry)
        _update_db_status(subscription.id, 'ok', None)
        return events

    except Exception as exc:
        err_msg = str(exc)
        logger.error(
            'Failed to refresh subscription %s (%r): %s',
            subscription.id, subscription.name, err_msg,
        )
        _update_db_status(subscription.id, 'error', err_msg)

        stale = _read_cache(subscription.id)
        if stale is not None:
            logger.info(
                'Serving stale cache for subscription %s (last fetched %s)',
                subscription.id, stale.get('fetched_at'),
            )
            return stale['events']

        return []


def get_cached_subscription_events(subscription) -> list[SubscriptionEvent]:
    """
    Return events for *subscription*, refreshing if the cache is stale.

    This is the main entry point for callers that want events for a
    single subscription.
    """
    return refresh_subscription_events(subscription, force=False)


def get_cached_events_stale_ok(subscription) -> list[SubscriptionEvent]:
    """
    Return cached events without triggering a network request.

    Returns the cached list even if it is stale (expired).  Returns an
    empty list when no cache entry exists at all.  This is safe to call
    from latency-sensitive paths such as the dashboard.
    """
    entry = _read_cache(subscription.id)
    if entry is not None:
        return entry['events']
    return []


def get_cached_events_or_refresh_on_miss(subscription) -> list[SubscriptionEvent]:
    """
    Return cached events from DB without triggering a synchronous refresh.

    Callers should schedule background refreshes when :func:`is_cache_stale`
    is true.
    """
    entry = _read_cache(subscription.id)
    if entry is not None:
        return entry['events']
    return []


def is_cache_stale(subscription) -> bool:
    """Return True when persisted events are absent or past refresh TTL."""
    if subscription.last_refresh_at is None:
        return True
    ttl = _get_ttl(subscription)
    expires_at = subscription.last_refresh_at + timedelta(minutes=ttl)
    return expires_at <= datetime.utcnow()


def refresh_subscription_events_background(subscription_id: int, app) -> None:
    """
    Spawn a daemon thread to refresh a subscription's cache.

    Duplicate refreshes for the same subscription are coalesced so repeated
    requests cannot create a thread storm while an existing refresh is active.

    The caller must pass the concrete Flask application object (not a
    proxy), e.g.::

        refresh_subscription_events_background(
            sub.id, current_app._get_current_object()
        )

    The thread pushes its own application context so all Flask/SQLAlchemy
    helpers work correctly outside the request lifecycle.
    """
    with _refresh_inflight_lock:
        if subscription_id in _refresh_inflight:
            return
        _refresh_inflight.add(subscription_id)

    def _worker() -> None:
        try:
            with app.app_context():
                from app.models.calendar_subscription import CalendarSubscription  # noqa: PLC0415
                from app.extensions import db  # noqa: PLC0415
                sub = db.session.get(CalendarSubscription, subscription_id)
                if sub:
                    refresh_subscription_events(sub, force=True)
        finally:
            with _refresh_inflight_lock:
                _refresh_inflight.discard(subscription_id)

    thread = threading.Thread(
        target=_worker,
        daemon=True,
        name=f'cal-refresh-{subscription_id}',
    )
    thread.start()


# ---------------------------------------------------------------------------
# Multi-subscription helpers
# ---------------------------------------------------------------------------

def get_user_calendar_subscriptions(user_id: int) -> list:
    """
    Return all enabled CalendarSubscription rows for *user_id*.

    Ordered by name ascending.
    """
    from app.models.calendar_subscription import CalendarSubscription

    return (
        CalendarSubscription.query
        .filter_by(user_id=user_id, enabled=True)
        .order_by(CalendarSubscription.name.asc())
        .all()
    )


def get_all_display_events_for_user(
    user,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list:
    """
    Return a merged, time-sorted list of local DB events and subscription
    events for *user*.

    Local events are :class:`~app.models.Event` ORM objects.
    Subscription events are :class:`SubscriptionEvent` dataclass instances.

    Both expose the same attribute names used by the event templates
    (``title``, ``start_at``, ``end_at``, ``location``, ``notes``,
    ``is_all_day``, ``read_only``, ``source_type``, etc.).

    Parameters
    ----------
    user:
        The authenticated :class:`~app.models.User`.
    start, end:
        Optional datetime bounds.  Events outside this range are
        excluded.  When omitted, all cached events are returned.
    """
    from app.models.event import Event

    # ------------------------------------------------------------------
    # 1. Local DB events
    # ------------------------------------------------------------------
    query = Event.query.filter_by(user_id=user.id)
    if start:
        query = query.filter(Event.start_at >= start)
    if end:
        query = query.filter(Event.start_at < end)
    db_events = query.order_by(Event.start_at.asc()).all()

    # ------------------------------------------------------------------
    # 2. Subscription events
    # ------------------------------------------------------------------
    sub_events: list[SubscriptionEvent] = []
    subscriptions = get_user_calendar_subscriptions(user.id)

    _app = current_app._get_current_object()
    for sub in subscriptions:
        try:
            events = get_cached_events_stale_ok(sub)
            if is_cache_stale(sub):
                refresh_subscription_events_background(sub.id, _app)
            for ev in events:
                if start and ev.start_at and ev.start_at < start:
                    continue
                if end and ev.start_at and ev.start_at >= end:
                    continue
                sub_events.append(ev)
        except Exception:
            logger.exception(
                'Unexpected error retrieving events for subscription %s', sub.id
            )

    # ------------------------------------------------------------------
    # 3. Merge and sort
    # ------------------------------------------------------------------
    all_events: list = list(db_events) + sub_events
    all_events.sort(key=lambda e: e.start_at or datetime.min)
    return all_events


# ---------------------------------------------------------------------------
# SSRF protection helpers
# ---------------------------------------------------------------------------

def _is_private_ip(ip_str: str) -> bool:
    """Return True if the IP address is private, loopback, link-local, or otherwise reserved."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        )
    except ValueError:
        return True  # Fail safe: treat unparseable as unsafe


def _host_resolves_to_private(hostname: str) -> bool:
    """
    Resolve *hostname* and return True if ANY resolved address is private/internal.

    Returns True (unsafe) on DNS failures to fail closed.
    """
    try:
        results = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        if not results:
            return True
        for result in results:
            if _is_private_ip(result[4][0]):
                return True
        return False
    except socket.gaierror:
        return True  # Unresolvable host — block


def _assert_ssrf_safe(url: str) -> None:
    """
    Raise ``ValueError`` if *url* targets a private/internal host.

    Should be called immediately before any server-side HTTP request.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ''
    except Exception:
        raise ValueError('Invalid URL.')

    if not hostname:
        raise ValueError('URL has no hostname.')

    if _host_resolves_to_private(hostname):
        raise ValueError(
            f'URL hostname "{hostname}" resolves to a private or internal address.'
        )


# ---------------------------------------------------------------------------
# URL validation helper
# ---------------------------------------------------------------------------

def validate_subscription_url(url: str) -> Optional[str]:
    """
    Validate that *url* is an acceptable ICS subscription URL.

    Returns an error message string on failure, or ``None`` on success.
    """
    if not url:
        return 'URL is required.'

    normalised = url.strip()
    if normalised.lower().startswith('webcal://'):
        normalised = 'https://' + normalised[9:]

    try:
        parsed = urlparse(normalised)
    except Exception:
        return 'URL could not be parsed.'

    if parsed.scheme not in ('http', 'https'):
        return 'URL must use http://, https://, or webcal:// scheme.'

    if not parsed.netloc:
        return 'URL must include a hostname.'

    hostname = parsed.hostname or ''
    if _host_resolves_to_private(hostname):
        return 'URL must not point to a private or internal network address.'

    return None

"""CalDAV integration built on top of the ``caldav`` package."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

from flask import current_app


@dataclass
class CaldavResolutionResult:
    calendar: object
    resolved_calendar_url: Optional[str] = None
    principal_url: Optional[str] = None
    calendar_name: Optional[str] = None


@dataclass
class CaldavFetchResult:
    events: list
    source_last_modified: Optional[datetime]
    item_count_retrieved: int
    detail: str
    resolved_calendar_url: Optional[str] = None
    principal_url: Optional[str] = None
    calendar_name: Optional[str] = None


def _import_caldav() -> tuple[object, object]:
    """Import caldav lazily so the app can still boot without the package."""
    try:
        import caldav
        from caldav.objects import Calendar
    except ImportError as exc:
        raise RuntimeError(
            'CalDAV support requires the "caldav" package to be installed.'
        ) from exc
    return caldav, Calendar


def build_caldav_client(subscription):
    """Create an authenticated ``caldav.DAVClient`` for a subscription."""
    caldav, _ = _import_caldav()

    timeout = current_app.config.get(
        'CALENDAR_SUBSCRIPTION_FETCH_TIMEOUT_SECONDS', 120
    )
    return caldav.DAVClient(
        url=subscription.url,
        username=subscription.caldav_username or '',
        password=subscription.caldav_password or '',
        timeout=timeout,
    )


def _looks_like_calendar_collection_url(url: str) -> bool:
    """Best-effort check for direct Nextcloud-style calendar collection URLs."""
    path = (urlparse(url).path or '').lower()
    return '/calendars/' in path


def resolve_caldav_calendar(subscription, client=None) -> CaldavResolutionResult:
    """Resolve the concrete calendar object using either direct URL or discovery."""
    _, Calendar = _import_caldav()

    client = client or build_caldav_client(subscription)
    configured_url = (subscription.url or '').strip()

    if _looks_like_calendar_collection_url(configured_url):
        calendar = Calendar(client=client, url=configured_url)
        return CaldavResolutionResult(
            calendar=calendar,
            resolved_calendar_url=getattr(calendar, 'url', configured_url),
        )

    principal = client.principal()
    calendars = list(principal.calendars() or [])
    if not calendars:
        raise ValueError('No calendars were discovered for this account.')

    preferred_name = (getattr(subscription, 'name', '') or '').strip().lower()

    selected = None
    for cal in calendars:
        cal_url = str(getattr(cal, 'url', '') or '')
        if cal_url.rstrip('/') == configured_url.rstrip('/'):
            selected = cal
            break

    if selected is None and preferred_name:
        for cal in calendars:
            cal_name = str(getattr(cal, 'name', '') or '').strip().lower()
            if cal_name == preferred_name:
                selected = cal
                break

    if selected is None:
        selected = calendars[0]

    return CaldavResolutionResult(
        calendar=selected,
        resolved_calendar_url=str(getattr(selected, 'url', '') or configured_url),
        principal_url=str(getattr(principal, 'url', '') or ''),
        calendar_name=str(getattr(selected, 'name', '') or ''),
    )


def normalize_caldav_event(event, subscription, lookahead_days: int) -> list:
    """Normalize a single caldav Event object into HelmHub SubscriptionEvents."""
    from app.services.calendar_subscriptions import parse_ics_events

    raw_data = getattr(event, 'data', None)
    if not raw_data:
        return []

    raw_bytes = raw_data.encode('utf-8') if isinstance(raw_data, str) else raw_data
    return parse_ics_events(raw_bytes, subscription, lookahead_days=lookahead_days)


def fetch_caldav_events(
    subscription,
    start: datetime,
    end: datetime,
    lookahead_days: int,
    *,
    client=None,
) -> CaldavFetchResult:
    """Fetch and normalize CalDAV events in a date window using ``caldav``."""
    resolution = resolve_caldav_calendar(subscription, client=client)
    calendar = resolution.calendar

    search_kwargs = {'start': start, 'end': end}
    try:
        raw_objects = list(calendar.date_search(**search_kwargs))
    except TypeError:
        # Compatibility with older/newer caldav versions.
        raw_objects = list(calendar.search(**search_kwargs))

    seen_ids: set[str] = set()
    events: list = []
    for obj in raw_objects:
        try:
            normalized = normalize_caldav_event(obj, subscription, lookahead_days)
        except Exception:
            continue
        for parsed in normalized:
            if parsed.id in seen_ids:
                continue
            seen_ids.add(parsed.id)
            events.append(parsed)

    events.sort(key=lambda e: e.start_at or datetime.min)

    if events:
        detail = f'OK — {len(events)} events imported'
    else:
        detail = 'Warning — calendar resolved but no events in time window'

    return CaldavFetchResult(
        events=events,
        source_last_modified=None,
        item_count_retrieved=len(raw_objects),
        detail=detail,
        resolved_calendar_url=resolution.resolved_calendar_url,
        principal_url=resolution.principal_url,
        calendar_name=resolution.calendar_name,
    )


def refresh_caldav_subscription(subscription, lookahead_days: int) -> CaldavFetchResult:
    """End-to-end CalDAV refresh helper used by the calendar subscription service."""
    now_utc = datetime.utcnow()
    window_start = now_utc
    window_end = now_utc + timedelta(days=lookahead_days)
    return fetch_caldav_events(
        subscription,
        start=window_start,
        end=window_end,
        lookahead_days=lookahead_days,
    )

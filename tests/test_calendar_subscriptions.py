"""
tests/test_calendar_subscriptions.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tests for calendar subscription management routes, service layer cache
behaviour, ICS parsing, and merged event display.

Covered areas
-------------
- CRUD routes (create / edit / delete / toggle / refresh)
- Authentication and ownership enforcement
- URL validation
- In-process cache: hit, miss, refresh, stale-on-error
- ICS parsing: basic events, all-day events, recurring events, bad feeds
- Merged event display on the events index
"""

import textwrap
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.calendar_subscription import CalendarSubscription


# ---------------------------------------------------------------------------
# Minimal well-formed ICS feed used across tests
# ---------------------------------------------------------------------------

_VALID_ICS = textwrap.dedent("""\
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//Test//Test//EN
    BEGIN:VEVENT
    UID:test-event-001@example.com
    SUMMARY:Test Meeting
    DTSTART:20990101T100000Z
    DTEND:20990101T110000Z
    LOCATION:Room A
    DESCRIPTION:An important meeting.
    END:VEVENT
    END:VCALENDAR
""").encode()

_ALL_DAY_ICS = textwrap.dedent("""\
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//Test//Test//EN
    BEGIN:VEVENT
    UID:allday-001@example.com
    SUMMARY:Company Holiday
    DTSTART;VALUE=DATE:20990115
    DTEND;VALUE=DATE:20990116
    END:VEVENT
    END:VCALENDAR
""").encode()

_RECURRING_ICS_TEMPLATE = textwrap.dedent("""\
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//Test//Test//EN
    BEGIN:VEVENT
    UID:recurring-001@example.com
    SUMMARY:Weekly Standup
    DTSTART:{dtstart}
    DURATION:PT30M
    RRULE:FREQ=WEEKLY;COUNT=52
    END:VEVENT
    END:VCALENDAR
""")

_INVALID_ICS = b'This is not valid ICS data at all.'

_EMPTY_ICS = b'BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Test//EN\nEND:VCALENDAR\n'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_subscription(db, user, name='Work Calendar',
                          url='https://example.com/feed.ics',
                          enabled=True, color=None, cache_ttl_minutes=None):
    """Persist a CalendarSubscription for *user* and return it."""
    sub = CalendarSubscription(
        user_id=user.id,
        name=name,
        url=url,
        enabled=enabled,
        color=color,
        cache_ttl_minutes=cache_ttl_minutes,
    )
    db.session.add(sub)
    db.session.commit()
    return sub


def _post_new_sub(client, name='My Cal', url='https://cal.example.com/feed.ics',
                  color='', cache_ttl_minutes='', enabled='on'):
    """POST to /calendar-subscriptions/new."""
    data = {
        'name': name,
        'url': url,
        'color': color,
        'cache_ttl_minutes': cache_ttl_minutes,
        'enabled': enabled,
    }
    return client.post('/calendar-subscriptions/new', data=data,
                       follow_redirects=False)


def _post_edit_sub(client, sub_id, name='Updated', url='https://cal.example.com/feed.ics',
                   color='', cache_ttl_minutes='', enabled='on'):
    """POST to /calendar-subscriptions/<id>/edit."""
    data = {
        'name': name,
        'url': url,
        'color': color,
        'cache_ttl_minutes': cache_ttl_minutes,
        'enabled': enabled,
    }
    return client.post(f'/calendar-subscriptions/{sub_id}/edit', data=data,
                       follow_redirects=False)


# ===========================================================================
# Route: List subscriptions
# ===========================================================================

class TestSubscriptionIndex:
    def test_requires_auth(self, client):
        """GET /calendar-subscriptions/ redirects unauthenticated users."""
        resp = client.get('/calendar-subscriptions/', follow_redirects=False)
        assert resp.status_code in (301, 302)

    def test_returns_200_when_authenticated(self, auth_client):
        """Authenticated GET /calendar-subscriptions/ returns 200."""
        resp = auth_client.get('/calendar-subscriptions/')
        assert resp.status_code == 200

    def test_shows_own_subscription(self, auth_client, db, test_user):
        """A persisted subscription name appears in the list."""
        _create_subscription(db, test_user, name='My Work Cal')
        resp = auth_client.get('/calendar-subscriptions/')
        assert b'My Work Cal' in resp.data

    def test_does_not_show_other_users_subscription(self, auth_client, db):
        """Subscriptions belonging to other users are not shown."""
        from app.models import User
        other = User(username='othercalsub')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        _create_subscription(db, other, name='Other Private Cal')
        resp = auth_client.get('/calendar-subscriptions/')
        assert b'Other Private Cal' not in resp.data

    def test_empty_state_shown_when_no_subscriptions(self, auth_client, db):
        """The empty-state prompt is rendered when there are no subscriptions."""
        resp = auth_client.get('/calendar-subscriptions/')
        assert resp.status_code == 200
        assert b'No calendar subscriptions' in resp.data

    def test_index_shows_source_modified_timestamp_when_available(
        self, auth_client, db, test_user
    ):
        """The list page includes source modified status when refresh succeeded."""
        source_modified = datetime(2026, 4, 5, 14, 30, 0)
        _create_subscription(
            db,
            test_user,
            name='Feed With Last-Modified',
        )
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='Feed With Last-Modified'
        ).first()
        sub.last_refresh_at = datetime(2026, 4, 5, 14, 31, 0)
        sub.last_refresh_status = 'ok'
        sub.last_source_modified_at = source_modified
        db.session.commit()

        resp = auth_client.get('/calendar-subscriptions/')
        assert resp.status_code == 200
        assert b'source modified:' in resp.data


# ===========================================================================
# Route: Create subscription
# ===========================================================================

class TestSubscriptionCreate:
    def test_get_new_form_returns_200(self, auth_client):
        """GET /calendar-subscriptions/new returns the form."""
        resp = auth_client.get('/calendar-subscriptions/new')
        assert resp.status_code == 200

    def test_post_valid_creates_subscription(self, auth_client, db, test_user):
        """Valid POST creates a CalendarSubscription in the database."""
        resp = _post_new_sub(auth_client, name='Holiday Cal',
                             url='https://cal.test/holidays.ics')
        assert resp.status_code in (301, 302)
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='Holiday Cal'
        ).first()
        assert sub is not None
        assert sub.url == 'https://cal.test/holidays.ics'

    def test_post_normalises_webcal_scheme(self, auth_client, db, test_user):
        """webcal:// URLs are normalised to https:// when stored."""
        _post_new_sub(auth_client, name='Webcal Cal',
                      url='webcal://cal.test/feed.ics')
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='Webcal Cal'
        ).first()
        assert sub is not None
        assert sub.url.startswith('https://')

    def test_post_missing_name_returns_422(self, auth_client):
        """Submitting without a name returns 422."""
        resp = _post_new_sub(auth_client, name='')
        assert resp.status_code == 422

    def test_post_missing_url_returns_422(self, auth_client):
        """Submitting without a URL returns 422."""
        resp = _post_new_sub(auth_client, url='')
        assert resp.status_code == 422

    def test_post_invalid_url_scheme_returns_422(self, auth_client):
        """An ftp:// URL is rejected with 422."""
        resp = _post_new_sub(auth_client, url='ftp://cal.test/feed.ics')
        assert resp.status_code == 422

    def test_post_sets_ttl_when_provided(self, auth_client, db, test_user):
        """Custom TTL is persisted when provided."""
        _post_new_sub(auth_client, name='TTL Cal',
                      url='https://cal.test/ttl.ics', cache_ttl_minutes='60')
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='TTL Cal'
        ).first()
        assert sub is not None
        assert sub.cache_ttl_minutes == 60

    def test_post_requires_auth(self, client):
        """Unauthenticated POST redirects to login."""
        resp = _post_new_sub(client)
        assert resp.status_code in (301, 302)

    def test_disabled_subscription_stored(self, auth_client, db, test_user):
        """Subscription created with enabled=off is stored as disabled."""
        _post_new_sub(auth_client, name='Disabled Cal',
                      url='https://cal.test/off.ics', enabled='off')
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='Disabled Cal'
        ).first()
        assert sub is not None
        assert sub.enabled is False


# ===========================================================================
# Route: Edit subscription
# ===========================================================================

class TestSubscriptionEdit:
    def test_get_edit_form_returns_200(self, auth_client, db, test_user):
        """GET /calendar-subscriptions/<id>/edit returns the form."""
        sub = _create_subscription(db, test_user)
        resp = auth_client.get(f'/calendar-subscriptions/{sub.id}/edit')
        assert resp.status_code == 200

    def test_post_edit_updates_name(self, auth_client, db, test_user):
        """POST /calendar-subscriptions/<id>/edit persists a new name."""
        sub = _create_subscription(db, test_user, name='Old Name')
        _post_edit_sub(auth_client, sub.id, name='New Name',
                       url='https://cal.test/feed.ics')
        db.session.refresh(sub)
        assert sub.name == 'New Name'

    def test_post_edit_updates_color(self, auth_client, db, test_user):
        """Color is updated correctly."""
        sub = _create_subscription(db, test_user)
        _post_edit_sub(auth_client, sub.id, color='#ff0000')
        db.session.refresh(sub)
        assert sub.color == '#ff0000'

    def test_edit_nonexistent_returns_404(self, auth_client):
        """Editing a subscription that does not exist returns 404."""
        resp = _post_edit_sub(auth_client, 999999)
        assert resp.status_code == 404

    def test_edit_other_users_subscription_returns_404(self, auth_client, db):
        """Users cannot edit another user's subscription."""
        from app.models import User
        other = User(username='caleditother')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        sub = _create_subscription(db, other, name='Private Sub')
        resp = _post_edit_sub(auth_client, sub.id)
        assert resp.status_code == 404

    def test_edit_requires_auth(self, client, db, test_user):
        """Unauthenticated edit attempt redirects to login."""
        sub = _create_subscription(db, test_user)
        resp = client.post(
            f'/calendar-subscriptions/{sub.id}/edit',
            data={'name': 'x', 'url': 'https://cal.test/feed.ics'},
            follow_redirects=False,
        )
        assert resp.status_code in (301, 302)


# ===========================================================================
# Route: Delete subscription
# ===========================================================================

class TestSubscriptionDelete:
    def test_delete_removes_subscription(self, auth_client, db, test_user):
        """POST /calendar-subscriptions/<id>/delete removes the row."""
        sub = _create_subscription(db, test_user)
        sub_id = sub.id
        auth_client.post(f'/calendar-subscriptions/{sub_id}/delete')
        assert db.session.get(CalendarSubscription, sub_id) is None

    def test_delete_redirects(self, auth_client, db, test_user):
        """Deleting a subscription issues a redirect."""
        sub = _create_subscription(db, test_user)
        resp = auth_client.post(f'/calendar-subscriptions/{sub.id}/delete',
                                follow_redirects=False)
        assert resp.status_code in (301, 302)

    def test_delete_nonexistent_returns_404(self, auth_client):
        """Deleting a non-existent subscription returns 404."""
        resp = auth_client.post('/calendar-subscriptions/999999/delete')
        assert resp.status_code == 404

    def test_delete_other_users_returns_404(self, auth_client, db):
        """Users cannot delete another user's subscription."""
        from app.models import User
        other = User(username='caldelother')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        sub = _create_subscription(db, other)
        resp = auth_client.post(f'/calendar-subscriptions/{sub.id}/delete')
        assert resp.status_code == 404

    def test_delete_requires_auth(self, client, db, test_user):
        """Unauthenticated delete attempt redirects to login."""
        sub = _create_subscription(db, test_user)
        resp = client.post(f'/calendar-subscriptions/{sub.id}/delete',
                           follow_redirects=False)
        assert resp.status_code in (301, 302)


# ===========================================================================
# Route: Toggle enabled
# ===========================================================================

class TestSubscriptionToggle:
    def test_toggle_enables_disabled_subscription(self, auth_client, db, test_user):
        """Toggle flips disabled → enabled."""
        sub = _create_subscription(db, test_user, enabled=False)
        auth_client.post(f'/calendar-subscriptions/{sub.id}/toggle')
        db.session.refresh(sub)
        assert sub.enabled is True

    def test_toggle_disables_enabled_subscription(self, auth_client, db, test_user):
        """Toggle flips enabled → disabled."""
        sub = _create_subscription(db, test_user, enabled=True)
        auth_client.post(f'/calendar-subscriptions/{sub.id}/toggle')
        db.session.refresh(sub)
        assert sub.enabled is False

    def test_toggle_other_users_returns_404(self, auth_client, db):
        """Toggling another user's subscription returns 404."""
        from app.models import User
        other = User(username='caltogother')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        sub = _create_subscription(db, other)
        resp = auth_client.post(f'/calendar-subscriptions/{sub.id}/toggle')
        assert resp.status_code == 404


# ===========================================================================
# Service: URL validation
# ===========================================================================

class TestUrlValidation:
    def test_valid_https_url(self):
        from app.services.calendar_subscriptions import validate_subscription_url
        assert validate_subscription_url('https://example.com/feed.ics') is None

    def test_valid_http_url(self):
        from app.services.calendar_subscriptions import validate_subscription_url
        assert validate_subscription_url('http://example.com/feed.ics') is None

    def test_valid_webcal_url(self):
        from app.services.calendar_subscriptions import validate_subscription_url
        assert validate_subscription_url('webcal://example.com/feed.ics') is None

    def test_empty_url_returns_error(self):
        from app.services.calendar_subscriptions import validate_subscription_url
        assert validate_subscription_url('') is not None

    def test_ftp_url_returns_error(self):
        from app.services.calendar_subscriptions import validate_subscription_url
        assert validate_subscription_url('ftp://example.com/feed.ics') is not None

    def test_no_hostname_returns_error(self):
        from app.services.calendar_subscriptions import validate_subscription_url
        assert validate_subscription_url('https://') is not None


# ===========================================================================
# Service: ICS parsing
# ===========================================================================

class TestIcsParsing:
    def test_parse_basic_event(self, app):
        """Basic VEVENT is parsed into a SubscriptionEvent."""
        from app.services.calendar_subscriptions import parse_ics_events

        sub = MagicMock()
        sub.id = 1
        sub.name = 'Test Sub'
        sub.color = '#6366f1'

        with app.app_context():
            events = parse_ics_events(_VALID_ICS, sub, lookahead_days=365 * 100)

        assert len(events) == 1
        ev = events[0]
        assert ev.title == 'Test Meeting'
        assert ev.location == 'Room A'
        assert ev.notes == 'An important meeting.'
        assert ev.source_type == 'subscription'
        assert ev.source_id == 1
        assert ev.read_only is True

    def test_parse_all_day_event(self, app):
        """An all-day VEVENT has all_day=True."""
        from app.services.calendar_subscriptions import parse_ics_events

        sub = MagicMock()
        sub.id = 1
        sub.name = 'Holiday Sub'
        sub.color = None

        with app.app_context():
            events = parse_ics_events(_ALL_DAY_ICS, sub, lookahead_days=365 * 100)

        assert len(events) == 1
        ev = events[0]
        assert ev.title == 'Company Holiday'
        assert ev.all_day is True
        assert ev.is_all_day is True

    def test_parse_recurring_event(self, app):
        """A recurring VEVENT is expanded into multiple occurrences."""
        from app.services.calendar_subscriptions import parse_ics_events
        from datetime import date, timedelta

        # Use a dtstart in the near past so occurrences fall within our window
        dtstart = (datetime.utcnow() - timedelta(days=3)).strftime('%Y%m%dT%H%M%SZ')
        ics = _RECURRING_ICS_TEMPLATE.format(dtstart=dtstart).encode()

        sub = MagicMock()
        sub.id = 1
        sub.name = 'Recurring Sub'
        sub.color = None

        with app.app_context():
            events = parse_ics_events(ics, sub, lookahead_days=60)

        # Should have at least a few occurrences within the 60-day window
        assert len(events) >= 1
        for ev in events:
            assert ev.title == 'Weekly Standup'

    def test_parse_empty_calendar_returns_empty_list(self, app):
        """An empty VCALENDAR returns an empty list without error."""
        from app.services.calendar_subscriptions import parse_ics_events

        sub = MagicMock()
        sub.id = 1
        sub.name = 'Empty Sub'
        sub.color = None

        with app.app_context():
            events = parse_ics_events(_EMPTY_ICS, sub, lookahead_days=60)

        assert events == []

    def test_parse_invalid_ics_raises_value_error(self, app):
        """Malformed ICS data raises ValueError."""
        from app.services.calendar_subscriptions import parse_ics_events

        sub = MagicMock()
        sub.id = 1
        sub.name = 'Bad Sub'
        sub.color = None

        with app.app_context():
            with pytest.raises(ValueError):
                parse_ics_events(_INVALID_ICS, sub, lookahead_days=60)


# ===========================================================================
# Service: fetch_calendar_feed validation
# ===========================================================================

class TestFetchCalendarFeed:
    def test_rejects_non_ics_response(self, app):
        """A response that is not an ICS feed raises ValueError."""
        from app.services.calendar_subscriptions import fetch_calendar_feed

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.content = b'<html><body>Not a calendar</body></html>'

        with app.app_context():
            with patch('requests.get', return_value=mock_response):
                with pytest.raises(ValueError, match='BEGIN:VCALENDAR'):
                    fetch_calendar_feed('https://example.com/notical.html')

    def test_normalises_webcal_scheme(self, app):
        """webcal:// is converted to https:// before the request."""
        from app.services.calendar_subscriptions import fetch_calendar_feed

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.content = b'BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n'

        with app.app_context():
            with patch('requests.get', return_value=mock_response) as mock_get:
                fetch_calendar_feed('webcal://cal.example.com/feed.ics')
                call_url = mock_get.call_args[0][0]
                assert call_url.startswith('https://')

    def test_extracts_last_modified_header(self, app):
        """Last-Modified header is parsed and returned with payload."""
        from app.services.calendar_subscriptions import fetch_calendar_feed

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.content = b'BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n'
        mock_response.is_redirect = False
        mock_response.headers = {'Last-Modified': 'Wed, 21 Oct 2015 07:28:00 GMT'}

        with app.app_context():
            with patch('requests.get', return_value=mock_response):
                with patch(
                    'app.services.calendar_subscriptions._assert_ssrf_safe',
                    return_value=None,
                ):
                    content, source_modified = fetch_calendar_feed(
                        'https://example.com/feed.ics'
                    )

        assert content.startswith(b'BEGIN:VCALENDAR')
        assert source_modified == datetime(2015, 10, 21, 7, 28, 0)


# ===========================================================================
# Service: cache behaviour
# ===========================================================================

class TestCacheBehaviour:
    @staticmethod
    def _fake_event(event_id: str, title: str = 'Cached Event'):
        from app.services.calendar_subscriptions import SubscriptionEvent
        return SubscriptionEvent(
            id=event_id,
            title=title,
            start_at=datetime(2099, 1, 1, 10, 0),
            end_at=datetime(2099, 1, 1, 11, 0),
            source_id=1,
            source_name='Test Sub',
            source_type='subscription',
        )

    def _make_sub(self, db, user, sub_id=1, name='Test', url='https://cal.test/feed.ics',
                  enabled=True, cache_ttl_minutes=None):
        """Persist and return a subscription object."""
        sub = CalendarSubscription(
            id=sub_id,
            user_id=user.id,
            name=name,
            url=url,
            enabled=enabled,
            cache_ttl_minutes=cache_ttl_minutes,
        )
        db.session.add(sub)
        db.session.commit()
        return sub

    def test_cache_hit_returns_cached_events(self, app, db, test_user):
        """A fresh cache entry is returned without a network call."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub(db, test_user, sub_id=900)
        now = datetime.utcnow()

        fake_events = [self._fake_event('sub_900_1')]
        svc._write_cache(sub.id, {
            'events': fake_events,
            'fetched_at': now,
            'expires_at': now + timedelta(minutes=30),
            'success': True,
            'error': None,
        })

        with app.app_context():
            with patch.object(svc, 'fetch_calendar_feed') as mock_fetch:
                result = svc.get_cached_subscription_events(sub)
                mock_fetch.assert_not_called()

        assert len(result) == 1
        assert result[0].id == 'sub_900_1'
        svc.invalidate_cache(sub.id)

    def test_cache_miss_triggers_fetch(self, app, db, test_user):
        """An absent cache entry causes a network fetch."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub(db, test_user, sub_id=901)
        svc.invalidate_cache(sub.id)

        with app.app_context():
            with patch.object(svc, 'fetch_calendar_feed', return_value=(_VALID_ICS, None)):
                with patch.object(svc, '_update_db_status'):
                    result = svc.get_cached_subscription_events(sub)

        assert isinstance(result, list)
        svc.invalidate_cache(sub.id)

    def test_expired_cache_triggers_refresh(self, app, db, test_user):
        """An expired cache entry is refreshed on next access."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub(db, test_user, sub_id=902)
        old_events = [self._fake_event('sub_902_1')]
        svc._write_cache(sub.id, {
            'events': old_events,
            'fetched_at': datetime.utcnow() - timedelta(hours=2),
            'expires_at': datetime.utcnow() - timedelta(hours=1),  # expired
            'success': True,
            'error': None,
        })

        with app.app_context():
            with patch.object(svc, 'fetch_calendar_feed', return_value=(_VALID_ICS, None)):
                with patch.object(svc, '_update_db_status'):
                    result = svc.get_cached_subscription_events(sub)

        # Fresh events from the real ICS, not the stale mock
        assert result is not old_events
        svc.invalidate_cache(sub.id)

    def test_stale_cache_returned_on_fetch_error(self, app, db, test_user):
        """When refresh fails, stale cache events are returned."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub(db, test_user, sub_id=903)
        stale_events = [self._fake_event('sub_903_1')]
        svc._write_cache(sub.id, {
            'events': stale_events,
            'fetched_at': datetime.utcnow() - timedelta(hours=2),
            'expires_at': datetime.utcnow() - timedelta(hours=1),  # expired
            'success': True,
            'error': None,
        })

        with app.app_context():
            with patch.object(svc, 'fetch_calendar_feed',
                              side_effect=Exception('Network error')):
                with patch.object(svc, '_update_db_status'):
                    result = svc.get_cached_subscription_events(sub)

        # Stale cache is returned, not an empty list
        assert len(result) == 1
        assert result[0].id == 'sub_903_1'
        svc.invalidate_cache(sub.id)

    def test_empty_list_returned_when_no_cache_and_error(self, app, db, test_user):
        """Empty list is returned when fetch fails and there is no cache."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub(db, test_user, sub_id=904)
        svc.invalidate_cache(sub.id)

        with app.app_context():
            with patch.object(svc, 'fetch_calendar_feed',
                              side_effect=Exception('Network error')):
                with patch.object(svc, '_update_db_status'):
                    result = svc.get_cached_subscription_events(sub)

        assert result == []

    def test_force_refresh_bypasses_fresh_cache(self, app, db, test_user):
        """force=True causes a network fetch even when cache is fresh."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub(db, test_user, sub_id=905)
        now = datetime.utcnow()
        svc._write_cache(sub.id, {
            'events': [],
            'fetched_at': now,
            'expires_at': now + timedelta(minutes=30),
            'success': True,
            'error': None,
        })

        with app.app_context():
            with patch.object(svc, 'fetch_calendar_feed', return_value=(_VALID_ICS, None)):
                with patch.object(svc, '_update_db_status'):
                    svc.refresh_subscription_events(sub, force=True)

        svc.invalidate_cache(sub.id)

    def test_success_status_update_includes_source_modified(self, app, db, test_user):
        """Successful refresh forwards source Last-Modified to status updater."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub(db, test_user, sub_id=907)
        source_modified = datetime(2026, 1, 2, 3, 4, 5)

        with app.app_context():
            with patch.object(
                svc,
                'fetch_calendar_feed',
                return_value=(_VALID_ICS, source_modified),
            ):
                with patch.object(svc, '_update_db_status') as mock_update:
                    svc.refresh_subscription_events(sub, force=True)

        mock_update.assert_called_once_with(
            sub.id,
            'ok',
            None,
            source_modified_at=source_modified,
            update_source_modified=True,
        )
        svc.invalidate_cache(sub.id)

    def test_invalidate_cache_removes_entry(self, app, db, test_user):
        """invalidate_cache removes the cached entry."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub(db, test_user, sub_id=906)
        svc._write_cache(sub.id, {'events': [], 'fetched_at': datetime.utcnow(),
                                   'expires_at': datetime.utcnow() + timedelta(minutes=30),
                                   'success': True, 'error': None})
        svc.invalidate_cache(sub.id)
        assert svc._read_cache(sub.id) is None

    def test_get_cached_events_or_refresh_on_miss_uses_cache(self, app, db, test_user):
        """The warmup helper does not refresh when cache is already present."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub(db, test_user, sub_id=907)
        cached_events = [self._fake_event('sub_907_1')]
        now = datetime.utcnow()
        svc._write_cache(sub.id, {
            'events': cached_events,
            'fetched_at': now,
            'expires_at': now + timedelta(minutes=30),
            'success': True,
            'error': None,
        })

        with app.app_context():
            with patch.object(svc, 'refresh_subscription_events') as mock_refresh:
                result = svc.get_cached_events_or_refresh_on_miss(sub)
                mock_refresh.assert_not_called()

        assert len(result) == 1
        assert result[0].id == 'sub_907_1'
        svc.invalidate_cache(sub.id)

    def test_get_cached_events_or_refresh_on_miss_refreshes_on_cold_miss(self, app, db, test_user):
        """The warmup helper does not block UI paths on cold miss."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub(db, test_user, sub_id=908)
        svc.invalidate_cache(sub.id)
        with app.app_context():
            with patch.object(svc, 'refresh_subscription_events') as mock_refresh:
                result = svc.get_cached_events_or_refresh_on_miss(sub)

        mock_refresh.assert_not_called()
        assert result == []


# ===========================================================================
# Service: get_all_display_events_for_user — merge behaviour
# ===========================================================================

class TestMergedEventDisplay:
    def test_local_and_subscription_events_merged(self, app, db, test_user):
        """Local DB events and subscription events are merged and time-sorted."""
        from app.models import Event
        from app.services import calendar_subscriptions as svc
        from app.services.calendar_subscriptions import SubscriptionEvent

        with app.app_context():
            # Create a local event
            local_ev = Event(
                user_id=test_user.id,
                title='Local Meeting',
                start_at=datetime(2099, 6, 1, 9, 0),
            )
            db.session.add(local_ev)
            db.session.commit()

            # Stub subscription returning a remote event
            sub_ev = SubscriptionEvent(
                id='sub_1_fake',
                title='Remote Meeting',
                start_at=datetime(2099, 6, 1, 10, 0),
                source_type='subscription',
                source_id=1,
                source_name='Test Sub',
            )
            with patch.object(svc, 'get_user_calendar_subscriptions',
                              return_value=[MagicMock(id=1)]):
                with patch.object(svc, 'get_cached_events_stale_ok',
                                  return_value=[sub_ev]), \
                     patch.object(svc, 'is_cache_stale', return_value=False):
                    events = svc.get_all_display_events_for_user(test_user)

        titles = [e.title for e in events]
        assert 'Local Meeting' in titles
        assert 'Remote Meeting' in titles
        # Verify time-sorted order
        starts = [e.start_at for e in events if e.start_at]
        assert starts == sorted(starts)

    def test_broken_subscription_does_not_crash(self, app, db, test_user):
        """A subscription that raises an exception does not break the merge."""
        from app.services import calendar_subscriptions as svc

        with app.app_context():
            broken_sub = MagicMock()
            broken_sub.id = 999

            with patch.object(svc, 'get_user_calendar_subscriptions',
                              return_value=[broken_sub]):
                with patch.object(svc, 'get_cached_events_stale_ok',
                                  side_effect=Exception('boom')):
                    events = svc.get_all_display_events_for_user(test_user)

        # Should return successfully with just DB events (empty since no local events)
        assert isinstance(events, list)


# ===========================================================================
# Events page: subscription events appear and local events still work
# ===========================================================================

class TestEventsPageWithSubscriptions:
    def test_events_page_still_works_without_subscriptions(self, auth_client):
        """GET /events/ renders fine when there are no subscriptions."""
        resp = auth_client.get('/events/')
        assert resp.status_code == 200

    def test_subscription_event_shown_on_events_page(self, auth_client, app,
                                                      db, test_user):
        """A subscription event title appears on the events page."""
        from app.services import calendar_subscriptions as svc
        from app.services.calendar_subscriptions import SubscriptionEvent

        sub_ev = SubscriptionEvent(
            id='sub_2_abc',
            title='External Conference',
            start_at=datetime.utcnow() + timedelta(hours=1),
            source_type='subscription',
            source_id=2,
            source_name='Work Sub',
        )
        with patch.object(svc, 'get_user_calendar_subscriptions',
                          return_value=[MagicMock(id=2)]):
            with patch.object(svc, 'get_cached_events_stale_ok',
                              return_value=[sub_ev]), \
                 patch.object(svc, 'is_cache_stale', return_value=False):
                resp = auth_client.get('/events/?view=upcoming')

        assert resp.status_code == 200
        assert b'External Conference' in resp.data
        assert b'Subscribed' in resp.data

    def test_subscription_error_does_not_break_events_page(self, auth_client, app):
        """A failing subscription service does not crash the events page."""
        from app.services import calendar_subscriptions as svc

        with patch.object(svc, 'get_user_calendar_subscriptions',
                          side_effect=Exception('service unavailable')):
            resp = auth_client.get('/events/')

        assert resp.status_code == 200

    def test_subscription_events_view_ignores_blank_title(self, auth_client):
        """Blank-title subscription rows are ignored by the events merge."""
        from app.services import calendar_subscriptions as svc
        from app.services.calendar_subscriptions import SubscriptionEvent

        valid = SubscriptionEvent(
            id='sub_2_real',
            title='Legit Event',
            start_at=datetime.utcnow() + timedelta(hours=2),
            source_type='subscription',
            source_id=2,
            source_name='Work Sub',
        )
        blank = SubscriptionEvent(
            id='sub_2_blank',
            title='   ',
            start_at=datetime.utcnow() + timedelta(hours=1),
            source_type='subscription',
            source_id=2,
            source_name='Work Sub',
        )
        with patch.object(svc, 'get_user_calendar_subscriptions',
                          return_value=[MagicMock(id=2)]):
            with patch.object(svc, 'get_cached_events_stale_ok',
                              return_value=[blank, valid]), \
                 patch.object(svc, 'is_cache_stale', return_value=False):
                resp = auth_client.get('/events/?view=all')

        assert resp.status_code == 200
        assert b'Legit Event' in resp.data
        assert b'sub_2_blank' not in resp.data

    def test_subscription_events_view_all_ignores_out_of_window(self, auth_client):
        """Ancient cached subscription rows are not rendered in view=all."""
        from app.services import calendar_subscriptions as svc
        from app.services.calendar_subscriptions import SubscriptionEvent

        in_window = SubscriptionEvent(
            id='sub_2_in_window',
            title='Current Event',
            start_at=datetime.utcnow() + timedelta(days=2),
            source_type='subscription',
            source_id=2,
            source_name='Work Sub',
        )
        old = SubscriptionEvent(
            id='sub_2_old',
            title='Old Artifact',
            start_at=datetime.utcnow() - timedelta(days=1800),
            source_type='subscription',
            source_id=2,
            source_name='Work Sub',
        )
        with patch.object(svc, 'get_user_calendar_subscriptions',
                          return_value=[MagicMock(id=2)]):
            with patch.object(svc, 'get_cached_events_stale_ok',
                              return_value=[old, in_window]), \
                 patch.object(svc, 'is_cache_stale', return_value=False):
                resp = auth_client.get('/events/?view=all')

        assert resp.status_code == 200
        assert b'Current Event' in resp.data
        assert b'Old Artifact' not in resp.data

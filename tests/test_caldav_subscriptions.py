"""
tests/test_caldav_subscriptions.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tests for CalDAV calendar subscription support:

- CalDAV CRUD routes (create, edit, delete, toggle)
- CalDAV-specific form validation (type, username, password)
- Password encryption at rest
- CalDAV URL validation
- CalDAV service: REPORT fetch, PROPFIND fallback, XML parsing
- refresh_subscription_events dispatch (ics vs caldav)
- Security: SSRF protection, no credential leakage in errors
"""

import textwrap
from datetime import datetime, timedelta
from unittest.mock import ANY, MagicMock, call, patch

import pytest

from app.models.calendar_subscription import CalendarSubscription


# ---------------------------------------------------------------------------
# CalDAV multistatus response fixtures
# ---------------------------------------------------------------------------

_CALDAV_VEVENT_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Example//CalDAV//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:caldav-event-001@example.com\r\n"
    "SUMMARY:CalDAV Meeting\r\n"
    "DTSTART:20990201T090000Z\r\n"
    "DTEND:20990201T100000Z\r\n"
    "LOCATION:Online\r\n"
    "DESCRIPTION:A CalDAV event.\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

# Build the multistatus XML without textwrap.dedent so that embedding a
# non-indented ICS string does not prevent the '<?xml' declaration from
# appearing at column 0 (required by the XML parser).
_CALDAV_MULTISTATUS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">\n'
    '  <D:response>\n'
    '    <D:href>/calendars/user/calendar/event1.ics</D:href>\n'
    '    <D:propstat>\n'
    '      <D:prop>\n'
    '        <D:getetag>"abc123"</D:getetag>\n'
    f'        <C:calendar-data>{_CALDAV_VEVENT_ICS}</C:calendar-data>\n'
    '      </D:prop>\n'
    '      <D:status>HTTP/1.1 200 OK</D:status>\n'
    '    </D:propstat>\n'
    '  </D:response>\n'
    '</D:multistatus>\n'
)

_CALDAV_PROPFIND_RESPONSE = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <D:multistatus xmlns:D="DAV:">
      <D:response>
        <D:href>/calendars/user/calendar/</D:href>
        <D:propstat>
          <D:prop>
            <D:resourcetype><D:collection/></D:resourcetype>
          </D:prop>
          <D:status>HTTP/1.1 200 OK</D:status>
        </D:propstat>
      </D:response>
      <D:response>
        <D:href>/calendars/user/calendar/event1.ics</D:href>
        <D:propstat>
          <D:prop>
            <D:getcontenttype>text/calendar; charset=utf-8</D:getcontenttype>
          </D:prop>
          <D:status>HTTP/1.1 200 OK</D:status>
        </D:propstat>
      </D:response>
    </D:multistatus>
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_caldav_sub(db, user, name='Work CalDAV',
                        url='https://caldav.example.com/calendars/user/work/',
                        username='alice@example.com',
                        password='s3cr3t',
                        enabled=True):
    """Persist a CalDAV CalendarSubscription for *user* and return it."""
    sub = CalendarSubscription(
        user_id=user.id,
        name=name,
        url=url,
        subscription_type='caldav',
        caldav_username=username,
        enabled=enabled,
    )
    if password:
        sub.caldav_password = password
    db.session.add(sub)
    db.session.commit()
    return sub


def _post_new_caldav(client, name='Work CalDAV',
                     url='https://caldav.example.com/calendars/user/cal/',
                     username='user@example.com',
                     password='secret',
                     enabled='on'):
    """POST to /calendar-subscriptions/new with subscription_type=caldav."""
    return client.post(
        '/calendar-subscriptions/new',
        data={
            'name': name,
            'subscription_type': 'caldav',
            'url': url,
            'caldav_username': username,
            'caldav_password': password,
            'enabled': enabled,
        },
        follow_redirects=False,
    )


def _post_edit_caldav(client, sub_id, name='Work CalDAV',
                      url='https://caldav.example.com/calendars/user/cal/',
                      username='user@example.com',
                      password='',
                      enabled='on'):
    """POST to /calendar-subscriptions/<id>/edit with subscription_type=caldav."""
    return client.post(
        f'/calendar-subscriptions/{sub_id}/edit',
        data={
            'name': name,
            'subscription_type': 'caldav',
            'url': url,
            'caldav_username': username,
            'caldav_password': password,
            'enabled': enabled,
        },
        follow_redirects=False,
    )


# ===========================================================================
# CalDAV URL validation
# ===========================================================================

class TestCalDAVUrlValidation:
    def test_valid_https_url(self):
        from app.services.calendar_subscriptions import validate_caldav_url
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            assert validate_caldav_url('https://caldav.example.com/calendars/') is None

    def test_valid_http_url(self):
        from app.services.calendar_subscriptions import validate_caldav_url
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            assert validate_caldav_url('http://caldav.example.com/calendars/') is None

    def test_rejects_webcal_scheme(self):
        """CalDAV does not accept webcal:// (unlike ICS)."""
        from app.services.calendar_subscriptions import validate_caldav_url
        result = validate_caldav_url('webcal://caldav.example.com/cal/')
        assert result is not None
        assert 'http' in result.lower()

    def test_rejects_ftp_scheme(self):
        from app.services.calendar_subscriptions import validate_caldav_url
        assert validate_caldav_url('ftp://caldav.example.com/cal/') is not None

    def test_rejects_empty_url(self):
        from app.services.calendar_subscriptions import validate_caldav_url
        assert validate_caldav_url('') is not None

    def test_rejects_no_hostname(self):
        from app.services.calendar_subscriptions import validate_caldav_url
        assert validate_caldav_url('https://') is not None


# ===========================================================================
# Route: Create CalDAV subscription
# ===========================================================================

class TestCalDAVCreate:
    def test_post_valid_creates_caldav_subscription(self, auth_client, db, test_user):
        """Valid POST with CalDAV type creates a subscription."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            resp = _post_new_caldav(auth_client, name='My CalDAV')
        assert resp.status_code in (301, 302)
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='My CalDAV'
        ).first()
        assert sub is not None
        assert sub.subscription_type == 'caldav'

    def test_caldav_subscription_stores_username(self, auth_client, db, test_user):
        """The CalDAV username is persisted in the database."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            _post_new_caldav(auth_client, name='UsernameTest',
                             username='bob@example.com')
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='UsernameTest'
        ).first()
        assert sub is not None
        assert sub.caldav_username == 'bob@example.com'

    def test_caldav_password_is_encrypted_not_plaintext(self, auth_client, db, test_user):
        """The CalDAV password column must not contain the plaintext password."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            _post_new_caldav(auth_client, name='EncryptionTest',
                             password='my-secret-password')
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='EncryptionTest'
        ).first()
        assert sub is not None
        assert sub.caldav_password_enc is not None
        assert 'my-secret-password' not in sub.caldav_password_enc

    def test_caldav_password_decrypts_correctly(self, auth_client, db, test_user, app):
        """The caldav_password property decrypts to the original plaintext."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            _post_new_caldav(auth_client, name='DecryptTest',
                             password='correct-horse-battery')
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='DecryptTest'
        ).first()
        with app.app_context():
            assert sub.caldav_password == 'correct-horse-battery'

    def test_missing_username_returns_422(self, auth_client):
        """CalDAV submission without a username is rejected."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            resp = _post_new_caldav(auth_client, username='')
        assert resp.status_code == 422

    def test_missing_password_for_new_caldav_returns_422(self, auth_client):
        """New CalDAV subscription without a password is rejected."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            resp = _post_new_caldav(auth_client, password='')
        assert resp.status_code == 422

    def test_create_triggers_background_refresh(self, auth_client):
        """Successful create starts an async refresh to populate cached rows."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            with patch(
                'app.routes.calendar_subscriptions.refresh_subscription_events_background'
            ) as mock_refresh:
                resp = _post_new_caldav(auth_client, name='RefreshOnCreate')

        assert resp.status_code in (301, 302)
        mock_refresh.assert_called_once()

    def test_caldav_url_must_not_use_ftp(self, auth_client):
        """ftp:// CalDAV URL is rejected."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            resp = _post_new_caldav(auth_client,
                                    url='ftp://caldav.example.com/cal/')
        assert resp.status_code == 422

    def test_subscription_type_ics_still_works(self, auth_client, db, test_user):
        """ICS subscription type still creates correctly via the updated form."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            resp = auth_client.post(
                '/calendar-subscriptions/new',
                data={
                    'name': 'ICS Test',
                    'subscription_type': 'ics',
                    'url': 'https://example.com/feed.ics',
                    'enabled': 'on',
                },
                follow_redirects=False,
            )
        assert resp.status_code in (301, 302)
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='ICS Test'
        ).first()
        assert sub is not None
        assert sub.subscription_type == 'ics'


# ===========================================================================
# Route: Edit CalDAV subscription
# ===========================================================================

class TestCalDAVEdit:
    def test_edit_keeps_existing_password_when_blank(self, auth_client, db,
                                                      test_user, app):
        """Blank password field on edit preserves the existing encrypted password."""
        sub = _create_caldav_sub(db, test_user, password='original-password')
        original_enc = sub.caldav_password_enc

        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            _post_edit_caldav(auth_client, sub.id, name='Updated Name',
                              password='')  # blank = keep existing
        db.session.refresh(sub)
        # Encrypted value must remain unchanged
        assert sub.caldav_password_enc == original_enc
        with app.app_context():
            assert sub.caldav_password == 'original-password'

    def test_edit_updates_password_when_provided(self, auth_client, db,
                                                  test_user, app):
        """Providing a new password on edit replaces the stored encrypted value."""
        sub = _create_caldav_sub(db, test_user, password='old-password')

        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            _post_edit_caldav(auth_client, sub.id, name='Updated',
                              password='new-password')
        db.session.refresh(sub)
        with app.app_context():
            assert sub.caldav_password == 'new-password'

    def test_edit_updates_username(self, auth_client, db, test_user):
        """Username is updated when changed via the edit form."""
        sub = _create_caldav_sub(db, test_user, username='old@example.com')

        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            _post_edit_caldav(auth_client, sub.id, username='new@example.com',
                              password='')
        db.session.refresh(sub)
        assert sub.caldav_username == 'new@example.com'

    def test_edit_switching_to_ics_clears_credentials(self, auth_client, db,
                                                        test_user):
        """Switching a CalDAV subscription back to ICS type clears credentials."""
        sub = _create_caldav_sub(db, test_user, password='secret')

        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            auth_client.post(
                f'/calendar-subscriptions/{sub.id}/edit',
                data={
                    'name': 'Now ICS',
                    'subscription_type': 'ics',
                    'url': 'https://example.com/feed.ics',
                    'enabled': 'on',
                },
                follow_redirects=False,
            )
        db.session.refresh(sub)
        assert sub.subscription_type == 'ics'
        assert sub.caldav_password_enc is None
        assert sub.caldav_username is None

    def test_edit_form_renders_for_caldav_subscription(self, auth_client, db,
                                                         test_user):
        """GET edit form returns 200 for a CalDAV subscription."""
        sub = _create_caldav_sub(db, test_user)
        resp = auth_client.get(f'/calendar-subscriptions/{sub.id}/edit')
        assert resp.status_code == 200
        assert b'CalDAV' in resp.data

    def test_edit_enabled_subscription_triggers_background_refresh(
        self, auth_client, db, test_user
    ):
        """Successful edit on enabled sub starts async refresh."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            sub = _create_caldav_sub(db, test_user, name='RefreshOnEdit')

        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            with patch(
                'app.routes.calendar_subscriptions.refresh_subscription_events_background'
            ) as mock_refresh:
                resp = _post_edit_caldav(
                    auth_client,
                    sub.id,
                    name='RefreshOnEdit Updated',
                    url='https://caldav.example.com/calendars/user/updated/',
                    username='alice@example.com',
                    password='',
                    enabled='on',
                )

        assert resp.status_code in (301, 302)
        mock_refresh.assert_called_once_with(sub.id, ANY)


# ===========================================================================
# UI: CalDAV type badge in index and partial
# ===========================================================================

class TestCalDAVIndexDisplay:
    def test_index_shows_caldav_badge(self, auth_client, db, test_user):
        """The subscription list renders a CalDAV badge for CalDAV subscriptions."""
        _create_caldav_sub(db, test_user, name='My CalDAV Cal')
        resp = auth_client.get('/calendar-subscriptions/')
        assert resp.status_code == 200
        assert b'CalDAV' in resp.data
        assert b'My CalDAV Cal' in resp.data

    def test_index_shows_ics_label_for_ics_subscription(self, auth_client, db,
                                                          test_user):
        """The subscription list shows an ICS label for ICS subscriptions."""
        from app.models.calendar_subscription import CalendarSubscription
        sub = CalendarSubscription(
            user_id=test_user.id,
            name='My ICS Cal',
            url='https://example.com/feed.ics',
            subscription_type='ics',
        )
        db.session.add(sub)
        db.session.commit()
        resp = auth_client.get('/calendar-subscriptions/')
        assert resp.status_code == 200
        assert b'ICS' in resp.data


# ===========================================================================
# Service: _parse_multistatus_calendar_data
# ===========================================================================

class TestParseMultistatusCalendarData:
    def test_extracts_calendar_data_from_valid_response(self, app):
        """calendar-data elements are extracted from a valid multistatus response."""
        from app.services.calendar_subscriptions import _parse_multistatus_calendar_data
        with app.app_context():
            blobs = _parse_multistatus_calendar_data(_CALDAV_MULTISTATUS)
        assert len(blobs) == 1
        assert 'BEGIN:VCALENDAR' in blobs[0]
        assert 'CalDAV Meeting' in blobs[0]

    def test_returns_empty_list_for_empty_input(self, app):
        """Empty input returns an empty list without raising."""
        from app.services.calendar_subscriptions import _parse_multistatus_calendar_data
        with app.app_context():
            blobs = _parse_multistatus_calendar_data('')
        assert blobs == []

    def test_returns_empty_list_for_invalid_xml(self, app):
        """Malformed XML is handled gracefully and returns an empty list."""
        from app.services.calendar_subscriptions import _parse_multistatus_calendar_data
        with app.app_context():
            blobs = _parse_multistatus_calendar_data('<not valid xml <<')
        assert blobs == []

    def test_returns_empty_list_when_no_calendar_data_elements(self, app):
        """A valid multistatus without calendar-data returns an empty list."""
        xml = (
            '<?xml version="1.0"?>'
            '<D:multistatus xmlns:D="DAV:">'
            '<D:response><D:href>/cal/</D:href></D:response>'
            '</D:multistatus>'
        )
        from app.services.calendar_subscriptions import _parse_multistatus_calendar_data
        with app.app_context():
            blobs = _parse_multistatus_calendar_data(xml)
        assert blobs == []

    def test_extracts_multiple_events(self, app):
        """Multiple calendar-data elements are all extracted."""
        second_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:second@example.com\r\n"
            "SUMMARY:Second Event\r\nDTSTART:20990303T090000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<D:multistatus xmlns:D="DAV:"'
            '  xmlns:C="urn:ietf:params:xml:ns:caldav">\n'
            '  <D:response><D:propstat><D:prop>\n'
            f'    <C:calendar-data>{_CALDAV_VEVENT_ICS}</C:calendar-data>\n'
            '  </D:prop></D:propstat></D:response>\n'
            '  <D:response><D:propstat><D:prop>\n'
            f'    <C:calendar-data>{second_ics}</C:calendar-data>\n'
            '  </D:prop></D:propstat></D:response>\n'
            '</D:multistatus>\n'
        )
        from app.services.calendar_subscriptions import _parse_multistatus_calendar_data
        with app.app_context():
            blobs = _parse_multistatus_calendar_data(xml)
        assert len(blobs) == 2


# ===========================================================================
# Service: _extract_ics_hrefs
# ===========================================================================

class TestExtractIcsHrefs:
    def test_extracts_ics_href_by_content_type(self, app):
        """Hrefs with text/calendar content-type are returned."""
        from app.services.calendar_subscriptions import _extract_ics_hrefs
        with app.app_context():
            hrefs = _extract_ics_hrefs(
                _CALDAV_PROPFIND_RESPONSE,
                'https://caldav.example.com/calendars/user/calendar/',
            )
        assert len(hrefs) == 1
        assert hrefs[0].endswith('/event1.ics')

    def test_returns_absolute_urls(self, app):
        """Relative hrefs in the PROPFIND response are made absolute."""
        from app.services.calendar_subscriptions import _extract_ics_hrefs
        with app.app_context():
            hrefs = _extract_ics_hrefs(
                _CALDAV_PROPFIND_RESPONSE,
                'https://caldav.example.com/calendars/user/calendar/',
            )
        for href in hrefs:
            assert href.startswith('https://')

    def test_returns_empty_list_for_invalid_xml(self, app):
        """Malformed PROPFIND XML returns an empty list."""
        from app.services.calendar_subscriptions import _extract_ics_hrefs
        with app.app_context():
            hrefs = _extract_ics_hrefs('<bad xml <<', 'https://example.com/')
        assert hrefs == []


# ===========================================================================
# Service: fetch_caldav_events
# ===========================================================================

class TestFetchCalDAVEvents:
    def _make_mock_response(self, status_code=207, text=None,
                             is_redirect=False, location=None):
        """Build a mock requests.Response-like object."""
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text or _CALDAV_MULTISTATUS
        resp.is_redirect = is_redirect
        if is_redirect:
            resp.headers = {'Location': location or ''}
        else:
            resp.headers = {}
        resp.raise_for_status.return_value = None
        return resp

    def _make_sub(self, url='https://caldav.example.com/cal/',
                  username='user', password_enc=None):
        sub = MagicMock()
        sub.id = 1
        sub.name = 'Test CalDAV'
        sub.url = url
        sub.caldav_username = username
        sub.caldav_password = 'testpass'
        sub.color = '#6366f1'
        sub.subscription_type = 'caldav'
        return sub

    def test_sends_report_request(self, app):
        """fetch_caldav_events issues a REPORT request to the calendar URL."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub()
        mock_resp = self._make_mock_response()

        with app.app_context():
            with patch.object(svc, '_caldav_request_safe',
                              return_value=mock_resp) as mock_req:
                with patch.object(svc, '_assert_ssrf_safe', return_value=None):
                    svc.fetch_caldav_events(sub, lookahead_days=365 * 100)

        first_call = mock_req.call_args_list[0]
        assert first_call[0][0] == 'REPORT'
        assert first_call[0][1] == sub.url

    def test_report_body_contains_time_range(self, app):
        """The REPORT request body includes a time-range element."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub()
        mock_resp = self._make_mock_response()

        with app.app_context():
            with patch.object(svc, '_caldav_request_safe',
                              return_value=mock_resp) as mock_req:
                with patch.object(svc, '_assert_ssrf_safe', return_value=None):
                    svc.fetch_caldav_events(sub, lookahead_days=60)

        body = mock_req.call_args_list[0][1].get('body') or mock_req.call_args_list[0][0][4] if len(mock_req.call_args_list[0][0]) > 4 else b''
        # Extract the body kwarg
        kwargs = mock_req.call_args_list[0][1]
        body_bytes = kwargs.get('body', b'')
        assert b'time-range' in body_bytes
        assert b'VEVENT' in body_bytes

    def test_falls_back_to_propfind_on_405(self, app):
        """A 405 REPORT response triggers the PROPFIND fallback."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub()
        report_405 = self._make_mock_response(status_code=405, text='')

        with app.app_context():
            with patch.object(svc, '_caldav_request_safe',
                              return_value=report_405):
                with patch.object(svc, '_caldav_propfind',
                                  return_value=[]) as mock_propfind:
                    with patch.object(svc, '_assert_ssrf_safe', return_value=None):
                        svc.fetch_caldav_events(sub, lookahead_days=60)

        mock_propfind.assert_called_once()

    def test_falls_back_to_propfind_on_501(self, app):
        """A 501 REPORT response also triggers the PROPFIND fallback."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub()
        report_501 = self._make_mock_response(status_code=501, text='')

        with app.app_context():
            with patch.object(svc, '_caldav_request_safe',
                              return_value=report_501):
                with patch.object(svc, '_caldav_propfind',
                                  return_value=[]) as mock_propfind:
                    with patch.object(svc, '_assert_ssrf_safe', return_value=None):
                        svc.fetch_caldav_events(sub, lookahead_days=60)

        mock_propfind.assert_called_once()

    def test_parses_events_from_multistatus_response(self, app):
        """Events are extracted and parsed from the multistatus XML response."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub()
        mock_resp = self._make_mock_response(text=_CALDAV_MULTISTATUS)

        with app.app_context():
            with patch.object(svc, '_caldav_request_safe',
                              return_value=mock_resp):
                with patch.object(svc, '_assert_ssrf_safe', return_value=None):
                    events, _ = svc.fetch_caldav_events(
                        sub, lookahead_days=365 * 100
                    )

        assert len(events) == 1
        assert events[0].title == 'CalDAV Meeting'
        assert events[0].location == 'Online'
        assert events[0].read_only is True
        assert events[0].source_type == 'subscription'

    def test_returns_empty_list_when_no_events(self, app):
        """An empty multistatus response results in an empty event list."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub()
        empty_ms = (
            '<?xml version="1.0"?>'
            '<D:multistatus xmlns:D="DAV:"'
            '  xmlns:C="urn:ietf:params:xml:ns:caldav">'
            '</D:multistatus>'
        )
        mock_resp = self._make_mock_response(text=empty_ms)

        with app.app_context():
            with patch.object(svc, '_caldav_request_safe',
                              return_value=mock_resp):
                with patch.object(svc, '_assert_ssrf_safe', return_value=None):
                    events, last_mod = svc.fetch_caldav_events(sub, lookahead_days=60)

        assert events == []
        assert last_mod is None

    def test_skips_unparseable_ics_blob(self, app):
        """A corrupt ICS blob is skipped; other events still returned."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub()
        bad_blob = 'not-valid-ics'
        mixed_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<D:multistatus xmlns:D="DAV:"'
            '  xmlns:C="urn:ietf:params:xml:ns:caldav">\n'
            '  <D:response><D:propstat><D:prop>\n'
            f'    <C:calendar-data>{bad_blob}</C:calendar-data>\n'
            '  </D:prop></D:propstat></D:response>\n'
            '  <D:response><D:propstat><D:prop>\n'
            f'    <C:calendar-data>{_CALDAV_VEVENT_ICS}</C:calendar-data>\n'
            '  </D:prop></D:propstat></D:response>\n'
            '</D:multistatus>\n'
        )
        mock_resp = self._make_mock_response(text=mixed_xml)

        with app.app_context():
            with patch.object(svc, '_caldav_request_safe',
                              return_value=mock_resp):
                with patch.object(svc, '_assert_ssrf_safe', return_value=None):
                    events, _ = svc.fetch_caldav_events(
                        sub, lookahead_days=365 * 100
                    )

        # Only the valid event is returned
        assert len(events) == 1
        assert events[0].title == 'CalDAV Meeting'

    def test_deduplicates_events_by_id(self, app):
        """Duplicate event UIDs across blobs are de-duplicated."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_sub()
        # Two blobs with the same UID
        duplicate_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<D:multistatus xmlns:D="DAV:"'
            '  xmlns:C="urn:ietf:params:xml:ns:caldav">\n'
            '  <D:response><D:propstat><D:prop>\n'
            f'    <C:calendar-data>{_CALDAV_VEVENT_ICS}</C:calendar-data>\n'
            '  </D:prop></D:propstat></D:response>\n'
            '  <D:response><D:propstat><D:prop>\n'
            f'    <C:calendar-data>{_CALDAV_VEVENT_ICS}</C:calendar-data>\n'
            '  </D:prop></D:propstat></D:response>\n'
            '</D:multistatus>\n'
        )
        mock_resp = self._make_mock_response(text=duplicate_xml)

        with app.app_context():
            with patch.object(svc, '_caldav_request_safe',
                              return_value=mock_resp):
                with patch.object(svc, '_assert_ssrf_safe', return_value=None):
                    events, _ = svc.fetch_caldav_events(
                        sub, lookahead_days=365 * 100
                    )

        # De-duplicated: only one event
        assert len(events) == 1


# ===========================================================================
# Service: refresh dispatch (ics vs caldav)
# ===========================================================================

class TestRefreshDispatch:
    def _make_ics_sub(self, db, user, sub_id):
        sub = CalendarSubscription(
            id=sub_id,
            user_id=user.id,
            name='ICS Sub',
            url='https://example.com/feed.ics',
            subscription_type='ics',
        )
        db.session.add(sub)
        db.session.commit()
        return sub

    def _make_caldav_sub_db(self, db, user, sub_id):
        sub = CalendarSubscription(
            id=sub_id,
            user_id=user.id,
            name='CalDAV Sub',
            url='https://caldav.example.com/cal/',
            subscription_type='caldav',
            caldav_username='user@example.com',
        )
        sub.caldav_password = 'pass'
        db.session.add(sub)
        db.session.commit()
        return sub

    def test_ics_subscription_calls_fetch_calendar_feed(self, app, db, test_user):
        """refresh_subscription_events uses ICS fetch for subscription_type='ics'."""
        from app.services import calendar_subscriptions as svc

        valid_ics = b'BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n'
        sub = self._make_ics_sub(db, test_user, sub_id=801)
        svc.invalidate_cache(sub.id)

        with app.app_context():
            with patch.object(svc, 'fetch_calendar_feed',
                              return_value=(valid_ics, None)) as mock_ics:
                with patch.object(svc, 'fetch_caldav_events') as mock_caldav:
                    with patch.object(svc, '_update_db_status'):
                        svc.refresh_subscription_events(sub, force=True)

        mock_ics.assert_called_once()
        mock_caldav.assert_not_called()
        svc.invalidate_cache(sub.id)

    def test_caldav_subscription_calls_fetch_caldav_events(self, app, db, test_user):
        """refresh_subscription_events uses CalDAV fetch for subscription_type='caldav'."""
        from app.services import calendar_subscriptions as svc

        sub = self._make_caldav_sub_db(db, test_user, sub_id=802)
        svc.invalidate_cache(sub.id)

        with app.app_context():
            with patch.object(svc, 'fetch_caldav_events',
                              return_value=([], None)) as mock_caldav:
                with patch.object(svc, 'fetch_calendar_feed') as mock_ics:
                    with patch.object(svc, '_update_db_status'):
                        svc.refresh_subscription_events(sub, force=True)

        mock_caldav.assert_called_once()
        mock_ics.assert_not_called()
        svc.invalidate_cache(sub.id)

    def test_caldav_stale_cache_returned_on_error(self, app, db, test_user):
        """Failed CalDAV refresh returns stale cache instead of empty list."""
        from app.services import calendar_subscriptions as svc
        from app.services.calendar_subscriptions import SubscriptionEvent

        sub = self._make_caldav_sub_db(db, test_user, sub_id=803)
        stale_event = SubscriptionEvent(
            id='sub_803_cached',
            title='Stale CalDAV Event',
            start_at=datetime(2099, 6, 1, 9, 0),
            source_id=803,
            source_name='CalDAV Sub',
            source_type='subscription',
        )
        now = datetime.utcnow()
        svc._write_cache(sub.id, {
            'events': [stale_event],
            'fetched_at': now - timedelta(hours=2),
            'expires_at': now - timedelta(hours=1),
            'success': True,
            'error': None,
        })

        with app.app_context():
            with patch.object(svc, 'fetch_caldav_events',
                              side_effect=Exception('CalDAV server down')):
                with patch.object(svc, '_update_db_status'):
                    result = svc.get_cached_subscription_events(sub)

        assert len(result) == 1
        assert result[0].title == 'Stale CalDAV Event'
        svc.invalidate_cache(sub.id)


# ===========================================================================
# Security: SSRF protection for CalDAV
# ===========================================================================

class TestCalDAVSecurity:
    def test_ssrf_guard_blocks_report_to_private_ip(self, app):
        """SSRF check prevents CalDAV REPORT requests to private/internal hosts."""
        from app.services import calendar_subscriptions as svc

        sub = MagicMock()
        sub.id = 1
        sub.name = 'SSRF Test'
        sub.url = 'https://192.168.1.1/caldav/'
        sub.caldav_username = 'user'
        sub.caldav_password = 'pass'

        with app.app_context():
            with pytest.raises((ValueError, Exception)):
                # _assert_ssrf_safe is NOT mocked here — it should fire
                svc.fetch_caldav_events(sub, lookahead_days=60)

    def test_ssrf_guard_blocks_loopback(self, app):
        """SSRF check blocks loopback addresses in CalDAV URLs."""
        from app.services import calendar_subscriptions as svc

        sub = MagicMock()
        sub.id = 2
        sub.name = 'Loopback Test'
        sub.url = 'http://127.0.0.1/caldav/'
        sub.caldav_username = 'user'
        sub.caldav_password = 'pass'

        with app.app_context():
            with pytest.raises((ValueError, Exception)):
                svc.fetch_caldav_events(sub, lookahead_days=60)

    def test_caldav_password_not_in_last_error(self, app, db, test_user):
        """A CalDAV fetch failure must not leak the plaintext password in last_error."""
        from app.services import calendar_subscriptions as svc

        sub = _create_caldav_sub(db, test_user, name='Error Leak Test',
                                  password='super-secret-caldav-password')
        svc.invalidate_cache(sub.id)

        with app.app_context():
            with patch.object(
                svc, 'fetch_caldav_events',
                side_effect=Exception('Connection refused to caldav.example.com'),
            ):
                svc.refresh_subscription_events(sub, force=True)

        db.session.refresh(sub)
        assert sub.last_error is not None
        assert 'super-secret-caldav-password' not in (sub.last_error or '')
        svc.invalidate_cache(sub.id)

    def test_caldav_password_not_in_db_column_plaintext(self, auth_client, db,
                                                          test_user):
        """The database caldav_password_enc column never stores plaintext."""
        with patch(
            'app.services.calendar_subscriptions._host_resolves_to_private',
            return_value=False,
        ):
            _post_new_caldav(auth_client, name='DB Plaintext Test',
                             password='plaintext-db-check')
        sub = CalendarSubscription.query.filter_by(
            user_id=test_user.id, name='DB Plaintext Test'
        ).first()
        assert sub is not None
        # The encrypted column must not equal the plaintext password
        assert sub.caldav_password_enc != 'plaintext-db-check'
        # And must not contain it as a substring
        assert 'plaintext-db-check' not in sub.caldav_password_enc

    def test_caldav_request_safe_respects_ssrf_on_redirect(self, app):
        """_caldav_request_safe blocks redirects pointing to private IPs."""
        from app.services.calendar_subscriptions import _caldav_request_safe

        # First response is a redirect to a private IP
        redirect_resp = MagicMock()
        redirect_resp.is_redirect = True
        redirect_resp.status_code = 301
        redirect_resp.headers = {'Location': 'http://192.168.1.1/evil/'}

        with app.app_context():
            with patch('requests.request', return_value=redirect_resp):
                with pytest.raises((ValueError, Exception)):
                    _caldav_request_safe(
                        'REPORT',
                        'https://legit.example.com/caldav/',
                        auth=None,
                        headers={'User-Agent': 'test'},
                        timeout=10,
                    )

    def test_unknown_subscription_type_treated_as_ics(self, app, db, test_user):
        """An unknown subscription_type falls back to ICS fetch, not CalDAV."""
        from app.services import calendar_subscriptions as svc

        # Directly create a sub with a bogus type (bypassing route validation)
        sub = CalendarSubscription(
            id=809,
            user_id=test_user.id,
            name='Unknown Type Sub',
            url='https://example.com/feed.ics',
            subscription_type='unknown_type',
        )
        db.session.add(sub)
        db.session.commit()
        svc.invalidate_cache(sub.id)

        valid_ics = b'BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n'

        with app.app_context():
            with patch.object(svc, 'fetch_calendar_feed',
                              return_value=(valid_ics, None)) as mock_ics:
                with patch.object(svc, 'fetch_caldav_events') as mock_caldav:
                    with patch.object(svc, '_update_db_status'):
                        svc.refresh_subscription_events(sub, force=True)

        mock_ics.assert_called_once()
        mock_caldav.assert_not_called()
        svc.invalidate_cache(sub.id)


# ===========================================================================
# Model: CalendarSubscription CalDAV helpers
# ===========================================================================

class TestCalendarSubscriptionModel:
    def test_is_caldav_true_for_caldav_type(self, app, db, test_user):
        sub = _create_caldav_sub(db, test_user)
        assert sub.is_caldav is True

    def test_is_caldav_false_for_ics_type(self, app, db, test_user):
        sub = CalendarSubscription(
            user_id=test_user.id,
            name='ICS Sub',
            url='https://example.com/feed.ics',
            subscription_type='ics',
        )
        db.session.add(sub)
        db.session.commit()
        assert sub.is_caldav is False

    def test_type_label_returns_caldav(self, app, db, test_user):
        sub = _create_caldav_sub(db, test_user)
        assert sub.type_label == 'CalDAV'

    def test_type_label_returns_ics(self, app, db, test_user):
        sub = CalendarSubscription(
            user_id=test_user.id,
            name='ICS Sub',
            url='https://example.com/feed.ics',
            subscription_type='ics',
        )
        db.session.add(sub)
        db.session.commit()
        assert sub.type_label == 'ICS'

    def test_caldav_password_none_when_not_set(self, app, db, test_user):
        sub = CalendarSubscription(
            user_id=test_user.id,
            name='No Password Sub',
            url='https://caldav.example.com/cal/',
            subscription_type='caldav',
        )
        db.session.add(sub)
        db.session.commit()
        with app.app_context():
            assert sub.caldav_password is None

    def test_caldav_password_setter_clears_on_none(self, app, db, test_user):
        sub = _create_caldav_sub(db, test_user, password='initial')
        assert sub.caldav_password_enc is not None
        with app.app_context():
            sub.caldav_password = None
        assert sub.caldav_password_enc is None

    def test_caldav_password_roundtrip(self, app, db, test_user):
        """Setting and getting the password via the property is lossless."""
        sub = CalendarSubscription(
            user_id=test_user.id,
            name='Roundtrip Sub',
            url='https://caldav.example.com/cal/',
            subscription_type='caldav',
        )
        with app.app_context():
            sub.caldav_password = 'roundtrip-value-123'
        db.session.add(sub)
        db.session.commit()
        with app.app_context():
            assert sub.caldav_password == 'roundtrip-value-123'

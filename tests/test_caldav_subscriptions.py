"""Tests for CalDAV subscription behavior using the high-level caldav service."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.calendar_subscription import CalendarSubscription
from app.models.subscription_event import SubscriptionEvent as SubscriptionEventRow


TIMED_EVENT_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:timed-001@example.com\r\n"
    "SUMMARY:Timed Event\r\n"
    "DTSTART:20261001T090000Z\r\n"
    "DTEND:20261001T100000Z\r\n"
    "LOCATION:Online\r\n"
    "DESCRIPTION:Timed event description\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

ALL_DAY_EVENT_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:allday-001@example.com\r\n"
    "SUMMARY:All Day Event\r\n"
    "DTSTART;VALUE=DATE:20261002\r\n"
    "DTEND;VALUE=DATE:20261003\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _create_caldav_sub(db, user, url='https://caldav.example.com/remote.php/dav'):
    sub = CalendarSubscription(
        user_id=user.id,
        name='Work',
        url=url,
        subscription_type='caldav',
        caldav_username='alice@example.com',
        enabled=True,
    )
    sub.caldav_password = 'app-password'
    db.session.add(sub)
    db.session.commit()
    return sub


class TestCaldavLibraryService:
    def test_direct_calendar_url_flow_uses_calendar_object(self, app, db, test_user):
        from app.services import caldav_subscriptions as svc

        sub = _create_caldav_sub(
            db,
            test_user,
            url='https://caldav.example.com/remote.php/dav/calendars/alice/personal/',
        )

        fake_calendar = MagicMock()
        fake_calendar.url = sub.url
        fake_calendar.date_search.return_value = [SimpleNamespace(data=TIMED_EVENT_ICS)]

        FakeCalendarClass = MagicMock(return_value=fake_calendar)

        with app.app_context():
            with patch.object(svc, '_import_caldav', return_value=(MagicMock(), FakeCalendarClass)):
                result = svc.fetch_caldav_events(
                    sub,
                    start=datetime(2099, 1, 1),
                    end=datetime(2099, 12, 31),
                    lookahead_days=365,
                    client=MagicMock(),
                )

        FakeCalendarClass.assert_called_once()
        assert len(result.events) == 1
        assert result.events[0].title == 'Timed Event'
        assert result.resolved_calendar_url == sub.url

    def test_root_url_discovery_flow_selects_named_calendar(self, app, db, test_user):
        from app.services import caldav_subscriptions as svc

        sub = _create_caldav_sub(
            db,
            test_user,
            url='https://caldav.example.com/remote.php/dav',
        )
        sub.name = 'Team Calendar'

        wrong_calendar = MagicMock()
        wrong_calendar.name = 'Personal'
        wrong_calendar.url = 'https://caldav.example.com/calendars/alice/personal/'

        target_calendar = MagicMock()
        target_calendar.name = 'Team Calendar'
        target_calendar.url = 'https://caldav.example.com/calendars/alice/team/'
        target_calendar.date_search.return_value = [SimpleNamespace(data=TIMED_EVENT_ICS)]

        principal = MagicMock()
        principal.url = 'https://caldav.example.com/remote.php/dav/principals/users/alice/'
        principal.calendars.return_value = [wrong_calendar, target_calendar]

        client = MagicMock()
        client.principal.return_value = principal

        with app.app_context():
            with patch.object(svc, '_import_caldav', return_value=(MagicMock(), MagicMock())):
                resolution = svc.resolve_caldav_calendar(sub, client=client)

        assert resolution.calendar is target_calendar
        assert resolution.calendar_name == 'Team Calendar'
        assert 'principals/users/alice' in resolution.principal_url

    def test_normalization_handles_timed_and_all_day_events(self, app, db, test_user):
        from app.services import caldav_subscriptions as svc

        sub = _create_caldav_sub(db, test_user)
        fake_calendar = MagicMock()
        fake_calendar.url = sub.url
        fake_calendar.date_search.return_value = [
            SimpleNamespace(data=TIMED_EVENT_ICS),
            SimpleNamespace(data=ALL_DAY_EVENT_ICS),
        ]

        with app.app_context():
            with patch.object(
                svc,
                'resolve_caldav_calendar',
                return_value=svc.CaldavResolutionResult(calendar=fake_calendar, resolved_calendar_url=sub.url),
            ):
                result = svc.fetch_caldav_events(
                    sub,
                    start=datetime(2099, 1, 1),
                    end=datetime(2099, 12, 31),
                    lookahead_days=365,
                )

        assert len(result.events) == 2
        titles = {event.title for event in result.events}
        assert titles == {'Timed Event', 'All Day Event'}
        all_day = [event for event in result.events if event.title == 'All Day Event'][0]
        assert all_day.all_day is True


class TestRefreshAndStatusBehavior:
    def test_refresh_materializes_rows_and_status_detail(self, app, db, test_user):
        from app.services import calendar_subscriptions as svc

        sub = _create_caldav_sub(db, test_user)
        svc.invalidate_cache(sub.id)

        fake_result = SimpleNamespace(
            events=[
                svc.SubscriptionEvent(
                    id=f'sub_{sub.id}_timed-001@example.com',
                    title='Timed Event',
                    start_at=datetime(2099, 2, 1, 9, 0),
                    end_at=datetime(2099, 2, 1, 10, 0),
                    source_id=sub.id,
                    source_name=sub.name,
                    source_type='subscription',
                    read_only=True,
                )
            ],
            source_last_modified=None,
            item_count_retrieved=1,
            detail='OK — 1 events imported',
            resolved_calendar_url='https://caldav.example.com/calendars/alice/work/',
            principal_url='https://caldav.example.com/principals/alice/',
            calendar_name='Work',
        )

        with app.app_context():
            with patch('app.services.caldav_subscriptions.refresh_caldav_subscription', return_value=fake_result):
                events = svc.refresh_subscription_events(sub, force=True)

        assert len(events) == 1

        rows = SubscriptionEventRow.query.filter_by(subscription_id=sub.id).all()
        assert len(rows) == 1
        db.session.refresh(sub)
        assert sub.last_refresh_status == 'ok'
        assert 'OK — 1 events imported' in (sub.last_refresh_detail or '')
        assert 'url=https://caldav.example.com/calendars/alice/work/' in (sub.last_refresh_detail or '')

    def test_zero_event_response_sets_informative_status(self, app, db, test_user):
        from app.services import calendar_subscriptions as svc

        sub = _create_caldav_sub(db, test_user)
        svc.invalidate_cache(sub.id)

        fake_result = SimpleNamespace(
            events=[],
            source_last_modified=None,
            item_count_retrieved=0,
            detail='Warning — calendar resolved but no events in time window',
            resolved_calendar_url='https://caldav.example.com/calendars/alice/work/',
            principal_url='https://caldav.example.com/principals/alice/',
            calendar_name='Work',
        )

        with app.app_context():
            with patch('app.services.caldav_subscriptions.refresh_caldav_subscription', return_value=fake_result):
                events = svc.refresh_subscription_events(sub, force=True)

        assert events == []
        db.session.refresh(sub)
        assert sub.last_refresh_status == 'ok'
        assert 'Warning — calendar resolved but no events in time window' in (sub.last_refresh_detail or '')

    def test_invalid_auth_exception_updates_error_status(self, app, db, test_user):
        from app.services import calendar_subscriptions as svc

        sub = _create_caldav_sub(db, test_user)

        with app.app_context():
            with patch(
                'app.services.caldav_subscriptions.refresh_caldav_subscription',
                side_effect=RuntimeError('Unauthorized'),
            ):
                result = svc.refresh_subscription_events(sub, force=True)

        assert result == []
        db.session.refresh(sub)
        assert sub.last_refresh_status == 'error'
        assert 'Unauthorized' in (sub.last_error or '')


class TestRefreshDispatchAndSecurity:
    def test_ics_subscription_still_uses_ics_fetch(self, app, db, test_user):
        from app.services import calendar_subscriptions as svc

        sub = CalendarSubscription(
            user_id=test_user.id,
            name='ICS Sub',
            url='https://example.com/feed.ics',
            subscription_type='ics',
        )
        db.session.add(sub)
        db.session.commit()

        with app.app_context():
            with patch.object(svc, 'fetch_calendar_feed', return_value=(b'BEGIN:VCALENDAR\nEND:VCALENDAR\n', None)) as mock_ics:
                with patch.object(svc, 'fetch_caldav_events_with_metadata') as mock_caldav:
                    with patch.object(svc, '_update_db_status'):
                        svc.refresh_subscription_events(sub, force=True)

        mock_ics.assert_called_once()
        mock_caldav.assert_not_called()

    def test_password_encrypted_at_rest(self, auth_client, db, test_user):
        with patch('app.services.calendar_subscriptions._host_resolves_to_private', return_value=False):
            auth_client.post(
                '/calendar-subscriptions/new',
                data={
                    'name': 'Encrypted',
                    'subscription_type': 'caldav',
                    'url': 'https://caldav.example.com/remote.php/dav/calendars/alice/work/',
                    'caldav_username': 'alice@example.com',
                    'caldav_password': 'my-secret-password',
                    'enabled': 'on',
                },
                follow_redirects=False,
            )

        sub = CalendarSubscription.query.filter_by(user_id=test_user.id, name='Encrypted').first()
        assert sub is not None
        assert sub.caldav_password_enc is not None
        assert 'my-secret-password' not in (sub.caldav_password_enc or '')

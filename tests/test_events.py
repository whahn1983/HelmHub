"""
tests/test_events.py
~~~~~~~~~~~~~~~~~~~~

Tests for HelmHub events routes:
  - Listing events
  - Creating events (valid and invalid)
  - Editing events
  - Deleting events
"""

from datetime import datetime, timedelta

import pytest

from app.models import Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_START = '2099-12-31T10:00'
_END = '2099-12-31T11:00'


def _create_event(db, user, title='Test Event', start_at=None, end_at=None, location=None, notes=None):
    """Persist an Event directly via the ORM and return it."""
    if start_at is None:
        start_at = datetime(2099, 12, 31, 10, 0)
    event = Event(
        user_id=user.id,
        title=title,
        start_at=start_at,
        end_at=end_at,
        location=location,
        notes=notes,
    )
    db.session.add(event)
    db.session.commit()
    return event


def _post_new_event(client, title='My Event', start_at=_START, end_at='', location='', notes='', headers=None):
    """POST to /events/new with the form fields the route expects."""
    data = {
        'title': title,
        'start_at': start_at,
        'end_at': end_at,
        'location': location,
        'notes': notes,
    }
    return client.post('/events/new', data=data, headers=headers or {}, follow_redirects=False)


def _post_edit_event(client, event_id, title='Updated Event', start_at=_START, end_at='', location='', notes='', headers=None):
    """POST to /events/<id>/edit."""
    data = {
        'title': title,
        'start_at': start_at,
        'end_at': end_at,
        'location': location,
        'notes': notes,
    }
    return client.post(f'/events/{event_id}/edit', data=data, headers=headers or {}, follow_redirects=False)


# ---------------------------------------------------------------------------
# Event list
# ---------------------------------------------------------------------------

class TestEventIndex:
    def test_events_page_requires_auth(self, client):
        """Unauthenticated GET /events/ redirects to login."""
        response = client.get('/events/', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_events_page_returns_200(self, auth_client):
        """Authenticated GET /events/ returns 200."""
        response = auth_client.get('/events/')
        assert response.status_code == 200

    def test_events_page_shows_existing_event(self, auth_client, db, test_user):
        """A persisted event title appears in the rendered list."""
        _create_event(db, test_user, title='Team standup')
        response = auth_client.get('/events/')
        assert b'Team standup' in response.data

    def test_events_view_today_returns_200(self, auth_client):
        """GET /events/?view=today returns 200."""
        response = auth_client.get('/events/?view=today')
        assert response.status_code == 200

    def test_events_view_upcoming_returns_200(self, auth_client):
        """GET /events/?view=upcoming returns 200."""
        response = auth_client.get('/events/?view=upcoming')
        assert response.status_code == 200

    def test_events_only_shows_own(self, auth_client, db, test_user):
        """Events belonging to other users are not visible."""
        from app.models import User
        other = User(username='eventstranger')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        _create_event(db, other, title='Private event')
        response = auth_client.get('/events/')
        assert b'Private event' not in response.data

    def test_events_index_paginates_first_10(self, auth_client, db, test_user):
        """The first page shows 10 events and hides later rows until load-more."""
        base = datetime(2099, 12, 1, 9, 0)
        for idx in range(11):
            _create_event(
                db,
                test_user,
                title=f'Paged Event {idx + 1}',
                start_at=base + timedelta(minutes=idx),
            )

        response = auth_client.get('/events/?view=all')

        assert response.status_code == 200
        assert b'Paged Event 1' in response.data
        assert b'Paged Event 10' in response.data
        assert b'Paged Event 11' not in response.data
        assert b'Load more' in response.data

    def test_events_index_load_more_returns_next_page(self, auth_client, db, test_user):
        """HTMX page=2 returns appendable markup with the remaining events."""
        base = datetime(2099, 12, 1, 9, 0)
        for idx in range(11):
            _create_event(
                db,
                test_user,
                title=f'Chunk Event {idx + 1}',
                start_at=base + timedelta(minutes=idx),
            )

        response = auth_client.get(
            '/events/?view=all&page=2',
            headers={'HX-Request': 'true'},
        )

        assert response.status_code == 200
        assert b'id="events-appended"' in response.data
        assert b'Chunk Event 11' in response.data


# ---------------------------------------------------------------------------
# Create event
# ---------------------------------------------------------------------------

class TestEventCreate:
    def test_get_new_event_page_returns_200(self, auth_client):
        """GET /events/new renders the create-event form."""
        response = auth_client.get('/events/new')
        assert response.status_code == 200

    def test_post_new_event_creates_event(self, auth_client, db, test_user):
        """POST /events/new with valid data creates an event in the DB."""
        response = _post_new_event(auth_client, title='Sprint planning')
        assert response.status_code in (301, 302)
        event = Event.query.filter_by(user_id=test_user.id, title='Sprint planning').first()
        assert event is not None

    def test_post_new_event_stores_start_at(self, auth_client, db, test_user):
        """The start_at datetime is persisted correctly."""
        _post_new_event(auth_client, title='Timed event', start_at='2099-07-04T14:00')
        event = Event.query.filter_by(user_id=test_user.id, title='Timed event').first()
        assert event is not None
        assert event.start_at is not None
        assert event.start_at.month == 7
        assert event.start_at.day == 4

    def test_post_new_event_stores_end_at(self, auth_client, db, test_user):
        """An end_at datetime is persisted when provided."""
        _post_new_event(auth_client, title='Event with end', start_at=_START, end_at=_END)
        event = Event.query.filter_by(user_id=test_user.id, title='Event with end').first()
        assert event is not None
        assert event.end_at is not None

    def test_post_new_event_end_at_optional(self, auth_client, db, test_user):
        """end_at is stored as None when not provided."""
        _post_new_event(auth_client, title='No end event', end_at='')
        event = Event.query.filter_by(user_id=test_user.id, title='No end event').first()
        assert event is not None
        assert event.end_at is None

    def test_post_new_event_stores_location(self, auth_client, db, test_user):
        """Location is persisted when provided."""
        _post_new_event(auth_client, title='Located event', location='Conference Room A')
        event = Event.query.filter_by(user_id=test_user.id, title='Located event').first()
        assert event is not None
        assert event.location == 'Conference Room A'

    def test_post_new_event_location_none_when_empty(self, auth_client, db, test_user):
        """Empty location is stored as None."""
        _post_new_event(auth_client, title='No location event', location='')
        event = Event.query.filter_by(user_id=test_user.id, title='No location event').first()
        assert event is not None
        assert event.location is None

    def test_post_new_event_stores_notes(self, auth_client, db, test_user):
        """Notes are persisted when provided."""
        _post_new_event(auth_client, title='Event with notes', notes='Bring laptop')
        event = Event.query.filter_by(user_id=test_user.id, title='Event with notes').first()
        assert event is not None
        assert event.notes == 'Bring laptop'

    def test_post_new_event_redirects_to_list(self, auth_client, db, test_user):
        """After creation the user is redirected to the events index."""
        response = _post_new_event(auth_client, title='Redirect event')
        location = response.headers.get('Location', '')
        assert '/events' in location

    def test_post_new_event_missing_title_returns_422(self, auth_client):
        """Submitting without a title returns 422."""
        response = _post_new_event(auth_client, title='')
        assert response.status_code == 422

    def test_post_new_event_missing_start_returns_422(self, auth_client):
        """Submitting without a start_at date returns 422."""
        response = _post_new_event(auth_client, start_at='')
        assert response.status_code == 422

    def test_post_new_event_end_before_start_returns_422(self, auth_client):
        """Submitting end_at before start_at returns 422."""
        response = _post_new_event(
            auth_client,
            start_at='2099-12-31T12:00',
            end_at='2099-12-31T10:00',
        )
        assert response.status_code == 422

    def test_post_new_event_requires_auth(self, client):
        """Unauthenticated POST to /events/new redirects to login."""
        response = _post_new_event(client, title='Should fail')
        assert response.status_code in (301, 302)


# ---------------------------------------------------------------------------
# Edit event
# ---------------------------------------------------------------------------

class TestEventEdit:
    def test_get_edit_event_page_returns_200(self, auth_client, db, test_user):
        """GET /events/<id>/edit renders the edit form."""
        event = _create_event(db, test_user, title='Editable event')
        response = auth_client.get(f'/events/{event.id}/edit')
        assert response.status_code == 200

    def test_edit_updates_title(self, auth_client, db, test_user):
        """POST /events/<id>/edit persists the new title."""
        event = _create_event(db, test_user, title='Old event title')
        _post_edit_event(auth_client, event.id, title='New event title')
        db.session.refresh(event)
        assert event.title == 'New event title'

    def test_edit_updates_start_at(self, auth_client, db, test_user):
        """POST /events/<id>/edit persists the new start_at."""
        event = _create_event(db, test_user, title='Reschedule event')
        _post_edit_event(auth_client, event.id, title='Reschedule event', start_at='2099-08-15T09:00')
        db.session.refresh(event)
        assert event.start_at.month == 8
        assert event.start_at.day == 15

    def test_edit_updates_end_at(self, auth_client, db, test_user):
        """POST /events/<id>/edit persists the new end_at."""
        event = _create_event(db, test_user, title='End edit event')
        _post_edit_event(auth_client, event.id, title='End edit event', end_at='2099-12-31T12:00')
        db.session.refresh(event)
        assert event.end_at is not None

    def test_edit_clears_end_at(self, auth_client, db, test_user):
        """POST /events/<id>/edit with empty end_at stores None."""
        event = _create_event(db, test_user, title='Clear end event', end_at=datetime(2099, 12, 31, 11, 0))
        _post_edit_event(auth_client, event.id, title='Clear end event', end_at='')
        db.session.refresh(event)
        assert event.end_at is None

    def test_edit_updates_location(self, auth_client, db, test_user):
        """POST /events/<id>/edit persists the new location."""
        event = _create_event(db, test_user, title='Location event')
        _post_edit_event(auth_client, event.id, title='Location event', location='Room B')
        db.session.refresh(event)
        assert event.location == 'Room B'

    def test_edit_clears_location(self, auth_client, db, test_user):
        """POST /events/<id>/edit with empty location stores None."""
        event = _create_event(db, test_user, title='Clear location event', location='Old room')
        _post_edit_event(auth_client, event.id, title='Clear location event', location='')
        db.session.refresh(event)
        assert event.location is None

    def test_edit_updates_notes(self, auth_client, db, test_user):
        """POST /events/<id>/edit persists the new notes."""
        event = _create_event(db, test_user, title='Notes event', notes='old notes')
        _post_edit_event(auth_client, event.id, title='Notes event', notes='new notes')
        db.session.refresh(event)
        assert event.notes == 'new notes'

    def test_edit_redirects_on_success(self, auth_client, db, test_user):
        """Successful edit redirects to the events index."""
        event = _create_event(db, test_user, title='Redirect edit event')
        response = _post_edit_event(auth_client, event.id, title='Updated event')
        assert response.status_code in (301, 302)
        assert '/events' in response.headers.get('Location', '')

    def test_edit_missing_title_returns_422(self, auth_client, db, test_user):
        """POST /events/<id>/edit without a title returns 422."""
        event = _create_event(db, test_user, title='Title required event')
        response = _post_edit_event(auth_client, event.id, title='')
        assert response.status_code == 422

    def test_edit_end_before_start_returns_422(self, auth_client, db, test_user):
        """POST /events/<id>/edit with end_at before start_at returns 422."""
        event = _create_event(db, test_user, title='Invalid end event')
        response = _post_edit_event(
            auth_client, event.id,
            title='Invalid end event',
            start_at='2099-12-31T12:00',
            end_at='2099-12-31T10:00',
        )
        assert response.status_code == 422

    def test_edit_nonexistent_returns_404(self, auth_client):
        """Editing an event that does not exist returns 404."""
        response = _post_edit_event(auth_client, 999999)
        assert response.status_code == 404

    def test_edit_another_users_event_returns_404(self, auth_client, db):
        """Users cannot edit another user's event."""
        from app.models import User
        other = User(username='eventeditor')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        event = _create_event(db, other, title='Others event')
        response = _post_edit_event(auth_client, event.id, title='Hijacked')
        assert response.status_code == 404

    def test_edit_requires_auth(self, client, db, test_user):
        """Unauthenticated edit attempt redirects to login."""
        event = _create_event(db, test_user, title='Auth edit event')
        response = client.post(f'/events/{event.id}/edit', data={'title': 'x', 'start_at': _START}, follow_redirects=False)
        assert response.status_code in (301, 302)


# ---------------------------------------------------------------------------
# Delete event
# ---------------------------------------------------------------------------

class TestEventDelete:
    def test_delete_removes_event(self, auth_client, db, test_user):
        """POST /events/<id>/delete removes the event from the database."""
        event = _create_event(db, test_user, title='Temporary event')
        event_id = event.id
        auth_client.post(f'/events/{event_id}/delete')
        assert db.session.get(Event, event_id) is None

    def test_delete_redirects(self, auth_client, db, test_user):
        """Deleting an event issues a redirect (non-HTMX)."""
        event = _create_event(db, test_user, title='Delete redirect event')
        response = auth_client.post(f'/events/{event.id}/delete', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_delete_nonexistent_returns_404(self, auth_client):
        """Attempting to delete an event that does not exist returns 404."""
        response = auth_client.post('/events/999999/delete')
        assert response.status_code == 404

    def test_delete_another_users_event_returns_404(self, auth_client, db):
        """Users cannot delete another user's event."""
        from app.models import User
        other = User(username='eventdeleter')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        event = _create_event(db, other, title='Others delete event')
        response = auth_client.post(f'/events/{event.id}/delete')
        assert response.status_code == 404

    def test_delete_requires_auth(self, client, db, test_user):
        """Unauthenticated delete attempt redirects to login."""
        event = _create_event(db, test_user, title='Auth delete event')
        response = client.post(f'/events/{event.id}/delete', follow_redirects=False)
        assert response.status_code in (301, 302)

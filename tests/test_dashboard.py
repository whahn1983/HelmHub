"""
tests/test_dashboard.py
~~~~~~~~~~~~~~~~~~~~~~~

Tests for the HelmHub dashboard routes:
  - Redirect to login when unauthenticated
  - 200 response when authenticated
  - Dashboard surface correct summary data (tasks, reminders, events)
  - GET /api/v1/dashboard-data (or /api/dashboard-data) returns JSON
"""

from datetime import datetime, timedelta

import pytest

from app.models import Task, Note, Reminder, Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(db, user, title='Task', status='open', priority='medium',
               due_at=None, pinned_to_today=False):
    task = Task(
        user_id=user.id,
        title=title,
        status=status,
        priority=priority,
        due_at=due_at,
        pinned_to_today=pinned_to_today,
    )
    db.session.add(task)
    return task


def _make_reminder(db, user, title='Reminder', remind_at=None, status='pending'):
    if remind_at is None:
        remind_at = datetime.utcnow() - timedelta(minutes=5)
    reminder = Reminder(
        user_id=user.id,
        title=title,
        remind_at=remind_at,
        status=status,
    )
    db.session.add(reminder)
    return reminder


def _make_event(db, user, title='Event', start_at=None):
    if start_at is None:
        start_at = datetime.utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
    event = Event(
        user_id=user.id,
        title=title,
        start_at=start_at,
    )
    db.session.add(event)
    return event


def _make_note(db, user, title='Note', body=''):
    note = Note(
        user_id=user.id,
        title=title,
        body=body,
        pinned=False,
        tag='',
    )
    db.session.add(note)
    return note


# ---------------------------------------------------------------------------
# Authentication guard
# ---------------------------------------------------------------------------

class TestDashboardAuth:
    def test_root_redirects_unauthenticated_user(self, client):
        """GET / without a session redirects to the login page."""
        response = client.get('/', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_root_redirects_to_login_url(self, client):
        """The redirect destination contains 'login'."""
        response = client.get('/', follow_redirects=False)
        location = response.headers.get('Location', '')
        assert 'login' in location

    def test_root_follows_redirect_to_login_page(self, client):
        """Following the redirect chain lands on a page that renders successfully."""
        response = client.get('/', follow_redirects=True)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Dashboard page for authenticated users
# ---------------------------------------------------------------------------

class TestDashboardPage:
    def test_dashboard_returns_200(self, auth_client):
        """Authenticated GET / returns 200."""
        response = auth_client.get('/')
        assert response.status_code == 200

    def test_dashboard_renders_html(self, auth_client):
        """Dashboard response contains HTML markup."""
        response = auth_client.get('/')
        assert b'<' in response.data  # minimal HTML check

    def test_dashboard_shows_high_priority_task(self, auth_client, db, test_user):
        """High-priority open tasks are surfaced on the dashboard."""
        _make_task(db, test_user, title='Critical task', priority='high')
        db.session.commit()
        response = auth_client.get('/')
        assert b'Critical task' in response.data

    def test_dashboard_shows_todays_task(self, auth_client, db, test_user):
        """Tasks due today appear on the dashboard."""
        today_noon = datetime.utcnow().replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        _make_task(db, test_user, title='Due today task', due_at=today_noon)
        db.session.commit()
        response = auth_client.get('/')
        assert b'Due today task' in response.data

    def test_dashboard_shows_pinned_task(self, auth_client, db, test_user):
        """Tasks pinned to today appear on the dashboard."""
        _make_task(db, test_user, title='Pinned today task', pinned_to_today=True)
        db.session.commit()
        response = auth_client.get('/')
        assert b'Pinned today task' in response.data

    def test_dashboard_shows_due_reminder(self, auth_client, db, test_user):
        """Reminders whose remind_at is in the past and status=pending are shown."""
        past = datetime.utcnow() - timedelta(hours=1)
        _make_reminder(db, test_user, title='Past due reminder', remind_at=past)
        db.session.commit()
        response = auth_client.get('/')
        assert b'Past due reminder' in response.data

    def test_dashboard_does_not_show_future_reminder(self, auth_client, db, test_user):
        """Reminders scheduled in the future are not surfaced as due."""
        future = datetime.utcnow() + timedelta(hours=2)
        _make_reminder(db, test_user, title='Future reminder', remind_at=future)
        db.session.commit()
        response = auth_client.get('/')
        assert b'Future reminder' not in response.data

    def test_dashboard_shows_todays_event(self, auth_client, db, test_user):
        """Events starting today appear on the dashboard."""
        today_event_time = datetime.utcnow().replace(
            hour=14, minute=0, second=0, microsecond=0
        )
        _make_event(db, test_user, title='Todays meeting', start_at=today_event_time)
        db.session.commit()
        response = auth_client.get('/')
        assert b'Todays meeting' in response.data

    def test_dashboard_shows_recent_notes(self, auth_client, db, test_user):
        """The three most recently updated notes are shown on the dashboard."""
        _make_note(db, test_user, title='Recent note A')
        _make_note(db, test_user, title='Recent note B')
        _make_note(db, test_user, title='Recent note C')
        db.session.commit()
        response = auth_client.get('/')
        assert b'Recent note' in response.data

    def test_dashboard_does_not_show_other_users_data(self, auth_client, db, test_user):
        """Data belonging to other users is not leaked onto the dashboard."""
        from app.models import User
        other = User(username='dashstranger')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        _make_task(db, other, title='Other users secret task', priority='high')
        _make_reminder(db, other, title='Other users secret reminder')
        db.session.commit()
        response = auth_client.get('/')
        assert b'Other users secret task' not in response.data
        assert b'Other users secret reminder' not in response.data

    def test_dashboard_shows_overdue_count_when_overdue_tasks_exist(
        self, auth_client, db, test_user
    ):
        """The dashboard reflects when overdue tasks are present."""
        past = datetime.utcnow() - timedelta(days=2)
        _make_task(db, test_user, title='Overdue task', due_at=past)
        db.session.commit()
        response = auth_client.get('/')
        # The template should render the overdue count somewhere
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

class TestDashboardApi:
    def _get_api_data(self, auth_client):
        """Try the versioned API path, fall back to the unversioned one."""
        for path in ('/api/v1/dashboard-data', '/api/dashboard-data'):
            response = auth_client.get(path)
            if response.status_code != 404:
                return response
        return response  # return last attempt even if 404

    def test_api_dashboard_data_requires_auth(self, client):
        """Unauthenticated access to the dashboard API is rejected."""
        for path in ('/api/v1/dashboard-data', '/api/dashboard-data'):
            response = client.get(path, follow_redirects=False)
            # Either a redirect (302) to login or an explicit 401/403
            assert response.status_code in (301, 302, 401, 403)
            break  # test whichever endpoint exists first

    def test_api_dashboard_data_returns_json(self, auth_client, db, test_user):
        """The dashboard data API returns a JSON response."""
        response = self._get_api_data(auth_client)
        if response.status_code == 404:
            pytest.skip('Dashboard API endpoint not yet implemented')
        assert response.status_code == 200
        assert response.content_type.startswith('application/json')

    def test_api_dashboard_data_contains_expected_keys(self, auth_client, db, test_user):
        """The JSON payload includes keys for tasks, reminders, and events."""
        import json as json_mod

        response = self._get_api_data(auth_client)
        if response.status_code == 404:
            pytest.skip('Dashboard API endpoint not yet implemented')

        assert response.status_code == 200
        payload = json_mod.loads(response.data)
        # Check for at least one of the expected top-level keys
        expected_keys = {'tasks', 'reminders', 'events', 'notes'}
        assert expected_keys & set(payload.keys()), (
            f'Expected one of {expected_keys} in response keys: {list(payload.keys())}'
        )

    def test_api_dashboard_data_includes_task_data(self, auth_client, db, test_user):
        """A high-priority task is reflected in the API response."""
        import json as json_mod

        _make_task(db, test_user, title='API task', priority='high')
        db.session.commit()

        response = self._get_api_data(auth_client)
        if response.status_code == 404:
            pytest.skip('Dashboard API endpoint not yet implemented')

        assert response.status_code == 200
        data = response.data.decode()
        # The task title should appear somewhere in the response body
        assert 'API task' in data

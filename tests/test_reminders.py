"""
tests/test_reminders.py
~~~~~~~~~~~~~~~~~~~~~~~

Tests for HelmHub reminders routes:
  - Listing reminders
  - Creating reminders (valid and invalid)
  - Editing reminders
  - Completing reminders
  - Dismissing reminders
  - Snoozing reminders
  - Deleting reminders
"""

from datetime import datetime, timedelta

import pytest

from app.models import Reminder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FUTURE_DT = '2099-12-31T10:00'


def _create_reminder(db, user, title='Test Reminder', notes='', status=None, remind_at=None, snoozed_until=None):
    """Persist a Reminder directly via the ORM and return it."""
    if remind_at is None:
        remind_at = datetime(2099, 12, 31, 10, 0)
    if status is None:
        status = Reminder.STATUS_PENDING
    reminder = Reminder(
        user_id=user.id,
        title=title,
        notes=notes,
        remind_at=remind_at,
        status=status,
        snoozed_until=snoozed_until,
    )
    db.session.add(reminder)
    db.session.commit()
    return reminder


def _post_new_reminder(client, title='My Reminder', remind_at=_FUTURE_DT, notes='', headers=None):
    """POST to /reminders/new with the form fields the route expects."""
    data = {
        'title': title,
        'remind_at': remind_at,
        'notes': notes,
    }
    return client.post('/reminders/new', data=data, headers=headers or {}, follow_redirects=False)


def _post_edit_reminder(client, reminder_id, title='Updated Reminder', remind_at=_FUTURE_DT, notes='', headers=None):
    """POST to /reminders/<id>/edit."""
    data = {
        'title': title,
        'remind_at': remind_at,
        'notes': notes,
    }
    return client.post(f'/reminders/{reminder_id}/edit', data=data, headers=headers or {}, follow_redirects=False)


# ---------------------------------------------------------------------------
# Reminder list
# ---------------------------------------------------------------------------

class TestReminderIndex:
    def test_reminders_page_requires_auth(self, client):
        """Unauthenticated GET /reminders/ redirects to login."""
        response = client.get('/reminders/', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_reminders_page_returns_200(self, auth_client):
        """Authenticated GET /reminders/ returns 200."""
        response = auth_client.get('/reminders/')
        assert response.status_code == 200

    def test_reminders_page_shows_existing_reminder(self, auth_client, db, test_user):
        """A persisted reminder title appears in the rendered list."""
        _create_reminder(db, test_user, title='Doctor appointment')
        response = auth_client.get('/reminders/')
        assert b'Doctor appointment' in response.data

    def test_reminders_status_filter_pending(self, auth_client, db, test_user):
        """?status=pending shows only pending reminders."""
        _create_reminder(db, test_user, title='Pending reminder', status=Reminder.STATUS_PENDING)
        _create_reminder(db, test_user, title='Done reminder', status=Reminder.STATUS_COMPLETED)
        response = auth_client.get('/reminders/?status=pending')
        assert b'Pending reminder' in response.data
        assert b'Done reminder' not in response.data

    def test_reminders_status_filter_completed(self, auth_client, db, test_user):
        """?status=completed shows only completed reminders."""
        _create_reminder(db, test_user, title='Completed one', status=Reminder.STATUS_COMPLETED)
        _create_reminder(db, test_user, title='Active one', status=Reminder.STATUS_PENDING)
        response = auth_client.get('/reminders/?status=completed')
        assert b'Completed one' in response.data
        assert b'Active one' not in response.data

    def test_reminders_only_shows_own(self, auth_client, db, test_user):
        """Reminders belonging to other users are not visible."""
        from app.models import User
        other = User(username='reminderstranger')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        _create_reminder(db, other, title='Private reminder')
        response = auth_client.get('/reminders/')
        assert b'Private reminder' not in response.data


# ---------------------------------------------------------------------------
# Create reminder
# ---------------------------------------------------------------------------

class TestReminderCreate:
    def test_get_new_reminder_page_returns_200(self, auth_client):
        """GET /reminders/new renders the create-reminder form."""
        response = auth_client.get('/reminders/new')
        assert response.status_code == 200

    def test_post_new_reminder_creates_reminder(self, auth_client, db, test_user):
        """POST /reminders/new with valid data creates a reminder in the DB."""
        response = _post_new_reminder(auth_client, title='Pick up keys')
        assert response.status_code in (301, 302)
        reminder = Reminder.query.filter_by(user_id=test_user.id, title='Pick up keys').first()
        assert reminder is not None

    def test_post_new_reminder_status_is_pending(self, auth_client, db, test_user):
        """Newly created reminders have STATUS_PENDING."""
        _post_new_reminder(auth_client, title='Status check reminder')
        reminder = Reminder.query.filter_by(user_id=test_user.id, title='Status check reminder').first()
        assert reminder is not None
        assert reminder.status == Reminder.STATUS_PENDING

    def test_post_new_reminder_stores_remind_at(self, auth_client, db, test_user):
        """The remind_at datetime is persisted correctly."""
        _post_new_reminder(auth_client, title='Timed reminder', remind_at='2099-06-15T09:30')
        reminder = Reminder.query.filter_by(user_id=test_user.id, title='Timed reminder').first()
        assert reminder is not None
        assert reminder.remind_at is not None
        assert reminder.remind_at.year == 2099
        assert reminder.remind_at.month == 6

    def test_post_new_reminder_stores_notes(self, auth_client, db, test_user):
        """Notes submitted in the form are persisted."""
        _post_new_reminder(auth_client, title='Notes reminder', notes='Bring passport')
        reminder = Reminder.query.filter_by(user_id=test_user.id, title='Notes reminder').first()
        assert reminder is not None
        assert reminder.notes == 'Bring passport'

    def test_post_new_reminder_redirects_to_list(self, auth_client, db, test_user):
        """After creation the user is redirected to the reminders index."""
        response = _post_new_reminder(auth_client, title='Redirect reminder')
        location = response.headers.get('Location', '')
        assert '/reminders' in location

    def test_post_new_reminder_missing_title_returns_422(self, auth_client):
        """Submitting without a title returns 422."""
        response = _post_new_reminder(auth_client, title='')
        assert response.status_code == 422

    def test_post_new_reminder_missing_date_returns_422(self, auth_client):
        """Submitting without a remind_at date returns 422."""
        response = _post_new_reminder(auth_client, remind_at='')
        assert response.status_code == 422

    def test_post_new_reminder_requires_auth(self, client):
        """Unauthenticated POST to /reminders/new redirects to login."""
        response = _post_new_reminder(client, title='Should fail')
        assert response.status_code in (301, 302)

    def test_post_new_reminder_htmx_returns_trigger_header(self, auth_client, db, test_user):
        """HTMX create request returns HX-Trigger: reminderCreated."""
        response = _post_new_reminder(
            auth_client, title='HTMX reminder',
            headers={'HX-Request': 'true'},
        )
        assert response.headers.get('HX-Trigger') == 'reminderCreated'


# ---------------------------------------------------------------------------
# Edit reminder
# ---------------------------------------------------------------------------

class TestReminderEdit:
    def test_get_edit_reminder_page_returns_200(self, auth_client, db, test_user):
        """GET /reminders/<id>/edit renders the edit form."""
        reminder = _create_reminder(db, test_user, title='Editable reminder')
        response = auth_client.get(f'/reminders/{reminder.id}/edit')
        assert response.status_code == 200

    def test_edit_updates_title(self, auth_client, db, test_user):
        """POST /reminders/<id>/edit persists the new title."""
        reminder = _create_reminder(db, test_user, title='Old title')
        _post_edit_reminder(auth_client, reminder.id, title='New title')
        db.session.refresh(reminder)
        assert reminder.title == 'New title'

    def test_edit_updates_remind_at(self, auth_client, db, test_user):
        """POST /reminders/<id>/edit persists the new remind_at."""
        reminder = _create_reminder(db, test_user, title='Time edit reminder')
        _post_edit_reminder(auth_client, reminder.id, title='Time edit reminder', remind_at='2099-03-20T08:00')
        db.session.refresh(reminder)
        assert reminder.remind_at.month == 3
        assert reminder.remind_at.day == 20

    def test_edit_updates_notes(self, auth_client, db, test_user):
        """POST /reminders/<id>/edit persists the new notes."""
        reminder = _create_reminder(db, test_user, title='Notes edit reminder', notes='old notes')
        _post_edit_reminder(auth_client, reminder.id, title='Notes edit reminder', notes='new notes')
        db.session.refresh(reminder)
        assert reminder.notes == 'new notes'

    def test_edit_reopens_completed_reminder(self, auth_client, db, test_user):
        """Editing a completed reminder resets its status to pending."""
        reminder = _create_reminder(db, test_user, title='Done reminder', status=Reminder.STATUS_COMPLETED)
        _post_edit_reminder(auth_client, reminder.id, title='Done reminder reopened')
        db.session.refresh(reminder)
        assert reminder.status == Reminder.STATUS_PENDING

    def test_edit_reopens_dismissed_reminder(self, auth_client, db, test_user):
        """Editing a dismissed reminder resets its status to pending."""
        reminder = _create_reminder(db, test_user, title='Dismissed reminder', status=Reminder.STATUS_DISMISSED)
        _post_edit_reminder(auth_client, reminder.id, title='Dismissed reminder reopened')
        db.session.refresh(reminder)
        assert reminder.status == Reminder.STATUS_PENDING

    def test_edit_redirects_on_success(self, auth_client, db, test_user):
        """Successful edit redirects to the reminders index."""
        reminder = _create_reminder(db, test_user, title='Redirect edit reminder')
        response = _post_edit_reminder(auth_client, reminder.id, title='Updated')
        assert response.status_code in (301, 302)
        assert '/reminders' in response.headers.get('Location', '')

    def test_edit_missing_title_returns_422(self, auth_client, db, test_user):
        """POST /reminders/<id>/edit without a title returns 422."""
        reminder = _create_reminder(db, test_user, title='Title required')
        response = _post_edit_reminder(auth_client, reminder.id, title='')
        assert response.status_code == 422

    def test_edit_htmx_returns_trigger_header(self, auth_client, db, test_user):
        """HTMX edit request returns HX-Trigger: reminderUpdated."""
        reminder = _create_reminder(db, test_user, title='HTMX edit reminder')
        response = _post_edit_reminder(
            auth_client, reminder.id,
            title='HTMX edited',
            headers={'HX-Request': 'true'},
        )
        assert response.headers.get('HX-Trigger') == 'reminderUpdated'

    def test_edit_nonexistent_returns_404(self, auth_client):
        """Editing a reminder that does not exist returns 404."""
        response = _post_edit_reminder(auth_client, 999999)
        assert response.status_code == 404

    def test_edit_another_users_reminder_returns_404(self, auth_client, db):
        """Users cannot edit another user's reminder."""
        from app.models import User
        other = User(username='remindereditor')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        reminder = _create_reminder(db, other, title='Others reminder')
        response = _post_edit_reminder(auth_client, reminder.id, title='Hijacked')
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Complete reminder
# ---------------------------------------------------------------------------

class TestReminderComplete:
    def test_complete_sets_status(self, auth_client, db, test_user):
        """POST /reminders/<id>/complete marks reminder as completed."""
        reminder = _create_reminder(db, test_user, title='Complete me')
        auth_client.post(f'/reminders/{reminder.id}/complete')
        db.session.refresh(reminder)
        assert reminder.status == Reminder.STATUS_COMPLETED

    def test_complete_redirects(self, auth_client, db, test_user):
        """Completing a reminder issues a redirect (non-HTMX)."""
        reminder = _create_reminder(db, test_user, title='Complete redirect')
        response = auth_client.post(f'/reminders/{reminder.id}/complete', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_complete_htmx_returns_trigger_header(self, auth_client, db, test_user):
        """HTMX complete request returns HX-Trigger: reminderCompleted."""
        reminder = _create_reminder(db, test_user, title='HTMX complete')
        response = auth_client.post(
            f'/reminders/{reminder.id}/complete',
            headers={'HX-Request': 'true'},
        )
        assert response.headers.get('HX-Trigger') == 'reminderCompleted'

    def test_complete_nonexistent_returns_404(self, auth_client):
        """Completing a reminder that does not exist returns 404."""
        response = auth_client.post('/reminders/999999/complete')
        assert response.status_code == 404

    def test_complete_another_users_reminder_returns_404(self, auth_client, db):
        """Users cannot complete another user's reminder."""
        from app.models import User
        other = User(username='remindercompletor')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        reminder = _create_reminder(db, other, title='Others complete')
        response = auth_client.post(f'/reminders/{reminder.id}/complete')
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Dismiss reminder
# ---------------------------------------------------------------------------

class TestReminderDismiss:
    def test_dismiss_sets_status(self, auth_client, db, test_user):
        """POST /reminders/<id>/dismiss marks reminder as dismissed."""
        reminder = _create_reminder(db, test_user, title='Dismiss me')
        auth_client.post(f'/reminders/{reminder.id}/dismiss')
        db.session.refresh(reminder)
        assert reminder.status == Reminder.STATUS_DISMISSED

    def test_dismiss_redirects(self, auth_client, db, test_user):
        """Dismissing a reminder issues a redirect (non-HTMX)."""
        reminder = _create_reminder(db, test_user, title='Dismiss redirect')
        response = auth_client.post(f'/reminders/{reminder.id}/dismiss', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_dismiss_htmx_returns_trigger_header(self, auth_client, db, test_user):
        """HTMX dismiss request returns HX-Trigger: reminderDismissed."""
        reminder = _create_reminder(db, test_user, title='HTMX dismiss')
        response = auth_client.post(
            f'/reminders/{reminder.id}/dismiss',
            headers={'HX-Request': 'true'},
        )
        assert response.headers.get('HX-Trigger') == 'reminderDismissed'

    def test_dismiss_nonexistent_returns_404(self, auth_client):
        """Dismissing a reminder that does not exist returns 404."""
        response = auth_client.post('/reminders/999999/dismiss')
        assert response.status_code == 404

    def test_dismiss_another_users_reminder_returns_404(self, auth_client, db):
        """Users cannot dismiss another user's reminder."""
        from app.models import User
        other = User(username='reminderdismisser')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        reminder = _create_reminder(db, other, title='Others dismiss')
        response = auth_client.post(f'/reminders/{reminder.id}/dismiss')
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Snooze reminder
# ---------------------------------------------------------------------------

class TestReminderSnooze:
    def test_snooze_sets_snoozed_status(self, auth_client, db, test_user):
        """POST /reminders/<id>/snooze sets status to snoozed."""
        reminder = _create_reminder(db, test_user, title='Snooze me')
        auth_client.post(f'/reminders/{reminder.id}/snooze')
        db.session.refresh(reminder)
        assert reminder.status == Reminder.STATUS_SNOOZED

    def test_snooze_sets_snoozed_until(self, auth_client, db, test_user):
        """Snoozing a reminder sets snoozed_until to a future datetime."""
        reminder = _create_reminder(db, test_user, title='Snooze until')
        before = datetime.utcnow()
        auth_client.post(f'/reminders/{reminder.id}/snooze', data={'minutes': '30'})
        db.session.refresh(reminder)
        assert reminder.snoozed_until is not None
        assert reminder.snoozed_until > before

    def test_snooze_default_15_minutes(self, auth_client, db, test_user):
        """Default snooze is 15 minutes when no minutes param is provided."""
        reminder = _create_reminder(db, test_user, title='Default snooze')
        before = datetime.utcnow()
        auth_client.post(f'/reminders/{reminder.id}/snooze')
        db.session.refresh(reminder)
        assert reminder.snoozed_until is not None
        expected_min = before + timedelta(minutes=14)
        expected_max = before + timedelta(minutes=16)
        assert expected_min <= reminder.snoozed_until <= expected_max

    def test_snooze_custom_minutes(self, auth_client, db, test_user):
        """Snooze duration uses the provided minutes parameter."""
        reminder = _create_reminder(db, test_user, title='Custom snooze')
        before = datetime.utcnow()
        auth_client.post(f'/reminders/{reminder.id}/snooze', data={'minutes': '60'})
        db.session.refresh(reminder)
        assert reminder.snoozed_until is not None
        assert reminder.snoozed_until >= before + timedelta(minutes=59)

    def test_snooze_redirects(self, auth_client, db, test_user):
        """Snoozing a reminder issues a redirect (non-HTMX)."""
        reminder = _create_reminder(db, test_user, title='Snooze redirect')
        response = auth_client.post(f'/reminders/{reminder.id}/snooze', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_snooze_htmx_returns_trigger_header(self, auth_client, db, test_user):
        """HTMX snooze request returns HX-Trigger: reminderSnoozed."""
        reminder = _create_reminder(db, test_user, title='HTMX snooze')
        response = auth_client.post(
            f'/reminders/{reminder.id}/snooze',
            headers={'HX-Request': 'true'},
        )
        assert response.headers.get('HX-Trigger') == 'reminderSnoozed'

    def test_snooze_nonexistent_returns_404(self, auth_client):
        """Snoozing a reminder that does not exist returns 404."""
        response = auth_client.post('/reminders/999999/snooze')
        assert response.status_code == 404

    def test_snooze_another_users_reminder_returns_404(self, auth_client, db):
        """Users cannot snooze another user's reminder."""
        from app.models import User
        other = User(username='remindersnoozr')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        reminder = _create_reminder(db, other, title='Others snooze')
        response = auth_client.post(f'/reminders/{reminder.id}/snooze')
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Delete reminder
# ---------------------------------------------------------------------------

class TestReminderDelete:
    def test_delete_removes_reminder(self, auth_client, db, test_user):
        """POST /reminders/<id>/delete removes the reminder from the database."""
        reminder = _create_reminder(db, test_user, title='Temporary reminder')
        reminder_id = reminder.id
        auth_client.post(f'/reminders/{reminder_id}/delete')
        assert db.session.get(Reminder, reminder_id) is None

    def test_delete_redirects(self, auth_client, db, test_user):
        """Deleting a reminder issues a redirect (non-HTMX)."""
        reminder = _create_reminder(db, test_user, title='Delete redirect reminder')
        response = auth_client.post(f'/reminders/{reminder.id}/delete', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_delete_htmx_returns_trigger_header(self, auth_client, db, test_user):
        """HTMX delete request returns HX-Trigger: reminderDeleted."""
        reminder = _create_reminder(db, test_user, title='HTMX delete reminder')
        response = auth_client.post(
            f'/reminders/{reminder.id}/delete',
            headers={'HX-Request': 'true'},
        )
        assert response.headers.get('HX-Trigger') == 'reminderDeleted'

    def test_delete_nonexistent_returns_404(self, auth_client):
        """Attempting to delete a reminder that does not exist returns 404."""
        response = auth_client.post('/reminders/999999/delete')
        assert response.status_code == 404

    def test_delete_another_users_reminder_returns_404(self, auth_client, db):
        """Users cannot delete another user's reminder."""
        from app.models import User
        other = User(username='reminderdeleter')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        reminder = _create_reminder(db, other, title='Others delete reminder')
        response = auth_client.post(f'/reminders/{reminder.id}/delete')
        assert response.status_code == 404

    def test_delete_requires_auth(self, client, db, test_user):
        """Unauthenticated delete attempt redirects to login."""
        reminder = _create_reminder(db, test_user, title='Auth delete reminder')
        response = client.post(f'/reminders/{reminder.id}/delete', follow_redirects=False)
        assert response.status_code in (301, 302)

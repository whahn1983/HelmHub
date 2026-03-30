"""
tests/test_tasks.py
~~~~~~~~~~~~~~~~~~~

Tests for HelmHub task management routes:
  - Listing tasks (all, today, overdue views)
  - Creating tasks (valid and invalid data)
  - Toggling task completion
  - Deleting tasks
"""

from datetime import datetime, timedelta

import pytest

from app.models import Task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_task(db, user, title='Test Task', priority='medium', status='open',
                 due_at=None, pinned_to_today=False):
    """Persist a Task directly via the ORM and return it."""
    task = Task(
        user_id=user.id,
        title=title,
        priority=priority,
        status=status,
        due_at=due_at,
        pinned_to_today=pinned_to_today,
    )
    db.session.add(task)
    db.session.commit()
    return task


def _post_new_task(client, **kwargs):
    """POST to /tasks/new with optional form field overrides."""
    data = {
        'title': kwargs.get('title', 'My Task'),
        'priority': kwargs.get('priority', 'medium'),
        'description': kwargs.get('description', ''),
        'due_date': kwargs.get('due_date', ''),
        'due_time': kwargs.get('due_time', ''),
    }
    if kwargs.get('pinned_to_today'):
        data['pinned_to_today'] = 'on'
    return client.post('/tasks/new', data=data, follow_redirects=False)


# ---------------------------------------------------------------------------
# Task list
# ---------------------------------------------------------------------------

class TestTaskIndex:
    def test_tasks_page_requires_auth(self, client):
        """Unauthenticated access to /tasks/ redirects to login."""
        response = client.get('/tasks/', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_tasks_page_returns_200(self, auth_client):
        """Authenticated GET /tasks/ returns 200."""
        response = auth_client.get('/tasks/')
        assert response.status_code == 200

    def test_tasks_page_shows_existing_task(self, auth_client, db, test_user):
        """A persisted task title appears in the rendered task list."""
        _create_task(db, test_user, title='Buy groceries')
        response = auth_client.get('/tasks/')
        assert b'Buy groceries' in response.data

    def test_tasks_view_today_returns_200(self, auth_client):
        """GET /tasks/?view=today returns 200."""
        response = auth_client.get('/tasks/?view=today')
        assert response.status_code == 200

    def test_tasks_view_today_shows_pinned_task(self, auth_client, db, test_user):
        """A task pinned to today appears in the today view."""
        _create_task(db, test_user, title='Pinned task', pinned_to_today=True)
        response = auth_client.get('/tasks/?view=today')
        assert b'Pinned task' in response.data

    def test_tasks_view_today_shows_task_due_today(self, auth_client, db, test_user):
        """A task with today's due date appears in the today view."""
        today_noon = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
        _create_task(db, test_user, title='Due today', due_at=today_noon)
        response = auth_client.get('/tasks/?view=today')
        assert b'Due today' in response.data

    def test_tasks_view_overdue_returns_200(self, auth_client):
        """GET /tasks/?view=overdue returns 200."""
        response = auth_client.get('/tasks/?view=overdue')
        assert response.status_code == 200

    def test_tasks_view_overdue_shows_past_due_task(self, auth_client, db, test_user):
        """A task with a past due date appears in the overdue view."""
        past = datetime.utcnow() - timedelta(days=3)
        _create_task(db, test_user, title='Past due task', due_at=past)
        response = auth_client.get('/tasks/?view=overdue')
        assert b'Past due task' in response.data

    def test_tasks_view_overdue_excludes_completed(self, auth_client, db, test_user):
        """Completed tasks do not appear in the overdue view."""
        past = datetime.utcnow() - timedelta(days=2)
        _create_task(db, test_user, title='Done task', due_at=past, status='completed')
        response = auth_client.get('/tasks/?view=overdue')
        assert b'Done task' not in response.data

    def test_tasks_view_completed(self, auth_client, db, test_user):
        """Completed tasks appear in the completed view."""
        _create_task(db, test_user, title='Done task', status='completed')
        response = auth_client.get('/tasks/?view=completed')
        assert b'Done task' in response.data

    def test_tasks_search_filters_results(self, auth_client, db, test_user):
        """The search query parameter filters tasks by title substring."""
        _create_task(db, test_user, title='Alpha task')
        _create_task(db, test_user, title='Beta task')
        response = auth_client.get('/tasks/?search=Alpha')
        assert b'Alpha task' in response.data
        assert b'Beta task' not in response.data

    def test_tasks_only_shows_own_tasks(self, auth_client, db, test_user):
        """Tasks belonging to other users are not visible."""
        from app.models import User
        other = User(username='otherone')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        _create_task(db, other, title='Other users secret')
        response = auth_client.get('/tasks/')
        assert b'Other users secret' not in response.data


# ---------------------------------------------------------------------------
# Create task
# ---------------------------------------------------------------------------

class TestTaskCreate:
    def test_get_new_task_page_returns_200(self, auth_client):
        """GET /tasks/new renders the create-task form."""
        response = auth_client.get('/tasks/new')
        assert response.status_code == 200

    def test_post_new_task_creates_task(self, auth_client, db, test_user):
        """POST /tasks/new with valid data creates a new task in the database."""
        response = _post_new_task(auth_client, title='Walk the dog')
        # Expect redirect after creation
        assert response.status_code in (301, 302)
        task = Task.query.filter_by(user_id=test_user.id, title='Walk the dog').first()
        assert task is not None

    def test_post_new_task_redirects_to_task_list(self, auth_client, db, test_user):
        """After creating a task the user is redirected to the task index."""
        response = _post_new_task(auth_client, title='Redirect test task')
        location = response.headers.get('Location', '')
        assert '/tasks' in location

    def test_post_new_task_with_priority(self, auth_client, db, test_user):
        """Priority is stored correctly when provided."""
        _post_new_task(auth_client, title='High prio', priority='high')
        task = Task.query.filter_by(user_id=test_user.id, title='High prio').first()
        assert task is not None
        assert task.priority == 'high'

    def test_post_new_task_with_due_date(self, auth_client, db, test_user):
        """A task created with a due date stores the due_at field."""
        tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%d')
        _post_new_task(auth_client, title='Due tomorrow', due_date=tomorrow)
        task = Task.query.filter_by(user_id=test_user.id, title='Due tomorrow').first()
        assert task is not None
        assert task.due_at is not None

    def test_post_new_task_missing_title_returns_422(self, auth_client):
        """Submitting without a title returns a 422 Unprocessable Entity."""
        response = _post_new_task(auth_client, title='')
        assert response.status_code == 422

    def test_post_new_task_invalid_priority_returns_422(self, auth_client):
        """An unrecognised priority value returns a 422."""
        response = _post_new_task(auth_client, priority='urgent')
        assert response.status_code == 422

    def test_post_new_task_requires_auth(self, client):
        """Unauthenticated POST to /tasks/new redirects to login."""
        response = _post_new_task(client, title='Should fail')
        assert response.status_code in (301, 302)


# ---------------------------------------------------------------------------
# Toggle completion
# ---------------------------------------------------------------------------

class TestTaskComplete:
    def test_complete_marks_task_completed(self, auth_client, db, test_user):
        """POST /tasks/<id>/complete on an open task marks it as completed."""
        task = _create_task(db, test_user, title='Open task')
        auth_client.post(f'/tasks/{task.id}/complete')
        db.session.refresh(task)
        assert task.status == 'completed'

    def test_complete_toggles_back_to_open(self, auth_client, db, test_user):
        """POST /tasks/<id>/complete on a completed task reopens it."""
        task = _create_task(db, test_user, title='Done task', status='completed')
        auth_client.post(f'/tasks/{task.id}/complete')
        db.session.refresh(task)
        assert task.status == 'open'

    def test_complete_redirects(self, auth_client, db, test_user):
        """Toggling completion redirects (non-HTMX request)."""
        task = _create_task(db, test_user, title='Toggle me')
        response = auth_client.post(
            f'/tasks/{task.id}/complete', follow_redirects=False
        )
        assert response.status_code in (301, 302)

    def test_complete_nonexistent_task_returns_404(self, auth_client):
        """POST to a task id that does not exist returns 404."""
        response = auth_client.post('/tasks/999999/complete')
        assert response.status_code == 404

    def test_complete_another_users_task_returns_404(self, auth_client, db):
        """Users cannot toggle another user's task."""
        from app.models import User
        other = User(username='stranger')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        task = _create_task(db, other, title='Private task')
        response = auth_client.post(f'/tasks/{task.id}/complete')
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Delete task
# ---------------------------------------------------------------------------

class TestTaskDelete:
    def test_delete_removes_task(self, auth_client, db, test_user):
        """POST /tasks/<id>/delete removes the task from the database."""
        task = _create_task(db, test_user, title='Temporary task')
        task_id = task.id
        auth_client.post(f'/tasks/{task_id}/delete')
        assert db.session.get(Task, task_id) is None

    def test_delete_redirects(self, auth_client, db, test_user):
        """Deleting a task redirects (non-HTMX request)."""
        task = _create_task(db, test_user, title='Delete redirect')
        response = auth_client.post(
            f'/tasks/{task.id}/delete', follow_redirects=False
        )
        assert response.status_code in (301, 302)

    def test_delete_nonexistent_task_returns_404(self, auth_client):
        """Attempting to delete a task that does not exist returns 404."""
        response = auth_client.post('/tasks/999999/delete')
        assert response.status_code == 404

    def test_delete_another_users_task_returns_404(self, auth_client, db):
        """Users cannot delete another user's task."""
        from app.models import User
        other = User(username='stranger2')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        task = _create_task(db, other, title='Others task')
        response = auth_client.post(f'/tasks/{task.id}/delete')
        assert response.status_code == 404

    def test_delete_requires_auth(self, client, db, test_user):
        """Unauthenticated DELETE attempt redirects to login."""
        task = _create_task(db, test_user, title='Auth guard task')
        response = client.post(
            f'/tasks/{task.id}/delete', follow_redirects=False
        )
        assert response.status_code in (301, 302)


# ---------------------------------------------------------------------------
# Pin toggle
# ---------------------------------------------------------------------------

class TestTaskPin:
    def test_pin_toggles_pinned_flag(self, auth_client, db, test_user):
        """POST /tasks/<id>/pin flips the pinned_to_today flag."""
        task = _create_task(db, test_user, title='Unpin me', pinned_to_today=False)
        auth_client.post(f'/tasks/{task.id}/pin')
        db.session.refresh(task)
        assert task.pinned_to_today is True

    def test_pin_toggles_off(self, auth_client, db, test_user):
        """Pinning an already-pinned task unpins it."""
        task = _create_task(db, test_user, title='Already pinned', pinned_to_today=True)
        auth_client.post(f'/tasks/{task.id}/pin')
        db.session.refresh(task)
        assert task.pinned_to_today is False


# ---------------------------------------------------------------------------
# Edit task
# ---------------------------------------------------------------------------

def _post_edit_task(client, task_id, **kwargs):
    """POST to /tasks/<id>/edit with optional form field overrides."""
    data = {
        'title': kwargs.get('title', 'Updated Task'),
        'priority': kwargs.get('priority', 'medium'),
        'description': kwargs.get('description', ''),
        'due_date': kwargs.get('due_date', ''),
        'due_time': kwargs.get('due_time', ''),
    }
    if kwargs.get('pinned_to_today'):
        data['pinned_to_today'] = 'on'
    headers = kwargs.get('headers', {})
    return client.post(f'/tasks/{task_id}/edit', data=data, headers=headers, follow_redirects=False)


class TestTaskEdit:
    def test_get_edit_task_page_returns_200(self, auth_client, db, test_user):
        """GET /tasks/<id>/edit renders the edit form."""
        task = _create_task(db, test_user, title='Editable task')
        response = auth_client.get(f'/tasks/{task.id}/edit')
        assert response.status_code == 200

    def test_edit_updates_title(self, auth_client, db, test_user):
        """POST /tasks/<id>/edit persists the new title."""
        task = _create_task(db, test_user, title='Old title')
        _post_edit_task(auth_client, task.id, title='New title')
        db.session.refresh(task)
        assert task.title == 'New title'

    def test_edit_updates_priority(self, auth_client, db, test_user):
        """POST /tasks/<id>/edit persists the new priority."""
        task = _create_task(db, test_user, title='Priority task', priority='low')
        _post_edit_task(auth_client, task.id, title='Priority task', priority='high')
        db.session.refresh(task)
        assert task.priority == 'high'

    def test_edit_updates_description(self, auth_client, db, test_user):
        """POST /tasks/<id>/edit persists the new description."""
        task = _create_task(db, test_user, title='Desc task')
        _post_edit_task(auth_client, task.id, title='Desc task', description='New description')
        db.session.refresh(task)
        assert task.description == 'New description'

    def test_edit_updates_due_date(self, auth_client, db, test_user):
        """POST /tasks/<id>/edit persists a new due date."""
        task = _create_task(db, test_user, title='Due date task')
        tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%d')
        _post_edit_task(auth_client, task.id, title='Due date task', due_date=tomorrow)
        db.session.refresh(task)
        assert task.due_at is not None

    def test_edit_sets_pinned_to_today(self, auth_client, db, test_user):
        """POST /tasks/<id>/edit with pinned_to_today sets the flag."""
        task = _create_task(db, test_user, title='Pin today task', pinned_to_today=False)
        _post_edit_task(auth_client, task.id, title='Pin today task', pinned_to_today=True)
        db.session.refresh(task)
        assert task.pinned_to_today is True

    def test_edit_redirects_on_success(self, auth_client, db, test_user):
        """Successful edit redirects to the task index."""
        task = _create_task(db, test_user, title='Redirect task')
        response = _post_edit_task(auth_client, task.id, title='Redirect task updated')
        assert response.status_code in (301, 302)
        assert '/tasks' in response.headers.get('Location', '')

    def test_edit_missing_title_returns_422(self, auth_client, db, test_user):
        """POST /tasks/<id>/edit without a title returns 422."""
        task = _create_task(db, test_user, title='Title required task')
        response = _post_edit_task(auth_client, task.id, title='')
        assert response.status_code == 422

    def test_edit_htmx_returns_trigger_header(self, auth_client, db, test_user):
        """HTMX edit request returns HX-Trigger: taskUpdated."""
        task = _create_task(db, test_user, title='HTMX edit task')
        response = _post_edit_task(
            auth_client, task.id,
            title='HTMX edited',
            headers={'HX-Request': 'true'},
        )
        assert response.headers.get('HX-Trigger') == 'taskUpdated'

    def test_edit_nonexistent_task_returns_404(self, auth_client):
        """Editing a task that does not exist returns 404."""
        response = _post_edit_task(auth_client, 999999, title='Ghost task')
        assert response.status_code == 404

    def test_edit_another_users_task_returns_404(self, auth_client, db):
        """Users cannot edit another user's task."""
        from app.models import User
        other = User(username='taskeditor')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        task = _create_task(db, other, title='Others edit task')
        response = _post_edit_task(auth_client, task.id, title='Hijacked')
        assert response.status_code == 404

    def test_edit_requires_auth(self, client, db, test_user):
        """Unauthenticated edit attempt redirects to login."""
        task = _create_task(db, test_user, title='Auth edit task')
        response = client.post(f'/tasks/{task.id}/edit', data={'title': 'x', 'priority': 'medium'}, follow_redirects=False)
        assert response.status_code in (301, 302)

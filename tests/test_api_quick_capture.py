"""
Tests for the /api/quick-capture endpoint.
"""

from datetime import datetime

from app.models import Task, Note, Bookmark


def test_quick_capture_form_post_redirects_back_to_app(auth_client, test_user):
    """Non-HTMX form submissions should redirect instead of rendering raw JSON."""
    response = auth_client.post(
        '/api/quick-capture',
        data={
            'type': 'task',
            'title': 'Quick task from form',
            'priority': 'medium',
        },
        headers={'Referer': '/tasks'},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers['Location'].endswith('/tasks')

    task = Task.query.filter_by(user_id=test_user.id, title='Quick task from form').first()
    assert task is not None


def test_quick_capture_json_still_returns_json(auth_client, test_user):
    """JSON API submissions keep JSON semantics."""
    response = auth_client.post(
        '/api/quick-capture',
        json={
            'type': 'task',
            'title': 'Quick task from json',
            'priority': 'medium',
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload['status'] == 'created'
    assert payload['type'] == 'task'

    task = Task.query.filter_by(user_id=test_user.id, title='Quick task from json').first()
    assert task is not None


def test_quick_capture_requires_csrf_when_enabled(app, auth_client):
    """Session-authenticated API POST is rejected when CSRF is enabled and token is missing."""
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        response = auth_client.post(
            '/api/quick-capture',
            data={'type': 'task', 'title': 'No CSRF token'},
            follow_redirects=False,
        )
        assert response.status_code == 400
    finally:
        app.config['WTF_CSRF_ENABLED'] = False


def test_quick_capture_task_supports_due_time_and_extra_fields(auth_client, test_user):
    """Task quick-capture should persist optional fields used by the full task form."""
    response = auth_client.post(
        '/api/quick-capture',
        json={
            'type': 'task',
            'title': 'Task with extras',
            'priority': 'high',
            'due_date': '2026-04-01',
            'due_time': '09:30',
            'description': 'Detailed context',
            'pinned_to_today': True,
        },
    )

    assert response.status_code == 201

    task = Task.query.filter_by(user_id=test_user.id, title='Task with extras').first()
    assert task is not None
    assert task.priority == 'high'
    assert task.description == 'Detailed context'
    assert task.pinned_to_today is True
    assert task.due_at == datetime(2026, 4, 1, 9, 30)


def test_quick_capture_note_and_bookmark_support_pinned_and_description(auth_client, test_user):
    """Note/bookmark quick-capture should persist optional fields used by full forms."""
    note_response = auth_client.post(
        '/api/quick-capture',
        json={
            'type': 'note',
            'title': 'Pinned note',
            'body': 'body',
            'tag': 'work',
            'pinned': True,
        },
    )
    assert note_response.status_code == 201

    note = Note.query.filter_by(user_id=test_user.id, title='Pinned note').first()
    assert note is not None
    assert note.pinned is True
    assert note.tag == 'work'

    bookmark_response = auth_client.post(
        '/api/quick-capture',
        json={
            'type': 'bookmark',
            'title': 'Pinned bookmark',
            'url': 'https://example.com',
            'category': 'tools',
            'description': 'useful link',
            'pinned': True,
        },
    )
    assert bookmark_response.status_code == 201

    bookmark = Bookmark.query.filter_by(user_id=test_user.id, title='Pinned bookmark').first()
    assert bookmark is not None
    assert bookmark.pinned is True
    assert bookmark.description == 'useful link'
    assert bookmark.category == 'tools'


def test_quick_capture_form_post_prefers_safe_next_redirect(auth_client):
    """Non-HTMX form submissions honor a safe local next path from the form payload."""
    response = auth_client.post(
        '/api/quick-capture',
        data={
            'type': 'task',
            'title': 'Quick task with next redirect',
            'next': '/bookmarks',
        },
        headers={'Referer': '/tasks'},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers['Location'].endswith('/bookmarks')


def test_quick_capture_form_post_rejects_external_next_redirect(auth_client):
    """External next targets are ignored to avoid open-redirect behavior."""
    response = auth_client.post(
        '/api/quick-capture',
        data={
            'type': 'task',
            'title': 'Quick task with bad next redirect',
            'next': 'https://evil.example/phish',
        },
        headers={'Referer': '/tasks'},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers['Location'].endswith('/tasks')

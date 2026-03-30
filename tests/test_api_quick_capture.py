"""
Tests for the /api/quick-capture endpoint.
"""

from app.models import Task


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

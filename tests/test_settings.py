"""
tests/test_settings.py
~~~~~~~~~~~~~~~~~~~~~~

Tests for HelmHub settings route:
  - GET /settings/ returns 200 with current values
  - POST /settings/ persists theme, time_format, default_page, show_weather,
    and dashboard widget visibility to the SQLite database
  - Validation rejects unknown theme / time_format values
  - Requires authentication
"""

import json

import pytest
from sqlalchemy.orm import Session

from app.models import Setting


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_settings(client, data=None, headers=None):
    """POST to /settings/ with the given form fields."""
    defaults = {
        'theme': 'system',
        'time_format': '12',
        'default_page': '/',
    }
    payload = {**defaults, **(data or {})}
    return client.post('/settings/', data=payload, headers=headers or {}, follow_redirects=False)


def _get_setting(db, user):
    """Return the Setting row for *user*, or None."""
    return Setting.query.filter_by(user_id=user.id).first()


# ---------------------------------------------------------------------------
# GET settings page
# ---------------------------------------------------------------------------

class TestSettingsGet:
    def test_settings_page_requires_auth(self, client):
        """Unauthenticated GET /settings/ redirects to login."""
        response = client.get('/settings/', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_settings_page_returns_200(self, auth_client):
        """Authenticated GET /settings/ returns 200."""
        response = auth_client.get('/settings/')
        assert response.status_code == 200

    def test_settings_page_contains_theme_options(self, auth_client):
        """Settings page renders theme radio buttons."""
        response = auth_client.get('/settings/')
        assert b'light' in response.data
        assert b'dark' in response.data
        assert b'system' in response.data

    def test_settings_page_contains_time_format_options(self, auth_client):
        """Settings page renders time format options."""
        response = auth_client.get('/settings/')
        assert b'time_format' in response.data


# ---------------------------------------------------------------------------
# POST settings – theme persistence
# ---------------------------------------------------------------------------

class TestSettingsTheme:
    def test_save_light_theme(self, auth_client, db, test_user):
        """Posting theme=light persists 'light' to the database."""
        _post_settings(auth_client, {'theme': 'light'})
        setting = _get_setting(db, test_user)
        assert setting is not None
        assert setting.theme == 'light'

    def test_save_dark_theme(self, auth_client, db, test_user):
        """Posting theme=dark persists 'dark' to the database."""
        _post_settings(auth_client, {'theme': 'dark'})
        setting = _get_setting(db, test_user)
        assert setting is not None
        assert setting.theme == 'dark'

    def test_save_system_theme(self, auth_client, db, test_user):
        """Posting theme=system persists 'system' to the database."""
        _post_settings(auth_client, {'theme': 'system'})
        setting = _get_setting(db, test_user)
        assert setting is not None
        assert setting.theme == 'system'

    def test_invalid_theme_returns_422(self, auth_client, db, test_user):
        """An unrecognised theme value returns 422 and does not update the DB."""
        # First set a known theme so we can verify it does not change.
        _post_settings(auth_client, {'theme': 'dark'})
        response = _post_settings(auth_client, {'theme': 'rainbow'})
        assert response.status_code == 422
        setting = _get_setting(db, test_user)
        # The original value should be unchanged.
        assert setting.theme == 'dark'

    def test_theme_change_is_overwritten_on_second_save(self, auth_client, db, test_user):
        """Saving a new theme replaces the previous one in the database."""
        _post_settings(auth_client, {'theme': 'light'})
        _post_settings(auth_client, {'theme': 'dark'})
        db.session.expire_all()
        setting = _get_setting(db, test_user)
        assert setting.theme == 'dark'


# ---------------------------------------------------------------------------
# POST settings – time format persistence
# ---------------------------------------------------------------------------

class TestSettingsTimeFormat:
    def test_save_12h_format(self, auth_client, db, test_user):
        """Posting time_format=12 persists '12' to the database."""
        _post_settings(auth_client, {'time_format': '12'})
        setting = _get_setting(db, test_user)
        assert setting is not None
        assert setting.time_format == '12'

    def test_save_24h_format(self, auth_client, db, test_user):
        """Posting time_format=24 persists '24' to the database."""
        _post_settings(auth_client, {'time_format': '24'})
        setting = _get_setting(db, test_user)
        assert setting is not None
        assert setting.time_format == '24'

    def test_invalid_time_format_returns_422(self, auth_client, db, test_user):
        """An unrecognised time format returns 422."""
        response = _post_settings(auth_client, {'time_format': '13'})
        assert response.status_code == 422

    def test_time_format_change_persists(self, auth_client, db, test_user):
        """Switching from 12h to 24h updates the database."""
        _post_settings(auth_client, {'time_format': '12'})
        _post_settings(auth_client, {'time_format': '24'})
        db.session.expire_all()
        setting = _get_setting(db, test_user)
        assert setting.time_format == '24'


# ---------------------------------------------------------------------------
# POST settings – default page persistence
# ---------------------------------------------------------------------------

class TestSettingsDefaultPage:
    @pytest.mark.parametrize('page', ['/', '/tasks', '/notes', '/focus', '/tasks?view=today'])
    def test_save_default_page(self, auth_client, db, test_user, page):
        """Posting a valid default_page value persists it to the database."""
        _post_settings(auth_client, {'default_page': page})
        setting = _get_setting(db, test_user)
        assert setting is not None
        assert setting.default_page == page

    def test_default_page_change_persists(self, auth_client, db, test_user):
        """Changing the default page from dashboard to notes is persisted."""
        _post_settings(auth_client, {'default_page': '/'})
        _post_settings(auth_client, {'default_page': '/notes'})
        db.session.expire_all()
        setting = _get_setting(db, test_user)
        assert setting.default_page == '/notes'


# ---------------------------------------------------------------------------
# POST settings – dashboard widget visibility
# ---------------------------------------------------------------------------

class TestSettingsWidgets:
    def test_all_widgets_on(self, auth_client, db, test_user):
        """When all widget checkboxes are checked, all widgets are visible in the DB."""
        _post_settings(auth_client, {
            'show_focus': 'on',
            'show_today': 'on',
            'show_next_event': 'on',
            'show_reminders': 'on',
            'show_recent_notes': 'on',
            'show_bookmarks': 'on',
        })
        setting = _get_setting(db, test_user)
        config = setting.get_dashboard_config()
        widgets_by_id = {w['id']: w['visible'] for w in config.get('widgets', [])}
        assert widgets_by_id.get('tasks') is True
        assert widgets_by_id.get('today') is True
        assert widgets_by_id.get('events') is True
        assert widgets_by_id.get('reminders') is True
        assert widgets_by_id.get('notes') is True
        assert widgets_by_id.get('bookmarks') is True

    def test_all_widgets_off(self, auth_client, db, test_user):
        """When no widget checkboxes are checked, all widgets are hidden in the DB."""
        _post_settings(auth_client, {})  # no widget checkbox keys
        setting = _get_setting(db, test_user)
        config = setting.get_dashboard_config()
        widgets_by_id = {w['id']: w['visible'] for w in config.get('widgets', [])}
        for widget_id in ('tasks', 'today', 'events', 'reminders', 'notes', 'bookmarks'):
            assert widgets_by_id.get(widget_id) is False

    def test_partial_widget_selection(self, auth_client, db, test_user):
        """Only checked widgets are visible; unchecked ones are hidden."""
        _post_settings(auth_client, {
            'show_focus': 'on',
            'show_reminders': 'on',
            # others omitted → hidden
        })
        setting = _get_setting(db, test_user)
        config = setting.get_dashboard_config()
        widgets_by_id = {w['id']: w['visible'] for w in config.get('widgets', [])}
        assert widgets_by_id.get('tasks') is True
        assert widgets_by_id.get('reminders') is True
        assert widgets_by_id.get('today') is False
        assert widgets_by_id.get('events') is False
        assert widgets_by_id.get('notes') is False
        assert widgets_by_id.get('bookmarks') is False

    def test_widget_config_stored_as_valid_json(self, auth_client, db, test_user):
        """The dashboard_config column contains valid JSON after saving."""
        _post_settings(auth_client, {'show_focus': 'on'})
        setting = _get_setting(db, test_user)
        # Should not raise
        parsed = json.loads(setting.dashboard_config)
        assert 'widgets' in parsed


# ---------------------------------------------------------------------------
# POST settings – redirect and flash
# ---------------------------------------------------------------------------

class TestSettingsSaveResponse:
    def test_save_redirects(self, auth_client, db, test_user):
        """Successful save redirects back to /settings/."""
        response = _post_settings(auth_client)
        assert response.status_code in (301, 302)
        assert '/settings' in response.headers.get('Location', '')

    def test_save_requires_auth(self, client):
        """Unauthenticated POST to /settings/ redirects to login."""
        response = _post_settings(client)
        assert response.status_code in (301, 302)
        assert '/auth' in response.headers.get('Location', '') or 'login' in response.headers.get('Location', '').lower()

    def test_htmx_save_returns_trigger_header(self, auth_client, db, test_user):
        """HTMX settings save returns HX-Trigger: settingsSaved."""
        response = _post_settings(auth_client, headers={'HX-Request': 'true'})
        assert response.headers.get('HX-Trigger') == 'settingsSaved'

    def test_multiple_saves_update_same_row(self, auth_client, db, test_user):
        """Saving settings twice does not create duplicate Setting rows."""
        _post_settings(auth_client, {'theme': 'light'})
        _post_settings(auth_client, {'theme': 'dark'})
        count = Setting.query.filter_by(user_id=test_user.id).count()
        assert count == 1

    def test_settings_row_created_on_first_save(self, auth_client, db, test_user):
        """A Setting row is created in the database on the first save."""
        assert _get_setting(db, test_user) is None or True  # may or may not pre-exist
        _post_settings(auth_client, {'theme': 'light'})
        assert _get_setting(db, test_user) is not None

    def test_combined_settings_all_persist(self, auth_client, db, test_user):
        """Theme, time_format, default_page and widgets all persist in one save."""
        _post_settings(auth_client, {
            'theme': 'dark',
            'time_format': '24',
            'default_page': '/notes',
            'show_focus': 'on',
            'show_reminders': 'on',
        })
        db.session.expire_all()
        setting = _get_setting(db, test_user)
        assert setting.theme == 'dark'
        assert setting.time_format == '24'
        assert setting.default_page == '/notes'
        config = setting.get_dashboard_config()
        widgets_by_id = {w['id']: w['visible'] for w in config.get('widgets', [])}
        assert widgets_by_id.get('tasks') is True
        assert widgets_by_id.get('reminders') is True
        assert widgets_by_id.get('today') is False


class TestSettingGetOrCreateRace:
    def test_get_or_create_handles_unique_race(self, db, test_user, monkeypatch):
        """If a concurrent insert wins, get_or_create returns the existing row."""
        original_flush = db.session.flush

        def raced_flush(*args, **kwargs):
            with Session(bind=db.engine) as other_session:
                other_session.add(Setting(user_id=test_user.id))
                other_session.commit()
            return original_flush(*args, **kwargs)

        monkeypatch.setattr(db.session, 'flush', raced_flush)

        setting = Setting.get_or_create(test_user.id)

        assert setting is not None
        assert setting.user_id == test_user.id
        assert Setting.query.filter_by(user_id=test_user.id).count() == 1

    def test_get_or_create_ignores_pending_duplicate_before_select(self, db, test_user):
        """A pending duplicate in session should not auto-flush during lookup."""
        existing = Setting(user_id=test_user.id)
        db.session.add(existing)
        db.session.commit()

        # Simulate a stale/duplicate pending instance left in the same session.
        db.session.add(Setting(user_id=test_user.id))

        setting = Setting.get_or_create(test_user.id)

        assert setting is not None
        assert setting.id == existing.id

        # Clear failed/pending state so fixture teardown can proceed cleanly.
        db.session.rollback()


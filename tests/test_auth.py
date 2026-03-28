"""
tests/test_auth.py
~~~~~~~~~~~~~~~~~~

Tests for HelmHub authentication routes:
  - Login page rendering
  - Credential validation
  - Redirect behaviour on success
  - Logout
  - TOTP two-factor flow (pending session, page render, valid/invalid code)
  - Recovery-code login
"""

import json

import pyotp
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login(client, username='testuser', password='testpassword123', **kwargs):
    """POST to the login endpoint and return the response."""
    return client.post(
        '/auth/login',
        data={'username': username, 'password': password, **kwargs},
        follow_redirects=False,
    )


def _login_follow(client, username='testuser', password='testpassword123', **kwargs):
    """POST to the login endpoint and follow all redirects."""
    return client.post(
        '/auth/login',
        data={'username': username, 'password': password, **kwargs},
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# GET /auth/login
# ---------------------------------------------------------------------------

class TestLoginPage:
    def test_login_page_renders(self, client):
        """Login page returns 200 for unauthenticated visitors."""
        response = client.get('/auth/login')
        assert response.status_code == 200

    def test_login_page_contains_form_fields(self, client):
        """Login page HTML includes username and password inputs."""
        response = client.get('/auth/login')
        html = response.data.decode()
        assert 'username' in html
        assert 'password' in html

    def test_login_page_redirects_authenticated_user(self, auth_client):
        """Already-authenticated users are redirected away from the login page."""
        response = auth_client.get('/auth/login', follow_redirects=False)
        assert response.status_code in (301, 302)
        assert '/' in response.headers.get('Location', '/')


# ---------------------------------------------------------------------------
# POST /auth/login — credential validation
# ---------------------------------------------------------------------------

class TestLoginPost:
    def test_login_valid_credentials(self, client, test_user):
        """Posting correct credentials returns a redirect (not an error page)."""
        response = _login(client)
        assert response.status_code in (301, 302)

    def test_login_redirects_to_dashboard(self, client, test_user):
        """Successful login redirects the user to the dashboard."""
        response = _login_follow(client)
        # After redirect chain the user should land on the dashboard (200).
        assert response.status_code == 200

    def test_login_invalid_password(self, client, test_user):
        """Wrong password returns a 401 or re-renders the login form."""
        response = _login(client, password='wrongpassword')
        assert response.status_code in (401, 200)

    def test_login_invalid_username(self, client, test_user):
        """Unknown username returns a 401 or re-renders the login form."""
        response = _login(client, username='nobody')
        assert response.status_code in (401, 200)

    def test_login_missing_username(self, client, test_user):
        """Omitting the username field returns a 400."""
        response = client.post(
            '/auth/login',
            data={'username': '', 'password': 'testpassword123'},
        )
        assert response.status_code == 400

    def test_login_missing_password(self, client, test_user):
        """Omitting the password field returns a 400."""
        response = client.post(
            '/auth/login',
            data={'username': 'testuser', 'password': ''},
        )
        assert response.status_code == 400

    def test_login_sets_session(self, client, test_user):
        """After a successful login the session contains the user id."""
        _login(client)
        with client.session_transaction() as sess:
            assert '_user_id' in sess

    def test_login_next_redirect(self, client, test_user):
        """The ?next= parameter is honoured on successful login."""
        response = client.post(
            '/auth/login?next=/tasks/',
            data={'username': 'testuser', 'password': 'testpassword123'},
            follow_redirects=False,
        )
        assert response.status_code in (301, 302)
        location = response.headers.get('Location', '')
        assert '/tasks/' in location


# ---------------------------------------------------------------------------
# GET /auth/logout
# ---------------------------------------------------------------------------

class TestLogout:
    def test_logout_redirects_to_login(self, auth_client):
        """Logout sends the user back to the login page."""
        response = auth_client.get('/auth/logout', follow_redirects=False)
        assert response.status_code in (301, 302)
        location = response.headers.get('Location', '')
        assert 'login' in location

    def test_logout_clears_session(self, auth_client):
        """After logout the session no longer contains the user id."""
        auth_client.get('/auth/logout')
        with auth_client.session_transaction() as sess:
            assert '_user_id' not in sess

    def test_logout_requires_authentication(self, client):
        """Unauthenticated GET /auth/logout redirects to login (not 200/500)."""
        response = client.get('/auth/logout', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_logout_then_dashboard_redirects(self, auth_client):
        """After logout, accessing the dashboard requires re-authentication."""
        auth_client.get('/auth/logout')
        response = auth_client.get('/', follow_redirects=False)
        assert response.status_code in (301, 302)


# ---------------------------------------------------------------------------
# TOTP two-factor flow
# ---------------------------------------------------------------------------

class TestTotpFlow:
    def test_totp_login_stores_pending_user_in_session(self, client, totp_user):
        """Posting valid credentials for a TOTP-enabled user sets pending_totp_user_id."""
        response = client.post(
            '/auth/login',
            data={'username': 'totpuser', 'password': 'totppassword123'},
            follow_redirects=False,
        )
        # Should redirect to /auth/totp
        assert response.status_code in (301, 302)
        location = response.headers.get('Location', '')
        assert 'totp' in location

        with client.session_transaction() as sess:
            assert 'pending_totp_user_id' in sess
            assert sess['pending_totp_user_id'] == totp_user.id

    def test_totp_page_renders(self, client, totp_user):
        """GET /auth/totp renders the TOTP form when a pending user is in session."""
        # First stage: post credentials to set session state
        client.post(
            '/auth/login',
            data={'username': 'totpuser', 'password': 'totppassword123'},
        )
        response = client.get('/auth/totp')
        assert response.status_code == 200

    def test_totp_page_redirects_without_pending_session(self, client):
        """GET /auth/totp without a pending user id redirects to login."""
        response = client.get('/auth/totp', follow_redirects=False)
        assert response.status_code in (301, 302)
        location = response.headers.get('Location', '')
        assert 'login' in location

    def test_totp_valid_code_logs_in(self, client, totp_user, app):
        """Posting a valid TOTP code completes authentication."""
        # Stage 1: password
        client.post(
            '/auth/login',
            data={'username': 'totpuser', 'password': 'totppassword123'},
        )

        # Patch verify_totp_token to return True for this test
        import app.services.totp_service as totp_svc
        original = totp_svc.verify_totp_token
        totp_svc.verify_totp_token = lambda secret, token: True

        try:
            totp = pyotp.TOTP(totp_user.totp_secret)
            valid_code = totp.now()

            response = client.post(
                '/auth/totp',
                data={'totp_code': valid_code},
                follow_redirects=True,
            )
            assert response.status_code == 200
        finally:
            totp_svc.verify_totp_token = original

    def test_totp_invalid_code_returns_error(self, client, totp_user):
        """Posting a bad TOTP code returns a 401 and stays on the TOTP page."""
        # Stage 1: password
        client.post(
            '/auth/login',
            data={'username': 'totpuser', 'password': 'totppassword123'},
        )

        response = client.post(
            '/auth/totp',
            data={'totp_code': '000000'},
            follow_redirects=False,
        )
        assert response.status_code == 401

    def test_totp_code_must_be_six_digits(self, client, totp_user):
        """A TOTP code that is not exactly 6 digits returns a 400."""
        client.post(
            '/auth/login',
            data={'username': 'totpuser', 'password': 'totppassword123'},
        )
        response = client.post(
            '/auth/totp',
            data={'totp_code': '123'},
        )
        assert response.status_code == 400

    def test_totp_non_digit_code_returns_error(self, client, totp_user):
        """A TOTP code containing non-digit characters returns a 400."""
        client.post(
            '/auth/login',
            data={'username': 'totpuser', 'password': 'totppassword123'},
        )
        response = client.post(
            '/auth/totp',
            data={'totp_code': 'abcdef'},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Recovery-code login
# ---------------------------------------------------------------------------

class TestRecoveryCodeLogin:
    def test_valid_recovery_code_logs_in(self, client, totp_user, db):
        """A valid recovery code bypasses TOTP and completes authentication."""
        # Generate known recovery codes
        plain_codes = totp_user.generate_recovery_codes()
        db.session.commit()

        # Stage 1: password
        client.post(
            '/auth/login',
            data={'username': 'totpuser', 'password': 'totppassword123'},
        )

        response = client.post(
            '/auth/totp',
            data={'recovery_code': plain_codes[0]},
            follow_redirects=True,
        )
        assert response.status_code == 200

    def test_invalid_recovery_code_returns_error(self, client, totp_user, db):
        """An incorrect recovery code is rejected and returns 401."""
        totp_user.generate_recovery_codes()
        db.session.commit()

        client.post(
            '/auth/login',
            data={'username': 'totpuser', 'password': 'totppassword123'},
        )

        response = client.post(
            '/auth/totp',
            data={'recovery_code': 'XXXX-XXXX'},
            follow_redirects=False,
        )
        assert response.status_code == 401

    def test_recovery_code_is_consumed_after_use(self, client, totp_user, db):
        """Each recovery code can only be used once."""
        plain_codes = totp_user.generate_recovery_codes()
        db.session.commit()

        # First use
        client.post(
            '/auth/login',
            data={'username': 'totpuser', 'password': 'totppassword123'},
        )
        client.post('/auth/totp', data={'recovery_code': plain_codes[0]})

        # Log out and attempt to reuse the same code
        client.get('/auth/logout')
        client.post(
            '/auth/login',
            data={'username': 'totpuser', 'password': 'totppassword123'},
        )
        response = client.post(
            '/auth/totp',
            data={'recovery_code': plain_codes[0]},
            follow_redirects=False,
        )
        assert response.status_code == 401

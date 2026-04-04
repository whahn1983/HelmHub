"""
app/config.py
~~~~~~~~~~~~~

Configuration classes for different deployment environments.
All settings can be overridden via environment variables.
"""

import os
import secrets
from datetime import timedelta


class BaseConfig:
    """
    Shared base configuration.

    All subclasses inherit these settings and may override individual values.
    Never commit real secrets — use environment variables instead.
    """

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    SECRET_KEY: str = os.environ.get('SECRET_KEY') or secrets.token_urlsafe(64)
    TOTP_ENCRYPTION_KEY: str | None = os.environ.get('TOTP_ENCRYPTION_KEY')

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        'DATABASE_URL', 'sqlite:///helmhub.db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # ------------------------------------------------------------------
    # CSRF
    # ------------------------------------------------------------------
    WTF_CSRF_ENABLED: bool = True

    # ------------------------------------------------------------------
    # Session / Cookie security
    # ------------------------------------------------------------------
    # Defaults to secure cookies; local development can override explicitly.
    SESSION_COOKIE_SECURE: bool = (
        os.environ.get('SESSION_COOKIE_SECURE', 'True').lower() == 'true'
    )
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = 'Lax'
    PERMANENT_SESSION_LIFETIME: timedelta = timedelta(days=30)

    # ------------------------------------------------------------------
    # Rate limiting (Flask-Limiter)
    # ------------------------------------------------------------------
    RATELIMIT_DEFAULT: str = '200 per minute'
    RATELIMIT_STORAGE_URI: str = 'memory://'
    RATELIMIT_HEADERS_ENABLED: bool = True

    # ------------------------------------------------------------------
    # Reverse proxy / forwarded headers
    # ------------------------------------------------------------------
    PROXY_FIX_X_FOR: int = int(os.environ.get('PROXY_FIX_X_FOR', '0'))
    PROXY_FIX_X_PROTO: int = int(os.environ.get('PROXY_FIX_X_PROTO', '0'))
    PROXY_FIX_X_HOST: int = int(os.environ.get('PROXY_FIX_X_HOST', '0'))
    PROXY_FIX_X_PORT: int = int(os.environ.get('PROXY_FIX_X_PORT', '0'))
    PROXY_FIX_X_PREFIX: int = int(os.environ.get('PROXY_FIX_X_PREFIX', '0'))

    # ------------------------------------------------------------------
    # Calendar subscriptions (ICS/iCal feeds)
    # ------------------------------------------------------------------
    # Default cache TTL for subscription event feeds (minutes)
    CALENDAR_SUBSCRIPTION_DEFAULT_TTL_MINUTES: int = int(
        os.environ.get('CALENDAR_SUBSCRIPTION_DEFAULT_TTL_MINUTES', '30')
    )
    # HTTP request timeout when fetching a remote ICS feed (seconds)
    CALENDAR_SUBSCRIPTION_FETCH_TIMEOUT_SECONDS: int = int(
        os.environ.get('CALENDAR_SUBSCRIPTION_FETCH_TIMEOUT_SECONDS', '15')
    )
    # Maximum number of events to keep per subscription after parsing
    CALENDAR_SUBSCRIPTION_MAX_EVENTS: int = int(
        os.environ.get('CALENDAR_SUBSCRIPTION_MAX_EVENTS', '500')
    )
    # How many days ahead to expand recurring events
    CALENDAR_SUBSCRIPTION_LOOKAHEAD_DAYS: int = int(
        os.environ.get('CALENDAR_SUBSCRIPTION_LOOKAHEAD_DAYS', '60')
    )

    # ------------------------------------------------------------------
    # Application metadata
    # ------------------------------------------------------------------
    APP_NAME: str = 'HelmHub'


class DevelopmentConfig(BaseConfig):
    """
    Development configuration.

    Debug mode is on; SQL echo is off by default to keep the console clean,
    but can be toggled for query inspection.
    """

    DEBUG: bool = True
    SQLALCHEMY_ECHO: bool = False
    SESSION_COOKIE_SECURE: bool = False


class ProductionConfig(BaseConfig):
    """
    Production configuration.

    Cookies are always sent over HTTPS and debug mode is disabled.
    Ensure SECRET_KEY and DATABASE_URL are set in the environment.
    """

    DEBUG: bool = False
    SESSION_COOKIE_SECURE: bool = True


class TestingConfig(BaseConfig):
    """
    Testing configuration.

    Uses an in-memory SQLite database so tests never touch real data.
    CSRF protection is disabled to simplify form-submission tests.
    Rate limiting is disabled to avoid interfering with test suites.
    SERVER_NAME is required by url_for() in tests without a live request.
    """

    TESTING: bool = True
    WTF_CSRF_ENABLED: bool = False
    SQLALCHEMY_DATABASE_URI: str = 'sqlite:///:memory:'
    SERVER_NAME: str = 'localhost'
    # Disable rate limiting during test runs
    RATELIMIT_ENABLED: bool = False
    TOTP_ENCRYPTION_KEY: str = os.environ.get('TOTP_ENCRYPTION_KEY', 'Hv5bgL0x0dSWLwy5rMFHXqEMwFHMViLs8TVDosUAWn4=')


# ---------------------------------------------------------------------------
# Config registry
# ---------------------------------------------------------------------------
config: dict[str, type[BaseConfig]] = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}

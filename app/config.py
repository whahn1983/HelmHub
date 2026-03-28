"""
app/config.py
~~~~~~~~~~~~~

Configuration classes for different deployment environments.
All settings can be overridden via environment variables.
"""

import os
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
    SECRET_KEY: str = os.environ.get(
        'SECRET_KEY', 'dev-secret-key-CHANGE-ME-before-production'
    )

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
    # Overridden to True in ProductionConfig; also respectable via env var
    SESSION_COOKIE_SECURE: bool = (
        os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    )
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = 'Lax'
    PERMANENT_SESSION_LIFETIME: timedelta = timedelta(days=30)

    # ------------------------------------------------------------------
    # Rate limiting (Flask-Limiter)
    # ------------------------------------------------------------------
    RATELIMIT_DEFAULT: str = '200 per day'
    RATELIMIT_STORAGE_URI: str = 'memory://'
    RATELIMIT_HEADERS_ENABLED: bool = True

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


# ---------------------------------------------------------------------------
# Config registry
# ---------------------------------------------------------------------------
config: dict[str, type[BaseConfig]] = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}

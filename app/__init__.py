"""
app/__init__.py
~~~~~~~~~~~~~~~

Flask application factory for HelmHub.

Usage
-----
    from app import create_app
    app = create_app('production')   # or 'development' / 'testing'
"""

import logging
import os
import secrets
from datetime import datetime

from flask import Flask, g, render_template, send_from_directory
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import config
from app.extensions import csrf, db, limiter, login_manager, migrate

logger = logging.getLogger(__name__)


# ===========================================================================
# Public factory
# ===========================================================================

def create_app(config_name: str | None = None) -> Flask:
    """
    Create and configure a Flask application instance.

    Parameters
    ----------
    config_name:
        One of ``'development'``, ``'production'``, ``'testing'``, or
        ``'default'``.  Falls back to the ``FLASK_ENV`` environment variable
        and then to ``'default'`` (which maps to ``DevelopmentConfig``).

    Returns
    -------
    Flask
        A fully initialised Flask application.
    """
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'default')

    app = Flask(__name__, instance_relative_config=True)

    # ------------------------------------------------------------------
    # 1. Configuration
    # ------------------------------------------------------------------
    app.config.from_object(config[config_name])

    # Optional per-instance overrides (instance/config.py, git-ignored)
    app.config.from_pyfile('config.py', silent=True)
    _validate_security_config(app)
    _configure_proxy_fix(app)

    # ------------------------------------------------------------------
    # 2. Extensions
    # ------------------------------------------------------------------
    _init_extensions(app)

    # ------------------------------------------------------------------
    # 3. Blueprints
    # ------------------------------------------------------------------
    _register_blueprints(app)

    # ------------------------------------------------------------------
    # 3b. Service-worker served from root so its scope covers the whole app
    # ------------------------------------------------------------------
    @app.route('/sw.js')
    def service_worker():  # noqa: WPS430
        response = send_from_directory(app.static_folder, 'sw.js')
        response.headers['Service-Worker-Allowed'] = '/'
        response.headers['Content-Type'] = 'application/javascript'
        return response

    # ------------------------------------------------------------------
    # 4. Login-manager user loader
    # ------------------------------------------------------------------
    _configure_login_manager(app)

    # ------------------------------------------------------------------
    # 5. Template context processors
    # ------------------------------------------------------------------
    _register_context_processors(app)

    # ------------------------------------------------------------------
    # 6. Error handlers
    # ------------------------------------------------------------------
    _register_error_handlers(app)
    _register_security_headers(app)

    # ------------------------------------------------------------------
    # 7. Template filters
    # ------------------------------------------------------------------
    _register_template_filters(app)

    # ------------------------------------------------------------------
    # 8. First-run: create default admin if the DB is empty
    # ------------------------------------------------------------------
    with app.app_context():
        _create_default_admin(app)

    return app


# ===========================================================================
# Private helpers
# ===========================================================================

def _register_template_filters(app: Flask) -> None:
    """Register custom Jinja2 filters used across templates."""
    from datetime import datetime, date

    app.jinja_env.globals['getattr'] = getattr

    def _fmt_time(value):
        """Return HH:MM AM/PM without a leading zero, cross-platform."""
        return value.strftime('%I:%M %p').lstrip('0')

    @app.template_filter('format_datetime')
    def format_datetime_filter(value, fmt=None):  # noqa: WPS430
        if value is None:
            return ''
        if isinstance(value, datetime):
            today = date.today()
            if value.date() == today:
                return _fmt_time(value)
            return value.strftime('%b ') + str(value.day) + ', ' + _fmt_time(value)
        return str(value)

    @app.template_filter('format_time')
    def format_time_filter(value):  # noqa: WPS430
        if value is None:
            return ''
        if isinstance(value, datetime):
            return _fmt_time(value)
        return str(value)

    @app.template_filter('format_date')
    def format_date_filter(value):  # noqa: WPS430
        if value is None:
            return ''
        if hasattr(value, 'strftime'):
            return value.strftime('%b ') + str(value.day) + value.strftime(', %Y')
        return str(value)


def _init_extensions(app: Flask) -> None:
    """Bind all Flask extensions to the application instance."""
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)


def _validate_security_config(app: Flask) -> None:
    """Validate and backfill required security configuration."""
    if not app.config.get('SECRET_KEY'):
        app.config['SECRET_KEY'] = secrets.token_urlsafe(64)
        logger.warning('SECRET_KEY missing; generated a random runtime key.')

    if app.config.get('TESTING'):
        app.config.setdefault('TOTP_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')

    if not app.config.get('TOTP_ENCRYPTION_KEY'):
        raise RuntimeError(
            'TOTP_ENCRYPTION_KEY is required to protect TOTP secrets at rest.'
        )


def _configure_proxy_fix(app: Flask) -> None:
    """Apply ProxyFix when explicit trusted proxy hops are configured."""
    x_for = int(app.config.get('PROXY_FIX_X_FOR', 0))
    x_proto = int(app.config.get('PROXY_FIX_X_PROTO', 0))
    x_host = int(app.config.get('PROXY_FIX_X_HOST', 0))
    x_port = int(app.config.get('PROXY_FIX_X_PORT', 0))
    x_prefix = int(app.config.get('PROXY_FIX_X_PREFIX', 0))

    if any((x_for, x_proto, x_host, x_port, x_prefix)):
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=x_for,
            x_proto=x_proto,
            x_host=x_host,
            x_port=x_port,
            x_prefix=x_prefix,
        )


def _register_blueprints(app: Flask) -> None:
    """Import and register every feature blueprint."""

    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.focus import focus_bp
    from app.routes.tasks import tasks_bp
    from app.routes.notes import notes_bp
    from app.routes.reminders import reminders_bp
    from app.routes.events import events_bp
    from app.routes.settings import settings_bp
    from app.routes.api import api_bp
    from app.routes.bookmarks import bookmarks_bp

    app.register_blueprint(auth_bp,       url_prefix='/auth')
    app.register_blueprint(dashboard_bp,  url_prefix='/')
    app.register_blueprint(tasks_bp,      url_prefix='/tasks')
    app.register_blueprint(notes_bp,      url_prefix='/notes')
    app.register_blueprint(reminders_bp,  url_prefix='/reminders')
    app.register_blueprint(events_bp,     url_prefix='/events')
    app.register_blueprint(settings_bp,   url_prefix='/settings')
    app.register_blueprint(focus_bp,      url_prefix='/focus')
    app.register_blueprint(api_bp,        url_prefix='/api')
    app.register_blueprint(bookmarks_bp,  url_prefix='/bookmarks')


def _configure_login_manager(app: Flask) -> None:  # noqa: ARG001
    """Register the ``user_loader`` callback with Flask-Login."""

    from app.models.user import User

    @login_manager.user_loader
    def load_user(user_id: str):  # noqa: WPS430
        """Return the ``User`` object for *user_id*, or ``None``."""
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None


def _register_context_processors(app: Flask) -> None:
    """Inject common variables into every Jinja2 template context."""

    from app.models.setting import Setting

    @app.context_processor
    def inject_globals() -> dict:  # noqa: WPS430
        """
        Inject the following names into all templates:

        ``current_user``
            The Flask-Login proxy for the currently logged-in user
            (already available in templates; re-exported here for
            explicitness and to avoid relying on Flask-Login's implicit
            injection in every template).

        ``now``
            Current UTC ``datetime`` — useful for date/time comparisons
            and display without a separate template call.

        ``settings``
            The logged-in user's ``Setting`` row, or a transient
            ``Setting()`` instance carrying default values when the user
            is anonymous or when the DB query fails.
        """
        user_settings = None

        if current_user.is_authenticated:
            # Cache on the request context so the query only runs once per
            # request even if multiple templates/macros call this processor.
            user_settings = getattr(g, '_user_settings', None)
            if user_settings is None:
                try:
                    user_settings = Setting.get_or_create(current_user.id)
                    g._user_settings = user_settings
                except Exception:
                    logger.exception(
                        'Failed to load settings for user %s', current_user.id
                    )
                    user_settings = Setting()   # transient, uses column defaults

        if user_settings is None:
            user_settings = Setting()           # anonymous visitor

        return {
            'current_user': current_user,
            'now': datetime.utcnow(),
            'user_settings': user_settings,
        }


def _register_error_handlers(app: Flask) -> None:
    """Register custom HTTP error page handlers."""

    @app.errorhandler(403)
    def forbidden(exc):  # noqa: WPS430
        logger.warning('403 Forbidden: %s', exc)
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def page_not_found(exc):  # noqa: WPS430
        logger.warning('404 Not Found: %s', exc)
        return render_template('errors/404.html'), 404

    @app.errorhandler(429)
    def too_many_requests(exc):  # noqa: WPS430
        logger.warning('429 Too Many Requests: %s', exc)
        return render_template('errors/429.html'), 429

    @app.errorhandler(500)
    def internal_server_error(exc):  # noqa: WPS430
        logger.error('500 Internal Server Error: %s', exc, exc_info=True)
        # Roll back any broken transaction so the session is usable again.
        db.session.rollback()
        return render_template('errors/500.html'), 500


def _register_security_headers(app: Flask) -> None:
    """Attach baseline hardening response headers."""

    @app.after_request
    def add_security_headers(response):  # noqa: WPS430
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'",
        )
        if request_is_secure():
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        return response


def request_is_secure() -> bool:
    """Return True when the current request is HTTPS."""
    from flask import request, has_request_context

    return bool(has_request_context() and request.is_secure)


def _create_default_admin(app: Flask) -> None:
    """
    On first run, create an admin user from environment variables.

    Reads ``DEFAULT_ADMIN_USERNAME`` and ``DEFAULT_ADMIN_PASSWORD``.
    This function is a no-op if any user already exists in the database,
    so it is safe to call on every application start.

    A default ``Setting`` row is created alongside the user.
    """
    from app.models.setting import Setting
    from app.models.user import User

    try:
        # Tables may not exist yet if migrations haven't run; skip gracefully.
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(db.engine)
        if 'users' not in inspector.get_table_names():
            return

        if db.session.query(User).count() > 0:
            return  # At least one user exists — skip first-run setup.

        username: str = os.environ.get('DEFAULT_ADMIN_USERNAME', '').strip()
        password: str = os.environ.get('DEFAULT_ADMIN_PASSWORD', '').strip()

        if not username or not password:
            logger.warning(
                'No users exist and DEFAULT_ADMIN_USERNAME / '
                'DEFAULT_ADMIN_PASSWORD are not set. '
                'Skipping automatic admin creation. '
                'Set these environment variables before first launch.'
            )
            return

        admin = User(username=username)
        admin.set_password(password)
        db.session.add(admin)
        db.session.flush()   # Populate admin.id before the Setting FK insert.

        # Create default preferences for the new admin user.
        Setting.get_or_create(admin.id)

        db.session.commit()
        logger.info('Default admin user %r created successfully.', username)

    except Exception:
        db.session.rollback()
        logger.exception('Failed to create default admin user.')

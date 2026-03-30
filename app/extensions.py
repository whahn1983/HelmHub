"""
app/extensions.py
~~~~~~~~~~~~~~~~~

Instantiate Flask extensions without binding them to an application.
Each extension is initialised via its ``init_app()`` method inside the
application factory (``app/__init__.py``).
"""

import bcrypt
from flask import current_app, request
from flask_limiter import Limiter
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

# ---------------------------------------------------------------------------
# SQLAlchemy – ORM / database layer
# ---------------------------------------------------------------------------
db: SQLAlchemy = SQLAlchemy()

# ---------------------------------------------------------------------------
# Flask-Migrate – Alembic-based database migrations
# ---------------------------------------------------------------------------
migrate: Migrate = Migrate()

# ---------------------------------------------------------------------------
# Flask-Login – session-based authentication
# ---------------------------------------------------------------------------
login_manager: LoginManager = LoginManager()
login_manager.login_view = 'auth.login'           # redirect unauthenticated users here
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'
login_manager.refresh_view = 'auth.login'         # redirect for fresh-login-required views
login_manager.needs_refresh_message = 'Please re-authenticate to continue.'
login_manager.needs_refresh_message_category = 'warning'

# ---------------------------------------------------------------------------
# Flask-Limiter – rate limiting
# ---------------------------------------------------------------------------
def _rate_limit_key() -> str:
    """Return client IP using trusted proxy settings when configured."""
    trusted_hops = int(current_app.config.get('PROXY_FIX_X_FOR', 0))
    if trusted_hops > 0 and request.access_route:
        return request.access_route[0]
    return request.remote_addr or 'unknown'


limiter: Limiter = Limiter(key_func=_rate_limit_key)

# ---------------------------------------------------------------------------
# Flask-WTF CSRFProtect – CSRF token validation for all forms
# ---------------------------------------------------------------------------
csrf: CSRFProtect = CSRFProtect()

# ---------------------------------------------------------------------------
# bcrypt – password hashing (used directly via the bcrypt module)
#
# Usage:
#   hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
#   match  = bcrypt.checkpw(password.encode('utf-8'), hashed)
#
# ``bcrypt_obj`` is exported so that other modules can import the module
# reference from a single, consistent location.
# ---------------------------------------------------------------------------
bcrypt_obj = bcrypt

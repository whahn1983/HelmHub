"""
tests/conftest.py
~~~~~~~~~~~~~~~~~

Shared pytest fixtures for HelmHub.

The application factory (app/__init__.py) imports several blueprints and
services that are not yet fully implemented.  Before importing ``create_app``
we register lightweight stub modules for every missing dependency so that the
factory can complete without ImportError.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stub out missing blueprint and service modules
# ---------------------------------------------------------------------------

def _make_blueprint_stub(name: str, url_prefix: str):
    """Return a minimal Flask Blueprint instance inside a stub module."""
    from flask import Blueprint

    bp = Blueprint(name, __name__, url_prefix=url_prefix)
    mod = types.ModuleType(f'app.blueprints.{name}')
    setattr(mod, f'{name}_bp', bp)
    return mod


def _register_blueprint_stubs():
    """
    Insert stub modules for every blueprint/service that is referenced by the
    app factory but does not yet have a real implementation on disk.
    """
    # Map blueprint attribute name -> url_prefix
    missing_blueprints = {
        'reminders': '/reminders',
        'events': '/events',
        'settings': '/settings',
        'api': '/api/v1',
    }

    # Ensure the parent package exists in sys.modules
    blueprints_pkg = types.ModuleType('app.blueprints')
    sys.modules.setdefault('app.blueprints', blueprints_pkg)

    for bp_name, prefix in missing_blueprints.items():
        full_name = f'app.blueprints.{bp_name}'
        if full_name not in sys.modules:
            sys.modules[full_name] = _make_blueprint_stub(bp_name, prefix)

    # Stub the four blueprints that DO exist as app.routes.* but the factory
    # expects them under app.blueprints.*
    existing_route_map = {
        'auth': ('app.routes.auth', 'auth_bp'),
        'dashboard': ('app.routes.dashboard', 'dashboard_bp'),
        'tasks': ('app.routes.tasks', 'tasks_bp'),
        'notes': ('app.routes.notes', 'notes_bp'),
    }
    for bp_name, (route_module, bp_attr) in existing_route_map.items():
        full_name = f'app.blueprints.{bp_name}'
        if full_name not in sys.modules:
            stub = types.ModuleType(full_name)
            # Import the real routes module and re-export the blueprint
            import importlib
            real_mod = importlib.import_module(route_module)
            setattr(stub, bp_attr, getattr(real_mod, bp_attr))
            sys.modules[full_name] = stub

    # Stub missing service modules
    _stub_service('app.services.totp_service', {
        'verify_totp_token': lambda secret, token: False,
    })
    _stub_service('app.services.auth_service', {
        'parse_datetime': _parse_datetime_impl,
    })


def _stub_service(module_name: str, attrs: dict):
    if module_name not in sys.modules:
        mod = types.ModuleType(module_name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[module_name] = mod


def _parse_datetime_impl(date_str, time_str=None):
    """Minimal parse_datetime used by tasks route."""
    from datetime import datetime
    fmt = '%Y-%m-%d'
    try:
        if time_str:
            return datetime.strptime(f'{date_str} {time_str}', '%Y-%m-%d %H:%M')
        return datetime.strptime(date_str, fmt)
    except ValueError:
        return None


# Run stub registration immediately at import time so that create_app can be
# imported cleanly by any test module that imports this conftest.
_register_blueprint_stubs()


# ---------------------------------------------------------------------------
# App / DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def app():
    """Create a testing application instance with an in-memory SQLite DB."""
    from app import create_app

    application = create_app('testing')
    with application.app_context():
        from app.extensions import db as _db
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(scope='function')
def db(app):
    """
    Yield the SQLAlchemy db object and clean all tables after each test.

    A rollback is issued first to discard any broken transaction, then every
    table is truncated so each test starts from an empty state.
    """
    from app.extensions import db as _db

    with app.app_context():
        yield _db
        _db.session.rollback()
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()


@pytest.fixture(scope='function')
def client(app):
    """Return a Flask test client."""
    return app.test_client()


@pytest.fixture(scope='function')
def test_user(db):
    """Create and persist a plain (no TOTP) test user."""
    from app.models import User

    user = User(username='testuser')
    user.set_password('testpassword123')
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture(scope='function')
def totp_user(db):
    """Create and persist a test user with TOTP enabled."""
    import pyotp
    from app.models import User

    user = User(username='totpuser')
    user.set_password('totppassword123')
    user.totp_secret = pyotp.random_base32()
    user.totp_enabled = True
    user.generate_recovery_codes()
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture(scope='function')
def auth_client(client, test_user, app):
    """Return a test client with the test_user already logged in."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(test_user.id)
        sess['_fresh'] = True
    return client

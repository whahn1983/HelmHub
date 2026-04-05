"""
Authentication routes: login, TOTP two-factor, logout, and first-run setup.
"""

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, session, abort,
)
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db, limiter
from app.models import User
from app.services.totp_service import verify_totp_token

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def _safe_next_target(target: str | None) -> str | None:
    """Return a safe internal redirect target, else ``None``."""
    if not target:
        return None
    cleaned = target.strip()
    if not cleaned.startswith('/') or cleaned.startswith('//'):
        return None
    return cleaned


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
def login():
    """Username/password login. Redirects to TOTP step when 2FA is enabled."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember_me = request.form.get('remember_me') == 'on'

        if not username or not password:
            flash('Username and password are required.', 'danger')
            return render_template('auth/login.html'), 400

        user = User.query.filter_by(username=username).first()

        if user is None or not user.check_password(password):
            flash('Invalid username or password.', 'danger')
            return render_template('auth/login.html'), 401

        if user.totp_enabled:
            # Park the user id in the session and continue to the TOTP step.
            session['pending_totp_user_id'] = user.id
            session['pending_totp_remember'] = remember_me
            return redirect(url_for('auth.totp'))

        # No TOTP — log in directly.
        login_user(user, remember=remember_me)
        flash('Welcome back!', 'success')

        next_page = _safe_next_target(request.args.get('next'))
        if next_page:
            return redirect(next_page)
        return redirect(url_for('dashboard.index'))

    return render_template('auth/login.html')


# ---------------------------------------------------------------------------
# TOTP verification
# ---------------------------------------------------------------------------

@auth_bp.route('/totp', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
def totp():
    """Second factor verification step (TOTP token or recovery code)."""
    user_id = session.get('pending_totp_user_id')
    if not user_id:
        flash('Please log in first.', 'warning')
        return redirect(url_for('auth.login'))

    user = db.session.get(User, user_id)
    if user is None:
        session.pop('pending_totp_user_id', None)
        session.pop('pending_totp_remember', None)
        flash('Session expired. Please log in again.', 'warning')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        totp_code = request.form.get('totp_code', '').strip()
        recovery_code = request.form.get('recovery_code', '').strip().upper()

        verified = False

        if totp_code:
            # Standard 6-digit TOTP token.
            if len(totp_code) != 6 or not totp_code.isdigit():
                flash('TOTP code must be exactly 6 digits.', 'danger')
                return render_template('auth/totp.html'), 400
            verified = verify_totp_token(user.totp_secret, totp_code)

        elif recovery_code:
            # Recovery code path.
            verified = user.use_recovery_code(recovery_code)
            if verified:
                db.session.commit()

        if not verified:
            flash('Invalid verification code. Please try again.', 'danger')
            return render_template('auth/totp.html'), 401

        remember_me = session.pop('pending_totp_remember', False)
        session.pop('pending_totp_user_id', None)

        login_user(user, remember=remember_me)
        flash('Welcome back!', 'success')

        next_page = _safe_next_target(request.args.get('next'))
        if next_page:
            return redirect(next_page)
        return redirect(url_for('dashboard.index'))

    return render_template('auth/totp.html')


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@auth_bp.route('/logout')
@login_required
def logout():
    """Log out the current user and redirect to the login page."""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


# ---------------------------------------------------------------------------
# First-run setup
# ---------------------------------------------------------------------------

@auth_bp.route('/setup')
@login_required
def setup():
    """First-run setup page (accessible only to authenticated admin users)."""
    return render_template('auth/setup.html')

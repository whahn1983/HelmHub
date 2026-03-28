"""
Settings routes.

Handles user preferences (theme, time format, etc.) and TOTP/2FA
management including enrollment, verification, disabling, and
recovery-code regeneration.
"""

import base64
import io

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, make_response, abort, current_app,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Setting
from app.services.totp_service import (
    generate_totp_secret,
    get_totp_uri,
    verify_totp_token,
    generate_qr_code_png,
    generate_recovery_codes,
)

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')

# Valid choices (mirrors Setting model constants).
VALID_THEMES = ('light', 'dark', 'system')
VALID_TIME_FORMATS = ('12', '24')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_htmx():
    return request.headers.get('HX-Request') == 'true'


def _get_or_create_settings():
    """Return (and persist) the Setting row for the current user."""
    setting = Setting.get_or_create(current_user.id)
    return setting


# ---------------------------------------------------------------------------
# General settings
# ---------------------------------------------------------------------------

@settings_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    """Show and save general user preferences."""
    setting = _get_or_create_settings()

    if request.method == 'POST':
        theme = request.form.get('theme', 'system').strip().lower()
        time_format = request.form.get('time_format', '12').strip()
        default_page = request.form.get('default_page', 'dashboard').strip()
        show_weather = request.form.get('show_weather') == 'on'

        errors = []
        if theme not in VALID_THEMES:
            errors.append(f'Invalid theme. Choose from: {", ".join(VALID_THEMES)}.')
        if time_format not in VALID_TIME_FORMATS:
            errors.append(f'Invalid time format. Choose from: {", ".join(VALID_TIME_FORMATS)}.')

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            return render_template('settings/index.html', setting=setting, errors=errors), 422

        setting.theme = theme
        setting.time_format = time_format
        setting.default_page = default_page
        setting.show_weather = show_weather
        db.session.commit()

        flash('Settings saved.', 'success')

        if _is_htmx():
            response = make_response(render_template('partials/settings_saved.html'))
            response.headers['HX-Trigger'] = 'settingsSaved'
            return response

        return redirect(url_for('settings.index'))

    return render_template('settings/index.html', setting=setting)


# ---------------------------------------------------------------------------
# TOTP management page
# ---------------------------------------------------------------------------

@settings_bp.route('/totp')
@login_required
def totp():
    """TOTP management overview page."""
    return render_template(
        'settings/totp.html',
        totp_enabled=current_user.totp_enabled,
    )


# ---------------------------------------------------------------------------
# TOTP enable: generate secret and show QR code
# ---------------------------------------------------------------------------

@settings_bp.route('/totp/enable', methods=['POST'])
@login_required
def totp_enable():
    """
    Begin TOTP enrollment.

    Generates a new TOTP secret, stores it (unconfirmed) on the user,
    and renders a page with the QR code and provisioning URI.

    The secret is only activated after the user verifies a valid token
    via /settings/totp/verify.
    """
    if current_user.totp_enabled:
        flash('Two-factor authentication is already enabled.', 'info')
        return redirect(url_for('settings.totp'))

    secret = generate_totp_secret()
    uri = get_totp_uri(secret, current_user.username)
    qr_b64 = generate_qr_code_png(uri)

    # Store the pending secret on the user so /totp/verify can read it.
    # We do NOT set totp_enabled=True yet — that happens after verification.
    current_user.totp_secret = secret
    db.session.commit()

    return render_template(
        'settings/totp_setup.html',
        secret=secret,
        uri=uri,
        qr_b64=qr_b64,
    )


# ---------------------------------------------------------------------------
# TOTP verify: confirm enrollment
# ---------------------------------------------------------------------------

@settings_bp.route('/totp/verify', methods=['POST'])
@login_required
def totp_verify():
    """
    Confirm TOTP enrollment by verifying a live token.

    Requires a valid 6-digit code from the user's authenticator app.
    On success, sets totp_enabled=True and generates recovery codes.
    """
    if current_user.totp_enabled:
        flash('Two-factor authentication is already enabled.', 'info')
        return redirect(url_for('settings.totp'))

    if not current_user.totp_secret:
        flash('No TOTP secret found. Please start the setup again.', 'danger')
        return redirect(url_for('settings.totp'))

    token = request.form.get('totp_code', '').strip()
    if not token or len(token) != 6 or not token.isdigit():
        flash('Please enter a valid 6-digit code.', 'danger')
        return redirect(url_for('settings.totp_enable'))

    if not verify_totp_token(current_user.totp_secret, token):
        flash('Invalid code. Please try again.', 'danger')
        return redirect(url_for('settings.totp_enable'))

    # Activate TOTP and generate recovery codes.
    current_user.totp_enabled = True
    recovery_codes = current_user.generate_recovery_codes()
    db.session.commit()

    flash('Two-factor authentication has been enabled.', 'success')
    return render_template(
        'settings/totp_recovery_codes.html',
        recovery_codes=recovery_codes,
        just_enabled=True,
    )


# ---------------------------------------------------------------------------
# TOTP disable
# ---------------------------------------------------------------------------

@settings_bp.route('/totp/disable', methods=['POST'])
@login_required
def totp_disable():
    """
    Disable TOTP after confirming the user's password.

    Requires 'password' in the POST body.
    """
    if not current_user.totp_enabled:
        flash('Two-factor authentication is not currently enabled.', 'info')
        return redirect(url_for('settings.totp'))

    password = request.form.get('password', '')
    if not current_user.check_password(password):
        flash('Incorrect password. Two-factor authentication was not disabled.', 'danger')
        return redirect(url_for('settings.totp'))

    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_recovery_codes = None
    db.session.commit()

    flash('Two-factor authentication has been disabled.', 'success')
    return redirect(url_for('settings.totp'))


# ---------------------------------------------------------------------------
# Regenerate recovery codes
# ---------------------------------------------------------------------------

@settings_bp.route('/totp/recovery-codes', methods=['POST'])
@login_required
def totp_recovery_codes():
    """
    Regenerate TOTP recovery codes.

    Requires password confirmation. The previous codes are invalidated.
    """
    if not current_user.totp_enabled:
        flash('Two-factor authentication is not enabled.', 'warning')
        return redirect(url_for('settings.totp'))

    password = request.form.get('password', '')
    if not current_user.check_password(password):
        flash('Incorrect password. Recovery codes were not regenerated.', 'danger')
        return redirect(url_for('settings.totp'))

    recovery_codes = current_user.generate_recovery_codes()
    db.session.commit()

    flash('Recovery codes have been regenerated. Save these codes somewhere safe.', 'success')
    return render_template(
        'settings/totp_recovery_codes.html',
        recovery_codes=recovery_codes,
        just_enabled=False,
    )


# ---------------------------------------------------------------------------
# QR code image endpoint
# ---------------------------------------------------------------------------

@settings_bp.route('/totp/qr')
@login_required
def totp_qr():
    """
    Return the TOTP provisioning QR code as a PNG image.

    Only available while the user has a totp_secret set (during or after
    enrollment). Returns 404 if no secret exists.
    """
    if not current_user.totp_secret:
        abort(404)

    uri = get_totp_uri(current_user.totp_secret, current_user.username)
    qr_b64 = generate_qr_code_png(uri)
    png_bytes = base64.b64decode(qr_b64)

    response = make_response(png_bytes)
    response.headers['Content-Type'] = 'image/png'
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return response

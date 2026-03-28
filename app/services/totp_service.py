"""
TOTP (Time-based One-Time Password) service helpers.

Provides functions for:
  - Generating TOTP secrets
  - Building provisioning URIs (for QR codes)
  - Verifying tokens
  - Producing QR-code PNGs as base64-encoded strings
  - Generating one-time recovery codes
"""

import io
import base64
import secrets

import pyotp
import qrcode


# ---------------------------------------------------------------------------
# Secret generation
# ---------------------------------------------------------------------------

def generate_totp_secret() -> str:
    """Generate a cryptographically random base32 TOTP secret."""
    return pyotp.random_base32()


# ---------------------------------------------------------------------------
# Provisioning URI (for QR codes)
# ---------------------------------------------------------------------------

def get_totp_uri(secret: str, username: str, issuer: str = 'HelmHub') -> str:
    """
    Return an otpauth:// provisioning URI for the given secret and username.

    This URI can be encoded into a QR code that authenticator apps scan.
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=issuer)


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def verify_totp_token(secret: str, token: str) -> bool:
    """
    Verify a 6-digit TOTP token against *secret*.

    Allows a 1-interval window on either side to accommodate clock skew
    between the server and the user's device.

    Returns True if the token is valid, False otherwise.
    """
    if not secret or not token:
        return False
    try:
        totp = pyotp.TOTP(secret)
        return totp.verify(token, valid_window=1)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# QR code generation
# ---------------------------------------------------------------------------

def generate_qr_code_png(uri: str) -> str:
    """
    Generate a QR code image for *uri* and return it as a base64-encoded PNG.

    The returned string can be embedded directly in an HTML <img> tag:
        <img src="data:image/png;base64,{{ qr_b64 }}">
    """
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode('ascii')


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------

def generate_recovery_codes(count: int = 8) -> list:
    """
    Generate *count* random one-time recovery codes.

    Each code is formatted as XXXXXXXX-XXXXXXXX (two 8-hex-char segments
    separated by a hyphen, rendered in uppercase).

    The plaintext codes are returned. The caller is responsible for
    hashing and storing them (e.g. via User.generate_recovery_codes()).
    """
    codes = []
    for _ in range(count):
        code = f"{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}"
        codes.append(code)
    return codes

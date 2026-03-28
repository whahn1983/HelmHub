"""
Authentication and request utility helpers.

Provides:
  - parse_datetime: parse date/time strings from HTML form inputs
  - get_client_ip: resolve the real client IP, respecting X-Forwarded-For
"""

from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Date / time parsing
# ---------------------------------------------------------------------------

# Supported date formats from HTML <input type="date"> and common manual
# entries.  Order matters: try the most specific formats first.
_DATE_FORMATS = [
    '%Y-%m-%d',    # HTML date input standard (2025-12-31)
    '%d/%m/%Y',    # European style (31/12/2025)
    '%m/%d/%Y',    # US style      (12/31/2025)
    '%d-%m-%Y',    # Dashed European (31-12-2025)
    '%d.%m.%Y',    # Dot-separated (31.12.2025)
]

_TIME_FORMATS = [
    '%H:%M',       # 24-hour (14:30)
    '%H:%M:%S',    # 24-hour with seconds (14:30:00)
    '%I:%M %p',    # 12-hour AM/PM (02:30 PM)
    '%I:%M%p',     # 12-hour AM/PM no space (02:30PM)
]


def parse_datetime(date_str: str, time_str: Optional[str] = None) -> Optional[datetime]:
    """
    Parse a date string (and optional time string) into a datetime object.

    Parameters
    ----------
    date_str:
        A date string from an HTML form input.  Accepts several common
        formats (see _DATE_FORMATS above).  Required.

    time_str:
        An optional time string.  When provided it is combined with the
        parsed date.  When omitted, midnight (00:00) is assumed.

    Returns
    -------
    datetime | None
        A naive UTC-normalised datetime, or None if parsing fails.
    """
    if not date_str:
        return None

    date_str = date_str.strip()
    parsed_date = None

    for fmt in _DATE_FORMATS:
        try:
            parsed_date = datetime.strptime(date_str, fmt).date()
            break
        except ValueError:
            continue

    if parsed_date is None:
        return None

    # Parse the time component (optional).
    parsed_time = None
    if time_str:
        time_str = time_str.strip()
        for fmt in _TIME_FORMATS:
            try:
                parsed_time = datetime.strptime(time_str, fmt).time()
                break
            except ValueError:
                continue

    if parsed_time is not None:
        return datetime.combine(parsed_date, parsed_time)

    # No time provided — default to midnight.
    return datetime(parsed_date.year, parsed_date.month, parsed_date.day, 0, 0, 0)


# ---------------------------------------------------------------------------
# Client IP resolution
# ---------------------------------------------------------------------------

def get_client_ip(request) -> str:
    """
    Return the best-guess real IP address of the HTTP client.

    When the application is deployed behind a reverse proxy (e.g. nginx or
    an AWS load balancer) the actual client IP is typically forwarded in the
    X-Forwarded-For header.  The leftmost address in that header is the
    original client; any addresses to the right are proxies.

    Falls back to request.remote_addr when no forwarding header is present.

    Parameters
    ----------
    request:
        A Flask ``request`` proxy or any object that exposes:
          - ``headers`` – a dict-like mapping of HTTP headers
          - ``remote_addr`` – the socket-level peer address

    Returns
    -------
    str
        The resolved client IP address string.
    """
    forwarded_for = request.headers.get('X-Forwarded-For', '')
    if forwarded_for:
        # X-Forwarded-For: <client>, <proxy1>, <proxy2>
        # Take the first (leftmost) address.
        client_ip = forwarded_for.split(',')[0].strip()
        if client_ip:
            return client_ip

    # Fall back to the direct connection address.
    return request.remote_addr or '0.0.0.0'

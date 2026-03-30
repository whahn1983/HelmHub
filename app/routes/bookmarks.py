"""
Bookmarks routes.

Handles CRUD for saved links/URLs.
Supports both full-page and HTMX partial responses.

Bookmark model fields: title, url, description, category (tag string), pinned.
"""

import re
import time
import urllib.request
import urllib.error

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, abort, make_response, Response,
)
from flask_login import login_required, current_user


from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from app.extensions import db
from app.models import Bookmark

bookmarks_bp = Blueprint('bookmarks', __name__, url_prefix='/bookmarks')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MAX_BOOKMARK_IMPORT_SIZE = 2 * 1024 * 1024
ALLOWED_BOOKMARK_SCHEMES = {'http', 'https', 'ftp'}


class _NetscapeBookmarkParser(HTMLParser):
    """Parse Netscape bookmark HTML into bookmark dictionaries."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries = []
        self._folder_stack = []
        self._in_h3 = False
        self._in_a = False
        self._pending_href = ''
        self._pending_title_parts = []
        self._current_folder_parts = []

    def handle_starttag(self, tag, attrs):
        attrs_map = dict(attrs)
        if tag.lower() == 'h3':
            self._in_h3 = True
            self._current_folder_parts = []
        elif tag.lower() == 'a':
            self._in_a = True
            self._pending_href = attrs_map.get('href', '')
            self._pending_title_parts = []

    def handle_endtag(self, tag):
        lower = tag.lower()
        if lower == 'h3':
            self._in_h3 = False
            folder_name = ''.join(self._current_folder_parts).strip()
            if folder_name:
                self._folder_stack.append(folder_name)
        elif lower == 'dl':
            if self._folder_stack:
                self._folder_stack.pop()
        elif lower == 'a':
            self._in_a = False
            title = ''.join(self._pending_title_parts).strip()
            href = (self._pending_href or '').strip()
            if href:
                category = self._folder_stack[-1].strip().lower() if self._folder_stack else None
                self.entries.append({
                    'title': title or href,
                    'url': href,
                    'category': category or None,
                })

    def handle_data(self, data):
        if self._in_h3:
            self._current_folder_parts.append(data)
        elif self._in_a:
            self._pending_title_parts.append(data)


def _is_safe_bookmark_url(url: str) -> bool:
    """Allow only valid absolute URLs with safe schemes."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_BOOKMARK_SCHEMES:
        return False
    if not parsed.netloc:
        return False
    return True


def _normalise_imported_bookmark(entry: dict) -> dict | None:
    """Sanitise imported bookmark entry and return a safe representation."""
    title = (entry.get('title') or '').strip()[:255]
    raw_url = (entry.get('url') or '').strip()
    parsed_raw = urlparse(raw_url)
    if parsed_raw.scheme and parsed_raw.scheme.lower() not in ALLOWED_BOOKMARK_SCHEMES:
        return None

    url = _normalise_url(raw_url)
    category = ((entry.get('category') or '').strip().lower() or None)

    if category:
        category = category[:64]

    if not url or not _is_safe_bookmark_url(url):
        return None

    if not title:
        title = url

    return {'title': title, 'url': url, 'category': category}


def _is_htmx():
    return request.headers.get('HX-Request') == 'true'


def _bookmark_or_404(bookmark_id):
    bookmark = db.session.get(Bookmark, bookmark_id)
    if bookmark is None or bookmark.user_id != current_user.id:
        abort(404)
    return bookmark


def _normalise_url(url: str) -> str:
    """Ensure the URL has a scheme so links open correctly."""
    url = url.strip()
    if url and not url.startswith(('http://', 'https://', 'ftp://')):
        url = 'https://' + url
    return url


# ---------------------------------------------------------------------------
# Favicon proxy helpers
# ---------------------------------------------------------------------------

_favicon_cache: dict = {}  # domain -> (prefer_direct, timestamp)
_FAVICON_CACHE_TTL = 3600  # seconds

_SAFE_DOMAIN_RE = re.compile(r'^[a-zA-Z0-9.\-]+(:\d+)?$')
_PRIVATE_HOST_RE = re.compile(
    r'^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|::1'
    r'|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+'
    r'|192\.168\.\d+\.\d+)$',
    re.IGNORECASE,
)


def _probe_direct_favicon(domain: str) -> bool:
    """Return True when the direct favicon URL appears reachable.

    We intentionally avoid ``HEAD`` requests here because many sites reject
    HEAD while still serving a valid favicon to normal browser GET requests.
    """
    try:
        req = urllib.request.Request(
            f'https://{domain}/favicon.ico',
            method='GET',
            headers={'User-Agent': 'Mozilla/5.0 HelmHub/1.0'},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status < 400
    except urllib.error.HTTPError:
        # Site explicitly rejected favicon request.
        return False
    except Exception:
        # Network/proxy/transient errors should not force a fallback provider;
        # let the browser attempt the direct favicon URL itself.
        return True


def _download_favicon(url: str) -> tuple[bytes, str] | None:
    """Fetch favicon bytes and best-effort content type from a URL."""
    try:
        req = urllib.request.Request(
            url,
            method='GET',
            headers={'User-Agent': 'Mozilla/5.0 HelmHub/1.0'},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status >= 400:
                return None
            content = resp.read()
            if not content:
                return None
            content_type = (resp.headers.get('Content-Type') or 'image/x-icon').split(';', 1)[0].strip()
            return content, content_type
    except Exception:
        return None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@bookmarks_bp.route('/')
@login_required
def index():
    """
    List bookmarks with optional filters.

    Query params:
      category – filter by category value (exact match)
      search   – substring search on title, url, or description
      pinned   – 'true' to show only pinned bookmarks
    """
    category = request.args.get('category', '').strip().lower()
    search = request.args.get('search', '').strip()
    pinned_only = request.args.get('pinned', '').lower() == 'true'

    query = Bookmark.query.filter_by(user_id=current_user.id)

    if pinned_only:
        query = query.filter(Bookmark.pinned == True)  # noqa: E712

    if category:
        query = query.filter(Bookmark.category.ilike(f'%{category}%'))

    if search:
        like = f'%{search}%'
        query = query.filter(
            db.or_(
                Bookmark.title.ilike(like),
                Bookmark.url.ilike(like),
                Bookmark.description.ilike(like),
            )
        )

    # Pinned bookmarks float to the top, then sort by creation date (newest first).
    bookmarks = query.order_by(Bookmark.pinned.desc(), Bookmark.created_at.desc()).all()

    # Collect all unique categories for the filter dropdown
    all_categories = (
        db.session.query(Bookmark.category)
        .filter(Bookmark.user_id == current_user.id, Bookmark.category.isnot(None))
        .distinct()
        .order_by(Bookmark.category.asc())
        .all()
    )
    all_categories = [row[0] for row in all_categories if row[0]]

    # Build grouped view when no filters are active
    show_grouped = not (category or search or pinned_only)
    grouped_bookmarks = None
    if show_grouped and bookmarks:
        cat_groups: dict = {}
        for bm in bookmarks:
            key = bm.category or ''
            cat_groups.setdefault(key, []).append(bm)
        # Named categories alphabetically, uncategorized at end
        named = sorted([(k, v) for k, v in cat_groups.items() if k], key=lambda x: x[0])
        if '' in cat_groups:
            named.append(('', cat_groups['']))
        grouped_bookmarks = named

    return render_template(
        'bookmarks/index.html',
        bookmarks=bookmarks,
        category=category,
        search=search,
        pinned_only=pinned_only,
        all_categories=all_categories,
        show_grouped=show_grouped,
        grouped_bookmarks=grouped_bookmarks,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@bookmarks_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    """Render the create-bookmark form (GET) or process a submission (POST)."""
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        url = _normalise_url(request.form.get('url', ''))
        description = request.form.get('description', '').strip()
        category = request.form.get('category', '').strip().lower()
        pinned = request.form.get('pinned') == 'on'

        errors = []
        if not title:
            errors.append('Title is required.')
        if not url:
            errors.append('URL is required.')

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            return render_template(
                'bookmarks/new.html',
                errors=errors,
                form=request.form,
            ), 422

        bookmark = Bookmark(
            user_id=current_user.id,
            title=title,
            url=url,
            description=description or None,
            category=category or None,
            pinned=pinned,
        )
        db.session.add(bookmark)
        db.session.commit()

        flash('Bookmark saved.', 'success')
        return redirect(url_for('bookmarks.index'))

    # GET
    return render_template('bookmarks/new.html', form={})


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@bookmarks_bp.route('/<int:bookmark_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(bookmark_id):
    """Edit an existing bookmark."""
    bookmark = _bookmark_or_404(bookmark_id)

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        url = _normalise_url(request.form.get('url', ''))
        description = request.form.get('description', '').strip()
        category = request.form.get('category', '').strip().lower()
        pinned = request.form.get('pinned') == 'on'

        errors = []
        if not title:
            errors.append('Title is required.')
        if not url:
            errors.append('URL is required.')

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            return render_template(
                'bookmarks/edit.html',
                bookmark=bookmark,
                errors=errors,
                form=request.form,
            ), 422

        bookmark.title = title
        bookmark.url = url
        bookmark.description = description or None
        bookmark.category = category or None
        bookmark.pinned = pinned
        db.session.commit()

        flash('Bookmark updated.', 'success')

        if _is_htmx():
            response = make_response(
                render_template('bookmarks/bookmark_item.html', bookmark=bookmark)
            )
            response.headers['HX-Trigger'] = 'bookmarkUpdated'
            return response

        return redirect(url_for('bookmarks.index'))

    # GET
    return render_template('bookmarks/edit.html', bookmark=bookmark, form=bookmark)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@bookmarks_bp.route('/<int:bookmark_id>/delete', methods=['POST'])
@login_required
def delete(bookmark_id):
    """Permanently delete a bookmark."""
    bookmark = _bookmark_or_404(bookmark_id)
    db.session.delete(bookmark)
    db.session.commit()

    flash('Bookmark deleted.', 'info')

    if _is_htmx():
        response = make_response('')
        response.headers['HX-Trigger'] = 'bookmarkDeleted'
        return response

    return redirect(request.referrer or url_for('bookmarks.index'))


# ---------------------------------------------------------------------------
# Pin toggle
# ---------------------------------------------------------------------------

@bookmarks_bp.route('/<int:bookmark_id>/pin', methods=['POST'])
@login_required
def pin(bookmark_id):
    """Toggle the pinned flag on a bookmark."""
    bookmark = _bookmark_or_404(bookmark_id)
    bookmark.pinned = not bookmark.pinned
    db.session.commit()

    if _is_htmx():
        response = make_response(
            render_template('bookmarks/bookmark_item.html', bookmark=bookmark)
        )
        response.headers['HX-Trigger'] = 'bookmarkPinChanged'
        return response

    return redirect(request.referrer or url_for('bookmarks.index'))


# ---------------------------------------------------------------------------
# Favicon proxy
# ---------------------------------------------------------------------------

@bookmarks_bp.route('/favicon')
@login_required
def favicon_proxy():
    """Fetch and serve a favicon via same-origin response.

    Resolution order:
      1. https://{domain}/favicon.ico  (direct, prefers HTTPS)
      2. Google favicon service         (last fallback)
    """
    domain = request.args.get('domain', '').strip()
    if not domain or not _SAFE_DOMAIN_RE.match(domain):
        abort(400)

    host = domain.split(':')[0]
    if _PRIVATE_HOST_RE.match(host):
        abort(400)

    now = time.time()
    cached = _favicon_cache.get(domain)
    direct_url = f'https://{domain}/favicon.ico'
    fallback_url = f'https://www.google.com/s2/favicons?domain={domain}&sz=32'

    if cached and now - cached[1] < _FAVICON_CACHE_TTL:
        prefer_direct = cached[0]
    else:
        prefer_direct = _probe_direct_favicon(domain)
        _favicon_cache[domain] = (prefer_direct, now)

    candidates = [direct_url, fallback_url] if prefer_direct else [fallback_url, direct_url]
    for candidate in candidates:
        fetched = _download_favicon(candidate)
        if fetched:
            content, content_type = fetched
            response = make_response(content)
            response.headers['Content-Type'] = content_type
            response.headers['Cache-Control'] = 'public, max-age=86400'
            return response

    abort(502)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@bookmarks_bp.route('/export', methods=['GET'])
@login_required
def export_bookmarks():
    """Export bookmarks using the Netscape bookmark HTML format."""
    bookmarks = (
        Bookmark.query
        .filter_by(user_id=current_user.id)
        .order_by(Bookmark.category.asc().nullslast(), Bookmark.title.asc())
        .all()
    )

    lines = [
        '<!DOCTYPE NETSCAPE-Bookmark-file-1>',
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        '<TITLE>Bookmarks</TITLE>',
        '<H1>Bookmarks</H1>',
        '<DL><p>',
    ]

    grouped = {}
    for bm in bookmarks:
        key = bm.category or 'uncategorized'
        grouped.setdefault(key, []).append(bm)

    for category in sorted(grouped.keys()):
        lines.append(f'  <DT><H3>{escape(category)}</H3>')
        lines.append('  <DL><p>')
        for bm in grouped[category]:
            lines.append(
                f'    <DT><A HREF="{escape(bm.url, quote=True)}">{escape(bm.title)}</A>'
            )
        lines.append('  </DL><p>')

    lines.append('</DL><p>')

    payload = '\n'.join(lines)
    response = Response(payload, mimetype='text/html; charset=utf-8')
    response.headers['Content-Disposition'] = 'attachment; filename=helmhub-bookmarks.html'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@bookmarks_bp.route('/import', methods=['POST'])
@login_required
def import_bookmarks():
    """Import bookmarks from Netscape bookmark HTML with safe upsert semantics."""
    uploaded = request.files.get('bookmark_file')
    if not uploaded or not uploaded.filename:
        flash('Please choose an HTML bookmarks file to import.', 'danger')
        return redirect(url_for('bookmarks.index'))

    raw_data = uploaded.read(MAX_BOOKMARK_IMPORT_SIZE + 1)
    if len(raw_data) > MAX_BOOKMARK_IMPORT_SIZE:
        flash('Import file is too large. Maximum size is 2 MB.', 'danger')
        return redirect(url_for('bookmarks.index'))

    try:
        html_text = raw_data.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        flash('Import failed: file must be valid UTF-8 encoded HTML.', 'danger')
        return redirect(url_for('bookmarks.index'))

    parser = _NetscapeBookmarkParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        flash('Import failed: unable to parse bookmark HTML file.', 'danger')
        return redirect(url_for('bookmarks.index'))

    if not parser.entries:
        flash('No bookmark entries were found in the uploaded file.', 'warning')
        return redirect(url_for('bookmarks.index'))

    existing = {
        row.url: row
        for row in Bookmark.query.filter_by(user_id=current_user.id).all()
    }

    unique_import_rows = {}
    for entry in parser.entries:
        normalised = _normalise_imported_bookmark(entry)
        if not normalised:
            continue
        unique_import_rows[normalised['url']] = normalised

    created = 0
    updated = 0

    for row in unique_import_rows.values():
        current = existing.get(row['url'])
        if current is None:
            db.session.add(
                Bookmark(
                    user_id=current_user.id,
                    title=row['title'],
                    url=row['url'],
                    category=row['category'],
                    pinned=False,
                )
            )
            created += 1
            continue

        changed = False
        if current.title != row['title']:
            current.title = row['title']
            changed = True
        if current.category != row['category']:
            current.category = row['category']
            changed = True
        if changed:
            updated += 1

    db.session.commit()

    skipped = len(parser.entries) - len(unique_import_rows)
    flash(
        f'Bookmark import complete: {created} added, {updated} updated, {skipped} skipped.',
        'success',
    )
    return redirect(url_for('bookmarks.index'))

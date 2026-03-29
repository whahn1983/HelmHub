"""
Bookmarks routes.

Handles CRUD for saved links/URLs.
Supports both full-page and HTMX partial responses.

Bookmark model fields: title, url, description, category (tag string), pinned.
"""

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, abort, make_response,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Bookmark

bookmarks_bp = Blueprint('bookmarks', __name__, url_prefix='/bookmarks')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

    return render_template(
        'bookmarks/index.html',
        bookmarks=bookmarks,
        category=category,
        search=search,
        pinned_only=pinned_only,
        all_categories=all_categories,
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

"""
Notes routes.

Handles CRUD for notes plus a special persistent scratchpad note.
Supports both full-page and HTMX partial responses.

Note model fields: title, body (text content), tag (single tag string), pinned.
"""

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, abort, make_response,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Note

notes_bp = Blueprint('notes', __name__, url_prefix='/notes')

SCRATCHPAD_TITLE = 'Scratchpad'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_htmx():
    return request.headers.get('HX-Request') == 'true'


def _note_or_404(note_id):
    note = db.session.get(Note, note_id)
    if note is None or note.user_id != current_user.id:
        abort(404)
    return note


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@notes_bp.route('/')
@login_required
def index():
    """
    List notes with optional filters.

    Query params:
      tag    – filter by tag value (exact match)
      search – substring search on title or body
      pinned – 'true' to show only pinned notes
    """
    tag = request.args.get('tag', '').strip().lower()
    search = request.args.get('search', '').strip()
    pinned_only = request.args.get('pinned', '').lower() == 'true'

    query = Note.query.filter_by(user_id=current_user.id)

    if pinned_only:
        query = query.filter(Note.pinned == True)  # noqa: E712

    if tag:
        query = query.filter(Note.tag.ilike(f'%{tag}%'))

    if search:
        like = f'%{search}%'
        query = query.filter(
            db.or_(Note.title.ilike(like), Note.body.ilike(like))
        )

    # Pinned notes float to the top, then sort by last-updated.
    notes = query.order_by(Note.pinned.desc(), Note.updated_at.desc()).all()

    if _is_htmx():
        return render_template(
            'partials/notes_list.html',
            notes=notes,
            tag=tag,
            search=search,
            pinned_only=pinned_only,
        )

    return render_template(
        'notes/index.html',
        notes=notes,
        tag=tag,
        search=search,
        pinned_only=pinned_only,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@notes_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    """Render the create-note form (GET) or process a submission (POST)."""
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        tag = request.form.get('tag', '').strip().lower()
        pinned = request.form.get('pinned') == 'on'

        errors = []
        if not title:
            errors.append('Title is required.')

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            if _is_htmx():
                return render_template(
                    'partials/note_form.html',
                    errors=errors,
                    form=request.form,
                ), 422
            return render_template('notes/new.html', errors=errors, form=request.form), 422

        note = Note(
            user_id=current_user.id,
            title=title,
            body=body,
            tag=tag or None,
            pinned=pinned,
        )
        db.session.add(note)
        db.session.commit()

        flash('Note created.', 'success')

        if _is_htmx():
            response = make_response(render_template('partials/note_item.html', note=note))
            response.headers['HX-Trigger'] = 'noteCreated'
            return response

        return redirect(url_for('notes.index'))

    # GET
    if _is_htmx():
        return render_template('partials/note_form.html', form={})

    return render_template('notes/new.html', form={})


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@notes_bp.route('/<int:note_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(note_id):
    """Edit an existing note."""
    note = _note_or_404(note_id)

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        tag = request.form.get('tag', '').strip().lower()
        pinned = request.form.get('pinned') == 'on'

        errors = []
        if not title:
            errors.append('Title is required.')

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            if _is_htmx():
                return render_template(
                    'partials/note_form.html',
                    note=note,
                    errors=errors,
                    form=request.form,
                ), 422
            return render_template('notes/edit.html', note=note, errors=errors, form=request.form), 422

        note.title = title
        note.body = body
        note.tag = tag or None
        note.pinned = pinned
        db.session.commit()

        flash('Note updated.', 'success')

        if _is_htmx():
            response = make_response(render_template('partials/note_item.html', note=note))
            response.headers['HX-Trigger'] = 'noteUpdated'
            return response

        return redirect(url_for('notes.index'))

    # GET
    if _is_htmx():
        return render_template('partials/note_form.html', note=note, form=note)

    return render_template('notes/edit.html', note=note, form=note)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@notes_bp.route('/<int:note_id>/delete', methods=['POST'])
@login_required
def delete(note_id):
    """Permanently delete a note."""
    note = _note_or_404(note_id)
    db.session.delete(note)
    db.session.commit()

    flash('Note deleted.', 'info')

    if _is_htmx():
        response = make_response('')
        response.headers['HX-Trigger'] = 'noteDeleted'
        return response

    return redirect(request.referrer or url_for('notes.index'))


# ---------------------------------------------------------------------------
# Pin toggle
# ---------------------------------------------------------------------------

@notes_bp.route('/<int:note_id>/pin', methods=['POST'])
@login_required
def pin(note_id):
    """Toggle the pinned flag on a note."""
    note = _note_or_404(note_id)
    note.pinned = not note.pinned
    db.session.commit()

    if _is_htmx():
        response = make_response(render_template('partials/note_item.html', note=note))
        response.headers['HX-Trigger'] = 'notePinChanged'
        return response

    return redirect(request.referrer or url_for('notes.index'))


# ---------------------------------------------------------------------------
# Scratchpad
# ---------------------------------------------------------------------------

@notes_bp.route('/scratchpad', methods=['GET'])
@login_required
def scratchpad():
    """
    Show the scratchpad note.

    The scratchpad is a special note titled 'Scratchpad'. It is created
    automatically on first visit so the user always has a quick-capture area.
    """
    note = Note.query.filter_by(
        user_id=current_user.id,
        title=SCRATCHPAD_TITLE,
    ).first()

    if note is None:
        note = Note(
            user_id=current_user.id,
            title=SCRATCHPAD_TITLE,
            body='',
            tag=None,
            pinned=False,
        )
        db.session.add(note)
        db.session.commit()

    if _is_htmx():
        return render_template('partials/scratchpad.html', note=note)

    return render_template('notes/scratchpad.html', note=note)


@notes_bp.route('/scratchpad', methods=['POST'])
@login_required
def save_scratchpad():
    """Save (auto-save) the scratchpad content."""
    note = Note.query.filter_by(
        user_id=current_user.id,
        title=SCRATCHPAD_TITLE,
    ).first()

    if note is None:
        note = Note(
            user_id=current_user.id,
            title=SCRATCHPAD_TITLE,
            body='',
            tag=None,
            pinned=False,
        )
        db.session.add(note)

    note.body = request.form.get('body', '')
    db.session.commit()

    if _is_htmx():
        response = make_response(render_template('partials/scratchpad_saved.html', note=note))
        response.headers['HX-Trigger'] = 'scratchpadSaved'
        return response

    flash('Scratchpad saved.', 'success')
    return redirect(url_for('notes.scratchpad'))

"""
tests/test_notes.py
~~~~~~~~~~~~~~~~~~~

Tests for HelmHub notes routes:
  - Listing notes
  - Creating notes (valid and invalid)
  - Deleting notes
  - Pinning / unpinning notes
  - Scratchpad special note
"""

import pytest

from app.models import Note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_note(db, user, title='Test Note', body='', pinned=False, tag=''):
    """Persist a Note directly via the ORM and return it.

    Uses the actual Note model column names (``body``, ``tag``).
    """
    note = Note(
        user_id=user.id,
        title=title,
        body=body,
        pinned=pinned,
        tag=tag,
    )
    db.session.add(note)
    db.session.commit()
    return note


def _post_new_note(client, title='My Note', content='', tags='', pinned=False):
    """POST to /notes/new with the form fields the route expects."""
    data = {
        'title': title,
        'content': content,
        'tags': tags,
    }
    if pinned:
        data['pinned'] = 'on'
    return client.post('/notes/new', data=data, follow_redirects=False)


# ---------------------------------------------------------------------------
# Note list
# ---------------------------------------------------------------------------

class TestNoteIndex:
    def test_notes_page_requires_auth(self, client):
        """Unauthenticated GET /notes/ redirects to login."""
        response = client.get('/notes/', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_notes_page_returns_200(self, auth_client):
        """Authenticated GET /notes/ returns 200."""
        response = auth_client.get('/notes/')
        assert response.status_code == 200

    def test_notes_page_shows_existing_note(self, auth_client, db, test_user):
        """A persisted note title appears in the rendered note list."""
        _create_note(db, test_user, title='Shopping list')
        response = auth_client.get('/notes/')
        assert b'Shopping list' in response.data

    def test_notes_pinned_filter(self, auth_client, db, test_user):
        """?pinned=true shows only pinned notes."""
        _create_note(db, test_user, title='Pinned note', pinned=True)
        _create_note(db, test_user, title='Regular note', pinned=False)
        response = auth_client.get('/notes/?pinned=true')
        assert b'Pinned note' in response.data
        assert b'Regular note' not in response.data

    def test_notes_search_filter(self, auth_client, db, test_user):
        """?search= filters notes by title or content substring."""
        _create_note(db, test_user, title='Alpha note')
        _create_note(db, test_user, title='Beta note')
        response = auth_client.get('/notes/?search=Alpha')
        assert b'Alpha note' in response.data
        assert b'Beta note' not in response.data

    def test_notes_only_shows_own_notes(self, auth_client, db, test_user):
        """Notes belonging to other users are not visible."""
        from app.models import User
        other = User(username='notestranger')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        _create_note(db, other, title='Private note of other user')
        response = auth_client.get('/notes/')
        assert b'Private note of other user' not in response.data


# ---------------------------------------------------------------------------
# Create note
# ---------------------------------------------------------------------------

class TestNoteCreate:
    def test_get_new_note_page_returns_200(self, auth_client):
        """GET /notes/new renders the create-note form."""
        response = auth_client.get('/notes/new')
        assert response.status_code == 200

    def test_post_new_note_creates_note(self, auth_client, db, test_user):
        """POST /notes/new with a title creates a new note in the database."""
        response = _post_new_note(auth_client, title='Meeting notes')
        assert response.status_code in (301, 302)
        note = Note.query.filter_by(user_id=test_user.id, title='Meeting notes').first()
        assert note is not None

    def test_post_new_note_redirects_to_notes_list(self, auth_client, db, test_user):
        """After creation the user is redirected to the notes index."""
        response = _post_new_note(auth_client, title='Redirect note')
        location = response.headers.get('Location', '')
        assert '/notes' in location

    def test_post_new_note_with_content(self, auth_client, db, test_user):
        """Content submitted in the form is persisted to the note."""
        _post_new_note(auth_client, title='Content note', content='Hello world')
        note = Note.query.filter_by(user_id=test_user.id, title='Content note').first()
        assert note is not None
        # The route stores the value in either 'body' or 'content' depending on
        # the model column name; check whichever is present.
        stored = getattr(note, 'content', None) or getattr(note, 'body', None)
        assert stored == 'Hello world'

    def test_post_new_note_pinned_flag(self, auth_client, db, test_user):
        """A note submitted with pinned=on is stored as pinned."""
        _post_new_note(auth_client, title='Pinned from form', pinned=True)
        note = Note.query.filter_by(user_id=test_user.id, title='Pinned from form').first()
        assert note is not None
        assert note.pinned is True

    def test_post_new_note_missing_title_returns_422(self, auth_client):
        """Submitting without a title returns 422."""
        response = _post_new_note(auth_client, title='')
        assert response.status_code == 422

    def test_post_new_note_requires_auth(self, client):
        """Unauthenticated POST to /notes/new redirects to login."""
        response = _post_new_note(client, title='Should fail')
        assert response.status_code in (301, 302)


# ---------------------------------------------------------------------------
# Delete note
# ---------------------------------------------------------------------------

class TestNoteDelete:
    def test_delete_removes_note(self, auth_client, db, test_user):
        """POST /notes/<id>/delete removes the note from the database."""
        note = _create_note(db, test_user, title='Temporary note')
        note_id = note.id
        auth_client.post(f'/notes/{note_id}/delete')
        assert db.session.get(Note, note_id) is None

    def test_delete_redirects(self, auth_client, db, test_user):
        """Deleting a note issues a redirect."""
        note = _create_note(db, test_user, title='Delete redirect note')
        response = auth_client.post(
            f'/notes/{note.id}/delete', follow_redirects=False
        )
        assert response.status_code in (301, 302)

    def test_delete_nonexistent_note_returns_404(self, auth_client):
        """Attempting to delete a note that does not exist returns 404."""
        response = auth_client.post('/notes/999999/delete')
        assert response.status_code == 404

    def test_delete_another_users_note_returns_404(self, auth_client, db):
        """Users cannot delete another user's note."""
        from app.models import User
        other = User(username='notethief')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        note = _create_note(db, other, title='Private note')
        response = auth_client.post(f'/notes/{note.id}/delete')
        assert response.status_code == 404

    def test_delete_requires_auth(self, client, db, test_user):
        """Unauthenticated delete attempt redirects to login."""
        note = _create_note(db, test_user, title='Auth guarded note')
        response = client.post(
            f'/notes/{note.id}/delete', follow_redirects=False
        )
        assert response.status_code in (301, 302)


# ---------------------------------------------------------------------------
# Pin toggle
# ---------------------------------------------------------------------------

class TestNotePin:
    def test_pin_sets_pinned_flag(self, auth_client, db, test_user):
        """POST /notes/<id>/pin on an unpinned note pins it."""
        note = _create_note(db, test_user, title='Not pinned', pinned=False)
        auth_client.post(f'/notes/{note.id}/pin')
        db.session.refresh(note)
        assert note.pinned is True

    def test_pin_unsets_pinned_flag(self, auth_client, db, test_user):
        """POST /notes/<id>/pin on a pinned note unpins it."""
        note = _create_note(db, test_user, title='Already pinned', pinned=True)
        auth_client.post(f'/notes/{note.id}/pin')
        db.session.refresh(note)
        assert note.pinned is False

    def test_pin_redirects(self, auth_client, db, test_user):
        """Pin toggle issues a redirect for non-HTMX requests."""
        note = _create_note(db, test_user, title='Toggle pin')
        response = auth_client.post(
            f'/notes/{note.id}/pin', follow_redirects=False
        )
        assert response.status_code in (301, 302)

    def test_pin_nonexistent_note_returns_404(self, auth_client):
        """Pinning a note that does not exist returns 404."""
        response = auth_client.post('/notes/999999/pin')
        assert response.status_code == 404

    def test_pin_another_users_note_returns_404(self, auth_client, db):
        """Users cannot pin another user's note."""
        from app.models import User
        other = User(username='pinstranger')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        note = _create_note(db, other, title='Others note')
        response = auth_client.post(f'/notes/{note.id}/pin')
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Scratchpad
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Edit note
# ---------------------------------------------------------------------------

def _post_edit_note(client, note_id, **kwargs):
    """POST to /notes/<id>/edit with optional form field overrides."""
    data = {
        'title': kwargs.get('title', 'Updated Note'),
        'body': kwargs.get('body', ''),
        'tag': kwargs.get('tag', ''),
    }
    if kwargs.get('pinned'):
        data['pinned'] = 'on'
    headers = kwargs.get('headers', {})
    return client.post(f'/notes/{note_id}/edit', data=data, headers=headers, follow_redirects=False)


class TestNoteEdit:
    def test_get_edit_note_page_returns_200(self, auth_client, db, test_user):
        """GET /notes/<id>/edit renders the edit form."""
        note = _create_note(db, test_user, title='Editable note')
        response = auth_client.get(f'/notes/{note.id}/edit')
        assert response.status_code == 200

    def test_edit_updates_title(self, auth_client, db, test_user):
        """POST /notes/<id>/edit persists the new title."""
        note = _create_note(db, test_user, title='Old title')
        _post_edit_note(auth_client, note.id, title='New title')
        db.session.refresh(note)
        assert note.title == 'New title'

    def test_edit_updates_body(self, auth_client, db, test_user):
        """POST /notes/<id>/edit persists the new body content."""
        note = _create_note(db, test_user, title='Body note')
        _post_edit_note(auth_client, note.id, title='Body note', body='Updated body text')
        db.session.refresh(note)
        assert note.body == 'Updated body text'

    def test_edit_updates_tag(self, auth_client, db, test_user):
        """POST /notes/<id>/edit persists the new tag."""
        note = _create_note(db, test_user, title='Tagged note', tag='oldtag')
        _post_edit_note(auth_client, note.id, title='Tagged note', tag='newtag')
        db.session.refresh(note)
        assert note.tag == 'newtag'

    def test_edit_clears_tag_when_empty(self, auth_client, db, test_user):
        """POST /notes/<id>/edit with empty tag stores None."""
        note = _create_note(db, test_user, title='Tag clear note', tag='removeme')
        _post_edit_note(auth_client, note.id, title='Tag clear note', tag='')
        db.session.refresh(note)
        assert note.tag is None

    def test_edit_sets_pinned(self, auth_client, db, test_user):
        """POST /notes/<id>/edit with pinned=on pins the note."""
        note = _create_note(db, test_user, title='Pin note', pinned=False)
        _post_edit_note(auth_client, note.id, title='Pin note', pinned=True)
        db.session.refresh(note)
        assert note.pinned is True

    def test_edit_unsets_pinned(self, auth_client, db, test_user):
        """POST /notes/<id>/edit without pinned field unpins the note."""
        note = _create_note(db, test_user, title='Unpin note', pinned=True)
        _post_edit_note(auth_client, note.id, title='Unpin note', pinned=False)
        db.session.refresh(note)
        assert note.pinned is False

    def test_edit_redirects_on_success(self, auth_client, db, test_user):
        """Successful edit redirects to the notes index."""
        note = _create_note(db, test_user, title='Redirect note')
        response = _post_edit_note(auth_client, note.id, title='Redirect note updated')
        assert response.status_code in (301, 302)
        assert '/notes' in response.headers.get('Location', '')

    def test_edit_missing_title_returns_422(self, auth_client, db, test_user):
        """POST /notes/<id>/edit without a title returns 422."""
        note = _create_note(db, test_user, title='Title required note')
        response = _post_edit_note(auth_client, note.id, title='')
        assert response.status_code == 422

    def test_edit_htmx_returns_trigger_header(self, auth_client, db, test_user):
        """HTMX edit request returns HX-Trigger: noteUpdated."""
        note = _create_note(db, test_user, title='HTMX edit note')
        response = _post_edit_note(
            auth_client, note.id,
            title='HTMX edited',
            headers={'HX-Request': 'true'},
        )
        assert response.headers.get('HX-Trigger') == 'noteUpdated'

    def test_edit_nonexistent_note_returns_404(self, auth_client):
        """Editing a note that does not exist returns 404."""
        response = _post_edit_note(auth_client, 999999, title='Ghost note')
        assert response.status_code == 404

    def test_edit_another_users_note_returns_404(self, auth_client, db):
        """Users cannot edit another user's note."""
        from app.models import User
        other = User(username='noteeditor')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        note = _create_note(db, other, title='Others edit note')
        response = _post_edit_note(auth_client, note.id, title='Hijacked')
        assert response.status_code == 404

    def test_edit_requires_auth(self, client, db, test_user):
        """Unauthenticated edit attempt redirects to login."""
        note = _create_note(db, test_user, title='Auth edit note')
        response = client.post(f'/notes/{note.id}/edit', data={'title': 'x', 'body': ''}, follow_redirects=False)
        assert response.status_code in (301, 302)


class TestScratchpad:
    def test_scratchpad_requires_auth(self, client):
        """Unauthenticated GET /notes/scratchpad redirects to login."""
        response = client.get('/notes/scratchpad', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_scratchpad_renders(self, auth_client):
        """Authenticated GET /notes/scratchpad returns 200."""
        response = auth_client.get('/notes/scratchpad')
        assert response.status_code == 200

    def test_scratchpad_auto_creates_note(self, auth_client, db, test_user):
        """Visiting the scratchpad creates a 'Scratchpad' note if none exists."""
        auth_client.get('/notes/scratchpad')
        note = Note.query.filter_by(
            user_id=test_user.id, title='Scratchpad'
        ).first()
        assert note is not None

    def test_scratchpad_idempotent(self, auth_client, db, test_user):
        """Visiting the scratchpad twice does not create duplicate notes."""
        auth_client.get('/notes/scratchpad')
        auth_client.get('/notes/scratchpad')
        count = Note.query.filter_by(
            user_id=test_user.id, title='Scratchpad'
        ).count()
        assert count == 1

    def test_scratchpad_save(self, auth_client, db, test_user):
        """POST /notes/scratchpad saves the content to the scratchpad note."""
        auth_client.get('/notes/scratchpad')  # ensure the note exists
        auth_client.post(
            '/notes/scratchpad',
            data={'content': 'Quick idea'},
            follow_redirects=True,
        )
        note = Note.query.filter_by(
            user_id=test_user.id, title='Scratchpad'
        ).first()
        assert note is not None
        stored = getattr(note, 'content', None) or getattr(note, 'body', None)
        assert stored == 'Quick idea'

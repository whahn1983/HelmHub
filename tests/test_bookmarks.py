"""
tests/test_bookmarks.py
~~~~~~~~~~~~~~~~~~~~~~~

Tests for HelmHub bookmark management routes and model:
  - Bookmark model computed properties (display_url, domain)
  - Listing bookmarks (all, filtered by category, search, pinned-only)
  - Creating bookmarks (valid and invalid data, URL normalisation)
  - Editing bookmarks (full-page and HTMX partial responses)
  - Deleting bookmarks (full-page and HTMX partial responses)
  - Toggling the pinned flag (full-page and HTMX partial responses)
"""

import pytest
from io import BytesIO
from urllib.error import HTTPError, URLError

from app.models import Bookmark
from app.routes.bookmarks import _probe_direct_favicon


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_bookmark(db, user, title='Test Bookmark',
                     url='https://example.com', description=None,
                     category=None, pinned=False):
    """Persist a Bookmark directly via the ORM and return it."""
    bookmark = Bookmark(
        user_id=user.id,
        title=title,
        url=url,
        description=description,
        category=category,
        pinned=pinned,
    )
    db.session.add(bookmark)
    db.session.commit()
    return bookmark


def _post_new_bookmark(client, **kwargs):
    """POST to /bookmarks/new with optional form field overrides."""
    data = {
        'title': kwargs.get('title', 'My Bookmark'),
        'url': kwargs.get('url', 'https://example.com'),
        'description': kwargs.get('description', ''),
        'category': kwargs.get('category', ''),
    }
    if kwargs.get('pinned'):
        data['pinned'] = 'on'
    return client.post('/bookmarks/new', data=data, follow_redirects=False)


def _post_edit_bookmark(client, bookmark_id, **kwargs):
    """POST to /bookmarks/<id>/edit with optional form field overrides."""
    data = {
        'title': kwargs.get('title', 'Updated Bookmark'),
        'url': kwargs.get('url', 'https://updated.com'),
        'description': kwargs.get('description', ''),
        'category': kwargs.get('category', ''),
    }
    if kwargs.get('pinned'):
        data['pinned'] = 'on'
    headers = kwargs.get('headers', {})
    return client.post(
        f'/bookmarks/{bookmark_id}/edit',
        data=data,
        headers=headers,
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# Model unit tests
# ---------------------------------------------------------------------------

class TestBookmarkModel:
    def test_display_url_strips_https(self):
        """display_url removes the https:// prefix."""
        b = Bookmark(title='T', url='https://example.com/path')
        assert b.display_url == 'example.com/path'

    def test_display_url_strips_http(self):
        """display_url removes the http:// prefix."""
        b = Bookmark(title='T', url='http://example.com')
        assert b.display_url == 'example.com'

    def test_display_url_strips_trailing_slash(self):
        """display_url also strips a trailing slash left after prefix removal."""
        b = Bookmark(title='T', url='https://example.com/')
        assert b.display_url == 'example.com'

    def test_display_url_no_scheme(self):
        """display_url returns the URL unchanged when there is no http/https prefix."""
        b = Bookmark(title='T', url='example.com/path')
        assert b.display_url == 'example.com/path'

    def test_domain_extracts_domain(self):
        """domain returns only the hostname portion of the URL."""
        b = Bookmark(title='T', url='https://example.com/some/path?q=1')
        assert b.domain == 'example.com'

    def test_domain_strips_www(self):
        """domain strips the www. prefix."""
        b = Bookmark(title='T', url='https://www.example.com/page')
        assert b.domain == 'example.com'

    def test_domain_bare_hostname(self):
        """domain works for URLs that are just a hostname with no path."""
        b = Bookmark(title='T', url='https://docs.python.org')
        assert b.domain == 'docs.python.org'

    def test_repr(self):
        """__repr__ includes id, title, category, and pinned."""
        b = Bookmark(id=1, title='My Link', category='tech', pinned=False)
        r = repr(b)
        assert 'My Link' in r
        assert 'tech' in r
        assert 'pinned=False' in r


class TestBookmarkFaviconHelpers:
    def test_probe_direct_favicon_returns_false_on_http_error(self, monkeypatch):
        """HTTP errors should treat direct favicon discovery as unavailable."""
        def _raise_http_error(*_args, **_kwargs):
            raise HTTPError(
                url='https://example.com/favicon.ico',
                code=404,
                msg='not found',
                hdrs=None,
                fp=None,
            )

        monkeypatch.setattr('urllib.request.urlopen', _raise_http_error)
        assert _probe_direct_favicon('example.com') is False

    def test_probe_direct_favicon_returns_true_on_network_error(self, monkeypatch):
        """Network/proxy failures should still prefer direct browser fetches."""
        def _raise_network_error(*_args, **_kwargs):
            raise URLError('proxy blocked')

        monkeypatch.setattr('urllib.request.urlopen', _raise_network_error)
        assert _probe_direct_favicon('example.com') is True


class TestBookmarkFaviconProxy:
    def test_favicon_proxy_returns_image_without_redirect(self, auth_client, monkeypatch):
        """The endpoint should serve bytes directly so CSP/connect-src is not required."""
        monkeypatch.setattr('app.routes.bookmarks._probe_direct_favicon', lambda _domain: True)
        monkeypatch.setattr(
            'app.routes.bookmarks._download_favicon',
            lambda _url: (b'ico-bytes', 'image/x-icon'),
        )

        response = auth_client.get('/bookmarks/favicon?domain=example.com')
        assert response.status_code == 200
        assert response.location is None
        assert response.data == b'ico-bytes'
        assert response.headers.get('Content-Type') == 'image/x-icon'

    def test_favicon_proxy_rejects_private_hosts(self, auth_client):
        """The endpoint should block private/localhost domains."""
        response = auth_client.get('/bookmarks/favicon?domain=127.0.0.1')
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Bookmark list
# ---------------------------------------------------------------------------

class TestBookmarkIndex:
    def test_bookmarks_page_requires_auth(self, client):
        """Unauthenticated access to /bookmarks/ redirects to login."""
        response = client.get('/bookmarks/', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_bookmarks_page_returns_200(self, auth_client):
        """Authenticated GET /bookmarks/ returns 200."""
        response = auth_client.get('/bookmarks/')
        assert response.status_code == 200

    def test_bookmarks_page_shows_existing_bookmark(self, auth_client, db, test_user):
        """A persisted bookmark title appears in the rendered list."""
        _create_bookmark(db, test_user, title='My Favourite Site')
        response = auth_client.get('/bookmarks/')
        assert b'My Favourite Site' in response.data

    def test_bookmarks_filter_by_category(self, auth_client, db, test_user):
        """?category= filters bookmarks to only those in that category."""
        _create_bookmark(db, test_user, title='Tech Blog', category='tech')
        _create_bookmark(db, test_user, title='Recipe Site', category='food')
        response = auth_client.get('/bookmarks/?category=tech')
        assert b'Tech Blog' in response.data
        assert b'Recipe Site' not in response.data

    def test_bookmarks_search_by_title(self, auth_client, db, test_user):
        """?search= filters bookmarks by title substring."""
        _create_bookmark(db, test_user, title='Python Docs')
        _create_bookmark(db, test_user, title='Flask Tutorial')
        response = auth_client.get('/bookmarks/?search=Python')
        assert b'Python Docs' in response.data
        assert b'Flask Tutorial' not in response.data

    def test_bookmarks_search_by_url(self, auth_client, db, test_user):
        """?search= matches against the URL field."""
        _create_bookmark(db, test_user, title='Bookmark A',
                         url='https://docs.python.org')
        _create_bookmark(db, test_user, title='Bookmark B',
                         url='https://flask.palletsprojects.com')
        response = auth_client.get('/bookmarks/?search=docs.python')
        assert b'Bookmark A' in response.data
        assert b'Bookmark B' not in response.data

    def test_bookmarks_search_by_description(self, auth_client, db, test_user):
        """?search= matches against the description field."""
        _create_bookmark(db, test_user, title='Site X',
                         description='Official language reference')
        _create_bookmark(db, test_user, title='Site Y',
                         description='Micro web framework')
        response = auth_client.get('/bookmarks/?search=Official+language')
        assert b'Site X' in response.data
        assert b'Site Y' not in response.data

    def test_bookmarks_pinned_only_filter(self, auth_client, db, test_user):
        """?pinned=true shows only pinned bookmarks."""
        _create_bookmark(db, test_user, title='Pinned Site', pinned=True)
        _create_bookmark(db, test_user, title='Normal Site', pinned=False)
        response = auth_client.get('/bookmarks/?pinned=true')
        assert b'Pinned Site' in response.data
        assert b'Normal Site' not in response.data

    def test_bookmarks_only_shows_own_bookmarks(self, auth_client, db, test_user):
        """Bookmarks belonging to another user are not visible."""
        from app.models import User
        other = User(username='otherone')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        _create_bookmark(db, other, title='Others Secret Bookmark')
        response = auth_client.get('/bookmarks/')
        assert b'Others Secret Bookmark' not in response.data

    def test_bookmarks_empty_list_returns_200(self, auth_client):
        """The index page renders successfully when there are no bookmarks."""
        response = auth_client.get('/bookmarks/')
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Create bookmark
# ---------------------------------------------------------------------------

class TestBookmarkCreate:
    def test_get_new_bookmark_page_returns_200(self, auth_client):
        """GET /bookmarks/new renders the create-bookmark form."""
        response = auth_client.get('/bookmarks/new')
        assert response.status_code == 200

    def test_get_new_bookmark_requires_auth(self, client):
        """Unauthenticated GET /bookmarks/new redirects to login."""
        response = client.get('/bookmarks/new', follow_redirects=False)
        assert response.status_code in (301, 302)

    def test_post_new_bookmark_creates_bookmark(self, auth_client, db, test_user):
        """POST /bookmarks/new with valid data persists a new Bookmark."""
        _post_new_bookmark(auth_client, title='New Site',
                           url='https://newsite.com')
        bookmark = Bookmark.query.filter_by(
            user_id=test_user.id, title='New Site'
        ).first()
        assert bookmark is not None
        assert bookmark.url == 'https://newsite.com'

    def test_post_new_bookmark_redirects_to_index(self, auth_client, db, test_user):
        """After creation the user is redirected to the bookmark index."""
        response = _post_new_bookmark(auth_client, title='Redirect Test')
        location = response.headers.get('Location', '')
        assert response.status_code in (301, 302)
        assert '/bookmarks' in location

    def test_post_new_bookmark_requires_auth(self, client):
        """Unauthenticated POST to /bookmarks/new redirects to login."""
        response = _post_new_bookmark(client)
        assert response.status_code in (301, 302)

    def test_post_new_bookmark_missing_title_returns_422(self, auth_client):
        """Submitting without a title returns 422 Unprocessable Entity."""
        response = _post_new_bookmark(auth_client, title='')
        assert response.status_code == 422

    def test_post_new_bookmark_missing_url_returns_422(self, auth_client):
        """Submitting without a URL returns 422 Unprocessable Entity."""
        response = _post_new_bookmark(auth_client, url='')
        assert response.status_code == 422

    def test_post_new_bookmark_normalises_url_without_scheme(
        self, auth_client, db, test_user
    ):
        """A URL submitted without a scheme gets https:// prepended."""
        _post_new_bookmark(auth_client, title='No Scheme', url='example.com')
        bookmark = Bookmark.query.filter_by(
            user_id=test_user.id, title='No Scheme'
        ).first()
        assert bookmark is not None
        assert bookmark.url == 'https://example.com'

    def test_post_new_bookmark_preserves_http_scheme(
        self, auth_client, db, test_user
    ):
        """A URL that already starts with http:// is stored unchanged."""
        _post_new_bookmark(auth_client, title='HTTP Site',
                           url='http://insecure.example.com')
        bookmark = Bookmark.query.filter_by(
            user_id=test_user.id, title='HTTP Site'
        ).first()
        assert bookmark is not None
        assert bookmark.url == 'http://insecure.example.com'

    def test_post_new_bookmark_stores_category(self, auth_client, db, test_user):
        """The category field is stored (lowercased) on creation."""
        _post_new_bookmark(auth_client, title='Cat Test', category='Tools')
        bookmark = Bookmark.query.filter_by(
            user_id=test_user.id, title='Cat Test'
        ).first()
        assert bookmark is not None
        assert bookmark.category == 'tools'

    def test_post_new_bookmark_stores_pinned(self, auth_client, db, test_user):
        """A bookmark created with pinned=on is persisted as pinned=True."""
        _post_new_bookmark(auth_client, title='Pinned On Create', pinned=True)
        bookmark = Bookmark.query.filter_by(
            user_id=test_user.id, title='Pinned On Create'
        ).first()
        assert bookmark is not None
        assert bookmark.pinned is True

    def test_post_new_bookmark_stores_description(self, auth_client, db, test_user):
        """The optional description is persisted correctly."""
        _post_new_bookmark(auth_client, title='With Desc',
                           description='A useful description')
        bookmark = Bookmark.query.filter_by(
            user_id=test_user.id, title='With Desc'
        ).first()
        assert bookmark is not None
        assert bookmark.description == 'A useful description'

    def test_post_new_bookmark_empty_category_stored_as_none(
        self, auth_client, db, test_user
    ):
        """An empty category string is stored as NULL."""
        _post_new_bookmark(auth_client, title='No Cat', category='')
        bookmark = Bookmark.query.filter_by(
            user_id=test_user.id, title='No Cat'
        ).first()
        assert bookmark is not None
        assert bookmark.category is None


# ---------------------------------------------------------------------------
# Edit bookmark
# ---------------------------------------------------------------------------

class TestBookmarkEdit:
    def test_get_edit_page_returns_200(self, auth_client, db, test_user):
        """GET /bookmarks/<id>/edit renders the edit form."""
        bookmark = _create_bookmark(db, test_user)
        response = auth_client.get(f'/bookmarks/{bookmark.id}/edit')
        assert response.status_code == 200

    def test_get_edit_page_requires_auth(self, client, db, test_user):
        """Unauthenticated GET of the edit page redirects to login."""
        bookmark = _create_bookmark(db, test_user)
        response = client.get(
            f'/bookmarks/{bookmark.id}/edit', follow_redirects=False
        )
        assert response.status_code in (301, 302)

    def test_get_edit_nonexistent_bookmark_returns_404(self, auth_client):
        """GET for a bookmark that does not exist returns 404."""
        response = auth_client.get('/bookmarks/999999/edit')
        assert response.status_code == 404

    def test_get_edit_other_users_bookmark_returns_404(self, auth_client, db):
        """Users cannot view the edit page for another user's bookmark."""
        from app.models import User
        other = User(username='editother')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        bookmark = _create_bookmark(db, other, title='Others Bookmark')
        response = auth_client.get(f'/bookmarks/{bookmark.id}/edit')
        assert response.status_code == 404

    def test_post_edit_updates_bookmark(self, auth_client, db, test_user):
        """POST /bookmarks/<id>/edit persists the updated fields."""
        bookmark = _create_bookmark(db, test_user, title='Old Title',
                                    url='https://old.com')
        _post_edit_bookmark(auth_client, bookmark.id, title='New Title',
                            url='https://new.com')
        db.session.refresh(bookmark)
        assert bookmark.title == 'New Title'
        assert bookmark.url == 'https://new.com'

    def test_post_edit_missing_title_returns_422(self, auth_client, db, test_user):
        """Submitting an edit with no title returns 422."""
        bookmark = _create_bookmark(db, test_user)
        response = _post_edit_bookmark(auth_client, bookmark.id, title='')
        assert response.status_code == 422

    def test_post_edit_missing_url_returns_422(self, auth_client, db, test_user):
        """Submitting an edit with no URL returns 422."""
        bookmark = _create_bookmark(db, test_user)
        response = _post_edit_bookmark(auth_client, bookmark.id, url='')
        assert response.status_code == 422

    def test_post_edit_redirects_to_index(self, auth_client, db, test_user):
        """A successful edit redirects to the bookmark index."""
        bookmark = _create_bookmark(db, test_user)
        response = _post_edit_bookmark(auth_client, bookmark.id)
        assert response.status_code in (301, 302)
        assert '/bookmarks' in response.headers.get('Location', '')

    def test_post_edit_nonexistent_bookmark_returns_404(self, auth_client):
        """Editing a bookmark that does not exist returns 404."""
        response = _post_edit_bookmark(auth_client, 999999)
        assert response.status_code == 404

    def test_post_edit_other_users_bookmark_returns_404(self, auth_client, db):
        """Users cannot edit another user's bookmark."""
        from app.models import User
        other = User(username='editother2')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        bookmark = _create_bookmark(db, other)
        response = _post_edit_bookmark(auth_client, bookmark.id)
        assert response.status_code == 404

    def test_post_edit_htmx_returns_partial(self, auth_client, db, test_user):
        """An HTMX edit request returns a partial response (not a redirect)."""
        bookmark = _create_bookmark(db, test_user, title='HTMX Target')
        response = _post_edit_bookmark(
            auth_client, bookmark.id,
            title='HTMX Updated',
            url='https://htmx-updated.com',
            headers={'HX-Request': 'true'},
        )
        assert response.status_code == 200

    def test_post_edit_htmx_sets_trigger_header(self, auth_client, db, test_user):
        """An HTMX edit response includes the HX-Trigger: bookmarkUpdated header."""
        bookmark = _create_bookmark(db, test_user)
        response = _post_edit_bookmark(
            auth_client, bookmark.id,
            headers={'HX-Request': 'true'},
        )
        assert response.headers.get('HX-Trigger') == 'bookmarkUpdated'

    def test_post_edit_updates_category(self, auth_client, db, test_user):
        """Editing a bookmark updates its category (stored lowercased)."""
        bookmark = _create_bookmark(db, test_user, category='old')
        _post_edit_bookmark(auth_client, bookmark.id, category='NewCat')
        db.session.refresh(bookmark)
        assert bookmark.category == 'newcat'

    def test_post_edit_clears_category_to_none(self, auth_client, db, test_user):
        """Submitting an empty category stores NULL."""
        bookmark = _create_bookmark(db, test_user, category='existing')
        _post_edit_bookmark(auth_client, bookmark.id, category='')
        db.session.refresh(bookmark)
        assert bookmark.category is None


# ---------------------------------------------------------------------------
# Delete bookmark
# ---------------------------------------------------------------------------

class TestBookmarkDelete:
    def test_delete_removes_bookmark(self, auth_client, db, test_user):
        """POST /bookmarks/<id>/delete removes the bookmark from the database."""
        bookmark = _create_bookmark(db, test_user, title='To Be Deleted')
        bookmark_id = bookmark.id
        auth_client.post(f'/bookmarks/{bookmark_id}/delete')
        assert db.session.get(Bookmark, bookmark_id) is None

    def test_delete_redirects(self, auth_client, db, test_user):
        """Deleting a bookmark redirects (non-HTMX request)."""
        bookmark = _create_bookmark(db, test_user)
        response = auth_client.post(
            f'/bookmarks/{bookmark.id}/delete', follow_redirects=False
        )
        assert response.status_code in (301, 302)

    def test_delete_requires_auth(self, client, db, test_user):
        """Unauthenticated delete attempt redirects to login."""
        bookmark = _create_bookmark(db, test_user)
        response = client.post(
            f'/bookmarks/{bookmark.id}/delete', follow_redirects=False
        )
        assert response.status_code in (301, 302)

    def test_delete_nonexistent_bookmark_returns_404(self, auth_client):
        """Attempting to delete a bookmark that does not exist returns 404."""
        response = auth_client.post('/bookmarks/999999/delete')
        assert response.status_code == 404

    def test_delete_other_users_bookmark_returns_404(self, auth_client, db):
        """Users cannot delete another user's bookmark."""
        from app.models import User
        other = User(username='delother')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        bookmark = _create_bookmark(db, other)
        response = auth_client.post(f'/bookmarks/{bookmark.id}/delete')
        assert response.status_code == 404

    def test_delete_htmx_returns_empty_body(self, auth_client, db, test_user):
        """An HTMX delete returns an empty response body."""
        bookmark = _create_bookmark(db, test_user)
        response = auth_client.post(
            f'/bookmarks/{bookmark.id}/delete',
            headers={'HX-Request': 'true'},
            follow_redirects=False,
        )
        assert response.data == b''

    def test_delete_htmx_sets_trigger_header(self, auth_client, db, test_user):
        """An HTMX delete response includes the HX-Trigger: bookmarkDeleted header."""
        bookmark = _create_bookmark(db, test_user)
        response = auth_client.post(
            f'/bookmarks/{bookmark.id}/delete',
            headers={'HX-Request': 'true'},
            follow_redirects=False,
        )
        assert response.headers.get('HX-Trigger') == 'bookmarkDeleted'


# ---------------------------------------------------------------------------
# Pin toggle
# ---------------------------------------------------------------------------

class TestBookmarkPin:
    def test_pin_toggles_from_false_to_true(self, auth_client, db, test_user):
        """POST /bookmarks/<id>/pin pins an unpinned bookmark."""
        bookmark = _create_bookmark(db, test_user, pinned=False)
        auth_client.post(f'/bookmarks/{bookmark.id}/pin')
        db.session.refresh(bookmark)
        assert bookmark.pinned is True

    def test_pin_toggles_from_true_to_false(self, auth_client, db, test_user):
        """POST /bookmarks/<id>/pin unpins a pinned bookmark."""
        bookmark = _create_bookmark(db, test_user, pinned=True)
        auth_client.post(f'/bookmarks/{bookmark.id}/pin')
        db.session.refresh(bookmark)
        assert bookmark.pinned is False

    def test_pin_redirects_non_htmx(self, auth_client, db, test_user):
        """A non-HTMX pin toggle redirects."""
        bookmark = _create_bookmark(db, test_user)
        response = auth_client.post(
            f'/bookmarks/{bookmark.id}/pin', follow_redirects=False
        )
        assert response.status_code in (301, 302)

    def test_pin_nonexistent_bookmark_returns_404(self, auth_client):
        """Toggling pin on a bookmark that does not exist returns 404."""
        response = auth_client.post('/bookmarks/999999/pin')
        assert response.status_code == 404

    def test_pin_other_users_bookmark_returns_404(self, auth_client, db):
        """Users cannot pin another user's bookmark."""
        from app.models import User
        other = User(username='pinother')
        other.set_password('pass')
        db.session.add(other)
        db.session.commit()
        bookmark = _create_bookmark(db, other)
        response = auth_client.post(f'/bookmarks/{bookmark.id}/pin')
        assert response.status_code == 404

    def test_pin_htmx_returns_partial(self, auth_client, db, test_user):
        """An HTMX pin toggle returns a 200 partial response."""
        bookmark = _create_bookmark(db, test_user)
        response = auth_client.post(
            f'/bookmarks/{bookmark.id}/pin',
            headers={'HX-Request': 'true'},
            follow_redirects=False,
        )
        assert response.status_code == 200

    def test_pin_htmx_sets_trigger_header(self, auth_client, db, test_user):
        """An HTMX pin response includes the HX-Trigger: bookmarkPinChanged header."""
        bookmark = _create_bookmark(db, test_user)
        response = auth_client.post(
            f'/bookmarks/{bookmark.id}/pin',
            headers={'HX-Request': 'true'},
            follow_redirects=False,
        )
        assert response.headers.get('HX-Trigger') == 'bookmarkPinChanged'

    def test_pin_requires_auth(self, client, db, test_user):
        """Unauthenticated pin attempt redirects to login."""
        bookmark = _create_bookmark(db, test_user)
        response = client.post(
            f'/bookmarks/{bookmark.id}/pin', follow_redirects=False
        )
        assert response.status_code in (301, 302)


# ---------------------------------------------------------------------------
# Import / Export
# ---------------------------------------------------------------------------

class TestBookmarkImportExport:
    def test_export_returns_netscape_html(self, auth_client, db, test_user):
        """GET /bookmarks/export returns an attachment in Netscape HTML format."""
        _create_bookmark(db, test_user, title='Docs', url='https://docs.python.org', category='dev')

        response = auth_client.get('/bookmarks/export')

        assert response.status_code == 200
        assert 'attachment; filename=helmhub-bookmarks.html' in response.headers.get('Content-Disposition', '')
        assert b'<!DOCTYPE NETSCAPE-Bookmark-file-1>' in response.data
        assert b'https://docs.python.org' in response.data

    def test_import_adds_and_updates_without_duplicates(self, auth_client, db, test_user):
        """Import upserts by URL and does not create duplicates."""
        _create_bookmark(db, test_user, title='Old Docs', url='https://docs.python.org', category='old')

        payload = b"""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><p>
  <DT><H3>Dev</H3>
  <DL><p>
    <DT><A HREF=\"https://docs.python.org\">Python Docs</A>
    <DT><A HREF=\"https://flask.palletsprojects.com\">Flask</A>
    <DT><A HREF=\"https://flask.palletsprojects.com\">Flask Duplicate</A>
  </DL><p>
</DL><p>
"""
        response = auth_client.post(
            '/bookmarks/import',
            data={'bookmark_file': (BytesIO(payload), 'bookmarks.html')},
            content_type='multipart/form-data',
            follow_redirects=False,
        )

        assert response.status_code in (301, 302)

        bookmarks = Bookmark.query.filter_by(user_id=test_user.id).all()
        by_url = {b.url: b for b in bookmarks}
        assert len(by_url) == 2
        assert by_url['https://docs.python.org'].title == 'Python Docs'
        assert by_url['https://docs.python.org'].category == 'dev'
        assert by_url['https://flask.palletsprojects.com'].title == 'Flask Duplicate'

    def test_import_skips_unsafe_schemes(self, auth_client, db, test_user):
        """Import ignores javascript/data schemes for safety."""
        payload = b"""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><p>
  <DT><A HREF=\"javascript:alert(1)\">Bad</A>
  <DT><A HREF=\"data:text/html;base64,abcd\">Bad2</A>
</DL><p>
"""
        auth_client.post(
            '/bookmarks/import',
            data={'bookmark_file': (BytesIO(payload), 'bookmarks.html')},
            content_type='multipart/form-data',
            follow_redirects=False,
        )

        bookmarks = Bookmark.query.filter_by(user_id=test_user.id).all()
        assert bookmarks == []

    def test_import_rejects_non_utf8(self, auth_client, db, test_user):
        """Import rejects non-UTF8 payloads."""
        response = auth_client.post(
            '/bookmarks/import',
            data={'bookmark_file': (BytesIO(b'\xff\xfe\x00'), 'bookmarks.html')},
            content_type='multipart/form-data',
            follow_redirects=False,
        )

        assert response.status_code in (301, 302)
        assert Bookmark.query.filter_by(user_id=test_user.id).count() == 0

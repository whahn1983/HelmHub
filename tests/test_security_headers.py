"""Security-header regression tests."""


def test_security_headers_present_on_login_page(client):
    response = client.get('/auth/login')

    assert response.status_code == 200
    assert response.headers.get('X-Frame-Options') in {'DENY', 'SAMEORIGIN'}
    assert response.headers.get('X-Content-Type-Options') == 'nosniff'
    assert response.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'
    csp = response.headers.get('Content-Security-Policy', '')
    assert 'default-src' in csp
    assert "connect-src 'self' https://api.open-meteo.com" in csp

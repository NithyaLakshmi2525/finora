import pytest
from unittest.mock import MagicMock
from db import get_db

def test_auth_registration_and_login(client):
    reg_user = "reg_pytest_user"
    reg_email = "reg_pytest@example.com"
    
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM users WHERE username=%s OR email=%s", (reg_user, reg_email))

    res = client.post('/register', data={
        'username': reg_user,
        'email': reg_email,
        'password': 'Password@123',
        'confirm_password': 'Password@123',
        'display_name': 'Registered User'
    }, follow_redirects=True)
    assert res.status_code == 200

    res = client.post('/login', data={
        'username': reg_user,
        'password': 'Password@123'
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b'Dashboard' in res.data or b'Expenses' in res.data or b'Finora' in res.data

def test_profile_and_settings_updates(auth_client, test_user):
    res = auth_client.get('/settings')
    assert res.status_code == 200

    res = auth_client.post('/profile/update-name', data={'display_name': 'Updated Pytest Name'}, follow_redirects=True)
    assert res.status_code == 200

    res = auth_client.post('/settings', data={
        'budget_alerts': 'on',
        'recurring_reminders': 'on',
        'goal_milestones': 'on'
    }, follow_redirects=True)
    assert res.status_code == 200

def test_404_error_page_rendering(client):
    res = client.get('/nonexistent-page-route')
    assert res.status_code == 404
    assert b'404' in res.data
    assert b'Page Not Found' in res.data

def test_google_oauth_flow(client, app, monkeypatch):
    mock_google = MagicMock()
    mock_google.authorize_redirect.return_value = app.response_class('Redirecting to Google', status=302, headers={'Location': 'https://accounts.google.com/o/oauth2/auth'})
    mock_google.authorize_access_token.return_value = {
        'userinfo': {
            'email': 'oauth_user@example.com',
            'name': 'Google OAuth User'
        }
    }

    mock_oauth = MagicMock()
    mock_oauth.create_client.return_value = mock_google

    monkeypatch.setattr(app, 'oauth', mock_oauth)

    # Clean test user if exists
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM users WHERE email='oauth_user@example.com'")

    # 1. Test /login/google redirect
    res = client.get('/login/google')
    assert res.status_code == 302
    assert 'accounts.google.com' in res.headers.get('Location', '')

    # 2. Test /authorize/google callback
    res = client.get('/authorize/google', follow_redirects=True)
    assert res.status_code == 200

    with client.session_transaction() as sess:
        assert sess.get('username') == 'oauth_user@example.com'
        assert sess.get('display_name') == 'Google OAuth User'

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM users WHERE email='oauth_user@example.com'")
        assert cursor.fetchone()[0] == 1

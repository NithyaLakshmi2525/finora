import pytest
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

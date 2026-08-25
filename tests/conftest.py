import pytest
import os
from app import create_app
from db import get_db

TEST_USERNAME = "pytest_suite_user"
TEST_EMAIL = "pytest_user@example.com"

@pytest.fixture(scope="session")
def app():
    """Provides Flask application configured for testing."""
    app_instance = create_app()
    app_instance.config['TESTING'] = True
    app_instance.config['WTF_CSRF_ENABLED'] = False
    return app_instance

@pytest.fixture(scope="session")
def client(app):
    """Provides Flask test client."""
    return app.test_client()

@pytest.fixture(scope="session")
def test_user():
    """Sets up a test user in database and yields user details."""
    with get_db() as (conn, cursor):
        cursor.execute("SELECT user_id FROM users WHERE username=%s OR email=%s", (TEST_USERNAME, TEST_EMAIL))
        existing = cursor.fetchall()
        for (uid,) in existing:
            _clean_user(cursor, uid)
            cursor.execute("DELETE FROM users WHERE user_id=%s", (uid,))

        cursor.execute(
            "INSERT INTO users (username, email, password, display_name) VALUES (%s, %s, 'test_hash', 'Pytest User')",
            (TEST_USERNAME, TEST_EMAIL)
        )
        user_id = cursor.lastrowid
        cursor.execute("INSERT IGNORE INTO notification_preferences (user_id) VALUES (%s)", (user_id,))
        cursor.execute(
            "INSERT INTO accounts (user_id, name, account_type, balance, currency) VALUES (%s, 'Main Checking', 'checking', 20000.00, 'INR')",
            (user_id,)
        )
        account_id = cursor.lastrowid

    user_info = {
        'user_id': user_id,
        'username': TEST_USERNAME,
        'email': TEST_EMAIL,
        'display_name': 'Pytest User',
        'account_id': account_id
    }

    yield user_info

    with get_db() as (conn, cursor):
        _clean_user(cursor, user_id)
        cursor.execute("DELETE FROM users WHERE user_id=%s", (user_id,))

@pytest.fixture
def auth_client(client, test_user):
    """Provides test client authenticated with active session."""
    with client.session_transaction() as sess:
        sess['user_id'] = test_user['user_id']
        sess['username'] = test_user['username']
        sess['display_name'] = test_user['display_name']
    return client

def _clean_user(cursor, uid):
    for tbl in ['goal_contributions', 'savings_goals', 'settlements', 'recurring_expenses',
                'expenses', 'income', 'notifications', 'notification_preferences', 'budgets', 'accounts']:
        cursor.execute(f"DELETE FROM {tbl} WHERE user_id=%s", (uid,))

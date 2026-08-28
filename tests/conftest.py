import pytest
import os
from werkzeug.security import generate_password_hash
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

        pw_hash = generate_password_hash('Password@123')
        cursor.execute(
            "INSERT INTO users (username, email, password, display_name) VALUES (%s, %s, %s, 'Pytest User')",
            (TEST_USERNAME, TEST_EMAIL, pw_hash)
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

@pytest.fixture
def dual_users(app, client):
    """Sets up two clean, isolated users (User A & User B) with known IDs, credentials, and main accounts."""
    email_a = "user_a_dual@example.com"
    email_b = "user_b_dual@example.com"
    pw = "Password123!"

    with get_db() as (conn, cursor):
        cursor.execute("SELECT user_id FROM users WHERE username IN ('user_a', 'user_b') OR email IN (%s, %s)", (email_a, email_b))
        rows = cursor.fetchall()
        for (uid,) in rows:
            for tbl in ['password_resets', 'goal_contributions', 'savings_goals', 'settlements',
                        'recurring_expenses', 'recurring_income', 'expenses', 'income', 'budgets',
                        'notifications', 'notification_preferences', 'accounts', 'users']:
                cursor.execute(f"DELETE FROM {tbl} WHERE user_id=%s", (uid,))

        pw_hash = generate_password_hash(pw)
        cursor.execute("INSERT INTO users (username, email, password, display_name) VALUES ('user_a', %s, %s, 'User A')", (email_a, pw_hash))
        id_a = cursor.lastrowid
        cursor.execute("INSERT INTO accounts (user_id, name, account_type, balance) VALUES (%s, 'Checking A', 'checking', 10000.00)", (id_a,))
        acc_a = cursor.lastrowid

        cursor.execute("INSERT INTO users (username, email, password, display_name) VALUES ('user_b', %s, %s, 'User B')", (email_b, pw_hash))
        id_b = cursor.lastrowid
        cursor.execute("INSERT INTO accounts (user_id, name, account_type, balance) VALUES (%s, 'Checking B', 'checking', 5000.00)", (id_b,))
        acc_b = cursor.lastrowid

    return {
        'id_a': id_a, 'email_a': email_a, 'acc_a': acc_a,
        'id_b': id_b, 'email_b': email_b, 'acc_b': acc_b,
        'pw': pw
    }


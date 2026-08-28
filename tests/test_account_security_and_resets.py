import pytest
import secrets
import hashlib
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db

def test_notification_badge_logic(auth_client, test_user):
    user_id = test_user['user_id']

    # 1. Clear notifications -> unread_count = 0
    auth_client.post('/notifications/clear')
    res = auth_client.get('/notifications')
    data = res.get_json()
    assert data['unread_count'] == 0
    assert data['notifications'] == []

    # 2. Add an unread notification directly to DB
    with get_db() as (conn, cursor):
        cursor.execute(
            "INSERT INTO notifications (user_id, icon, title, message, is_read) VALUES (%s, '🔔', 'Test Alert', 'Test message', 0)",
            (user_id,)
        )

    res = auth_client.get('/notifications')
    data = res.get_json()
    assert data['unread_count'] == 1
    assert len(data['notifications']) == 1
    assert data['notifications'][0]['is_read'] is False

    # 3. Mark read -> unread_count = 0
    notif_id = data['notifications'][0]['id']
    auth_client.post(f'/notifications/read/{notif_id}')

    res = auth_client.get('/notifications')
    data = res.get_json()
    assert data['unread_count'] == 0
    assert data['notifications'][0]['is_read'] is True


def test_reset_account_data_flow(auth_client, test_user, app):
    user_id = test_user['user_id']
    account_id = test_user['account_id']

    # Seed user financial data
    with get_db() as (conn, cursor):
        cursor.execute(
            "INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) VALUES (%s, 150.00, 'Food', 'Dinner', '2026-08-25', %s)",
            (user_id, account_id)
        )
        cursor.execute(
            "INSERT INTO income (user_id, amount, source, income_date, account_id) VALUES (%s, 1000.00, 'Salary', '2026-08-01', %s)",
            (user_id, account_id)
        )
        cursor.execute(
            "INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) VALUES (%s, 'Laptop', 50000, 10000)",
            (user_id,)
        )

    # 1. Attempt reset without confirmation text -> fails
    res = auth_client.post('/profile/reset-account-data', data={
        'password': 'TestPassword@123',
        'confirm_text': 'INVALID'
    }, follow_redirects=True)
    assert b"Confirmation text must match" in res.data

    # 2. Attempt reset with wrong password -> fails
    res = auth_client.post('/profile/reset-account-data', data={
        'password': 'WrongPassword123',
        'confirm_text': 'RESET'
    }, follow_redirects=True)
    assert b"Incorrect password" in res.data

    # Verify data is still present
    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s", (user_id,))
        assert cursor.fetchone()[0] == 1

    # 3. Successful reset with correct password and RESET confirmation
    res = auth_client.post('/profile/reset-account-data', data={
        'password': 'Password@123',
        'confirm_text': 'RESET'
    }, follow_redirects=True)
    assert b"Your financial data has been completely reset" in res.data

    # Verify user account & login still intact, but financial data cleared
    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM users WHERE user_id=%s", (user_id,))
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s", (user_id,))
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM income WHERE user_id=%s", (user_id,))
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE user_id=%s AND is_active=1", (user_id,))
        assert cursor.fetchone()[0] == 1  # Re-initialized default Main Account


def test_user_a_cannot_reset_user_b(client, app):
    with get_db() as (conn, cursor):
        cursor.execute("SELECT user_id FROM users WHERE username IN ('user_a', 'user_b') OR email IN ('user_a@example.com', 'user_b@example.com')")
        rows = cursor.fetchall()
        for r in rows:
            u_id = r[0]
            for tbl in ['password_resets', 'goal_contributions', 'savings_goals', 'settlements', 'recurring_expenses', 'recurring_income', 'expenses', 'income', 'budgets', 'notifications', 'notification_preferences', 'accounts', 'users']:
                cursor.execute(f"DELETE FROM {tbl} WHERE user_id=%s", (u_id,))

        pw_hash = generate_password_hash("Pass12345!")
        cursor.execute("INSERT INTO users (username, email, password) VALUES ('user_a', 'user_a@example.com', %s)", (pw_hash,))
        user_a_id = cursor.lastrowid
        cursor.execute("INSERT INTO users (username, email, password) VALUES ('user_b', 'user_b@example.com', %s)", (pw_hash,))
        user_b_id = cursor.lastrowid

        cursor.execute("INSERT INTO accounts (user_id, name, account_type, balance, currency) VALUES (%s, 'Main Account', 'checking', 0, 'INR')", (user_b_id,))
        b_acc_id = cursor.lastrowid
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) VALUES (%s, 99.00, 'Other', 'User B Expense', '2026-08-25', %s)", (user_b_id, b_acc_id))

    client.post('/login', data={'email': 'user_a@example.com', 'password': 'Pass12345!'})
    client.post('/profile/reset-account-data', data={'password': 'Pass12345!', 'confirm_text': 'RESET'})

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s", (user_b_id,))
        assert cursor.fetchone()[0] == 1


def test_google_only_account_reset_and_delete(client, app):
    with get_db() as (conn, cursor):
        cursor.execute("SELECT user_id FROM users WHERE email='google_user@example.com'")
        rows = cursor.fetchall()
        for r in rows:
            u_id = r[0]
            for tbl in ['password_resets', 'goal_contributions', 'savings_goals', 'settlements', 'recurring_expenses', 'recurring_income', 'expenses', 'income', 'budgets', 'notifications', 'notification_preferences', 'accounts', 'users']:
                cursor.execute(f"DELETE FROM {tbl} WHERE user_id=%s", (u_id,))

        google_pw = generate_password_hash(secrets.token_hex(32))
        cursor.execute(
            "INSERT INTO users (username, email, password, display_name, auth_provider) VALUES ('google_user@example.com', 'google_user@example.com', %s, 'Google User', 'google')",
            (google_pw,)
        )
        g_user_id = cursor.lastrowid
        cursor.execute("INSERT INTO accounts (user_id, name, account_type, balance, currency) VALUES (%s, 'Main Account', 'checking', 0, 'INR')", (g_user_id,))
        g_acc_id = cursor.lastrowid
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) VALUES (%s, 50.00, 'Food', 'Snack', '2026-08-25', %s)", (g_user_id, g_acc_id))

    with client.session_transaction() as sess:
        sess['user_id'] = g_user_id
        sess['username'] = 'google_user@example.com'
        sess['display_name'] = 'Google User'

    res = client.post('/profile/reset-account-data', data={'confirm_text': 'RESET', 'confirm_email': 'wrong@example.com'}, follow_redirects=True)
    assert b"Email confirmation does not match" in res.data

    res = client.post('/profile/reset-account-data', data={'confirm_text': 'RESET', 'confirm_email': 'google_user@example.com'}, follow_redirects=True)
    assert b"Your financial data has been completely reset" in res.data

    res = client.post('/profile/delete-account', data={'confirm_text': 'DELETE', 'confirm_username': 'google_user@example.com'}, follow_redirects=True)
    assert b"permanently deleted" in res.data

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM users WHERE user_id=%s", (g_user_id,))
        assert cursor.fetchone()[0] == 0


def test_forgot_password_and_token_reset_flow(client, app):
    res = client.post('/forgot-password', data={'email': 'nonexistent_test@example.com'}, follow_redirects=True)
    assert b"If an account exists for that email, you will receive password reset instructions." in res.data

    reg_email = "pw_reset_user@example.com"
    with get_db() as (conn, cursor):
        cursor.execute("SELECT user_id FROM users WHERE email=%s OR username='pw_reset_user'", (reg_email,))
        rows = cursor.fetchall()
        for r in rows:
            u_id = r[0]
            for tbl in ['password_resets', 'goal_contributions', 'savings_goals', 'settlements', 'recurring_expenses', 'recurring_income', 'expenses', 'income', 'budgets', 'notifications', 'notification_preferences', 'accounts', 'users']:
                cursor.execute(f"DELETE FROM {tbl} WHERE user_id=%s", (u_id,))

        pw_hash = generate_password_hash("OldPassword123!")
        cursor.execute("INSERT INTO users (username, email, password) VALUES ('pw_reset_user', %s, %s)", (reg_email, pw_hash))
        u_id = cursor.lastrowid

    res = client.post('/forgot-password', data={'email': reg_email}, follow_redirects=True)
    assert b"If an account exists for that email, you will receive password reset instructions." in res.data

    with get_db() as (conn, cursor):
        cursor.execute("SELECT token_hash, expires_at, used_at FROM password_resets WHERE user_id=%s", (u_id,))
        token_row = cursor.fetchone()
        assert token_row is not None
        db_token_hash, expires_at, used_at = token_row
        assert used_at is None

    res = client.get('/reset-password/invalid_token_str', follow_redirects=True)
    assert b"Invalid or expired password reset link." in res.data

    raw_token = secrets.token_urlsafe(32)
    raw_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
    with get_db() as (conn, cursor):
        cursor.execute(
            "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
            (u_id, raw_hash, datetime.now() + timedelta(hours=1))
        )

    res = client.post(f'/reset-password/{raw_token}', data={
        'new_password': 'NewPassword123!',
        'confirm_password': 'NewPassword123!'
    }, follow_redirects=True)
    assert b"Your password has been reset successfully!" in res.data

    res = client.post(f'/reset-password/{raw_token}', data={
        'new_password': 'AnotherPassword123!',
        'confirm_password': 'AnotherPassword123!'
    }, follow_redirects=True)
    assert b"expired or already been used" in res.data

    res = client.post('/login', data={'email': reg_email, 'password': 'NewPassword123!'}, follow_redirects=True)
    assert res.status_code == 200
    assert b"Welcome back!" in res.data


def test_login_and_oauth_redirects_to_dashboard(client, app):
    """Verifies login, registration, and OAuth callback redirect to Dashboard (/), and open redirects are rejected."""
    client.get('/logout')
    reg_email = "redirect_test@example.com"
    pw = "Password123!"

    with get_db() as (conn, cursor):
        cursor.execute("SELECT user_id FROM users WHERE email=%s", (reg_email,))
        row = cursor.fetchone()
        if row:
            u_id = row[0]
            for tbl in ['password_resets', 'goal_contributions', 'savings_goals', 'settlements', 'recurring_expenses', 'recurring_income', 'expenses', 'income', 'budgets', 'notifications', 'notification_preferences', 'accounts', 'users']:
                cursor.execute(f"DELETE FROM {tbl} WHERE user_id=%s", (u_id,))

    # 1. Registration redirects to Dashboard (/)
    res = client.post('/register', data={
        'username': 'redirect_test',
        'email': reg_email,
        'password': pw,
        'confirm_password': pw
    }, follow_redirects=False)
    assert res.status_code in (302, 303)
    assert res.headers['Location'] == '/'

    client.get('/logout')

    # 2. Login redirects to Dashboard (/)
    res = client.post('/login', data={'email': reg_email, 'password': pw}, follow_redirects=False)
    assert res.status_code in (302, 303)
    assert res.headers['Location'] == '/'

    client.get('/logout')

    # 3. Malicious next URL is rejected and falls back to Dashboard (/)
    res = client.post('/login?next=https://evil-external-site.com', data={'email': reg_email, 'password': pw}, follow_redirects=False)
    assert res.status_code in (302, 303)
    assert res.headers['Location'] == '/'


def test_delete_account_safety_and_user_isolation(client, test_user):
    """Verifies account deletion safety: unused accounts can be deleted, accounts with linked transactions are blocked."""
    client.get('/logout')
    user_id = test_user['user_id']
    main_acc_id = test_user['account_id']
    pw = 'Password@123'

    # Create two new accounts for user
    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO accounts (user_id, name, account_type, balance) VALUES (%s, 'Unused Cash', 'cash', 0.00)", (user_id,))
        unused_acc_id = cursor.lastrowid
        cursor.execute("INSERT INTO accounts (user_id, name, account_type, balance) VALUES (%s, 'Used Wallet', 'wallet', 500.00)", (user_id,))
        used_acc_id = cursor.lastrowid

        # Seed linked expense transaction for used_acc_id
        cursor.execute(
            "INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) VALUES (%s, 50.00, 'Food', 'Snack', '2026-08-25', %s)",
            (user_id, used_acc_id)
        )

    # Login
    client.post('/login', data={'email': test_user['email'], 'password': pw}, follow_redirects=True)

    # 1. Attempt delete without DELETE confirm_text -> fails
    res = client.post(f'/delete-account/{unused_acc_id}', data={'confirm_text': 'NO', 'password': pw}, follow_redirects=True)
    assert b"must type DELETE to confirm" in res.data

    # 2. Attempt delete with wrong password -> fails
    res = client.post(f'/delete-account/{unused_acc_id}', data={'confirm_text': 'DELETE', 'password': 'WrongPassword'}, follow_redirects=True)
    assert b"Incorrect password" in res.data

    # 3. Attempt delete account with linked transactions -> BLOCKED with warning message!
    res = client.post(f'/delete-account/{used_acc_id}', data={'confirm_text': 'DELETE', 'password': pw}, follow_redirects=True)
    assert b"linked transaction" in res.data
    assert b"cannot be deleted safely. Archive it instead" in res.data

    # Verify used account is NOT deleted
    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE account_id=%s", (used_acc_id,))
        assert cursor.fetchone()[0] == 1

    # 4. Successful deletion of unused account
    res = client.post(f'/delete-account/{unused_acc_id}', data={'confirm_text': 'DELETE', 'password': pw}, follow_redirects=True)
    assert b"Account deleted successfully" in res.data

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE account_id=%s", (unused_acc_id,))
        assert cursor.fetchone()[0] == 0

    client.get('/logout')


def test_legacy_google_auth_password_migration(app):
    """Verifies that ensure_schema() migrates legacy google_auth passwords without NameError."""
    from app import ensure_schema
    legacy_email = "legacy_google_user@example.com"
    legacy_pw_hash = generate_password_hash("google_auth")

    with get_db() as (conn, cursor):
        cursor.execute("SELECT user_id FROM users WHERE email=%s", (legacy_email,))
        row = cursor.fetchone()
        if row:
            u_id = row[0]
            for tbl in ['password_resets', 'goal_contributions', 'savings_goals', 'settlements', 'recurring_expenses', 'recurring_income', 'expenses', 'income', 'budgets', 'notifications', 'notification_preferences', 'accounts', 'users']:
                cursor.execute(f"DELETE FROM {tbl} WHERE user_id=%s", (u_id,))

        cursor.execute(
            "INSERT INTO users (username, email, password, auth_provider) VALUES (%s, %s, %s, 'google')",
            (legacy_email, legacy_email, legacy_pw_hash)
        )
        legacy_uid = cursor.lastrowid

    # Execute ensure_schema() — must not raise NameError and must migrate the legacy password
    ensure_schema()

    with get_db() as (conn, cursor):
        cursor.execute("SELECT password FROM users WHERE user_id=%s", (legacy_uid,))
        new_pw_hash = cursor.fetchone()[0]
        # Verify the password hash was successfully updated and is no longer matching 'google_auth'
        assert new_pw_hash != legacy_pw_hash
        assert check_password_hash(new_pw_hash, 'google_auth') is False



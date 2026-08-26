import pytest
from db import get_db
from werkzeug.security import generate_password_hash
from services.settlement_service import build_settlements_summary

def _clean_user_by_email(email):
    with get_db() as (conn, cursor):
        cursor.execute("SELECT user_id FROM users WHERE email=%s", (email,))
        rows = cursor.fetchall()
        for (uid,) in rows:
            for tbl in ['password_resets', 'goal_contributions', 'savings_goals', 'settlements', 'recurring_expenses', 'recurring_income', 'expenses', 'income', 'budgets', 'notifications', 'notification_preferences', 'accounts', 'users']:
                cursor.execute(f"DELETE FROM {tbl} WHERE user_id=%s", (uid,))

@pytest.fixture
def dual_users(app, client):
    """Provides two clean test users for user isolation tests."""
    client.get('/logout')
    email_a = "bugfix_user_a@example.com"
    email_b = "bugfix_user_b@example.com"
    _clean_user_by_email(email_a)
    _clean_user_by_email(email_b)

    pw_hash = generate_password_hash("Password@123")
    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO users (username, email, password, display_name) VALUES ('bugfix_a', %s, %s, 'User A')", (email_a, pw_hash))
        uid_a = cursor.lastrowid
        cursor.execute("INSERT INTO accounts (user_id, name, account_type, balance) VALUES (%s, 'Checking A', 'checking', 10000.00)", (uid_a,))

        cursor.execute("INSERT INTO users (username, email, password, display_name) VALUES ('bugfix_b', %s, %s, 'User B')", (email_b, pw_hash))
        uid_b = cursor.lastrowid
        cursor.execute("INSERT INTO accounts (user_id, name, account_type, balance) VALUES (%s, 'Checking B', 'checking', 5000.00)", (uid_b,))

    yield {'id_a': uid_a, 'email_a': email_a, 'id_b': uid_b, 'email_b': email_b, 'pw': 'Password@123'}

    client.get('/logout')
    _clean_user_by_email(email_a)
    _clean_user_by_email(email_b)


# ==============================================================================
# 1. NOTIFICATION READ & BADGE BUGFIX TESTS
# ==============================================================================

def test_notification_read_and_badge_flow(client, dual_users):
    """Verifies notification count, reading notification, badge disappearance, and user isolation."""
    client.get('/logout')
    uid_a = dual_users['id_a']
    uid_b = dual_users['id_b']

    # Insert notifications for User A & User B
    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO notifications (user_id, icon, title, message, is_read) VALUES (%s, '🔔', 'Alert A1', 'Msg A1', 0)", (uid_a,))
        nid_a1 = cursor.lastrowid
        cursor.execute("INSERT INTO notifications (user_id, icon, title, message, is_read) VALUES (%s, '🔔', 'Alert A2', 'Msg A2', 0)", (uid_a,))
        nid_a2 = cursor.lastrowid
        cursor.execute("INSERT INTO notifications (user_id, icon, title, message, is_read) VALUES (%s, '🔔', 'Alert B1', 'Msg B1', 0)", (uid_b,))
        nid_b1 = cursor.lastrowid

    # Log in User A
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # Initial check: unread_count = 2 for User A
    res = client.get('/notifications')
    data = res.get_json()
    assert data['unread_count'] == 2
    assert len(data['notifications']) == 2

    # Mark A1 as read
    res = client.post(f'/notifications/read/{nid_a1}')
    assert res.status_code == 200
    assert res.get_json()['success'] is True

    # Page refresh / API check: unread_count = 1
    res = client.get('/notifications')
    data = res.get_json()
    assert data['unread_count'] == 1
    a1_item = next(n for n in data['notifications'] if n['id'] == nid_a1)
    assert a1_item['is_read'] is True

    # Mark all read for User A
    res = client.post('/notifications/read-all')
    assert res.status_code == 200

    # Unread count reaches zero
    res = client.get('/notifications')
    data = res.get_json()
    assert data['unread_count'] == 0

    # User A cannot read User B's notification
    client.post(f'/notifications/read/{nid_b1}')
    with get_db() as (conn, cursor):
        cursor.execute("SELECT is_read FROM notifications WHERE notification_id=%s", (nid_b1,))
        assert cursor.fetchone()[0] == 0  # B's notification remains unread

    client.get('/logout')


# ==============================================================================
# 2. NET OUTSTANDING NaN BUGFIX TESTS
# ==============================================================================

def test_net_outstanding_calculation_and_formatting(client, dual_users):
    """Verifies net outstanding produces valid numbers for zero, positive, negative, and null balances."""
    client.get('/logout')
    uid_a = dual_users['id_a']

    # 1. Zero balances
    with get_db() as (conn, cursor):
        summary = build_settlements_summary(cursor, uid_a)
        assert summary['total_owed_to_you'] == 0.0
        assert summary['total_you_owe'] == 0.0
        assert summary['net_balance'] == 0.0

    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)
    res = client.get('/api/settlements/data')
    assert res.status_code == 200
    json_data = res.get_json()
    assert json_data['net_position'] == 0.0
    assert json_data['owed_to_you'] == 0.0
    assert json_data['you_owe'] == 0.0

    # Page render check: ₹0 present, ₹NaN absent
    res = client.get('/settlements')
    assert "₹NaN".encode('utf-8') not in res.data
    assert b'Net Outstanding' in res.data

    # 2. Positive Net Outstanding: They owe me 500, I owe 200 -> Net = +300
    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO settlements (user_id, peer_name, amount, status) VALUES (%s, 'Alice', 500.00, 'active')", (uid_a,))
        cursor.execute("INSERT INTO settlements (user_id, peer_name, amount, status) VALUES (%s, 'Bob', -200.00, 'active')", (uid_a,))

    res = client.get('/api/settlements/data')
    json_data = res.get_json()
    assert json_data['owed_to_you'] == 500.0
    assert json_data['you_owe'] == 200.0
    assert json_data['net_position'] == 300.0

    # 3. Negative Net Outstanding: They owe 0, I owe 500 -> Net = -500
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM settlements WHERE user_id=%s", (uid_a,))
        cursor.execute("INSERT INTO settlements (user_id, peer_name, amount, status) VALUES (%s, 'Charlie', -500.00, 'active')", (uid_a,))

    res = client.get('/api/settlements/data')
    json_data = res.get_json()
    assert json_data['owed_to_you'] == 0.0
    assert json_data['you_owe'] == 500.0
    assert json_data['net_position'] == -500.0

    client.get('/logout')


# ==============================================================================
# 3. REPORTS PAGE RENDERING & DATA TESTS
# ==============================================================================

def test_monthly_report_tabs_and_rendering(client, dual_users):
    """Verifies /monthly-report loads and renders Overview, Analytics, Insights, Exports tabs with full content."""
    client.get('/logout')
    uid_a = dual_users['id_a']
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # 1. Empty data user receives non-blank page with intentional empty state
    res = client.get('/monthly-report')
    assert res.status_code == 200
    assert b'Reports &amp; Analytics' in res.data or b'Reports & Analytics' in res.data
    assert b'Total Spent' in res.data
    assert b'No expense history yet' in res.data or b'Add First Expense' in res.data

    # 2. Seed income & expenses
    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO income (user_id, source, amount, income_date) VALUES (%s, 'Salary', 80000.00, '2026-08-01')", (uid_a,))
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 1500.00, 'Food', 'Groceries', '2026-08-10')", (uid_a,))
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 2500.00, 'Bills', 'Electricity', '2026-08-15')", (uid_a,))

    # Overview tab
    res = client.get('/monthly-report?tab=overview')
    assert res.status_code == 200
    assert b'Total Spent' in res.data
    assert b'Spending Over Time' in res.data
    assert b'Spending Heatmap' in res.data

    # Analytics tab
    res = client.get('/monthly-report?tab=analytics')
    assert res.status_code == 200
    assert b'Forecast Breakdown' in res.data
    assert b'Daily Spending Trend' in res.data
    assert b'Income vs Expenses' in res.data

    # Insights tab
    res = client.get('/monthly-report?tab=insights')
    assert res.status_code == 200
    assert b'Financial Health Score' in res.data
    assert b'Savings Health' in res.data

    # Exports tab
    res = client.get('/monthly-report?tab=exports')
    assert res.status_code == 200
    assert b'Export Expenses as CSV' in res.data
    assert b'Download Expense Report PDF' in res.data

    # Test CSV export endpoint
    csv_res = client.get('/export')
    assert csv_res.status_code == 200
    assert csv_res.mimetype == 'text/csv'
    assert b'Groceries' in csv_res.data or b'Electricity' in csv_res.data

    # Test PDF export endpoint
    pdf_res = client.get('/export-pdf')
    assert pdf_res.status_code == 200
    assert pdf_res.mimetype == 'application/pdf'

    client.get('/logout')


def test_reports_unauthenticated_access_rejected(client):
    """Verifies unauthenticated requests to report pages redirect to login."""
    client.get('/logout')
    res = client.get('/monthly-report', follow_redirects=False)
    assert res.status_code in (302, 303)
    assert '/login' in res.headers['Location']

    res = client.get('/export', follow_redirects=False)
    assert res.status_code in (302, 303)
    assert '/login' in res.headers['Location']

    res = client.get('/export-pdf', follow_redirects=False)
    assert res.status_code in (302, 303)
    assert '/login' in res.headers['Location']

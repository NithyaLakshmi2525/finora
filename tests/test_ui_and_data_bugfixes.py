import pytest
from datetime import date
from db import get_db
from werkzeug.security import generate_password_hash
from services.settlement_service import build_settlements_summary
from services.ledger_service import build_income_context

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
    """Verifies notification count, reading notification, mark-all-read, clear-all, and user isolation."""
    client.get('/logout')
    uid_a = dual_users['id_a']
    uid_b = dual_users['id_b']

    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO notifications (user_id, icon, title, message, is_read) VALUES (%s, '🔔', 'Alert A1', 'Msg A1', 0)", (uid_a,))
        nid_a1 = cursor.lastrowid
        cursor.execute("INSERT INTO notifications (user_id, icon, title, message, is_read) VALUES (%s, '🔔', 'Alert A2', 'Msg A2', 0)", (uid_a,))
        nid_a2 = cursor.lastrowid
        cursor.execute("INSERT INTO notifications (user_id, icon, title, message, is_read) VALUES (%s, '🔔', 'Alert B1', 'Msg B1', 0)", (uid_b,))
        nid_b1 = cursor.lastrowid

    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    res = client.get('/notifications')
    data = res.get_json()
    assert data['unread_count'] == 2
    assert len(data['notifications']) == 2

    res = client.post(f'/notifications/read/{nid_a1}')
    assert res.status_code == 200
    assert res.get_json()['success'] is True

    res = client.get('/notifications')
    data = res.get_json()
    assert data['unread_count'] == 1
    a1_item = next(n for n in data['notifications'] if n['id'] == nid_a1)
    assert a1_item['is_read'] is True

    res = client.post('/notifications/read-all')
    assert res.status_code == 200

    res = client.get('/notifications')
    data = res.get_json()
    assert data['unread_count'] == 0
    assert len(data['notifications']) == 2

    res = client.post('/notifications/clear')
    assert res.status_code == 200

    res = client.get('/notifications')
    data = res.get_json()
    assert data['unread_count'] == 0
    assert len(data['notifications']) == 0

    client.post(f'/notifications/read/{nid_b1}')
    with get_db() as (conn, cursor):
        cursor.execute("SELECT is_read FROM notifications WHERE notification_id=%s", (nid_b1,))
        assert cursor.fetchone()[0] == 0

    client.get('/logout')


# ==============================================================================
# 2. NET OUTSTANDING NaN BUGFIX TESTS
# ==============================================================================

def test_net_outstanding_calculation_and_formatting(client, dual_users):
    """Verifies net outstanding produces valid numbers for zero, positive, negative, and null balances."""
    client.get('/logout')
    uid_a = dual_users['id_a']

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

    res = client.get('/settlements')
    assert "₹NaN".encode('utf-8') not in res.data
    assert b'Net Outstanding' in res.data

    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO settlements (user_id, peer_name, amount, status) VALUES (%s, 'Alice', 500.00, 'active')", (uid_a,))
        cursor.execute("INSERT INTO settlements (user_id, peer_name, amount, status) VALUES (%s, 'Bob', -200.00, 'active')", (uid_a,))

    res = client.get('/api/settlements/data')
    json_data = res.get_json()
    assert json_data['owed_to_you'] == 500.0
    assert json_data['you_owe'] == 200.0
    assert json_data['net_position'] == 300.0

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
    today_str = date.today().strftime('%Y-%m')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    res = client.get('/monthly-report')
    assert res.status_code == 200
    assert b'Reports &amp; Analytics' in res.data or b'Reports & Analytics' in res.data
    assert b'Total Spent' in res.data

    with get_db() as (conn, cursor):
        cursor.execute(f"INSERT INTO income (user_id, source, amount, income_date) VALUES (%s, 'Salary', 80000.00, '{today_str}-01')", (uid_a,))
        cursor.execute(f"INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 1500.00, 'Food', 'Groceries', '{today_str}-05')", (uid_a,))
        cursor.execute(f"INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 2500.00, 'Bills', 'Electricity', '{today_str}-10')", (uid_a,))

    res = client.get('/monthly-report?tab=overview')
    assert res.status_code == 200
    assert b'Total Spent' in res.data
    assert b'Spending Over Time' in res.data

    res = client.get('/monthly-report?tab=analytics')
    assert res.status_code == 200
    assert b'Forecast Breakdown' in res.data

    res = client.get('/monthly-report?tab=insights')
    assert res.status_code == 200
    assert b'Financial Health Score' in res.data

    res = client.get('/monthly-report?tab=exports')
    assert res.status_code == 200
    assert b'Export Expenses as CSV' in res.data

    csv_res = client.get('/export')
    assert csv_res.status_code == 200
    assert csv_res.mimetype == 'text/csv'

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


# ==============================================================================
# 4. DASHBOARD UX & SAVINGS GOALS TESTS
# ==============================================================================

def test_dashboard_ux_and_goals_rendering(client, dual_users):
    """Verifies budget card empty/overall/category states, active savings goals rendering, and date formatting."""
    client.get('/logout')
    uid_a = dual_users['id_a']
    uid_b = dual_users['id_b']
    today_str = date.today().strftime('%Y-%m')

    # Clean budgets
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM budgets WHERE user_id=%s", (uid_a,))

    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # 1. Budget Card: No budgets
    res = client.get('/')
    assert b'No monthly budget set' in res.data or b'No budget set' in res.data
    assert b'Set a budget to track spending' in res.data or b'Set limits to track' in res.data

    # 2. Budget Card: Overall budget
    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO budgets (user_id, category, monthly_limit) VALUES (%s, 'Overall', 40000.00)", (uid_a,))

    res = client.get('/')
    assert b'Monthly Budget' in res.data
    assert b'40,000' in res.data

    # 3. Budget Card: Category budgets only
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM budgets WHERE user_id=%s", (uid_a,))
        cursor.execute("INSERT INTO budgets (user_id, category, monthly_limit) VALUES (%s, 'Food', 5000.00)", (uid_a,))
        cursor.execute("INSERT INTO budgets (user_id, category, monthly_limit) VALUES (%s, 'Bills', 7000.00)", (uid_a,))

    res = client.get('/')
    assert b'Category Budgets Active' in res.data or b'Category Budget' in res.data
    assert b'12,000' in res.data

    # 4. Savings Goals rendering: Active vs Closed vs Empty
    res = client.get('/')
    assert b'No goals yet' in res.data

    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount, icon) VALUES (%s, 'Emergency Fund', 100000.00, 25000.00, '🛡️')", (uid_a,))
        cursor.execute("INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount, closed_at) VALUES (%s, 'Old Car', 50000.00, 50000.00, NOW())", (uid_a,))
        cursor.execute("INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) VALUES (%s, 'User B Vacation', 30000.00, 5000.00)", (uid_b,))

    res = client.get('/')
    assert b'Emergency Fund' in res.data
    assert b'25,000' in res.data
    assert b'100,000' in res.data
    assert b'25%' in res.data
    assert b'Old Car' not in res.data
    assert b'User B Vacation' not in res.data

    # 5. Recent Expenses Date Formatting
    with get_db() as (conn, cursor):
        cursor.execute(f"INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 1200.00, 'Food', 'Dinner', '{today_str}-15')", (uid_a,))

    res = client.get('/')
    assert b'%d %b %Y' not in res.data
    assert b'Dinner' in res.data

    client.get('/logout')


# ==============================================================================
# 5. EXPORT CSV FILTER PRESERVATION TESTS
# ==============================================================================

def test_export_csv_filters_preservation(client, dual_users):
    """Verifies Export CSV preserves category, start_date, end_date, search, and show_income filters."""
    client.get('/logout')
    uid_a = dual_users['id_a']
    uid_b = dual_users['id_b']

    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 100.00, 'Food', 'Apple', '2026-08-01')", (uid_a,))
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 200.00, 'Bills', 'Electricity', '2026-08-10')", (uid_a,))
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 300.00, 'Food', 'Pizza', '2026-08-20')", (uid_a,))
        cursor.execute("INSERT INTO income (user_id, source, amount, income_date, description) VALUES (%s, 'Salary', 5000.00, '2026-08-05', 'Monthly Pay')", (uid_a,))
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 999.00, 'Food', 'User B Secret', '2026-08-15')", (uid_b,))

    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # 1. No filters -> All User A expenses exported (3 rows)
    res = client.get('/export')
    assert res.status_code == 200
    lines = [l for l in res.data.decode('utf-8').strip().split('\n') if l]
    assert len(lines) == 4  # Header + 3 expense rows
    assert 'User B Secret' not in res.data.decode('utf-8')

    # 2. Category filter -> Food only (2 rows)
    res = client.get('/export?category=Food')
    lines = [l for l in res.data.decode('utf-8').strip().split('\n') if l]
    assert len(lines) == 3  # Header + 2 Food rows
    assert 'Apple' in res.data.decode('utf-8')
    assert 'Pizza' in res.data.decode('utf-8')
    assert 'Electricity' not in res.data.decode('utf-8')

    # 3. Start Date filter -> 2026-08-10 onwards (2 rows)
    res = client.get('/export?start_date=2026-08-10')
    lines = [l for l in res.data.decode('utf-8').strip().split('\n') if l]
    assert len(lines) == 3  # Header + Electricity + Pizza
    assert 'Apple' not in res.data.decode('utf-8')

    # 4. End Date filter -> up to 2026-08-05 (1 row)
    res = client.get('/export?end_date=2026-08-05')
    lines = [l for l in res.data.decode('utf-8').strip().split('\n') if l]
    assert len(lines) == 2  # Header + Apple

    # 5. Combined Start + End Date range (2026-08-05 to 2026-08-15) -> 1 row (Electricity)
    res = client.get('/export?start_date=2026-08-05&end_date=2026-08-15')
    lines = [l for l in res.data.decode('utf-8').strip().split('\n') if l]
    assert len(lines) == 2  # Header + Electricity
    assert 'Electricity' in res.data.decode('utf-8')

    # 6. Search query -> 'Pizza' (1 row)
    res = client.get('/export?search=Pizza')
    lines = [l for l in res.data.decode('utf-8').strip().split('\n') if l]
    assert len(lines) == 2
    assert 'Pizza' in res.data.decode('utf-8')

    # 7. Show Income -> Expenses + Income included (4 rows)
    res = client.get('/export?show_income=true')
    lines = [l for l in res.data.decode('utf-8').strip().split('\n') if l]
    assert len(lines) == 5  # Header + 3 expenses + 1 income
    assert 'Monthly Pay' in res.data.decode('utf-8')

    client.get('/logout')


# ==============================================================================
# 6. INCOME PAGE AVERAGE & TOP SOURCE TESTS
# ==============================================================================

def test_income_page_average_and_top_source(client, dual_users):
    """Verifies total income, average income = total / count, top source calculation, and zero safety."""
    client.get('/logout')
    uid_a = dual_users['id_a']
    uid_b = dual_users['id_b']

    # 1. Zero income rows -> total = 0, avg = 0, top_source = None
    with get_db() as (conn, cursor):
        ctx = build_income_context(cursor, uid_a)
        assert ctx['total_income'] == 0.0
        assert ctx['avg_income'] == 0.0
        assert ctx['top_source'] is None

    # 2. Add 3 entries: 500,000 (Salary), 1,000 (Family Support), 500 (Gift)
    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO income (user_id, source, amount, income_date) VALUES (%s, 'Salary', 500000.00, '2026-08-01')", (uid_a,))
        cursor.execute("INSERT INTO income (user_id, source, amount, income_date) VALUES (%s, 'Family Support', 1000.00, '2026-08-05')", (uid_a,))
        cursor.execute("INSERT INTO income (user_id, source, amount, income_date) VALUES (%s, 'Gift', 500.00, '2026-08-10')", (uid_a,))
        # User B income (isolated)
        cursor.execute("INSERT INTO income (user_id, source, amount, income_date) VALUES (%s, 'User B Business', 999999.00, '2026-08-12')", (uid_b,))

    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)
    res = client.get('/income')
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        ctx = build_income_context(cursor, uid_a)
        assert ctx['total_income'] == 501500.0
        assert ctx['income_count'] == 3
        assert round(ctx['avg_income'], 2) == round(501500.0 / 3, 2)  # 167,166.67
        assert ctx['top_source'] == 'Salary'
        assert ctx['top_source_amount'] == 500000.0

    # HTML contains calculated average & top source, not User B data
    assert b'501,500' in res.data
    assert b'167,167' in res.data or b'167,166' in res.data
    assert b'Salary' in res.data
    assert b'User B Business' not in res.data

    client.get('/logout')


# ==============================================================================
# 7. ACCOUNTS & BUDGETS PAGE DATA & UI TESTS
# ==============================================================================

def test_accounts_and_budgets_page_rendering(client, dual_users):
    """Verifies Accounts and Budgets pages render real data, net worth, and empty states cleanly."""
    client.get('/logout')
    uid_a = dual_users['id_a']
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # Accounts Page
    res = client.get('/accounts')
    assert res.status_code == 200
    assert b'Accounts &amp; Ledger' in res.data or b'Accounts & Ledger' in res.data
    assert b'Total Net Worth' in res.data
    assert b'10,000' in res.data  # Initial balance of Checking A

    # Budgets Page: No overall budget -> Displays "No overall budget set"
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM budgets WHERE user_id=%s", (uid_a,))

    res = client.get('/budgets')
    assert res.status_code == 200
    assert b'No overall budget set' in res.data
    assert b'NORMAL (0.0%)' not in res.data  # Fake 0% state eliminated!

    # Budgets Page: Set overall budget -> Displays configured metrics
    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO budgets (user_id, category, monthly_limit) VALUES (%s, 'Overall', 30000.00)", (uid_a,))

    res = client.get('/budgets')
    assert res.status_code == 200
    assert b'Overall Monthly Target' in res.data
    assert b'30,000' in res.data

    client.get('/logout')


# ==============================================================================
# 8. SIDEBAR CONSISTENCY & CSV EXPORT UI INDICATION TESTS
# ==============================================================================

def test_sidebar_consistency_across_all_pages(client, dual_users):
    """Verifies that all authenticated routes include the shared sidebar partial and active link styling."""
    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    routes = [
        ('/', b'Dashboard'),
        ('/expenses', b'Expenses'),
        ('/income', b'Income'),
        ('/accounts', b'Accounts'),
        ('/budgets', b'Budgets'),
        ('/recurring', b'Recurring'),
        ('/goals', b'Goals'),
        ('/monthly-report', b'Reports'),
        ('/settlements', b'Balances'),
        ('/settings', b'Settings'),
        ('/profile', b'Personal Account')
    ]

    for path, expected_text in routes:
        res = client.get(path)
        assert res.status_code == 200, f"Failed on path {path}"
        assert b'id="sidebar"' in res.data, f"Sidebar drawer missing on path {path}"
        assert b'sidebar-drawer' in res.data, f"Sidebar drawer class missing on path {path}"
        assert b'Finora' in res.data, f"Brand logo missing on path {path}"
        assert expected_text in res.data, f"Expected text {expected_text} missing on path {path}"

    client.get('/logout')


def test_filtered_csv_export_ui_indication(client, dual_users):
    """Verifies the Expenses page clearly indicates when Export CSV will export filtered vs all transactions."""
    client.get('/logout')
    uid_a = dual_users['id_a']

    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 100.00, 'Food', 'Apple', '2026-08-01')", (uid_a,))
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 200.00, 'Bills', 'Electricity', '2026-08-10')", (uid_a,))

    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # 1. Unfiltered state
    res = client.get('/expenses')
    assert res.status_code == 200
    assert b'Export CSV downloads all' in res.data
    assert b'no active filters applied' in res.data
    assert b'Export CSV' in res.data

    # 2. Filtered state (Category = Food)
    res = client.get('/expenses?category=Food')
    assert res.status_code == 200
    assert b'Export CSV uses active filters' in res.data
    assert b'Export Filtered CSV' in res.data
    assert b'currently filtered transaction' in res.data

    # 3. Filtered state (Search = Electricity)
    res = client.get('/expenses?search=Electricity')
    assert res.status_code == 200
    assert b'Export CSV uses active filters' in res.data
    assert b'Export Filtered CSV' in res.data

    client.get('/logout')


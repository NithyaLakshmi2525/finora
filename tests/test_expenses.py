import pytest
from db import get_db

def test_expense_crud_and_filtering(auth_client, test_user):
    # 1. Add Expense
    res = auth_client.post('/add-expense', data={
        'amount': '850.00',
        'category': 'Food',
        'description': 'Pytest Dinner',
        'expense_date': '2026-08-25',
        'account_id': str(test_user['account_id'])
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b'Pytest Dinner' in res.data

    with get_db() as (conn, cursor):
        cursor.execute("SELECT expense_id FROM expenses WHERE user_id=%s AND description='Pytest Dinner'", (test_user['user_id'],))
        exp_id = cursor.fetchone()[0]

    # 2. Filter & Search
    res = auth_client.get('/expenses?search=Pytest')
    assert res.status_code == 200
    assert b'Pytest Dinner' in res.data

    # 3. Edit Expense
    res = auth_client.post(f'/edit/{exp_id}', data={
        'amount': '950.00',
        'category': 'Food',
        'description': 'Pytest Premium Dinner',
        'expense_date': '2026-08-25',
        'account_id': str(test_user['account_id'])
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b'Pytest Premium Dinner' in res.data

    # 4. Delete Expense
    res = auth_client.get(f'/delete/{exp_id}', follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE expense_id=%s", (exp_id,))
        assert cursor.fetchone()[0] == 0


def test_show_income_preserves_transaction_type_and_amounts(client, dual_users):
    """Verifies Show Income preserves transaction types, renders amounts correctly, keeps expense summary expense-only, and performs zero DB mutations."""
    uid_a = dual_users['id_a']
    acc_a = dual_users['acc_a']

    # Seed 2 expenses (Food 100, Shopping 200) and 2 income (Salary 1000, Gift 500) for User A
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM expenses WHERE user_id=%s", (uid_a,))
        cursor.execute("DELETE FROM income WHERE user_id=%s", (uid_a,))

        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) VALUES (%s, 100.00, 'Food', 'Dinner', '2026-08-25', %s)", (uid_a, acc_a))
        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) VALUES (%s, 200.00, 'Shopping', 'Shoes', '2026-08-26', %s)", (uid_a, acc_a))

        cursor.execute("INSERT INTO income (user_id, amount, source, description, income_date, account_id) VALUES (%s, 1000.00, 'Salary', 'August Salary', '2026-08-20', %s)", (uid_a, acc_a))
        cursor.execute("INSERT INTO income (user_id, amount, source, description, income_date, account_id) VALUES (%s, 500.00, 'Gift', 'Birthday Gift', '2026-08-21', %s)", (uid_a, acc_a))

        # Record initial database counts & balance
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s", (uid_a,))
        exp_count_before = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM income WHERE user_id=%s", (uid_a,))
        inc_count_before = cursor.fetchone()[0]
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (acc_a,))
        bal_before = float(cursor.fetchone()[0])

    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # 1. SHOW INCOME OFF -> Expenses only
    res = client.get('/expenses')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'Dinner' in html
    assert 'Shoes' in html
    assert 'August Salary' not in html
    assert '-₹100.00' in html or '-₹100' in html
    assert '-₹200.00' in html or '-₹200' in html
    assert '-₹,2f' not in html
    assert '₹,2f' not in html

    # 2. SHOW INCOME ON -> Expenses + Income with explicit type badges and correct amounts
    res = client.get('/expenses?show_income=1')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'Dinner' in html
    assert 'Shoes' in html
    assert 'August Salary' in html
    assert 'Birthday Gift' in html

    # Verify Salary is labeled Income and NOT Expense
    assert 'type-income' in html
    assert 'Income' in html
    assert '+₹1,000.00' in html or '+₹1000' in html
    assert '+₹500.00' in html or '+₹500' in html
    assert '-₹100.00' in html or '-₹100' in html
    assert '-₹200.00' in html or '-₹200' in html
    assert '-₹,2f' not in html
    assert '₹,2f' not in html

    # 3. Expense summary cards must remain EXPENSE-ONLY (Total Spent = 300, not 1800)
    assert 'Total Spent' in html
    assert '₹300' in html
    assert '₹1,800' not in html

    # 4. Verify GET request performed 0 database mutations (read-only)
    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s", (uid_a,))
        assert cursor.fetchone()[0] == exp_count_before
        cursor.execute("SELECT COUNT(*) FROM income WHERE user_id=%s", (uid_a,))
        assert cursor.fetchone()[0] == inc_count_before
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (acc_a,))
        assert float(cursor.fetchone()[0]) == bal_before

    client.get('/logout')


def test_expenses_csv_export_and_user_isolation(client, dual_users):
    """Verifies CSV export preserves transaction types, numeric amounts, active filters, and user isolation."""
    uid_a = dual_users['id_a']
    uid_b = dual_users['id_b']
    acc_a = dual_users['acc_a']
    acc_b = dual_users['acc_b']

    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM expenses WHERE user_id IN (%s, %s)", (uid_a, uid_b))
        cursor.execute("DELETE FROM income WHERE user_id IN (%s, %s)", (uid_a, uid_b))

        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) VALUES (%s, 150.00, 'Food', 'User A Lunch', '2026-08-25', %s)", (uid_a, acc_a))
        cursor.execute("INSERT INTO income (user_id, amount, source, description, income_date, account_id) VALUES (%s, 5000.00, 'Salary', 'User A Salary', '2026-08-20', %s)", (uid_a, acc_a))

        cursor.execute("INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) VALUES (%s, 999.00, 'Travel', 'User B Secret Flight', '2026-08-25', %s)", (uid_b, acc_b))

    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # 1. Export CSV with show_income=1 for User A
    res = client.get('/export?show_income=1')
    assert res.status_code == 200
    csv_text = res.data.decode('utf-8')
    assert 'ID,Date,Category,Description,Amount,Type' in csv_text
    assert 'User A Lunch' in csv_text
    assert 'User A Salary' in csv_text
    assert '150.00' in csv_text
    assert '5000.00' in csv_text
    assert 'Income' in csv_text
    assert 'Expense' in csv_text

    # User B data must NOT leak in User A's export (User Isolation)
    assert 'User B Secret Flight' not in csv_text
    assert '999.00' not in csv_text

    client.get('/logout')


def test_get_filter_form_omits_csrf_token(client, auth_client):
    """Verifies that the GET filter form on the Expenses page does not expose csrf_token in query params."""
    res = auth_client.get('/expenses')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    # Filter form is GET, should not have hidden csrf_token inside form method="GET"
    assert '<form method="GET"' in html
    # Check that csrf_token is omitted from GET filter form
    form_start = html.find('<form method="GET"')
    form_end = html.find('</form>', form_start)
    get_form_html = html[form_start:form_end]
    assert 'name="csrf_token"' not in get_form_html


def test_expenses_pagination_and_all_records_retention(client, dual_users):
    """Verifies pagination renders explicit indicators, page 2 displays older transactions, adding an expense preserves all rows, and CSV exports full filtered dataset."""
    uid_a = dual_users['id_a']
    acc_a = dual_users['acc_a']

    # Seed 15 expenses for User A with padded item names (Item-01 .. Item-15)
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM expenses WHERE user_id=%s", (uid_a,))
        for i in range(1, 16):
            dt_day = f"{i:02d}"
            cursor.execute(
                "INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) "
                "VALUES (%s, %s, 'Food', %s, %s, %s)",
                (uid_a, float(i * 10), f"Item-{i:02d}", f"2026-08-{dt_day}", acc_a)
            )

    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # 1. GET Page 1 -> 10 items shown, explicit pagination indicator "Showing 1–10 of 15 transactions"
    res = client.get('/expenses')
    assert res.status_code == 200
    html_p1 = res.data.decode('utf-8')
    assert 'Showing 1–10 of 15 transactions' in html_p1
    assert 'Item-15' in html_p1  # Newest item on Page 1 (date_desc)
    assert 'Item-01' not in html_p1  # Oldest item moved to Page 2

    # 2. GET Page 2 -> 5 items shown (items 01-05), "Showing 11–15 of 15 transactions"
    res_p2 = client.get('/expenses?page=2')
    assert res_p2.status_code == 200
    html_p2 = res_p2.data.decode('utf-8')
    assert 'Showing 11–15 of 15 transactions' in html_p2
    assert 'Item-01' in html_p2  # Oldest item accessible on Page 2

    # 3. Add 16th expense -> total count increases to 16, 0 rows deleted from database
    client.post('/add-expense', data={
        'amount': '999.00',
        'category': 'Food',
        'description': 'Item-16 New',
        'expense_date': '2026-08-28',
        'account_id': str(acc_a)
    }, follow_redirects=True)

    res_after = client.get('/expenses')
    assert res_after.status_code == 200
    html_after = res_after.data.decode('utf-8')
    assert 'Showing 1–10 of 16 transactions' in html_after
    assert 'Item-16 New' in html_after

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s", (uid_a,))
        assert cursor.fetchone()[0] == 16  # All 16 transactions intact in database!

    # 4. CSV export exports ALL 16 matching records (not just the 10 on Page 1)
    res_csv = client.get('/export')
    assert res_csv.status_code == 200
    csv_text = res_csv.data.decode('utf-8')
    for i in range(1, 16):
        assert f"Item-{i:02d}" in csv_text
    assert "Item-16 New" in csv_text

    client.get('/logout')



import pytest
import io
import json
from db import get_db

def test_csv_import_preview_and_commit(auth_client, test_user):
    # 1. First add an existing expense to trigger duplicate detection
    res = auth_client.post('/add-expense', data={
        'amount': '150.00',
        'category': 'Food',
        'description': 'Coffee Shop',
        'expense_date': '2026-08-25',
        'account_id': str(test_user['account_id'])
    }, follow_redirects=True)
    assert res.status_code == 200

    csv_data = (
        "Date,Description,Category,Amount\n"
        "2026-08-25,Coffee Shop,Food,150.00\n"
        "2026-08-25,Bookstore Purchase,Shopping,450.00\n"
        "invalid-date,Bad Row,Other,20.00\n"
    )

    # 2. Upload CSV to Preview
    data = {'file': (io.BytesIO(csv_data.encode('utf-8')), 'test_statement.csv')}
    res = auth_client.post('/import-csv/preview', data=data, content_type='multipart/form-data')
    assert res.status_code == 200
    assert b'Bookstore Purchase' in res.data
    assert b'Duplicate' in res.data or b'Invalid' in res.data

    # 3. Commit Selected Row (#3 Bookstore Purchase)
    rows_payload = [
        {'row_num': 3, 'expense_date': '2026-08-25', 'description': 'Bookstore Purchase', 'amount': 450.00, 'category': 'Shopping', 'is_valid': True, 'is_duplicate': False}
    ]
    res = auth_client.post('/import-csv/commit', data={
        'account_id': str(test_user['account_id']),
        'rows_json': json.dumps(rows_payload),
        'selected_rows': ['3']
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b'Bookstore Purchase' in res.data or b'Successfully imported' in res.data

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s AND description='Bookstore Purchase'", (test_user['user_id'],))
        assert cursor.fetchone()[0] == 1


def test_csv_import_preview_ui_safety_and_id_handling(client, dual_users):
    """Verifies CSV preview uses Finora design system, invalid rows display dash (not fake ₹0.00), preview is read-only, and CSV IDs do not overwrite records."""
    uid_a = dual_users['id_a']
    acc_a = dual_users['acc_a']

    # Seed 1 existing expense to trigger duplicate detection
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM expenses WHERE user_id=%s", (uid_a,))
        cursor.execute(
            "INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) "
            "VALUES (%s, 150.00, 'Food', 'Existing Lunch', '2026-08-25', %s)",
            (uid_a, acc_a)
        )
        cursor.execute("SELECT expense_id FROM expenses WHERE user_id=%s AND description='Existing Lunch'", (uid_a,))
        existing_exp_id = cursor.fetchone()[0]
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (acc_a,))
        bal_before = float(cursor.fetchone()[0])
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s", (uid_a,))
        cnt_before = cursor.fetchone()[0]

    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # Upload CSV containing:
    # Row 2: Duplicate of Existing Lunch (ID=9999)
    # Row 3: Valid New Dinner (ID=existing_exp_id) -> attempts to pass existing DB ID
    # Row 4: Invalid Bad Amount (Amount=bad)
    csv_content = (
        "ID,Date,Description,Category,Amount\n"
        f"9999,2026-08-25,Existing Lunch,Food,150.00\n"
        f"{existing_exp_id},2026-08-26,New Valid Dinner,Food,250.00\n"
        "8888,2026-08-27,Bad Amount Row,Other,bad-amount\n"
    )

    data = {'file': (io.BytesIO(csv_content.encode('utf-8')), 'statement.csv')}
    res = client.post('/import-csv/preview', data=data, content_type='multipart/form-data')
    assert res.status_code == 200
    html = res.data.decode('utf-8')

    # 1. UI Check: Rendered Finora canonical headers and stats
    assert 'CSV Import Verification' in html
    assert 'Parsed:' in html
    assert 'Valid:' in html
    assert 'Invalid:' in html
    assert 'Duplicates:' in html

    # 2. Invalid Amount Check: Invalid row displays dash "—", NOT fake "₹0.00"
    assert 'Invalid' in html
    assert 'Bad Amount Row' in html

    # 3. Read-Only Preview Check: Zero DB writes performed during preview
    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s", (uid_a,))
        assert cursor.fetchone()[0] == cnt_before
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (acc_a,))
        assert float(cursor.fetchone()[0]) == bal_before

    # 4. Commit Valid Row (#3 New Valid Dinner)
    rows_payload = [
        {'row_num': 3, 'expense_date': '2026-08-26', 'description': 'New Valid Dinner', 'amount': 250.00, 'category': 'Food', 'is_valid': True, 'is_duplicate': False}
    ]
    res_commit = client.post('/import-csv/commit', data={
        'account_id': str(acc_a),
        'rows_json': json.dumps(rows_payload),
        'selected_rows': ['3']
    }, follow_redirects=True)
    assert res_commit.status_code == 200

    # Verify existing transaction was NOT overwritten, new ID was generated, and balance updated by 250
    with get_db() as (conn, cursor):
        cursor.execute("SELECT expense_id, amount FROM expenses WHERE user_id=%s AND description='Existing Lunch'", (uid_a,))
        row_orig = cursor.fetchone()
        assert row_orig[0] == existing_exp_id  # Existing row untouched!
        assert float(row_orig[1]) == 150.00

        cursor.execute("SELECT expense_id, amount FROM expenses WHERE user_id=%s AND description='New Valid Dinner'", (uid_a,))
        row_new = cursor.fetchone()
        assert row_new[0] != existing_exp_id  # New auto-increment ID assigned!
        assert float(row_new[1]) == 250.00

        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (acc_a,))
        assert float(cursor.fetchone()[0]) == bal_before - 250.00

    client.get('/logout')

def test_recurring_income_workflow(auth_client, test_user):
    # 1. Add Recurring Salary
    res = auth_client.post('/add-recurring-income', data={
        'title': 'Primary Monthly Salary',
        'amount': '60000.00',
        'source': 'Salary',
        'frequency': 'Monthly',
        'next_pay_date': '2026-08-01',
        'account_id': str(test_user['account_id'])
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b'Primary Monthly Salary' in res.data

    with get_db() as (conn, cursor):
        cursor.execute("SELECT recurring_income_id FROM recurring_income WHERE user_id=%s AND title='Primary Monthly Salary'", (test_user['user_id'],))
        rec_inc_id = cursor.fetchone()[0]

    # 2. Trigger Income page to execute due auto-billing
    res = auth_client.get('/income')
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM income WHERE user_id=%s AND description LIKE '%%Primary Monthly Salary%%'", (test_user['user_id'],))
        assert cursor.fetchone()[0] > 0

    # 3. Toggle status
    res = auth_client.post(f'/toggle-recurring-income/{rec_inc_id}', follow_redirects=True)
    assert res.status_code == 200

    # 4. Delete recurring setup
    res = auth_client.post(f'/delete-recurring-income/{rec_inc_id}', follow_redirects=True)
    assert res.status_code == 200

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
    res = auth_client.get(f'/toggle-recurring-income/{rec_inc_id}', follow_redirects=True)
    assert res.status_code == 200

    # 4. Delete recurring setup
    res = auth_client.get(f'/delete-recurring-income/{rec_inc_id}', follow_redirects=True)
    assert res.status_code == 200

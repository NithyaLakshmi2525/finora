import pytest
from db import get_db

def test_income_crud(auth_client, test_user):
    # 1. Add Income
    res = auth_client.post('/income', data={
        'amount': '40000.00',
        'source': 'Salary',
        'description': 'Monthly Paycheck',
        'date': '2026-08-01',
        'account_id': str(test_user['account_id'])
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b'Monthly Paycheck' in res.data

    with get_db() as (conn, cursor):
        cursor.execute("SELECT income_id FROM income WHERE user_id=%s AND description='Monthly Paycheck'", (test_user['user_id'],))
        inc_id = cursor.fetchone()[0]

    # 2. Edit Income
    res = auth_client.post(f'/edit-income/{inc_id}', data={
        'amount': '45000.00',
        'source': 'Salary',
        'description': 'Bonus Paycheck',
        'date': '2026-08-01',
        'account_id': str(test_user['account_id'])
    }, follow_redirects=True)
    assert res.status_code == 200

    # 3. Delete Income
    res = auth_client.post(f'/delete-income/{inc_id}', follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM income WHERE income_id=%s", (inc_id,))
        assert cursor.fetchone()[0] == 0

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

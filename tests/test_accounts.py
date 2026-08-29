import pytest
from db import get_db

def test_accounts_crud_and_atomic_balances(auth_client, test_user):
    # 1. View Accounts
    res = auth_client.get('/accounts')
    assert res.status_code == 200

    # 2. Add Account
    res = auth_client.post('/add-account', data={
        'name': 'Pytest Savings Account',
        'account_type': 'savings',
        'initial_balance': '15000.00',
        'currency': 'INR'
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b'Pytest Savings Account' in res.data

    with get_db() as (conn, cursor):
        cursor.execute("SELECT account_id, balance FROM accounts WHERE name='Pytest Savings Account' AND user_id=%s", (test_user['user_id'],))
        acc_row = cursor.fetchone()
        assert acc_row is not None
        acc_id, init_bal = acc_row[0], float(acc_row[1])
        assert init_bal == 15000.00

    # 3. Add Expense to this account & verify atomic decrement
    res = auth_client.post('/add-expense', data={
        'amount': '2500.00',
        'category': 'Shopping',
        'description': 'Pytest Clothes',
        'expense_date': '2026-08-25',
        'account_id': str(acc_id)
    }, follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (acc_id,))
        assert float(cursor.fetchone()[0]) == 12500.00

    # 4. Archive / Toggle Account
    res = auth_client.post(f'/toggle-account/{acc_id}', follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT is_active FROM accounts WHERE account_id=%s", (acc_id,))
        assert cursor.fetchone()[0] == 0

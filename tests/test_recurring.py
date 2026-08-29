import pytest
from db import get_db

def test_recurring_expenses_workflow(auth_client, test_user):
    res = auth_client.get('/recurring')
    assert res.status_code == 200

    # Add Recurring Expense
    res = auth_client.post('/recurring', data={
        'name': 'Netflix Subscription',
        'amount': '649.00',
        'category': 'Entertainment',
        'repeats': 'Monthly',
        'next_charge_date': '2026-08-25',
        'recurring_type': 'auto'
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b'Netflix Subscription' in res.data

    with get_db() as (conn, cursor):
        cursor.execute("SELECT recurring_id FROM recurring_expenses WHERE user_id=%s AND title='Netflix Subscription'", (test_user['user_id'],))
        rec_id = cursor.fetchone()[0]

    # Confirm Paid -> Auto Expense creation
    res = auth_client.post(f'/confirm-paid/{rec_id}', follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s AND recurring_id=%s", (test_user['user_id'], rec_id))
        assert cursor.fetchone()[0] > 0

    # Delete Recurring
    res = auth_client.post(f'/delete-recurring/{rec_id}', follow_redirects=True)
    assert res.status_code == 200

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

    # Update Renewal Date & Details
    res = auth_client.post(f'/update-recurring/{rec_id}', data={
        'name': 'Netflix 4K',
        'amount': '799.00',
        'category': 'Entertainment',
        'repeats': 'Monthly',
        'next_charge_date': '2026-09-25',
        'recurring_type': 'manual'
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 200
    assert res.get_json()['success'] is True

    with get_db() as (conn, cursor):
        cursor.execute("SELECT amount, DATE_FORMAT(next_charge_date, '%Y-%m-%d') FROM recurring_expenses WHERE recurring_id=%s", (rec_id,))
        row = cursor.fetchone()
        assert float(row[0]) == 799.00
        assert row[1] == '2026-09-25'

    # Toggle Recurring (Pause / Resume)
    res = auth_client.post(f'/toggle-recurring/{rec_id}', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 200
    assert res.get_json()['status'] == 'paused'

    with get_db() as (conn, cursor):
        cursor.execute("SELECT status FROM recurring_expenses WHERE recurring_id=%s", (rec_id,))
        assert cursor.fetchone()[0] == 'paused'

    # Confirm Paid via AJAX
    res = auth_client.post(f'/confirm-paid/{rec_id}', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 200
    assert res.get_json()['success'] is True

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s AND recurring_id=%s", (test_user['user_id'], rec_id))
        assert cursor.fetchone()[0] > 0

    # Delete Recurring via AJAX
    res = auth_client.post(f'/delete-recurring/{rec_id}', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 200
    assert res.get_json()['success'] is True

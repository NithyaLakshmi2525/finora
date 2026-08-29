import pytest
from db import get_db

def test_peer_settlements_and_ledger_integration(auth_client, test_user):
    res = auth_client.get('/settlements')
    assert res.status_code == 200

    # Add Peer Balance
    res = auth_client.post('/settlements', data={
        'peer_name': 'Charlie',
        'direction': 'they_owe_me',
        'amount': '1200.00',
        'reason': 'Dinner Split',
        'txn_date': '2026-08-25',
        'counts_as_expense': '1'
    }, follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT settlement_id, linked_expense_id FROM settlements WHERE user_id=%s AND peer_name='Charlie'", (test_user['user_id'],))
        row = cursor.fetchone()
        assert row is not None
        s_id, exp_id = row[0], row[1]
        assert exp_id is not None

    # Settle balance
    res = auth_client.post(f'/settle/{s_id}', follow_redirects=True)
    assert res.status_code == 200

    # Reopen settlement
    res = auth_client.post(f'/api/settlements/{s_id}/reopen', follow_redirects=True)
    assert res.status_code == 200

    # Delete settlement
    res = auth_client.post(f'/api/settlements/{s_id}/delete', follow_redirects=True)
    assert res.status_code == 200

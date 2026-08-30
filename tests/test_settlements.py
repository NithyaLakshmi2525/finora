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

    # Settle balance via AJAX
    res = auth_client.post(f'/settle/{s_id}', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 200
    json_data = res.get_json()
    assert json_data['success'] is True
    assert 'data' in json_data
    assert 'categories' in json_data['data']
    assert len(json_data['data']['categories']) > 0

    # Reopen settlement via AJAX
    res = auth_client.post(f'/api/settlements/{s_id}/reopen', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 200
    assert res.get_json()['success'] is True

    # Delete settlement via AJAX
    res = auth_client.post(f'/api/settlements/{s_id}/delete', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res.status_code == 200
    assert res.get_json()['success'] is True

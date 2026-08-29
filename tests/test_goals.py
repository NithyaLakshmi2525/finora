import pytest
from db import get_db

def test_savings_goals_and_contributions(auth_client, test_user):
    res = auth_client.get('/goals')
    assert res.status_code == 200

    # Add Goal
    res = auth_client.post('/add-goal', data={
        'goal_name': 'Emergency Fund',
        'target_amount': '50000.00',
        'target_date': '2026-12-31',
        'description': '3 Months Rainy Day Fund',
        'icon': '🎯',
        'color': '#4edea3'
    }, follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT goal_id FROM savings_goals WHERE user_id=%s AND goal_name='Emergency Fund'", (test_user['user_id'],))
        g_id = cursor.fetchone()[0]

    # View Goal Details
    res = auth_client.get(f'/goals/{g_id}')
    assert res.status_code == 200

    # Contribute to Goal
    res = auth_client.post(f'/update-goal/{g_id}', data={
        'amount': '5000.00',
        'note': 'Pytest First Deposit'
    }, follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT current_amount FROM savings_goals WHERE goal_id=%s", (g_id,))
        assert float(cursor.fetchone()[0]) == 5000.00

    # Close & Delete Goal
    res = auth_client.post(f'/close-goal/{g_id}', follow_redirects=True)
    assert res.status_code == 200

    res = auth_client.post(f'/delete-goal/{g_id}', follow_redirects=True)
    assert res.status_code == 200


def test_goal_activity_history_endpoint_and_user_isolation(client, auth_client, dual_users):
    """Verifies goal activity history endpoint, deposits/withdrawals, empty states, unauthenticated rejection, and user isolation."""
    uid_a = dual_users['id_a']
    uid_b = dual_users['id_b']

    # Seed goals and contributions
    with get_db() as (conn, cursor):
        cursor.execute("INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) VALUES (%s, 'User A Trip', 10000.00, 2000.00)", (uid_a,))
        goal_a_id = cursor.lastrowid
        cursor.execute("INSERT INTO goal_contributions (goal_id, user_id, amount, note) VALUES (%s, %s, 2500.00, 'Initial Deposit')", (goal_a_id, uid_a))
        cursor.execute("INSERT INTO goal_contributions (goal_id, user_id, amount, note) VALUES (%s, %s, -500.00, 'Emergency Withdrawal')", (goal_a_id, uid_a))

        cursor.execute("INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) VALUES (%s, 'User A Empty', 5000.00, 0.00)", (uid_a,))
        goal_empty_id = cursor.lastrowid

        cursor.execute("INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) VALUES (%s, 'User B Secret', 50000.00, 10000.00)", (uid_b,))
        goal_b_id = cursor.lastrowid

    # 1. Unauthenticated request -> 401
    client.get('/logout')
    res = client.get(f'/goals/{goal_a_id}/history')
    assert res.status_code == 401

    # Login as User A
    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # 2. User A fetches own goal activity -> 200 OK with history array
    res = client.get(f'/goals/{goal_a_id}/history')
    assert res.status_code == 200
    data = res.get_json()
    assert 'history' in data
    assert len(data['history']) == 2
    assert data['history'][0]['amount'] == -500.00
    assert data['history'][0]['note'] == 'Emergency Withdrawal'
    assert data['history'][1]['amount'] == 2500.00
    assert data['history'][1]['note'] == 'Initial Deposit'

    # 3. Empty activity goal -> 200 OK with empty history array
    res = client.get(f'/goals/{goal_empty_id}/history')
    assert res.status_code == 200
    data = res.get_json()
    assert data['history'] == []

    # 4. User isolation: User A requesting User B's goal history -> 404
    res = client.get(f'/goals/{goal_b_id}/history')
    assert res.status_code == 404

    client.get('/logout')


def test_goal_progress_percentage_formatting_and_edge_cases(client, dual_users):
    """Verifies numeric progress percentage formatting, zero target safety, partial progress, and over-target handling."""
    client.get('/logout')
    uid_a = dual_users['id_a']

    with get_db() as (conn, cursor):
        # 1. Partial progress (280 / 559 => ~50.1%)
        cursor.execute("INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) VALUES (%s, 'Partial Goal', 559.00, 280.00)", (uid_a,))
        partial_id = cursor.lastrowid

        # 2. Zero target (0.00 / 0.00 => no division by zero, 0%)
        cursor.execute("INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) VALUES (%s, 'Zero Target', 0.00, 0.00)", (uid_a,))
        zero_id = cursor.lastrowid

        # 3. Reached target (1000.00 / 1000.00 => 100%)
        cursor.execute("INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) VALUES (%s, 'Reached Goal', 1000.00, 1000.00)", (uid_a,))
        reached_id = cursor.lastrowid

        # 4. Over target (1200.00 / 1000.00 => 120%)
        cursor.execute("INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) VALUES (%s, 'Over Goal', 1000.00, 1200.00)", (uid_a,))
        over_id = cursor.lastrowid

    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # Verify Partial Goal Detail Page
    res = client.get(f'/goals/{partial_id}')
    assert res.status_code == 200
    assert b'50.1%' in res.data or b'50%' in res.data
    assert b'statProgress' in res.data

    # Verify Zero Target Detail Page
    res = client.get(f'/goals/{zero_id}')
    assert res.status_code == 200
    assert b'0%' in res.data

    # Verify Reached Goal Detail Page
    res = client.get(f'/goals/{reached_id}')
    assert res.status_code == 200
    assert b'100%' in res.data
    assert b'Target Reached' in res.data

    # Verify Over Goal Detail Page
    res = client.get(f'/goals/{over_id}')
    assert res.status_code == 200
    assert b'120%' in res.data

    client.get('/logout')


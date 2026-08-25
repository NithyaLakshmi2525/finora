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
    res = auth_client.get(f'/close-goal/{g_id}', follow_redirects=True)
    assert res.status_code == 200

    res = auth_client.get(f'/delete-goal/{g_id}', follow_redirects=True)
    assert res.status_code == 200

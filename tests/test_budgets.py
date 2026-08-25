import pytest
from db import get_db
from services.budget_service import get_user_budgets, get_budget_alerts

def test_per_category_budgets_and_alerts(auth_client, test_user):
    res = auth_client.get('/budgets')
    assert res.status_code == 200

    # Set Overall budget
    res = auth_client.post('/set-budget', data={
        'category': 'Overall',
        'monthly_limit': '30000.00',
        'currency': 'INR'
    }, follow_redirects=True)
    assert res.status_code == 200

    # Set Category budget for Transport
    res = auth_client.post('/set-budget', data={
        'category': 'Transport',
        'monthly_limit': '2000.00',
        'currency': 'INR'
    }, follow_redirects=True)
    assert res.status_code == 200

    # Log expense in Transport to trigger warning (>80%)
    res = auth_client.post('/add-expense', data={
        'amount': '1800.00',
        'category': 'Transport',
        'description': 'Pytest Cab Ride',
        'expense_date': '2026-08-25',
        'account_id': str(test_user['account_id'])
    }, follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        alerts = get_budget_alerts(cursor, test_user['user_id'])
        assert any(a['category'] == 'Transport' and a['level'] == 'warning' for a in alerts)

    # Delete budget limit
    res = auth_client.post('/delete-budget', data={'category': 'Transport'}, follow_redirects=True)
    assert res.status_code == 200

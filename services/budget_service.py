from db import get_db

def get_user_budgets(cursor, user_id):
    """Retrieves overall and per-category budgets with real-time monthly spending progress."""
    # 1. Fetch budget definitions
    cursor.execute(
        "SELECT budget_id, COALESCE(category, 'Overall'), monthly_limit, currency "
        "FROM budgets WHERE user_id=%s",
        (user_id,)
    )
    b_rows = cursor.fetchall()
    budgets_by_cat = {r[1]: {'budget_id': r[0], 'limit': float(r[2] or 0), 'currency': r[3]} for r in b_rows}

    # 2. Fetch current month's spending by category
    cursor.execute(
        "SELECT COALESCE(category, 'Other'), COALESCE(SUM(amount), 0) FROM expenses "
        "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%%Y-%%m') = DATE_FORMAT(CURRENT_DATE(), '%%Y-%%m') "
        "GROUP BY category",
        (user_id,)
    )
    cat_spending = {r[0]: float(r[1]) for r in cursor.fetchall()}

    # 3. Fetch current month's overall total spending
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses "
        "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%%Y-%%m') = DATE_FORMAT(CURRENT_DATE(), '%%Y-%%m')",
        (user_id,)
    )
    total_spent = float(cursor.fetchone()[0])

    overall_info = budgets_by_cat.get('Overall', {'budget_id': None, 'limit': 0.0, 'currency': 'INR'})
    overall_limit = overall_info['limit']
    overall_pct = (total_spent / overall_limit * 100.0) if overall_limit > 0 else 0.0
    overall_status = 'exceeded' if overall_pct >= 100 else ('warning' if overall_pct >= 80 else 'normal')

    overall_budget = {
        'budget_id': overall_info['budget_id'],
        'category': 'Overall',
        'limit': overall_limit,
        'spent': total_spent,
        'remaining': max(0.0, overall_limit - total_spent),
        'percentage': round(overall_pct, 1),
        'status': overall_status,
        'currency': overall_info['currency']
    }

    category_budgets = []
    for cat_name, b_info in budgets_by_cat.items():
        if cat_name == 'Overall':
            continue
        c_spent = cat_spending.get(cat_name, 0.0)
        c_limit = b_info['limit']
        c_pct = (c_spent / c_limit * 100.0) if c_limit > 0 else 0.0
        c_status = 'exceeded' if c_pct >= 100 else ('warning' if c_pct >= 80 else 'normal')

        category_budgets.append({
            'budget_id': b_info['budget_id'],
            'category': cat_name,
            'limit': c_limit,
            'spent': c_spent,
            'remaining': max(0.0, c_limit - c_spent),
            'percentage': round(c_pct, 1),
            'status': c_status,
            'currency': b_info['currency']
        })

    return {
        'overall': overall_budget,
        'category_budgets': category_budgets,
        'total_spent': total_spent,
        'cat_spending': cat_spending
    }

def set_budget_limit(cursor, user_id, category='Overall', limit=0.0, currency='INR'):
    """Creates or updates budget limit for overall spending or specific category."""
    cat_val = category.strip() if category else 'Overall'
    lim_val = float(limit or 0.0)

    cursor.execute(
        "SELECT budget_id FROM budgets WHERE user_id=%s AND (category=%s OR (category IS NULL AND %s='Overall'))",
        (user_id, cat_val, cat_val)
    )
    r = cursor.fetchone()
    if r:
        cursor.execute(
            "UPDATE budgets SET monthly_limit=%s, currency=%s WHERE budget_id=%s AND user_id=%s",
            (lim_val, currency.upper(), r[0], user_id)
        )
    else:
        cursor.execute(
            "INSERT INTO budgets (user_id, category, monthly_limit, currency) VALUES (%s, %s, %s, %s)",
            (user_id, cat_val, lim_val, currency.upper())
        )

def delete_budget_limit(cursor, user_id, category):
    """Deletes category budget limit."""
    cursor.execute(
        "DELETE FROM budgets WHERE user_id=%s AND category=%s",
        (user_id, category)
    )

def get_budget_alerts(cursor, user_id):
    """Calculates active budget warning and exceeded alert messages."""
    b_data = get_user_budgets(cursor, user_id)
    alerts = []
    
    overall = b_data['overall']
    if overall['limit'] > 0 and overall['percentage'] >= 80:
        alerts.append({
            'category': 'Overall',
            'percentage': overall['percentage'],
            'spent': overall['spent'],
            'limit': overall['limit'],
            'level': overall['status']
        })

    for cb in b_data['category_budgets']:
        if cb['limit'] > 0 and cb['percentage'] >= 80:
            alerts.append({
                'category': cb['category'],
                'percentage': cb['percentage'],
                'spent': cb['spent'],
                'limit': cb['limit'],
                'level': cb['status']
            })

    return alerts

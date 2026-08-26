import calendar
from datetime import date, timedelta
from services.budget_service import get_user_budgets

def build_smart_insights(cursor, user_id):
    """Generates dynamic financial insights for dashboard and monthly reports."""
    insights = []
    today = date.today()
    month_str = today.strftime('%Y-%m')
    first_of_month = date(today.year, today.month, 1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    last_of_month = date(today.year, today.month, last_day)

    # 1. Budget utilization alert (handles Overall or Category budgets)
    user_budgets_info = get_user_budgets(cursor, user_id)
    overall_b = user_budgets_info.get('overall_budget', {})
    cat_budgets = user_budgets_info.get('category_budgets', [])

    budget = 0.0
    spent = 0.0
    if overall_b.get('limit', 0.0) > 0:
        budget = overall_b['limit']
        spent = overall_b['spent']
    elif len(cat_budgets) > 0:
        budget = sum(b['limit'] for b in cat_budgets)
        spent = sum(b['spent'] for b in cat_budgets)

    if budget > 0:
        pct = (spent / budget) * 100.0
        day_pct = (today.day / float(last_day)) * 100.0

        if pct > 100:
            insights.append({
                'type': 'danger', 'icon': 'warning',
                'title': 'Budget Exceeded',
                'text': f"You have spent ₹{spent:,.0f}, which is {pct - 100:.0f}% over your monthly budget of ₹{budget:,.0f}."
            })
        elif pct > day_pct + 15:
            insights.append({
                'type': 'warning', 'icon': 'trending_up',
                'title': 'Spending Pace Alert',
                'text': f"You've used {pct:.0f}% of your budget, but we're only {day_pct:.0f}% through the month."
            })
        elif pct < day_pct - 15:
            insights.append({
                'type': 'success', 'icon': 'thumb_up',
                'title': 'Great Budget Control',
                'text': f"You've used only {pct:.0f}% of your budget while {day_pct:.0f}% of the month has passed."
            })

    # 2. Highest spending category this month
    cursor.execute(
        "SELECT category, SUM(amount) as total FROM expenses "
        "WHERE user_id=%s AND expense_date >= %s AND expense_date <= %s "
        "GROUP BY category ORDER BY total DESC LIMIT 1",
        (user_id, first_of_month, last_of_month)
    )
    cat_row = cursor.fetchone()
    if cat_row:
        insights.append({
            'type': 'info', 'icon': 'pie_chart',
            'title': 'Top Expense Category',
            'text': f"Your highest spending category this month is {cat_row[0]} at ₹{float(cat_row[1]):,.0f}."
        })

    # 3. Monthly Savings Rate Insight (Strictly current month)
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM income "
        "WHERE user_id=%s AND income_date >= %s AND income_date <= %s",
        (user_id, first_of_month, last_of_month)
    )
    m_inc = float(cursor.fetchone()[0])
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses "
        "WHERE user_id=%s AND expense_date >= %s AND expense_date <= %s",
        (user_id, first_of_month, last_of_month)
    )
    m_exp = float(cursor.fetchone()[0])

    if m_inc > 0:
        savings = m_inc - m_exp
        savings_rate = (savings / m_inc) * 100.0
        if savings_rate >= 20:
            insights.append({
                'type': 'success', 'icon': 'savings',
                'title': 'Healthy Savings Rate',
                'text': f"You're currently saving {savings_rate:.1f}% of your income this month (₹{savings:,.0f})."
            })
        elif savings_rate < 0:
            insights.append({
                'type': 'danger', 'icon': 'trending_down',
                'title': 'Negative Cash Flow',
                'text': f"Expenses exceed income by ₹{abs(savings):,.0f} this month."
            })
    elif m_exp > 0:
        insights.append({
            'type': 'info', 'icon': 'receipt_long',
            'title': 'Monthly Cash Flow',
            'text': f"You've spent ₹{m_exp:,.0f} this month with no income recorded yet."
        })

    return insights

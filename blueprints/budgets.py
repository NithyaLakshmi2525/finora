from flask import Blueprint, render_template, request, redirect, session, flash
from db import get_db
from services.ledger_service import get_categories, parse_financial_amount
from services.budget_service import (
    get_user_budgets, set_budget_limit, delete_budget_limit, get_budget_alerts
)

budgets_bp = Blueprint('budgets', __name__)

@budgets_bp.route('/budgets', methods=['GET'])
def budgets():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        budget_data = get_user_budgets(cursor, user_id)
        categories = get_categories(cursor)
        alerts = get_budget_alerts(cursor, user_id)

    return render_template(
        'budgets/budgets.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        overall=budget_data['overall'],
        category_budgets=budget_data['category_budgets'],
        total_spent=budget_data['total_spent'],
        categories=categories,
        alerts=alerts,
        active_page='budgets'
    )

@budgets_bp.route('/set-budget', methods=['POST'])
def set_budget():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    category = request.form.get('category', 'Overall').strip() or 'Overall'
    raw_limit = request.form.get('monthly_limit') or request.form.get('monthly_budget') or request.form.get('amount')
    limit, amt_err = parse_financial_amount(raw_limit, allow_zero=True)
    if amt_err:
        flash(amt_err, "error")
        next_url = request.referrer if request.referrer and ('/settings' in request.referrer or '/budgets' in request.referrer) else '/budgets'
        return redirect(next_url)

    currency = request.form.get('currency', 'INR').strip() or 'INR'

    with get_db() as (conn, cursor):
        set_budget_limit(cursor, user_id, category, limit, currency)

    flash(f"Budget limit for '{category}' updated successfully!", "success")
    next_url = request.referrer if request.referrer and ('/settings' in request.referrer or '/budgets' in request.referrer) else '/budgets'
    return redirect(next_url)

@budgets_bp.route('/delete-budget', methods=['POST'])
def delete_budget():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    category = request.form.get('category', '').strip()

    if category and category != 'Overall':
        with get_db() as (conn, cursor):
            delete_budget_limit(cursor, user_id, category)
        flash(f"Budget for category '{category}' deleted.", "success")

    return redirect('/budgets')

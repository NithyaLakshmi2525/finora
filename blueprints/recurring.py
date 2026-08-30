from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from datetime import date
from db import get_db
from services.recurring_service import advance_recurring_date, process_due_auto_charges
from services.account_service import get_default_account_id, adjust_account_on_expense_create
from services.ledger_service import get_categories, parse_financial_amount
from services.notification_service import create_notification, get_notification_prefs
from services.budget_service import set_budget_limit

recurring_bp = Blueprint('recurring', __name__)

@recurring_bp.route('/recurring', methods=['GET', 'POST'])
def recurring():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        process_due_auto_charges(user_id, cursor, conn, get_notification_prefs, create_notification)

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            if not name:
                flash("Title/Name is required.", "error")
                return redirect('/recurring')

            amount, amt_err = parse_financial_amount(request.form.get('amount'))
            if amt_err:
                flash(amt_err, "error")
                return redirect('/recurring')

            category = request.form.get('category') or None
            repeats = request.form.get('repeats', 'Monthly')
            next_date = request.form.get('next_charge_date') or date.today().isoformat()
            icon = request.form.get('icon', '⚡')
            recurring_type = request.form.get('recurring_type', 'auto')

            cursor.execute(
                "INSERT INTO recurring_expenses "
                "(user_id, title, amount, category, frequency, next_charge_date, icon, status, recurring_type) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s)",
                (user_id, name, amount, category, repeats, next_date, icon, recurring_type)
            )
            flash("Subscription / Bill added successfully!", "success")
            return redirect('/recurring')

        cursor.execute(
            "SELECT recurring_id, title, amount, category, frequency, DATE_FORMAT(next_charge_date, '%Y-%m-%d'), icon, status, recurring_type "
            "FROM recurring_expenses WHERE user_id=%s ORDER BY status ASC, next_charge_date ASC",
            (user_id,)
        )
        raw_items = cursor.fetchall()
        items = [{
            'recurring_id': r[0], 'id': r[0], 'title': r[1], 'name': r[1],
            'amount': float(r[2] or 0), 'category': r[3], 'frequency': r[4], 'repeats': r[4],
            'next_charge_date': r[5], 'next_date': r[5], 'icon': r[6] or '⚡', 'status': r[7], 'recurring_type': r[8],
            'yearly': float(r[2] or 0) * (12 if r[4] == 'Monthly' else (52 if r[4] == 'Weekly' else 1))
        } for r in raw_items]

        cursor.execute("SELECT monthly_limit FROM budgets WHERE user_id=%s LIMIT 1", (user_id,))
        b_row = cursor.fetchone()
        monthly_budget = float(b_row[0]) if (b_row and b_row[0]) else 0.0

        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%Y-%m') = DATE_FORMAT(CURRENT_DATE(), '%Y-%m')",
            (user_id,)
        )
        current_spent = float(cursor.fetchone()[0])

        categories = get_categories(cursor)
        monthly_total = sum(r['amount'] for r in items if r['status'] == 'active')
        active_count = sum(1 for r in items if r['status'] == 'active')
        budget_percentage = (current_spent / monthly_budget * 100.0) if monthly_budget > 0 else 0.0

    return render_template(
        'recurring/recurring.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        items=items,
        monthly_budget=monthly_budget,
        current_spent=current_spent,
        budget_percentage=budget_percentage,
        monthly_total=monthly_total,
        active_count=active_count,
        categories=categories,
        active_page='recurring'
    )

@recurring_bp.route('/delete-recurring/<int:id>', methods=['POST'])
def delete_recurring(id):
    if 'user_id' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/login')
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM recurring_expenses WHERE recurring_id=%s AND user_id=%s", (id, session['user_id']))

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'success': True, 'message': 'Subscription deleted successfully!'})

    flash("Subscription deleted successfully!", "success")
    return redirect('/recurring')

@recurring_bp.route('/confirm-paid/<int:id>', methods=['POST'])
def confirm_paid(id):
    if 'user_id' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/login')
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT title, amount, category, frequency, next_charge_date FROM recurring_expenses "
            "WHERE recurring_id=%s AND user_id=%s",
            (id, user_id)
        )
        item = cursor.fetchone()
        if not item:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
                return jsonify({'error': 'Recurring expense not found.'}), 404
            flash("Recurring expense not found.", "error")
            return redirect('/recurring')

        title, amount, category, frequency, next_date = item
        raw_charge_date = request.form.get('charge_date') or request.form.get('expense_date')
        if raw_charge_date:
            try:
                charge_date = date.fromisoformat(str(raw_charge_date))
            except ValueError:
                charge_date = next_date if next_date else date.today()
        else:
            charge_date = next_date if next_date else date.today()
        account_id = get_default_account_id(cursor, user_id)

        # Idempotency check: check if an expense was already logged for this charge date
        cursor.execute(
            "SELECT expense_id FROM expenses WHERE recurring_id=%s AND expense_date=%s AND user_id=%s",
            (id, charge_date, user_id)
        )
        already_paid = cursor.fetchone()

        if already_paid:
            msg = f"Payment for {title} on {charge_date} was already logged!"
        else:
            cursor.execute(
                "INSERT INTO expenses (amount, category, description, expense_date, user_id, recurring_id, account_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (amount, category or 'Other', f"{title} (bill paid)", charge_date, user_id, id, account_id)
            )
            adjust_account_on_expense_create(cursor, account_id, amount)

            new_next_date = advance_recurring_date(charge_date, frequency)
            cursor.execute(
                "UPDATE recurring_expenses SET next_charge_date=%s WHERE recurring_id=%s AND user_id=%s",
                (new_next_date, id, user_id)
            )
            msg = f"Paid {title}! ₹{float(amount):,.0f} logged to Expenses."

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'success': True, 'message': msg})

    flash(msg, "success")
    return redirect('/recurring')

@recurring_bp.route('/toggle-recurring/<int:id>', methods=['POST'])
def toggle_recurring(id):
    if 'user_id' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/login')
    user_id = session['user_id']
    new_status = 'active'
    with get_db() as (conn, cursor):
        cursor.execute("SELECT status FROM recurring_expenses WHERE recurring_id=%s AND user_id=%s", (id, user_id))
        row = cursor.fetchone()
        if row:
            new_status = 'paused' if row[0] == 'active' else 'active'
            cursor.execute(
                "UPDATE recurring_expenses SET status=%s WHERE recurring_id=%s AND user_id=%s",
                (new_status, id, user_id)
            )
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
                return jsonify({'error': 'Subscription not found'}), 404

    msg = f"Subscription marked as {new_status}."
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'success': True, 'status': new_status, 'message': msg})

    flash(msg, "success")
    return redirect('/recurring')

@recurring_bp.route('/update-recurring/<int:id>', methods=['POST'])
def update_recurring(id):
    if 'user_id' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/login')
    name = request.form.get('name', '').strip()
    try:
        amount = float(request.form.get('amount', 0))
    except ValueError:
        amount = 0.0

    if not name or amount <= 0:
        err = "Valid name and positive amount are required."
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': err}), 400
        flash(err, "error")
        return redirect('/recurring')

    category = request.form.get('category') or None
    frequency = request.form.get('repeats', 'Monthly')
    icon = request.form.get('icon', '⚡')
    next_date = request.form.get('next_charge_date') or None
    if next_date:
        try:
            from datetime import datetime as dt_cls
            dt_cls.strptime(next_date, '%Y-%m-%d')
        except ValueError:
            next_date = None

    recurring_type = request.form.get('recurring_type', 'auto')

    with get_db() as (conn, cursor):
        cursor.execute(
            "UPDATE recurring_expenses "
            "SET title=%s, amount=%s, category=%s, frequency=%s, next_charge_date=%s, icon=%s, recurring_type=%s "
            "WHERE recurring_id=%s AND user_id=%s",
            (name, amount, category, frequency, next_date, icon, recurring_type, id, session['user_id'])
        )

    msg = "Subscription updated successfully!"
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'success': True, 'message': msg})

    flash(msg, "success")
    return redirect('/recurring')

@recurring_bp.route('/set-budget', methods=['POST'])
def set_budget():
    if 'user_id' not in session:
        return redirect('/login')
    raw_budget = request.form.get('monthly_budget') or request.form.get('monthly_limit') or request.form.get('amount')
    new_budget, amt_err = parse_financial_amount(raw_budget, allow_zero=True)
    if amt_err:
        flash(amt_err, "error")
        return redirect(request.referrer or '/recurring')

    category = request.form.get('category', 'Overall').strip() or 'Overall'
    currency = request.form.get('currency', 'INR').strip() or 'INR'
    user_id = session['user_id']

    with get_db() as (conn, cursor):
        set_budget_limit(cursor, user_id, category, new_budget, currency)

    flash("Budget updated successfully!", "success")
    return redirect(request.referrer or '/recurring')

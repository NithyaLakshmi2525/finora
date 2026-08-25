import json
from flask import Blueprint, render_template, request, redirect, session, flash, Response, jsonify
from datetime import date
from db import get_db
from services.ledger_service import get_categories, build_income_context, csv_escape
from services.settlement_service import balance_expense_description
from services.account_service import (
    get_user_accounts, get_default_account_id,
    adjust_account_on_expense_create, adjust_account_on_expense_delete, adjust_account_on_expense_update,
    adjust_account_on_income_create, adjust_account_on_income_delete, adjust_account_on_income_update
)
from services.csv_import_service import parse_and_preview_csv, commit_imported_csv_rows
from services.recurring_income_service import (
    get_user_recurring_income, add_recurring_income, process_due_recurring_income,
    toggle_recurring_income_status, delete_recurring_income
)

expenses_bp = Blueprint('expenses', __name__)

@expenses_bp.route('/add-expense', methods=['GET', 'POST'])
def add_expense():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    if request.method == 'POST':
        amount = float(request.form['amount'])
        category = request.form['category']
        description = request.form.get('description', '')
        expense_date = request.form['expense_date']
        raw_acc_id = request.form.get('account_id')

        with get_db() as (conn, cursor):
            account_id = int(raw_acc_id) if raw_acc_id else get_default_account_id(cursor, user_id)
            cursor.execute(
                "INSERT INTO expenses (amount, category, description, expense_date, user_id, account_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (amount, category, description, expense_date, user_id, account_id)
            )
            adjust_account_on_expense_create(cursor, account_id, amount)

        flash("Expense added successfully!", "success")
        return redirect('/expenses')

    with get_db() as (conn, cursor):
        categories = get_categories(cursor)
        user_accounts = get_user_accounts(cursor, user_id)

    return render_template(
        'expenses/expenses.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        categories=categories,
        user_accounts=user_accounts,
        active_page='expenses'
    )

@expenses_bp.route('/expenses')
def expenses():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    category = request.args.get('category', 'all')
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 10

    with get_db() as (conn, cursor):
        query = "SELECT expense_id, expense_date, category, description, amount, recurring_id, account_id FROM expenses WHERE user_id=%s"
        params = [user_id]

        if category != 'all':
            query += " AND category=%s"
            params.append(category)

        if search:
            query += " AND (description LIKE %s OR category LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%"])

        cursor.execute(f"SELECT COUNT(*) FROM ({query}) as count_table", tuple(params))
        total_items = cursor.fetchone()[0]

        total_pages = max(1, (total_items + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page

        query += " ORDER BY expense_date DESC, expense_id DESC LIMIT %s OFFSET %s"
        params.extend([per_page, offset])

        cursor.execute(query, tuple(params))
        expense_list = cursor.fetchall()

        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%Y-%m') = DATE_FORMAT(CURRENT_DATE(), '%Y-%m')",
            (user_id,)
        )
        current_month_total = float(cursor.fetchone()[0])

        cursor.execute(
            "SELECT COALESCE(MAX(amount), 0) FROM expenses WHERE user_id=%s",
            (user_id,)
        )
        highest_amount = float(cursor.fetchone()[0])

        categories = get_categories(cursor)
        user_accounts = get_user_accounts(cursor, user_id)

    return render_template(
        'expenses/expenses.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        expenses=expense_list,
        categories=categories,
        user_accounts=user_accounts,
        current_category=category,
        search_query=search,
        page=page,
        total_pages=total_pages,
        total_items=total_items,
        current_month_total=current_month_total,
        total_expenses=current_month_total,
        total_spent=current_month_total,
        avg_expense=current_month_total,
        highest_amount=highest_amount,
        active_page='expenses'
    )

@expenses_bp.route('/delete/<int:expense_id>')
def delete_expense(expense_id):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute("SELECT account_id, amount FROM expenses WHERE expense_id=%s AND user_id=%s", (expense_id, user_id))
        row = cursor.fetchone()
        if not row:
            flash("Expense record not found.", "error")
            return redirect('/expenses')

        acc_id, exp_amt = row[0], float(row[1])
        cursor.execute("DELETE FROM expenses WHERE expense_id=%s AND user_id=%s", (expense_id, user_id))
        cursor.execute(
            "UPDATE settlements SET linked_expense_id=NULL, counts_as_expense=0 "
            "WHERE linked_expense_id=%s AND user_id=%s",
            (expense_id, user_id)
        )
        adjust_account_on_expense_delete(cursor, acc_id, exp_amt)

    flash("Expense deleted successfully!", "success")
    return redirect('/expenses')

@expenses_bp.route('/edit/<int:expense_id>', methods=['GET', 'POST'])
def edit_expense(expense_id):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        if request.method == 'POST':
            amount = float(request.form['amount'])
            category = request.form['category']
            description = request.form.get('description', '')
            expense_date = request.form['expense_date']
            raw_acc_id = request.form.get('account_id')

            cursor.execute("SELECT account_id, amount FROM expenses WHERE expense_id=%s AND user_id=%s", (expense_id, user_id))
            old_row = cursor.fetchone()
            old_acc_id, old_amt = (old_row[0], float(old_row[1])) if old_row else (None, 0.0)

            new_acc_id = int(raw_acc_id) if raw_acc_id else (old_acc_id or get_default_account_id(cursor, user_id))

            cursor.execute(
                "UPDATE expenses SET amount=%s, category=%s, description=%s, expense_date=%s, account_id=%s "
                "WHERE expense_id=%s AND user_id=%s",
                (amount, category, description, expense_date, new_acc_id, expense_id, user_id)
            )
            adjust_account_on_expense_update(cursor, old_acc_id, old_amt, new_acc_id, amount)

            cursor.execute(
                "SELECT settlement_id, amount FROM settlements "
                "WHERE linked_expense_id=%s AND user_id=%s AND counts_as_expense=1",
                (expense_id, user_id)
            )
            s_row = cursor.fetchone()
            if s_row:
                s_id, s_amt = s_row[0], float(s_row[1])
                new_s_amt = amount if s_amt >= 0 else -amount
                cursor.execute(
                    "UPDATE settlements SET amount=%s, balance_date=%s WHERE settlement_id=%s AND user_id=%s",
                    (new_s_amt, expense_date, s_id, user_id)
                )

            flash("Expense updated successfully!", "success")
            return redirect('/expenses')

        cursor.execute("SELECT * FROM expenses WHERE expense_id=%s AND user_id=%s", (expense_id, user_id))
        expense = cursor.fetchone()
        categories = get_categories(cursor)
        user_accounts = get_user_accounts(cursor, user_id)

    if not expense:
        flash("Expense not found.", "error")
        return redirect('/expenses')

    return render_template(
        'expenses/expenses.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        edit_expense=expense,
        categories=categories,
        user_accounts=user_accounts,
        active_page='expenses'
    )

# ----------------- CSV IMPORT ROUTES -----------------

@expenses_bp.route('/import-csv/preview', methods=['POST'])
def import_csv_preview():
    if 'user_id' not in session:
        return redirect('/login')

    if 'file' not in request.files:
        flash("No file uploaded.", "error")
        return redirect('/expenses')

    file = request.files['file']
    if not file.filename or not file.filename.endswith('.csv'):
        flash("Please upload a valid CSV file.", "error")
        return redirect('/expenses')

    user_id = session['user_id']
    content_str = file.read().decode('utf-8', errors='ignore')

    with get_db() as (conn, cursor):
        result = parse_and_preview_csv(cursor, user_id, content_str)
        user_accounts = get_user_accounts(cursor, user_id)

    if result.get('error'):
        flash(result['error'], "error")
        return redirect('/expenses')

    valid_count = sum(1 for r in result['rows'] if r['is_valid'])
    dup_count = sum(1 for r in result['rows'] if r['is_duplicate'])

    return render_template(
        'expenses/csv_preview.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        rows=result['rows'],
        rows_json=json.dumps(result['rows']),
        valid_count=valid_count,
        dup_count=dup_count,
        user_accounts=user_accounts,
        active_page='expenses'
    )

@expenses_bp.route('/import-csv/commit', methods=['POST'])
def import_csv_commit():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    raw_acc_id = request.form.get('account_id')
    rows_json = request.form.get('rows_json', '[]')
    selected_indices = [int(i) for i in request.form.getlist('selected_rows')]

    try:
        all_rows = json.loads(rows_json)
    except Exception:
        all_rows = []

    selected_rows = [r for r in all_rows if r.get('row_num') in selected_indices]

    with get_db() as (conn, cursor):
        account_id = int(raw_acc_id) if raw_acc_id else get_default_account_id(cursor, user_id)
        imported_count = commit_imported_csv_rows(cursor, user_id, selected_rows, account_id)

    flash(f"Successfully imported {imported_count} expense transactions from CSV!", "success")
    return redirect('/expenses')

# ----------------- INCOME ROUTES -----------------

@expenses_bp.route('/add-income', methods=['GET', 'POST'])
@expenses_bp.route('/income', methods=['GET', 'POST'])
def income():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        process_due_recurring_income(user_id, cursor, conn)

        if request.method == 'POST':
            amount = float(request.form['amount'])
            source = request.form['source']
            description = request.form.get('description', '')
            income_date = request.form.get('date') or request.form.get('income_date') or date.today().isoformat()
            raw_acc_id = request.form.get('account_id')

            account_id = int(raw_acc_id) if raw_acc_id else get_default_account_id(cursor, user_id)
            cursor.execute(
                "INSERT INTO income (user_id, amount, source, description, income_date, account_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, amount, source, description, income_date, account_id)
            )
            adjust_account_on_income_create(cursor, account_id, amount)

            flash("Income entry added successfully!", "success")
            return redirect('/income')

        page = request.args.get('page', 1, type=int)
        context = build_income_context(cursor, user_id, page=page)
        user_accounts = get_user_accounts(cursor, user_id)
        recurring_incomes = get_user_recurring_income(cursor, user_id)

    return render_template(
        'expenses/income.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        user_accounts=user_accounts,
        recurring_incomes=recurring_incomes,
        active_page='income',
        **context
    )

@expenses_bp.route('/edit-income/<int:income_id>', methods=['GET', 'POST'])
def edit_income(income_id):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        if request.method == 'POST':
            amount = float(request.form['amount'])
            source = request.form['source']
            description = request.form.get('description', '')
            date_val = request.form['date']
            raw_acc_id = request.form.get('account_id')

            cursor.execute("SELECT account_id, amount FROM income WHERE income_id=%s AND user_id=%s", (income_id, user_id))
            old_row = cursor.fetchone()
            old_acc_id, old_amt = (old_row[0], float(old_row[1])) if old_row else (None, 0.0)

            new_acc_id = int(raw_acc_id) if raw_acc_id else (old_acc_id or get_default_account_id(cursor, user_id))

            cursor.execute(
                "UPDATE income SET amount=%s, source=%s, description=%s, income_date=%s, account_id=%s "
                "WHERE income_id=%s AND user_id=%s",
                (amount, source, description, date_val, new_acc_id, income_id, user_id)
            )
            adjust_account_on_income_update(cursor, old_acc_id, old_amt, new_acc_id, amount)

            flash("Income updated successfully!", "success")
            return redirect('/income')

        cursor.execute(
            "SELECT income_id, DATE_FORMAT(income_date,'%Y-%m-%d'), source, description, amount, "
            "DATE_FORMAT(income_date,'%d %b %Y'), account_id "
            "FROM income WHERE income_id=%s AND user_id=%s",
            (income_id, user_id)
        )
        entry = cursor.fetchone()
        if not entry:
            flash("Income entry not found.", "error")
            return redirect('/income')

        context = build_income_context(cursor, user_id, page=1)
        user_accounts = get_user_accounts(cursor, user_id)
        recurring_incomes = get_user_recurring_income(cursor, user_id)

    return render_template(
        'expenses/income.html',
        edit_entry=entry,
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        user_accounts=user_accounts,
        recurring_incomes=recurring_incomes,
        active_page='income',
        **context
    )

@expenses_bp.route('/delete-income/<int:income_id>')
def delete_income(income_id):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute("SELECT account_id, amount FROM income WHERE income_id=%s AND user_id=%s", (income_id, user_id))
        inc_row = cursor.fetchone()
        if inc_row:
            acc_id, inc_amt = inc_row[0], float(inc_row[1])
            cursor.execute("DELETE FROM income WHERE income_id=%s AND user_id=%s", (income_id, user_id))
            adjust_account_on_income_delete(cursor, acc_id, inc_amt)

    flash("Income entry deleted successfully!", "success")
    return redirect('/income')

# ----------------- RECURRING INCOME ROUTES -----------------

@expenses_bp.route('/add-recurring-income', methods=['POST'])
def add_rec_income():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    title = request.form['title']
    amount = float(request.form['amount'])
    source = request.form.get('source', 'Salary')
    frequency = request.form.get('frequency', 'Monthly')
    next_pay_date = request.form.get('next_pay_date') or date.today().isoformat()
    raw_acc_id = request.form.get('account_id')

    with get_db() as (conn, cursor):
        account_id = int(raw_acc_id) if raw_acc_id else get_default_account_id(cursor, user_id)
        add_recurring_income(cursor, user_id, title, amount, source, frequency, next_pay_date, '💼', account_id)

    flash(f"Recurring income '{title}' added successfully!", "success")
    return redirect('/income')

@expenses_bp.route('/toggle-recurring-income/<int:rec_inc_id>')
def toggle_rec_income(rec_inc_id):
    if 'user_id' not in session:
        return redirect('/login')

    with get_db() as (conn, cursor):
        toggle_recurring_income_status(cursor, session['user_id'], rec_inc_id)

    flash("Recurring income status toggled.", "success")
    return redirect('/income')

@expenses_bp.route('/delete-recurring-income/<int:rec_inc_id>')
def delete_rec_income(rec_inc_id):
    if 'user_id' not in session:
        return redirect('/login')

    with get_db() as (conn, cursor):
        delete_recurring_income(cursor, session['user_id'], rec_inc_id)

    flash("Recurring income setup deleted.", "success")
    return redirect('/income')

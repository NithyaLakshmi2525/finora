from flask import Blueprint, render_template, request, redirect, session, flash, Response
from db import get_db
from services.ledger_service import get_categories, build_income_context, csv_escape
from services.settlement_service import balance_expense_description

expenses_bp = Blueprint('expenses', __name__)

@expenses_bp.route('/add-expense', methods=['GET', 'POST'])
def add_expense():
    if 'user_id' not in session:
        return redirect('/login')

    if request.method == 'POST':
        amount = request.form['amount']
        category = request.form['category']
        description = request.form.get('description', '')
        expense_date = request.form['expense_date']

        with get_db() as (conn, cursor):
            cursor.execute(
                "INSERT INTO expenses (amount, category, description, expense_date, user_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (amount, category, description, expense_date, session['user_id'])
            )

        flash("Expense added successfully!", "success")
        return redirect('/expenses')

    with get_db() as (conn, cursor):
        categories = get_categories(cursor)

    return render_template(
        'expenses/expenses.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        categories=categories,
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
        query = "SELECT expense_id, expense_date, category, description, amount, recurring_id FROM expenses WHERE user_id=%s"
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

    return render_template(
        'expenses/expenses.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        expenses=expense_list,
        categories=categories,
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
        cursor.execute("SELECT recurring_id FROM expenses WHERE expense_id=%s AND user_id=%s", (expense_id, user_id))
        row = cursor.fetchone()
        if not row:
            flash("Expense record not found.", "error")
            return redirect('/expenses')

        cursor.execute("DELETE FROM expenses WHERE expense_id=%s AND user_id=%s", (expense_id, user_id))
        cursor.execute(
            "UPDATE settlements SET linked_expense_id=NULL, counts_as_expense=0 "
            "WHERE linked_expense_id=%s AND user_id=%s",
            (expense_id, user_id)
        )

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

            cursor.execute(
                "UPDATE expenses SET amount=%s, category=%s, description=%s, expense_date=%s "
                "WHERE expense_id=%s AND user_id=%s",
                (amount, category, description, expense_date, expense_id, user_id)
            )

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

    if not expense:
        flash("Expense not found.", "error")
        return redirect('/expenses')

    return render_template(
        'expenses/expenses.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        edit_expense=expense,
        categories=categories,
        active_page='expenses'
    )

@expenses_bp.route('/summary')
def summary():
    if 'user_id' not in session:
        return redirect('/login')
    return redirect('/monthly-report')

@expenses_bp.route('/breakdown')
def breakdown():
    if 'user_id' not in session:
        return redirect('/login')
    return redirect('/expenses')

# ----------------- INCOME ROUTES -----------------

@expenses_bp.route('/add-income', methods=['GET', 'POST'])
@expenses_bp.route('/income', methods=['GET', 'POST'])
def income():
    if 'user_id' not in session:
        return redirect('/login')

    if request.method == 'POST':
        amount = request.form['amount']
        source = request.form['source']
        description = request.form.get('description', '')
        income_date = request.form.get('date') or request.form.get('income_date') or date.today().isoformat()

        with get_db() as (conn, cursor):
            cursor.execute(
                "INSERT INTO income (user_id, amount, source, description, income_date) "
                "VALUES (%s, %s, %s, %s, %s)",
                (session['user_id'], amount, source, description, income_date)
            )

        flash("Income entry added successfully!", "success")
        return redirect('/income')

    page = request.args.get('page', 1, type=int)
    with get_db() as (conn, cursor):
        context = build_income_context(cursor, session['user_id'], page=page)

    return render_template(
        'expenses/income.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
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
            amount = request.form['amount']
            source = request.form['source']
            description = request.form.get('description', '')
            date_val = request.form['date']
            cursor.execute(
                "UPDATE income SET amount=%s, source=%s, description=%s, income_date=%s "
                "WHERE income_id=%s AND user_id=%s",
                (amount, source, description, date_val, income_id, user_id)
            )
            flash("Income updated successfully!", "success")
            return redirect('/income')

        cursor.execute(
            "SELECT income_id, DATE_FORMAT(income_date,'%Y-%m-%d'), source, description, amount, "
            "DATE_FORMAT(income_date,'%d %b %Y') "
            "FROM income WHERE income_id=%s AND user_id=%s",
            (income_id, user_id)
        )
        entry = cursor.fetchone()
        if not entry:
            flash("Income entry not found.", "error")
            return redirect('/income')

        context = build_income_context(cursor, user_id, page=1)

    return render_template(
        'expenses/income.html',
        edit_entry=entry,
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        active_page='income',
        **context
    )

@expenses_bp.route('/delete-income/<int:income_id>')
def delete_income(income_id):
    if 'user_id' not in session:
        return redirect('/login')

    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM income WHERE income_id=%s AND user_id=%s", (income_id, session['user_id']))

    flash("Income entry deleted successfully!", "success")
    return redirect('/income')

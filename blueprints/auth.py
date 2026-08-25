from flask import Blueprint, render_template, request, redirect, session, flash, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
from db import get_db
from services.insights_service import build_smart_insights
from services.goal_service import build_goal_summary
from services.settlement_service import build_settlements_summary
from services.notification_service import check_opportunistic_notifications, get_notification_prefs
from services.recurring_service import process_due_auto_charges

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/landing')
def landing():
    if 'user_id' in session:
        return redirect('/')
    return render_template('finora.html')

@auth_bp.route('/')
def home():
    if 'user_id' not in session:
        return redirect('/landing')

    user_id = session['user_id']

    with get_db() as (conn, cursor):
        process_due_auto_charges(user_id, cursor, conn, get_notification_prefs, None)
        check_opportunistic_notifications(cursor, user_id)

        # Monthly Budget & Expenses
        cursor.execute("SELECT monthly_limit FROM budgets WHERE user_id=%s LIMIT 1", (user_id,))
        b_row = cursor.fetchone()
        monthly_budget = float(b_row[0]) if (b_row and b_row[0]) else 0.0

        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%Y-%m') = DATE_FORMAT(CURRENT_DATE(), '%Y-%m')",
            (user_id,)
        )
        total_expenses = float(cursor.fetchone()[0])

        # Monthly Income
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM income "
            "WHERE user_id=%s AND DATE_FORMAT(income_date, '%Y-%m') = DATE_FORMAT(CURRENT_DATE(), '%Y-%m')",
            (user_id,)
        )
        monthly_income = float(cursor.fetchone()[0])

        net_cash_flow = monthly_income - total_expenses
        savings_rate = ((monthly_income - total_expenses) / monthly_income * 100.0) if monthly_income > 0 else 0.0

        # Recent Transactions
        cursor.execute(
            "SELECT DATE_FORMAT(expense_date, '%d %b %Y'), category, description, amount "
            "FROM expenses WHERE user_id=%s ORDER BY expense_date DESC, expense_id DESC LIMIT 5",
            (user_id,)
        )
        raw_recent = cursor.fetchall()
        recent_expenses = [(r[0], r[1], r[2], float(r[3] or 0)) for r in raw_recent]

        # Category Breakdown for Chart
        cursor.execute(
            "SELECT category, SUM(amount) FROM expenses "
            "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%Y-%m') = DATE_FORMAT(CURRENT_DATE(), '%Y-%m') "
            "GROUP BY category",
            (user_id,)
        )
        cat_data = cursor.fetchall()
        chart_labels = [row[0] for row in cat_data]
        chart_values = [float(row[1]) for row in cat_data]

        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%Y-%m') = DATE_FORMAT(CURRENT_DATE() - INTERVAL 1 MONTH, '%Y-%m')",
            (user_id,)
        )
        last_month = float(cursor.fetchone()[0])

        change_percentage = round(((total_expenses - last_month) / last_month * 100.0), 1) if last_month > 0 else 0.0
        insights = build_smart_insights(cursor, user_id)
        goal_summary = build_goal_summary(cursor, user_id)
        settlements_summary = build_settlements_summary(cursor, user_id)
        budget_percentage = (total_expenses / monthly_budget * 100.0) if monthly_budget > 0 else 0.0

    return render_template(
        'dashboard/dashboard.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        monthly_budget=monthly_budget,
        budget_percentage=budget_percentage,
        total_expenses=total_expenses,
        total_spent=total_expenses,
        this_month=total_expenses,
        last_month=last_month,
        change_percentage=change_percentage,
        monthly_income=monthly_income,
        total_income=monthly_income,
        net_cash_flow=net_cash_flow,
        projected_position=net_cash_flow,
        savings_rate=savings_rate,
        recent_expenses=recent_expenses,
        chart_labels=chart_labels,
        chart_values=chart_values,
        insights=insights,
        goal_summary=goal_summary,
        settlements_summary=settlements_summary,
        active_page='dashboard'
    )

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect('/')
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if password != confirm_password:
            flash("Passwords do not match. Please try again.", "error")
            return render_template('auth/register.html')

        hashed_password = generate_password_hash(password)
        with get_db() as (conn, cursor):
            cursor.execute("SELECT user_id FROM users WHERE username=%s OR email=%s", (username, email))
            if cursor.fetchone():
                flash("Username or email already registered.", "error")
                return render_template('auth/register.html')

            cursor.execute(
                "INSERT INTO users (username, password, email) VALUES (%s, %s, %s)",
                (username, hashed_password, email)
            )

        flash("Registration successful! Please log in.", "success")
        return redirect('/login')

    return render_template('auth/register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect('/')
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']

        with get_db() as (conn, cursor):
            cursor.execute("SELECT user_id, username, password, display_name FROM users WHERE email=%s", (email,))
            user = cursor.fetchone()

        if user and check_password_hash(user[2], password):
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['display_name'] = user[3] or user[1]
            flash("Welcome back!", "success")
            return redirect('/')
        else:
            flash("Invalid email or password. Please try again.", "error")

    return render_template('auth/login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect('/login')

@auth_bp.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect('/login')
    with get_db() as (conn, cursor):
        cursor.execute("SELECT username, email, display_name FROM users WHERE user_id=%s", (session['user_id'],))
        user_info = cursor.fetchone()

    return render_template(
        'user/profile.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        user_info=user_info,
        active_page='profile'
    )

@auth_bp.route('/profile/update-name', methods=['POST'])
def profile_update_name():
    if 'user_id' not in session:
        return redirect('/login')
    display_name = request.form['display_name'].strip()
    if not display_name:
        flash("Display name cannot be empty.", "error")
        return redirect('/profile')

    with get_db() as (conn, cursor):
        cursor.execute("UPDATE users SET display_name=%s WHERE user_id=%s", (display_name, session['user_id']))

    session['display_name'] = display_name
    flash("Display name updated successfully!", "success")
    return redirect('/profile')

@auth_bp.route('/profile/change-password', methods=['POST'])
def profile_change_password():
    if 'user_id' not in session:
        return redirect('/login')
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    if not current_password or not new_password or not confirm_password:
        flash("All password fields are required.", "error")
        return redirect('/profile')
    if new_password != confirm_password:
        flash("New passwords do not match. Please try again.", "error")
        return redirect('/profile')
    if len(new_password) < 8:
        flash("New password must be at least 8 characters.", "error")
        return redirect('/profile')

    with get_db() as (conn, cursor):
        cursor.execute("SELECT password FROM users WHERE user_id=%s", (session['user_id'],))
        row = cursor.fetchone()
        if not row or not check_password_hash(row[0], current_password):
            flash("Current password is incorrect.", "error")
            return redirect('/profile')

        hashed = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password=%s WHERE user_id=%s", (hashed, session['user_id']))

    flash("Password updated successfully!", "success")
    return redirect('/profile')

@auth_bp.route('/profile/delete-account', methods=['POST'])
def profile_delete_account():
    if 'user_id' not in session:
        return redirect('/login')
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
    session.clear()
    flash("Your account and all associated data have been permanently deleted.", "success")
    return redirect('/login')

@auth_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session:
        return redirect('/login')
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        if request.method == 'POST':
            budget_alerts = 1 if request.form.get('budget_alerts') else 0
            recurring_reminders = 1 if request.form.get('recurring_reminders') else 0
            goal_milestones = 1 if request.form.get('goal_milestones') else 0

            cursor.execute(
                "UPDATE notification_preferences SET budget_alerts=%s, recurring_reminders=%s, goal_milestones=%s "
                "WHERE user_id=%s",
                (budget_alerts, recurring_reminders, goal_milestones, user_id)
            )
            flash("Settings saved successfully!", "success")
            return redirect('/settings')

        prefs = get_notification_prefs(cursor, user_id)
        cursor.execute("SELECT monthly_limit FROM budgets WHERE user_id=%s LIMIT 1", (user_id,))
        b_row = cursor.fetchone()
        monthly_budget = float(b_row[0]) if (b_row and b_row[0]) else 0.0

    return render_template(
        'user/settings.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        prefs=prefs,
        notif_prefs=prefs,
        monthly_budget=monthly_budget,
        active_page='settings'
    )

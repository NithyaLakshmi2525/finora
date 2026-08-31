from flask import Blueprint, render_template, request, redirect, session, flash, url_for, current_app
from werkzeug.security import generate_password_hash, check_password_hash
import re
import secrets
import hashlib
from datetime import datetime, timedelta
from db import get_db
from services.insights_service import build_smart_insights
from services.goal_service import build_goal_summary, get_dashboard_goals
from services.settlement_service import build_settlements_summary
from services.notification_service import check_opportunistic_notifications, get_notification_prefs
from services.recurring_service import process_due_auto_charges
from services.account_service import reset_user_financial_data
from services.budget_service import get_user_budgets
from services.email_service import send_password_reset_email
from services.rate_limiter import check_rate_limit, reset_rate_limit, get_client_ip
from config import Config

auth_bp = Blueprint('auth', __name__)

def validate_password_policy(password: str) -> tuple[bool, str]:
    """
    Server-side authoritative password policy validation:
    1. Password length must be at least 8 characters.
    2. Password must contain at least one uppercase letter ([A-Z]).
    3. Password must contain at least one number ([0-9]).
    4. Password must not be entirely whitespace.
    5. Spaces inside password are allowed (internal spaces preserved, not stripped before hashing).
    Returns (is_valid, error_message).
    """
    if not password or not password.strip():
        return False, "Password cannot be empty or only whitespace."
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r'[0-9]', password):
        return False, "Password must contain at least one number."
    return True, ""

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

        # Lifetime Summary
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM income WHERE user_id=%s", (user_id,))
        lifetime_income = float(cursor.fetchone()[0])

        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=%s", (user_id,))
        lifetime_expenses = float(cursor.fetchone()[0])

        # Monthly Income & Monthly Expenses (Current Month)
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM income "
            "WHERE user_id=%s AND DATE_FORMAT(income_date, '%Y-%m') = DATE_FORMAT(CURRENT_DATE(), '%Y-%m')",
            (user_id,)
        )
        monthly_income = float(cursor.fetchone()[0])

        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%Y-%m') = DATE_FORMAT(CURRENT_DATE(), '%Y-%m')",
            (user_id,)
        )
        monthly_expenses = float(cursor.fetchone()[0])

        net_cash_flow = monthly_income - monthly_expenses
        savings_rate = ((monthly_income - monthly_expenses) / monthly_income * 100.0) if monthly_income > 0 else 0.0

        # Recent Transactions
        cursor.execute(
            "SELECT DATE_FORMAT(expense_date, '%d %b %Y'), category, description, amount "
            "FROM expenses WHERE user_id=%s ORDER BY expense_date DESC, expense_id DESC LIMIT 5",
            (user_id,)
        )
        raw_recent = cursor.fetchall()
        recent_expenses = [(r[0], r[1], r[2], float(r[3] or 0)) for r in raw_recent]

        # Category Breakdown for Chart (Current Month)
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

        change_percentage = round(((monthly_expenses - last_month) / last_month * 100.0), 1) if last_month > 0 else 0.0

        # All-time top category
        cursor.execute(
            "SELECT category, SUM(amount) as total, COUNT(*) as cnt FROM expenses "
            "WHERE user_id=%s GROUP BY category ORDER BY total DESC LIMIT 1",
            (user_id,)
        )
        top_cat_row = cursor.fetchone()
        top_category = (top_cat_row[0], float(top_cat_row[1]), top_cat_row[2]) if top_cat_row else None

        # Budget information via budget_service
        user_budgets_info = get_user_budgets(cursor, user_id)
        overall_b = user_budgets_info.get('overall', user_budgets_info.get('overall_budget', {}))
        cat_budgets = user_budgets_info.get('category_budgets', [])

        has_overall = overall_b.get('limit', 0.0) > 0
        has_cat = len(cat_budgets) > 0

        if has_overall:
            budget_limit = overall_b['limit']
            budget_spent = overall_b['spent']
            budget_pct = overall_b['percentage']
            budget_label = "Overall Budget"
            budget_summary_text = f"of ₹{budget_limit:,.0f} monthly limit"
            has_budget = True
        elif has_cat:
            budget_limit = sum(b['limit'] for b in cat_budgets)
            budget_spent = sum(b['spent'] for b in cat_budgets)
            budget_pct = (budget_spent / budget_limit * 100.0) if budget_limit > 0 else 0.0
            budget_label = f"{len(cat_budgets)} Category Budget{'s' if len(cat_budgets) != 1 else ''}"
            budget_summary_text = f"of ₹{budget_limit:,.0f} total category limits"
            has_budget = True
        else:
            budget_limit = 0.0
            budget_spent = 0.0
            budget_pct = 0.0
            budget_label = "No Budget Set"
            budget_summary_text = "No budget limits configured"
            has_budget = False

        insights = build_smart_insights(cursor, user_id)
        goal_summary = build_goal_summary(cursor, user_id)
        goals = get_dashboard_goals(cursor, user_id)
        settlements_summary = build_settlements_summary(cursor, user_id)

    return render_template(
        'dashboard/dashboard.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        monthly_budget=budget_limit,
        budget=budget_limit,
        budget_limit=budget_limit,
        budget_spent=budget_spent,
        budget_percentage=budget_pct,
        budget_label=budget_label,
        budget_summary_text=budget_summary_text,
        budget_cat_count=len(cat_budgets),
        has_overall_budget=has_overall,
        has_cat_budget=has_cat,
        has_budget=has_budget,
        total_expenses=lifetime_expenses,
        total_spent=lifetime_expenses,
        total_income=lifetime_income,
        this_month=monthly_expenses,
        monthly_expenses=monthly_expenses,
        last_month=last_month,
        change_percentage=change_percentage,
        monthly_income=monthly_income,
        net_cash_flow=net_cash_flow,
        projected_position=net_cash_flow,
        savings_rate=savings_rate,
        recent_expenses=recent_expenses,
        chart_labels=chart_labels,
        chart_values=chart_values,
        top_category=top_category,
        insights=insights,
        goals=goals,
        goal_summary=goal_summary,
        settlements_summary=settlements_summary,
        active_page='dashboard'
    )

def is_safe_url(target):
    if not target:
        return False
    return target.startswith('/') and not target.startswith('//') and not target.startswith('\\')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect('/')
    if request.method == 'POST':
        client_ip = get_client_ip()
        allowed, retry_after = check_rate_limit(f"register:{client_ip}", max_requests=5, window_seconds=60)
        if not allowed:
            flash(f"Too many registration attempts. Please try again in {retry_after} seconds.", "error")
            return render_template('auth/register.html'), 429

        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not username or not email or not password or not confirm_password:
            flash("All registration fields are required.", "error")
            return render_template('auth/register.html')

        is_valid_pw, pw_err = validate_password_policy(password)
        if not is_valid_pw:
            flash(pw_err, "error")
            return render_template('auth/register.html')

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
            user_id = cursor.lastrowid
            cursor.execute("INSERT IGNORE INTO notification_preferences (user_id) VALUES (%s)", (user_id,))
            cursor.execute(
                "INSERT INTO accounts (user_id, name, account_type, balance, currency) VALUES (%s, 'Main Account', 'checking', 0.00, 'INR')",
                (user_id,)
            )

        session['user_id'] = user_id
        session['username'] = username
        session['display_name'] = username
        flash("Registration successful! Welcome to Finora.", "success")
        return redirect('/')

    return render_template('auth/register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect('/')
    if request.method == 'POST':
        client_ip = get_client_ip()
        rate_key = f"login:{client_ip}"
        allowed, retry_after = check_rate_limit(rate_key, max_requests=5, window_seconds=60)
        if not allowed:
            flash(f"Too many failed login attempts. Please try again in {retry_after} seconds.", "error")
            return render_template('auth/login.html'), 429

        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        with get_db() as (conn, cursor):
            cursor.execute("SELECT user_id, username, password, display_name FROM users WHERE email=%s", (email,))
            user = cursor.fetchone()

        if user and check_password_hash(user[2], password):
            reset_rate_limit(rate_key)
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['display_name'] = user[3] or user[1]
            flash("Welcome back!", "success")
            next_url = request.args.get('next') or request.form.get('next')
            if is_safe_url(next_url):
                return redirect(next_url)
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
        cursor.execute("SELECT username, email, display_name, auth_provider FROM users WHERE user_id=%s", (session['user_id'],))
        user_info = cursor.fetchone()

    auth_provider = user_info[3] if user_info and len(user_info) > 3 else 'local'
    email = user_info[1] if user_info else ''

    return render_template(
        'user/profile.html',
        username=session['username'],
        email=email,
        display_name=session.get('display_name', session['username']),
        user_info=user_info,
        auth_provider=auth_provider,
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

    with get_db() as (conn, cursor):
        cursor.execute("SELECT password, auth_provider FROM users WHERE user_id=%s", (session['user_id'],))
        row = cursor.fetchone()
        if not row:
            flash("User not found.", "error")
            return redirect('/profile')

        user_pw, auth_provider = row[0], row[1]
        if auth_provider == 'google':
            flash("This account uses Google to sign in. You don't need a Finora password.", "info")
            return redirect('/profile')

        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not current_password or not new_password or not confirm_password:
            flash("All password fields are required.", "error")
            return redirect('/profile')
        if new_password != confirm_password:
            flash("New passwords do not match. Please try again.", "error")
            return redirect('/profile')

        is_valid_pw, pw_err = validate_password_policy(new_password)
        if not is_valid_pw:
            flash(pw_err, "error")
            return redirect('/profile')

        if not check_password_hash(user_pw, current_password):
            flash("Current password is incorrect.", "error")
            return redirect('/profile')

        hashed = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password=%s WHERE user_id=%s", (hashed, session['user_id']))

    flash("Password updated successfully!", "success")
    return redirect('/profile')

@auth_bp.route('/profile/reset-account-data', methods=['POST'])
def reset_account_data_route():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    confirm_text = request.form.get('confirm_text', '').strip()

    if confirm_text != 'RESET':
        flash("Confirmation text must match 'RESET' exactly.", "error")
        return redirect('/profile')

    with get_db() as (conn, cursor):
        cursor.execute("SELECT password, auth_provider, email FROM users WHERE user_id=%s", (user_id,))
        user_row = cursor.fetchone()
        if not user_row:
            session.clear()
            return redirect('/login')

        user_pw, auth_provider, user_email = user_row[0], user_row[1], user_row[2]

        if auth_provider == 'google':
            confirm_email = request.form.get('confirm_email', '').strip().lower()
            if confirm_email != (user_email or '').lower():
                flash("Email confirmation does not match your account email.", "error")
                return redirect('/profile')
        else:
            password = request.form.get('password', '')
            if not check_password_hash(user_pw, password):
                flash("Incorrect password. Account reset cancelled.", "error")
                return redirect('/profile')

        reset_user_financial_data(cursor, user_id)

    flash("Your financial data has been completely reset. Your account remains active with a fresh ledger.", "success")
    return redirect('/profile')

@auth_bp.route('/profile/delete-account', methods=['POST'])
def profile_delete_account():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    confirm_text = request.form.get('confirm_text', '').strip()
    confirm_username = request.form.get('confirm_username', '').strip()

    if confirm_text != 'DELETE':
        flash("Confirmation text must match 'DELETE' exactly.", "error")
        return redirect('/profile')

    with get_db() as (conn, cursor):
        cursor.execute("SELECT username, email, password, auth_provider FROM users WHERE user_id=%s", (user_id,))
        user_row = cursor.fetchone()
        if not user_row:
            session.clear()
            return redirect('/login')

        username, email, user_pw, auth_provider = user_row[0], user_row[1], user_row[2], user_row[3]

        if confirm_username.lower() not in [username.lower(), (email or '').lower()]:
            flash("Account confirmation name/email does not match.", "error")
            return redirect('/profile')

        if auth_provider != 'google':
            password = request.form.get('password', '')
            if not check_password_hash(user_pw, password):
                flash("Incorrect password. Account deletion cancelled.", "error")
                return redirect('/profile')

        # Clean all user data atomically
        tables_to_clear = [
            'password_resets',
            'goal_contributions',
            'savings_goals',
            'settlements',
            'recurring_expenses',
            'recurring_income',
            'expenses',
            'income',
            'budgets',
            'notifications',
            'notification_preferences',
            'accounts',
            'users'
        ]
        for table in tables_to_clear:
            cursor.execute(f"DELETE FROM {table} WHERE user_id=%s", (user_id,))

    session.clear()
    flash("Your account and all associated data have been permanently deleted.", "success")
    return redirect('/login')

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if 'user_id' in session:
        return redirect('/')

    if request.method == 'POST':
        client_ip = get_client_ip()
        allowed, retry_after = check_rate_limit(f"forgot_password:{client_ip}", max_requests=5, window_seconds=60)
        if not allowed:
            flash("Too many password reset requests. Please try again in a few moments.", "info")
            return render_template('auth/forgot_password.html'), 429

        email = request.form.get('email', '').strip().lower()
        if email:
            with get_db() as (conn, cursor):
                cursor.execute("SELECT user_id, auth_provider FROM users WHERE email = %s", (email,))
                user = cursor.fetchone()

                if user:
                    user_id, auth_provider = user[0], user[1]
                    if auth_provider == 'google':
                        print(f"[Password Reset Log] Account for {email} uses Google Sign-In.")
                    else:
                        raw_token = secrets.token_urlsafe(32)
                        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
                        expires_at = datetime.now() + timedelta(hours=1)

                        cursor.execute("UPDATE password_resets SET used_at=NOW() WHERE user_id=%s AND used_at IS NULL", (user_id,))
                        cursor.execute(
                            "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                            (user_id, token_hash, expires_at)
                        )
                        base_url = Config.APP_BASE_URL or request.host_url.rstrip('/')
                        reset_url = f"{base_url}/reset-password/{raw_token}"
                        
                        email_sent = send_password_reset_email(email, reset_url)
                        if not email_sent:
                            print(f"[Password Reset Notice] Email dispatch skipped or failed for {email}. (Configure MAIL_USERNAME and MAIL_PASSWORD in .env for live delivery)")

        flash("If an account exists for that email, you will receive password reset instructions.", "info")
        return redirect('/forgot-password')

    return render_template('auth/forgot_password.html')

@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if 'user_id' in session:
        return redirect('/')

    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT reset_id, user_id, expires_at, used_at FROM password_resets WHERE token_hash=%s",
            (token_hash,)
        )
        row = cursor.fetchone()

        if not row:
            flash("Invalid or expired password reset link.", "error")
            return redirect('/forgot-password')

        reset_id, user_id, expires_at, used_at = row
        if used_at is not None or expires_at < datetime.now():
            flash("This password reset link has expired or already been used.", "error")
            return redirect('/forgot-password')

        if request.method == 'POST':
            new_password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_password', '')

            is_valid_pw, pw_err = validate_password_policy(new_password)
            if not is_valid_pw:
                flash(pw_err, "error")
                return render_template('auth/reset_password.html', token=token)

            if new_password != confirm_password:
                flash("Passwords do not match.", "error")
                return render_template('auth/reset_password.html', token=token)

            pw_hash = generate_password_hash(new_password)
            cursor.execute(
                "UPDATE users SET password=%s, auth_provider='local' WHERE user_id=%s",
                (pw_hash, user_id)
            )
            cursor.execute("UPDATE password_resets SET used_at=NOW() WHERE reset_id=%s", (reset_id,))
            flash("Your password has been reset successfully! Please log in with your new password.", "success")
            return redirect('/login')

    return render_template('auth/reset_password.html', token=token)

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

# ----------------- GOOGLE OAUTH ROUTES -----------------

@auth_bp.route('/login/google')
def login_google():
    oauth = getattr(current_app, 'oauth', None)
    if not oauth:
        flash("Google OAuth is not configured on this server.", "error")
        return redirect('/login')

    try:
        google = oauth.create_client('google')
        if not google:
            flash("Google OAuth client not found.", "error")
            return redirect('/login')
        redirect_uri = url_for('auth.authorize_google', _external=True)
        return google.authorize_redirect(redirect_uri)
    except Exception as e:
        flash(f"Google login failed: {str(e)}", "error")
        return redirect('/login')

@auth_bp.route('/authorize/google')
def authorize_google():
    oauth = getattr(current_app, 'oauth', None)
    if not oauth:
        flash("Google OAuth is not configured on this server.", "error")
        return redirect('/login')

    try:
        google = oauth.create_client('google')
        if not google:
            flash("Google OAuth client not found.", "error")
            return redirect('/login')

        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info and hasattr(google, 'userinfo'):
            user_info = google.userinfo()

        if not user_info or not user_info.get('email'):
            flash("Failed to retrieve user info from Google.", "error")
            return redirect('/login')

        google_email = user_info['email'].strip().lower()
        display_name = user_info.get('name') or google_email.split('@')[0]

        with get_db() as (conn, cursor):
            cursor.execute("SELECT user_id, username, display_name, auth_provider FROM users WHERE email=%s OR username=%s", (google_email, google_email))
            user = cursor.fetchone()

            if not user:
                random_pw = generate_password_hash(secrets.token_hex(32))
                cursor.execute(
                    "INSERT INTO users (username, email, password, display_name, auth_provider) VALUES (%s, %s, %s, %s, 'google')",
                    (google_email, google_email, random_pw, display_name)
                )
                user_id = cursor.lastrowid
                cursor.execute("INSERT IGNORE INTO notification_preferences (user_id) VALUES (%s)", (user_id,))
                cursor.execute(
                    "INSERT INTO accounts (user_id, name, account_type, balance, currency) VALUES (%s, 'Main Account', 'checking', 0.00, 'INR')",
                    (user_id,)
                )
                username = google_email
            else:
                user_id, username, existing_display_name, auth_provider = user[0], user[1], user[2], user[3]
                display_name = existing_display_name or display_name
                if auth_provider != 'google':
                    new_random_pw = generate_password_hash(secrets.token_hex(32))
                    cursor.execute(
                        "UPDATE users SET auth_provider='google', password=%s WHERE user_id=%s",
                        (new_random_pw, user_id)
                    )

        session['user_id'] = user_id
        session['username'] = username
        session['display_name'] = display_name
        flash("Logged in with Google successfully!", "success")
        return redirect('/')

    except Exception as e:
        flash(f"Google authorization failed: {str(e)}", "error")
        return redirect('/login')

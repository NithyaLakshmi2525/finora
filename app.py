import os
import secrets
from flask import Flask, render_template, session
from flask_wtf.csrf import CSRFProtect
from authlib.integrations.flask_client import OAuth

from config import Config
from db import get_db, init_db_pool

# Import Blueprints
from blueprints.auth import auth_bp
from blueprints.expenses import expenses_bp
from blueprints.recurring import recurring_bp
from blueprints.settlements import settlements_bp
from blueprints.goals import goals_bp
from blueprints.reports import reports_bp
from blueprints.accounts import accounts_bp
from blueprints.budgets import budgets_bp
from services.ledger_service import DEFAULT_CATEGORIES, CATEGORY_ALIASES

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Initialize CSRF protection
    csrf = CSRFProtect(app)
    
    # Initialize connection pool
    init_db_pool()

    # OAuth Setup
    oauth = OAuth(app)
    google = oauth.register(
        name='google',
        client_id=Config.GOOGLE_CLIENT_ID,
        client_secret=Config.GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )

    # Register Blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(recurring_bp)
    app.register_blueprint(settlements_bp)
    app.register_blueprint(goals_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(budgets_bp)

    # Centralized Error Handlers
    @app.errorhandler(404)
    def not_found_error(error):
        return render_template(
            'base.html',
            page_title="Page Not Found",
            username=session.get('username'),
            display_name=session.get('display_name'),
            content="<div class='p-12 text-center'><h2 class='text-2xl font-bold text-gray-200 mb-2'>404 — Page Not Found</h2><p class='text-gray-400 mb-6'>The page you requested does not exist.</p><a href='/' class='px-5 py-2.5 bg-primary text-on-primary font-semibold rounded-xl'>Return to Dashboard</a></div>"
        ), 404

    @app.errorhandler(500)
    def internal_error(error):
        return render_template(
            'base.html',
            page_title="Server Error",
            username=session.get('username'),
            display_name=session.get('display_name'),
            content="<div class='p-12 text-center'><h2 class='text-2xl font-bold text-red-400 mb-2'>500 — Internal Server Error</h2><p class='text-gray-400 mb-6'>Something went wrong. Please try refreshing or return to dashboard.</p><a href='/' class='px-5 py-2.5 bg-primary text-on-primary font-semibold rounded-xl'>Return to Dashboard</a></div>"
        ), 500

    return app

def ensure_schema():
    """Self-healing, idempotent startup migration."""
    try:
        with get_db() as (conn, cursor):
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    category_id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(50) NOT NULL UNIQUE,
                    icon VARCHAR(10) DEFAULT '💰'
                )
            """)
            for name, icon in DEFAULT_CATEGORIES:
                cursor.execute("INSERT IGNORE INTO categories (name, icon) VALUES (%s, %s)", (name, icon))

            cursor.execute("DELETE FROM categories WHERE name='Healthcare'")

            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'expenses' AND column_name = 'recurring_id'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("ALTER TABLE expenses ADD COLUMN recurring_id INT NULL")

            for raw_variant, canonical in CATEGORY_ALIASES.items():
                cursor.execute(
                    "UPDATE expenses SET category=%s WHERE LOWER(TRIM(category))=%s AND category<>%s",
                    (canonical, raw_variant, canonical)
                )
                cursor.execute(
                    "UPDATE recurring_expenses SET category=%s WHERE LOWER(TRIM(category))=%s AND category<>%s",
                    (canonical, raw_variant, canonical)
                )

            for col_name, col_def in [
                ('icon', "VARCHAR(10) NOT NULL DEFAULT '🎯'"),
                ('color', "VARCHAR(20) NOT NULL DEFAULT '#4edea3'"),
                ('closed_at', "DATETIME NULL"),
            ]:
                cursor.execute("""
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_schema = DATABASE() AND table_name = 'savings_goals' AND column_name = %s
                """, (col_name,))
                if cursor.fetchone()[0] == 0:
                    cursor.execute(f"ALTER TABLE savings_goals ADD COLUMN {col_name} {col_def}")

            cursor.execute("UPDATE goal_contributions SET note = NULL WHERE note LIKE 'Used savings %%'")

            for col_name, col_def in [
                ('reason', "VARCHAR(255) NULL"),
                ('created_at', "DATETIME NULL"),
                ('balance_date', "DATE NULL"),
                ('counts_as_expense', "TINYINT(1) NOT NULL DEFAULT 0"),
                ('linked_expense_id', "INT NULL"),
            ]:
                cursor.execute("""
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_schema = DATABASE() AND table_name = 'settlements' AND column_name = %s
                """, (col_name,))
                if cursor.fetchone()[0] == 0:
                    cursor.execute(f"ALTER TABLE settlements ADD COLUMN {col_name} {col_def}")

            cursor.execute("UPDATE settlements SET created_at = updated_at WHERE created_at IS NULL")
            cursor.execute("UPDATE settlements SET balance_date = DATE(created_at) WHERE balance_date IS NULL")

            for col_name, col_def in [
                ('icon', "VARCHAR(10) NOT NULL DEFAULT '⚡'"),
                ('status', "VARCHAR(10) NOT NULL DEFAULT 'active'"),
                ('recurring_type', "VARCHAR(10) NOT NULL DEFAULT 'auto'"),
            ]:
                cursor.execute("""
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_schema = DATABASE() AND table_name = 'recurring_expenses' AND column_name = %s
                """, (col_name,))
                if cursor.fetchone()[0] == 0:
                    cursor.execute(f"ALTER TABLE recurring_expenses ADD COLUMN {col_name} {col_def}")

            # Recurring Income table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recurring_income (
                    recurring_income_id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    amount DECIMAL(12,2) NOT NULL,
                    source VARCHAR(50) NOT NULL DEFAULT 'Salary',
                    frequency VARCHAR(20) NOT NULL DEFAULT 'Monthly',
                    next_pay_date DATE NOT NULL,
                    icon VARCHAR(10) DEFAULT '💼',
                    status VARCHAR(10) NOT NULL DEFAULT 'active',
                    account_id INT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_rec_inc_user (user_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    icon VARCHAR(10) NULL,
                    title VARCHAR(255) NOT NULL,
                    message VARCHAR(500) NULL,
                    link VARCHAR(255) NULL,
                    is_read TINYINT(1) NOT NULL DEFAULT 0,
                    dedup_key VARCHAR(150) NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_notifications_user_created (user_id, created_at),
                    UNIQUE INDEX idx_notifications_user_dedup (user_id, dedup_key)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notification_preferences (
                    user_id INT NOT NULL PRIMARY KEY,
                    budget_alerts TINYINT(1) NOT NULL DEFAULT 1,
                    recurring_reminders TINYINT(1) NOT NULL DEFAULT 1,
                    goal_milestones TINYINT(1) NOT NULL DEFAULT 1
                )
            """)

            # Accounts - Multi-account support
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    account_type VARCHAR(20) NOT NULL DEFAULT 'checking',
                    balance DECIMAL(12,2) NOT NULL DEFAULT 0.00,
                    currency VARCHAR(10) NOT NULL DEFAULT 'INR',
                    is_active TINYINT(1) NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_accounts_user (user_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'expenses' AND column_name = 'account_id'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("ALTER TABLE expenses ADD COLUMN account_id INT NULL")

            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'income' AND column_name = 'account_id'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("ALTER TABLE income ADD COLUMN account_id INT NULL")

            cursor.execute("SELECT user_id FROM users")
            user_rows = cursor.fetchall()
            for (uid,) in user_rows:
                cursor.execute("SELECT account_id FROM accounts WHERE user_id=%s AND is_active=1 ORDER BY account_id ASC LIMIT 1", (uid,))
                acc_row = cursor.fetchone()
                if not acc_row:
                    cursor.execute(
                        "INSERT INTO accounts (user_id, name, account_type, balance, currency) VALUES (%s, 'Main Account', 'checking', 0.00, 'INR')",
                        (uid,)
                    )
                    acc_id = cursor.lastrowid
                else:
                    acc_id = acc_row[0]

                cursor.execute("UPDATE expenses SET account_id=%s WHERE user_id=%s AND account_id IS NULL", (acc_id, uid))
                cursor.execute("UPDATE income SET account_id=%s WHERE user_id=%s AND account_id IS NULL", (acc_id, uid))

                cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM income WHERE user_id=%s AND account_id=%s", (uid, acc_id))
                total_inc = float(cursor.fetchone()[0])
                cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=%s AND account_id=%s", (uid, acc_id))
                total_exp = float(cursor.fetchone()[0])
                net_bal = total_inc - total_exp
                cursor.execute("UPDATE accounts SET balance=%s WHERE account_id=%s", (net_bal, acc_id))

            # Budgets - Per-category & multi-currency support
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'budgets' AND column_name = 'user_id'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("ALTER TABLE budgets ADD COLUMN user_id INT NULL")
                cursor.execute("UPDATE budgets SET user_id = (SELECT user_id FROM users ORDER BY user_id LIMIT 1) WHERE user_id IS NULL")

            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'budgets' AND column_name = 'category'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("ALTER TABLE budgets ADD COLUMN category VARCHAR(50) NULL DEFAULT 'Overall'")
                cursor.execute("UPDATE budgets SET category='Overall' WHERE category IS NULL")

            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'budgets' AND column_name = 'currency'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("ALTER TABLE budgets ADD COLUMN currency VARCHAR(10) NOT NULL DEFAULT 'INR'")

            try:
                cursor.execute("ALTER TABLE budgets DROP INDEX idx_budgets_user")
            except Exception:
                pass

            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.statistics
                WHERE table_schema = DATABASE() AND table_name = 'budgets' AND index_name = 'idx_budgets_user_cat'
            """)
            if cursor.fetchone()[0] == 0:
                try:
                    cursor.execute("ALTER TABLE budgets ADD UNIQUE INDEX idx_budgets_user_cat (user_id, category)")
                except Exception:
                    pass

            try:
                cursor.execute("SELECT user_id, password FROM users WHERE password IS NOT NULL")
                legacy_rows = cursor.fetchall()
                for uid, pw_hash in legacy_rows:
                    if check_password_hash(pw_hash, 'google_auth'):
                        cursor.execute(
                            "UPDATE users SET password=%s WHERE user_id=%s",
                            (generate_password_hash(secrets.token_hex(32)), uid)
                        )
            except Exception:
                pass
    except Exception as e:
        print(f"[startup] schema check skipped: {e}")

app = create_app()
ensure_schema()

if __name__ == '__main__':
    app.run(debug=Config.DEBUG)

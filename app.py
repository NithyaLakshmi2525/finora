import os
import io
import secrets
import calendar

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    Response,
    session,
    send_file,
    url_for,
    flash
)
from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer
)
from reportlab.lib.styles import (
    getSampleStyleSheet
)
from datetime import datetime, date, timedelta
from db import get_db_connection
from authlib.integrations.flask_client import OAuth

# Load .env file if present (pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# Debug mode is OFF unless explicitly enabled — an unset environment variable
# must always be the safe (production) default, never the permissive one.
# Local development: set FLASK_DEBUG=1 in your .env or shell.
DEBUG = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")

_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    if DEBUG:
        # Local development only: generate a per-process throwaway key so the
        # app still runs without extra setup. Sessions won't persist across
        # restarts. This path is unreachable unless FLASK_DEBUG is explicitly on.
        _secret_key = secrets.token_hex(32)
        print(
            "[startup] WARNING: SECRET_KEY not set — using a temporary random "
            "key for this dev session only (FLASK_DEBUG is on). Set SECRET_KEY "
            "in your .env before deploying."
        )
    else:
        raise RuntimeError(
            "SECRET_KEY environment variable is not set. Refusing to start "
            "without a real secret key. Set SECRET_KEY in your .env (or "
            "environment), or set FLASK_DEBUG=1 for local development only."
        )
app.secret_key = _secret_key


def advance_recurring_date(current_date, frequency):
    """Bump a recurring item's next_charge_date forward by one period.
    Used when a 'manual confirmation' item (rent, bills, EMIs) is marked as paid."""
    if not current_date:
        return current_date
    if frequency == 'Daily':
        return current_date + timedelta(days=1)
    if frequency == 'Weekly':
        return current_date + timedelta(days=7)
    if frequency == 'Monthly':
        month = current_date.month + 1
        year = current_date.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        is_leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        days_in_month = [31, 29 if is_leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        day = min(current_date.day, days_in_month[month - 1])
        return date(year, month, day)
    if frequency == 'Yearly':
        try:
            return date(current_date.year + 1, current_date.month, current_date.day)
        except ValueError:
            # Feb 29 on a non-leap year
            return date(current_date.year + 1, current_date.month, 28)
    return current_date

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# ---------------------------------------------------------------------------
# Shared Categories (Expenses + Recurring unification)
# ---------------------------------------------------------------------------
# DB schema required — run once:
#   CREATE TABLE IF NOT EXISTS categories (
#       category_id INT AUTO_INCREMENT PRIMARY KEY,
#       name VARCHAR(50) NOT NULL UNIQUE,
#       icon VARCHAR(10) DEFAULT '💰'
#   );
#   INSERT IGNORE INTO categories (name, icon) VALUES
#       ('Food','🍕'), ('Shopping','🛍️'), ('Travel','🚗'), ('Bills','🧾'),
#       ('Entertainment','🎬'), ('Health','🏥'), ('Education','📚'), ('Repairs','🔧'),
#       ('Investment','📈'), ('Rent','🏠'), ('Subscription','📱'), ('Transport','🚌'),
#       ('Other','💰');
#
#   -- Lets an expense row point back at the recurring rule that generated it,
#   -- so the Expenses page can disable editing on those rows and point the
#   -- user to the Recurring page instead.
#   ALTER TABLE expenses ADD COLUMN recurring_id INT NULL,
#       ADD CONSTRAINT fk_expenses_recurring FOREIGN KEY (recurring_id)
#           REFERENCES recurring_expenses(recurring_id) ON DELETE SET NULL;
# ---------------------------------------------------------------------------

DEFAULT_CATEGORIES = [
    ('Food', '🍕'), ('Shopping', '🛍️'), ('Travel', '🚗'), ('Bills', '🧾'),
    ('Entertainment', '🎬'), ('Health', '🏥'), ('Education', '📚'), ('Repairs', '🔧'),
    ('Investment', '📈'), ('Rent', '🏠'), ('Subscription', '📱'), ('Transport', '🚌'),
    ('Other', '💰'),
]

# Known messy variants (different case, pluralization, or wording) that all
# mean the same real-world category — folded into one canonical name so the
# dropdown never shows the same category twice. "Health" is kept as the
# canonical name (not "Healthcare") since it's already the string every
# category-icon lookup across the app (dashboard, expenses, reports) checks
# against.
CATEGORY_ALIASES = {
    'investments': 'Investment',
    'investment': 'Investment',
    'healthcare': 'Health',
    'health care': 'Health',
    'health': 'Health',
    'transportation': 'Transport',
    'transports': 'Transport',
    'transport': 'Transport',
}


def normalize_category_name(name):
    """Maps known messy category variants to the single canonical name (see
    CATEGORY_ALIASES). Anything not in the map is just trimmed and returned
    as-is."""
    if not name:
        return name
    stripped = name.strip()
    return CATEGORY_ALIASES.get(stripped.lower(), stripped)


def ensure_schema():
    """Self-healing, idempotent startup migration — creates the shared
    `categories` table (seeded with DEFAULT_CATEGORIES), adds
    `expenses.recurring_id` if either is still missing, and normalizes known
    messy category variants (e.g. 'investments' -> 'Investment') on existing
    rows so they don't show up as duplicate entries in the category
    dropdown. Safe to run on every app start: every step checks before
    changing anything.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                category_id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(50) NOT NULL UNIQUE,
                icon VARCHAR(10) DEFAULT '💰'
            )
        """)
        for name, icon in DEFAULT_CATEGORIES:
            cursor.execute("INSERT IGNORE INTO categories (name, icon) VALUES (%s, %s)", (name, icon))

        # Drop 'Healthcare' if an earlier version of this migration already
        # inserted it as a separate row from 'Health'.
        cursor.execute("DELETE FROM categories WHERE name='Healthcare'")

        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'expenses' AND column_name = 'recurring_id'
        """)
        has_recurring_id = cursor.fetchone()[0] > 0
        if not has_recurring_id:
            cursor.execute("ALTER TABLE expenses ADD COLUMN recurring_id INT NULL")

        # One-time cleanup: fold known messy category variants into their
        # canonical form on real data, so old rows don't keep producing
        # duplicate dropdown entries every time the category list is built.
        for raw_variant, canonical in CATEGORY_ALIASES.items():
            cursor.execute(
                "UPDATE expenses SET category=%s WHERE LOWER(TRIM(category))=%s AND category<>%s",
                (canonical, raw_variant, canonical)
            )
            cursor.execute(
                "UPDATE recurring_expenses SET category=%s WHERE LOWER(TRIM(category))=%s AND category<>%s",
                (canonical, raw_variant, canonical)
            )

        # Final Goals data model: status is ALWAYS derived live, never
        # stored. A goal is either open (closed_at IS NULL) or closed
        # (closed_at set). While open, its display state is computed from
        # current_amount (reserved) vs target_amount/target_date:
        #   Target Reached  — current_amount >= target_amount (milestone
        #                      badge only, does not end the goal)
        #   Overdue         — target_date has passed and target not reached
        #   In Progress     — otherwise
        # There is no "Completed"/"Used" status and no used_amount tracking
        # — closing a goal simply zeroes current_amount (the reservation is
        # released) and stamps closed_at; restoring clears closed_at and
        # guarantees current_amount = 0, per spec.
        #
        # icon/color are new goal-level customization fields (previously
        # every goal just showed a hardcoded 🎯/✅ emoji).
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

        # One-time cleanup: an earlier design iteration auto-generated notes
        # like "Used savings – ₹1,000" on withdrawal, which implies a reason
        # the app was never supposed to assume. Clear those specific
        # auto-generated notes so the timeline falls back to the neutral
        # "Withdrawal" label — this never touches notes the user typed
        # themselves.
        cursor.execute("""
            UPDATE goal_contributions SET note = NULL
            WHERE note LIKE 'Used savings %'
        """)

        # Balances/Settlements — add `reason` (why the balance exists) and
        # `created_at` (when it was first logged, distinct from
        # `updated_at` which already moves whenever a balance is settled).
        # Backfilled from updated_at for any pre-existing rows so "Pending
        # for N days" still has something sane to compute against.
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

        cursor.execute("""
            UPDATE settlements SET created_at = updated_at WHERE created_at IS NULL
        """)
        # balance_date is the user-facing "when this actually happened" date,
        # separate from created_at (system record-keeping timestamp, never
        # editable). Existing rows didn't capture this distinction, so the
        # best available default is the day the record was created.
        cursor.execute("""
            UPDATE settlements SET balance_date = DATE(created_at) WHERE balance_date IS NULL
        """)
        # counts_as_expense / linked_expense_id default to 0/NULL for every
        # pre-existing row via the column defaults above — old balances are
        # never retroactively linked to an expense, since there's no way to
        # know whether that was the user's intent.

        # Recurring items — icon/status/recurring_type were previously only
        # documented as a manual ALTER TABLE comment, never actually applied
        # here, so a fresh database would break on first visit to /recurring.
        # Defaults match the exact fallbacks already used when reading rows
        # (see recurring() below), so existing rows behave identically to
        # before this migration ever ran.
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

        # Notifications — these two tables back the bell icon and the
        # Settings > Notifications preferences panel. Neither had a bootstrap
        # path anywhere before, so a fresh database would 500 on /settings
        # and /notifications. Columns are limited to exactly what the app
        # currently reads/writes.
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
        # Existing databases (from before dedup_key existed) get it added
        # here. dedup_key lets notification generation check "have I already
        # notified this exact event?" (e.g. 'goal-14-50pct') instead of
        # re-notifying every time the triggering condition is re-checked.
        # NULL dedup_key rows are exempt from the uniqueness constraint
        # (MySQL never treats two NULLs as duplicates), so old/undeduplicated
        # rows are unaffected.
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'notifications' AND column_name = 'dedup_key'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE notifications ADD COLUMN dedup_key VARCHAR(150) NULL")
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = 'notifications' AND index_name = 'idx_notifications_user_dedup'
        """)
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE notifications ADD UNIQUE INDEX idx_notifications_user_dedup (user_id, dedup_key)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_preferences (
                user_id INT NOT NULL PRIMARY KEY,
                budget_alerts TINYINT(1) NOT NULL DEFAULT 1,
                recurring_reminders TINYINT(1) NOT NULL DEFAULT 1,
                goal_milestones TINYINT(1) NOT NULL DEFAULT 1
            )
        """)

        # Security fix: earlier versions of Google sign-in created accounts
        # with a fixed placeholder password ('google_auth'), which meant
        # anyone who knew a Google-linked account's email could log in
        # through the normal password form using that literal string. Any
        # row still carrying that placeholder gets rotated to a random,
        # unusable password immediately — Google sign-in itself never reads
        # this stored hash, so this is invisible to the user. Idempotent:
        # once rotated, check_password_hash below will always return False
        # for that row on future startups.
        try:
            cursor.execute("SELECT user_id, password FROM users WHERE password IS NOT NULL")
            legacy_rows = cursor.fetchall()
        except Exception:
            legacy_rows = []
        for uid, pw_hash in legacy_rows:
            try:
                if check_password_hash(pw_hash, 'google_auth'):
                    cursor.execute(
                        "UPDATE users SET password=%s WHERE user_id=%s",
                        (generate_password_hash(secrets.token_hex(32)), uid)
                    )
            except Exception:
                # Malformed/unrecognized hash format — never let one bad row
                # block startup for everyone else.
                continue

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        # Don't crash app startup over this — worst case, get_categories()
        # falls back to defaults and recurring-linked rows just won't be
        # flagged until the DB is reachable/migrated.
        print(f"[startup] schema check skipped: {e}")


ensure_schema()


def get_categories(cursor):
    """The one shared category list (name, icon) used by both the Expenses
    and Recurring dropdowns — this is what makes a recurring item's category
    always show up when editing its generated expense, and vice versa.

    Reads from the `categories` table (see migration note above). If that
    table doesn't exist yet on this database, falls back to
    DEFAULT_CATEGORIES so the app still runs. Either way, any category name
    already in use on `expenses` or `recurring_expenses` but missing from the
    table gets folded in too, so older data never loses its category from
    the dropdown after this change ships — matched case-insensitively (and
    through CATEGORY_ALIASES) against names already added, so the same
    category never appears twice just because of casing or old wording.
    """
    categories = {}   # canonical display name -> icon
    seen_lower = set()  # lowercased names already accounted for

    def add(name, icon):
        if not name:
            return
        name = name.strip()
        if not name or name.lower() in seen_lower:
            return
        seen_lower.add(name.lower())
        categories[name] = icon or '💰'

    try:
        cursor.execute("SELECT name, icon FROM categories ORDER BY name")
        for name, icon in cursor.fetchall():
            add(name, icon)
    except Exception:
        pass

    for name, icon in DEFAULT_CATEGORIES:
        add(name, icon)

    try:
        cursor.execute("SELECT DISTINCT category FROM expenses WHERE category IS NOT NULL AND category <> ''")
        for (name,) in cursor.fetchall():
            add(normalize_category_name(name), '💰')
    except Exception:
        pass

    try:
        cursor.execute("SELECT DISTINCT category FROM recurring_expenses WHERE category IS NOT NULL AND category <> ''")
        for (name,) in cursor.fetchall():
            add(normalize_category_name(name), '💰')
    except Exception:
        pass

    # Alphabetical, but 'Other' always goes last — it's the catch-all, not
    # meant to compete alphabetically with real categories.
    return sorted(categories.items(), key=lambda c: (c[0] == 'Other', c[0]))


def get_notification_prefs(cursor, user_id):
    """Same defaults as the Settings page GET handler — a user with no
    saved row yet has every category on by default."""
    cursor.execute(
        "SELECT budget_alerts, recurring_reminders, goal_milestones "
        "FROM notification_preferences WHERE user_id=%s",
        (user_id,)
    )
    row = cursor.fetchone()
    if row:
        return {
            'budget_alerts': bool(row[0]),
            'recurring_reminders': bool(row[1]),
            'goal_milestones': bool(row[2]),
        }
    return {'budget_alerts': True, 'recurring_reminders': True, 'goal_milestones': True}


def create_notification(cursor, user_id, icon, title, message, link, dedup_key=None):
    """The single place that actually inserts a notification row. Callers
    are expected to have already checked notification_preferences for the
    relevant category before calling this.

    dedup_key identifies the specific event (e.g. 'goal-14-50pct',
    'budget-2026-08-80') so the same event can never notify a user twice —
    checked explicitly here, and backed by a real UNIQUE(user_id, dedup_key)
    index at the database level as a second line of defense against a race
    between two near-simultaneous requests. Pass dedup_key=None for a
    one-off notification with no repeat-prevention needed.

    Does not commit — caller's existing transaction/commit covers this,
    same as every other write in this file.
    """
    if dedup_key:
        cursor.execute(
            "SELECT notification_id FROM notifications WHERE user_id=%s AND dedup_key=%s",
            (user_id, dedup_key)
        )
        if cursor.fetchone():
            return False
    try:
        cursor.execute(
            "INSERT INTO notifications (user_id, icon, title, message, link, dedup_key) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, icon, title, message, link, dedup_key)
        )
    except Exception:
        # Two near-simultaneous requests both passed the SELECT check above
        # before either committed — the UNIQUE index rejects the second
        # INSERT. The first one already recorded the notification, so this
        # is a successful outcome from the user's perspective, not an error.
        return False
    return True


def generate_opportunistic_notifications(user_id, cursor, conn):
    """Budget-alert and recurring-due-soon checks. Both are cheap, mostly
    read-only, and safe to run on every authenticated page load — dedup_key
    means the same event still only ever notifies once no matter how many
    times this runs. Registered below via before_request specifically so
    these aren't only checked on the Dashboard: a user who only ever visits
    Reports, Expenses, or Goals should still get alerted.
    """
    cursor.execute("SELECT monthly_limit FROM budgets LIMIT 1")
    budget_row = cursor.fetchone()
    budget = float(budget_row[0]) if budget_row and budget_row[0] is not None else 0
    if budget > 0:
        cursor.execute("""
            SELECT IFNULL(SUM(amount),0) FROM expenses
            WHERE MONTH(expense_date)=MONTH(CURDATE()) AND YEAR(expense_date)=YEAR(CURDATE()) AND user_id=%s
        """, (user_id,))
        month_spent = float(cursor.fetchone()[0])
        budget_percentage = (month_spent / budget) * 100
        month_key = date.today().strftime('%Y-%m')
        prefs = get_notification_prefs(cursor, user_id)
        if prefs['budget_alerts']:
            if budget_percentage >= 100:
                create_notification(
                    cursor, user_id, icon='🚨', title="Budget exceeded",
                    message=f"You've spent ₹{month_spent:,.0f} this month — over your ₹{budget:,.0f} budget.",
                    link='/', dedup_key=f"budget-{month_key}-100",
                )
            elif budget_percentage >= 80:
                create_notification(
                    cursor, user_id, icon='⚠️', title="Approaching budget limit",
                    message=f"You've used {budget_percentage:.0f}% of your ₹{budget:,.0f} monthly budget.",
                    link='/', dedup_key=f"budget-{month_key}-80",
                )

    cursor.execute("""
        SELECT recurring_id, title, next_charge_date FROM recurring_expenses
        WHERE user_id=%s AND status='active' AND next_charge_date IS NOT NULL
        AND next_charge_date >= CURDATE() AND next_charge_date <= DATE_ADD(CURDATE(), INTERVAL 3 DAY)
    """, (user_id,))
    due_soon_items = cursor.fetchall()
    if due_soon_items:
        prefs = get_notification_prefs(cursor, user_id)
        if prefs['recurring_reminders']:
            for r_id, r_title, r_due in due_soon_items:
                days_until = (r_due - date.today()).days
                when_text = "today" if days_until <= 0 else "tomorrow" if days_until == 1 else f"in {days_until} days"
                create_notification(
                    cursor, user_id, icon='🔄', title=f"{r_title} due {when_text}",
                    message=f"Next charge date: {r_due.strftime('%d %b %Y')}.",
                    link='/recurring', dedup_key=f"recurring-due-{r_id}-{r_due.isoformat()}",
                )

    conn.commit()


@app.before_request
def check_opportunistic_notifications():
    """Runs before every request. Deliberately narrow: only authenticated,
    GET, non-AJAX, non-API/static requests trigger it — so this never adds
    overhead to the notification bell's own polling, to any AJAX mutation
    (deposits, settlements, etc.), or to anything pre-login. A failure here
    must never take down the actual page being requested, hence the broad
    except.
    """
    if 'user_id' not in session:
        return
    if request.method != 'GET':
        return
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return
    if request.path.startswith('/notifications') or request.path.startswith('/api/') or request.path.startswith('/static/'):
        return
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        generate_opportunistic_notifications(session['user_id'], cursor, conn)
        cursor.close()
    except Exception as e:
        # Logged, not swallowed — a bug here should be visible in the
        # server console instead of silently disappearing forever. Still
        # never raised further: the actual page being requested must load
        # regardless of whether this background check succeeded, and
        # nothing about the exception is ever shown to the user.
        print(f"[notifications] opportunistic check failed (user_id={session.get('user_id')}, path={request.path}): {e}")
    finally:
        if conn is not None:
            conn.close()


def csv_escape(value):
    """Minimal CSV field escaping — quotes the field if it contains a comma,
    quote character, or newline, per standard CSV quoting rules."""
    text = '' if value is None else str(value)
    if any(ch in text for ch in (',', '"', '\n')):
        text = '"' + text.replace('"', '""') + '"'
    return text


def fetch_filtered_transactions(cursor, user_id, search=None, sort=None,
                                 start_date=None, end_date=None, show_income=False):
    """Returns the same combined (expenses + optional income), filtered,
    sorted transaction list used by both the Expenses page and CSV export,
    so the two can never drift out of sync — exporting always matches
    exactly what's currently on screen.

    Each row: (id, amount, category, description, formatted_date, raw_date,
    type, recurring_id). recurring_id is always NULL for income rows.
    """
    expense_query = """
        SELECT expense_id, amount, category, description,
               DATE_FORMAT(expense_date, '%b %d, %Y'), DATE(expense_date), 'expense', recurring_id
        FROM expenses WHERE user_id=%s
    """
    expense_params = [user_id]
    if search:
        expense_query += " AND category LIKE %s"; expense_params.append('%' + search + '%')
    if start_date:
        expense_query += " AND expense_date >= %s"; expense_params.append(start_date)
    if end_date:
        expense_query += " AND expense_date <= %s"; expense_params.append(end_date)

    cursor.execute(expense_query, tuple(expense_params))
    combined = list(cursor.fetchall())

    if show_income:
        income_query = """
            SELECT income_id, amount, source, description,
                   DATE_FORMAT(income_date, '%b %d, %Y'), DATE(income_date), 'income', NULL
            FROM income WHERE user_id=%s
        """
        income_params = [user_id]
        if search:
            income_query += " AND source LIKE %s"; income_params.append('%' + search + '%')
        if start_date:
            income_query += " AND income_date >= %s"; income_params.append(start_date)
        if end_date:
            income_query += " AND income_date <= %s"; income_params.append(end_date)

        cursor.execute(income_query, tuple(income_params))
        combined += list(cursor.fetchall())

    if sort == 'amount_asc':
        combined.sort(key=lambda r: float(r[1]))
    elif sort == 'amount_desc':
        combined.sort(key=lambda r: float(r[1]), reverse=True)
    elif sort == 'date_asc':
        combined.sort(key=lambda r: str(r[5]))
    else:  # date_desc default
        combined.sort(key=lambda r: str(r[5]), reverse=True)

    return combined

def build_smart_insights(cursor, user_id, budget, budget_percentage, month_spent, last_month,
                          change_percentage, top_category_month, total_income, total_expenses):
    """Generate a short, data-driven list of Smart Insights for the dashboard.

    Each insight is a dict: {'icon': str, 'text': str}. The first four slots
    are near-always-present "core" insights (budget, trend, category,
    savings). A single additional "contextual" insight is appended on top —
    picked from recurring/goal/unusual-spending/positive-reinforcement, in
    that priority order — so the card stays short instead of listing all of
    them at once (goal progress in particular is only ever shown for the one
    goal closest to completion, never for every goal at once).
    """
    insights = []

    # 1. Budget insight
    if budget and budget > 0:
        if budget_percentage >= 100:
            over_by = month_spent - budget
            insights.append({'icon': '🚨', 'text': f"Budget exceeded by ₹{over_by:,.0f} this month."})
        elif budget_percentage >= 80:
            insights.append({'icon': '⚠️', 'text': f"You've used {budget_percentage:.0f}% of your monthly budget."})
        else:
            insights.append({'icon': '✅', 'text': "You're within budget this month."})

    # 2. Spending trend vs last month
    if last_month > 0:
        if change_percentage > 5:
            insights.append({'icon': '📈', 'text': f"Spending increased by {change_percentage:.0f}% compared to last month."})
        elif change_percentage < -5:
            insights.append({'icon': '📉', 'text': f"Spending decreased by {abs(change_percentage):.0f}% compared to last month."})

    # 3. Category concentration insight (this month)
    if top_category_month and month_spent > 0:
        cat_name, cat_amount = top_category_month[0], float(top_category_month[1])
        share = round((cat_amount / month_spent) * 100)
        insights.append({'icon': '🍕', 'text': f"{cat_name} accounts for {share}% of your spending this month."})

    # 4. Savings insight
    if total_income > 0:
        savings_rate = ((total_income - total_expenses) / total_income) * 100
        insights.append({'icon': '💰', 'text': f"Your all-time savings rate is {savings_rate:.0f}%."})

    # 5-8. One contextual "bonus" insight, in priority order, so the card
    # doesn't get cluttered with all four at once.
    extra_insight = None

    # 5. Recurring expense due soon (within the next 7 days)
    cursor.execute("""
        SELECT title, next_charge_date FROM recurring_expenses
        WHERE user_id=%s AND status='active' AND next_charge_date IS NOT NULL
        AND next_charge_date >= CURDATE() AND next_charge_date <= DATE_ADD(CURDATE(), INTERVAL 7 DAY)
        ORDER BY next_charge_date ASC LIMIT 1
    """, (user_id,))
    due_row = cursor.fetchone()
    if due_row:
        title, next_charge_date = due_row
        days_until = (next_charge_date - date.today()).days
        if days_until <= 0:
            extra_insight = {'icon': '🔄', 'text': f"{title} is due today."}
        elif days_until == 1:
            extra_insight = {'icon': '🔄', 'text': f"{title} is due tomorrow."}
        else:
            extra_insight = {'icon': '🔄', 'text': f"{title} is due in {days_until} days."}

    # 6. Goal progress — only the single goal closest to completion, never all of them
    if extra_insight is None:
        cursor.execute("""
            SELECT goal_name, target_amount, current_amount FROM savings_goals
            WHERE user_id=%s AND target_amount > 0 AND current_amount < target_amount
            ORDER BY (current_amount / target_amount) DESC
            LIMIT 1
        """, (user_id,))
        goal_row = cursor.fetchone()
        if goal_row:
            g_name, g_target, g_current = goal_row[0], float(goal_row[1]), float(goal_row[2])
            g_pct = round((g_current / g_target) * 100)
            g_remaining = g_target - g_current
            extra_insight = {
                'icon': '🎯',
                'text': f"You're {g_pct}% towards your {g_name}. Save ₹{g_remaining:,.0f} more to reach it.",
            }

    # 7. Unusual spending — a single expense dominating this month's total
    if extra_insight is None and month_spent > 0:
        cursor.execute("""
            SELECT category, amount FROM expenses
            WHERE user_id=%s AND MONTH(expense_date)=MONTH(CURDATE()) AND YEAR(expense_date)=YEAR(CURDATE())
            ORDER BY amount DESC LIMIT 1
        """, (user_id,))
        biggest_row = cursor.fetchone()
        if biggest_row:
            cat, amt = biggest_row[0], float(biggest_row[1])
            if (amt / month_spent) > 0.25:
                extra_insight = {'icon': '⚠️', 'text': f"Highest single expense this month: ₹{amt:,.0f} on {cat}."}

    # 8. Positive reinforcement
    if extra_insight is None:
        if last_month > 0 and month_spent < last_month:
            extra_insight = {'icon': '😊', 'text': "Great job! You're spending less than last month."}
        elif budget and budget > 0 and budget_percentage < 50:
            extra_insight = {'icon': '😊', 'text': "You're comfortably within budget this month."}

    if extra_insight:
        insights.append(extra_insight)

    return insights


@app.route('/landing')
def landing():
    """Public landing page — served from templates/finora.html"""
    if 'user_id' in session:
        return redirect('/')
    return render_template('finora.html')

@app.route('/', methods=['GET', 'POST'])
def home():
    if 'user_id' not in session:
        return redirect('/landing')
        
    conn = get_db_connection()
    cursor = conn.cursor()

    process_due_auto_charges(session['user_id'], cursor, conn)
    
    # Handle direct dashboard Budget Control form submissions
    if request.method == 'POST' and 'budget' in request.form:
        new_budget = request.form['budget']
        cursor.execute("SELECT COUNT(*) FROM budgets")
        exists = cursor.fetchone()[0]
        if exists:
            cursor.execute("UPDATE budgets SET monthly_limit=%s", (new_budget,))
        else:
            cursor.execute("INSERT INTO budgets (monthly_limit) VALUES (%s)", (new_budget,))
        conn.commit()
        cursor.close(); conn.close()
        flash("Budget updated successfully!", "success")
        return redirect('/')
    
    # 1. Fetch total spending amount
    cursor.execute("""
        SELECT SUM(amount) 
        FROM expenses
        WHERE user_id=%s 
        """,
        (session['user_id'],)
    )
    total_spent = cursor.fetchone()[0]
    if total_spent is None:
        total_spent = 0

    # 1b. Current month spending — budget usage, top category, and insights
    # are all scoped to "this month" so the dashboard matches how people
    # actually think about a monthly budget (all-time totals live in the
    # Total Income / Total Spent / Net Balance cards instead).
    cursor.execute("""
        SELECT IFNULL(SUM(amount),0)
        FROM expenses
        WHERE MONTH(expense_date)=MONTH(CURDATE())
        AND YEAR(expense_date)=YEAR(CURDATE())
        AND user_id=%s
        """,
        (session['user_id'],)
    )
    month_spent = float(cursor.fetchone()[0])

    # 2. Total count of transaction entries logged
    cursor.execute("""
        SELECT COUNT(*) 
        FROM expenses
        WHERE user_id=%s
        """,
        (session['user_id'],)
    )
    expense_count = cursor.fetchone()[0]

    # 3. Fetch current monthly limit from budget profile
    cursor.execute(
        "SELECT monthly_limit FROM budgets LIMIT 1"
    )
    budget_row = cursor.fetchone()
    budget = float(budget_row[0]) if budget_row and budget_row[0] is not None else 0
    budget_left = budget - month_spent
    
    alert_message = None
    if budget > 0:
        budget_percentage = (month_spent / budget) * 100
        if budget_percentage >= 100:
            budget_status = "🔴 Budget Exceeded"
            alert_message = "🚨 Budget exceeded! You have crossed your monthly limit."
        elif budget_percentage >= 80:
            budget_status = "⚠️ Near Limit"
            alert_message = "⚠️ Warning! You have used more than 80% of your budget."
        else:
            budget_status = "🟢 Budget Healthy"
    else:
        budget_percentage = 0
        budget_status = "No Budget Set"

    # Note: budget-alert and recurring-due-soon notifications are generated
    # by the check_opportunistic_notifications() before_request hook, which
    # runs for every authenticated page (not just this one) — see near
    # create_notification() above. Nothing further needed here.

    # 4. Fetch maximum individual transaction value
    cursor.execute("""
    SELECT amount, category
    FROM expenses
    WHERE user_id=%s
    ORDER BY amount DESC
    LIMIT 1
    """,
    (session['user_id'],)
    )
    highest_expense = cursor.fetchone()

    # 5. Fetch the category with the highest total spend this month (not just count).
    # This stays month-scoped because it feeds Smart Insights ("Food is 41% of
    # spending this month"), which is inherently a this-month statement.
    cursor.execute("""
    SELECT category, SUM(amount)
    FROM expenses
    WHERE user_id=%s
    AND MONTH(expense_date)=MONTH(CURDATE())
    AND YEAR(expense_date)=YEAR(CURDATE())
    GROUP BY category
    ORDER BY SUM(amount) DESC
    LIMIT 1
    """,
    (session['user_id'],)
    )
    top_category_month = cursor.fetchone()

    # 5b. All-time top category (name, total spent, transaction count) — this is
    # what the "Top Category" KPI card shows now, since every other card in that
    # row (Total Income / Total Spent / Net Balance) is an all-time metric too.
    cursor.execute("""
    SELECT category, SUM(amount), COUNT(*)
    FROM expenses
    WHERE user_id=%s
    GROUP BY category
    ORDER BY SUM(amount) DESC
    LIMIT 1
    """,
    (session['user_id'],)
    )
    top_category = cursor.fetchone()

    # 6. Fetch recent entries layout sequence including Dates 
    cursor.execute("""
        SELECT DATE_FORMAT(expense_date, '%b %d, %Y'), category, description, amount
        FROM expenses
        WHERE user_id=%s
        ORDER BY expense_id DESC
        LIMIT 5
        """,
        (session['user_id'],)
    )
    recent_expenses = cursor.fetchall()

    # 7. Chart timeline metrics extraction mapping
    cursor.execute("""
    SELECT
    DATE(expense_date),
    SUM(amount)
    FROM expenses
    WHERE user_id=%s
    GROUP BY DATE(expense_date)
    ORDER BY DATE(expense_date)
    """,
    (session['user_id'],)
    )
    trend_data = cursor.fetchall()
    dates = []
    amounts = []
    for row in trend_data:
        dates.append(str(row[0]))
        amounts.append(float(row[1]))

    # 8. Fetch active saving targets tracking milestones
    cursor.execute("""
    SELECT goal_id, user_id, goal_name, target_amount, current_amount 
    FROM savings_goals 
    WHERE user_id=%s 
    LIMIT 3
    """, (session['user_id'],))
    goals = cursor.fetchall()

    # 9. Process distribution items mapped with float safe percentages array
    cursor.execute("""
    SELECT category, SUM(amount)
    FROM expenses
    WHERE user_id=%s
    GROUP BY category
    ORDER BY SUM(amount) DESC
    LIMIT 5
    """, (session['user_id'],))
    raw_categories = cursor.fetchall()
    
    top_categories = []
    for row in raw_categories:
        cat_name = row[0]
        cat_amount = float(row[1]) if row[1] else 0.0
        current_total = float(total_spent)
        
        cat_percent = round((cat_amount / current_total) * 100) if current_total > 0 else 0
        top_categories.append((cat_name, cat_amount, cat_percent))

    # 10. Extract comparative variances between periods
    this_month = month_spent  # already computed above for budget usage

    cursor.execute("""
    SELECT IFNULL(SUM(amount),0)
    FROM expenses
    WHERE MONTH(expense_date)=MONTH(CURDATE()-INTERVAL 1 MONTH)
    AND YEAR(expense_date)=YEAR(CURDATE()-INTERVAL 1 MONTH)
    AND user_id=%s
    """,(session['user_id'],))
    last_month = float(cursor.fetchone()[0])
    
    if last_month > 0:
        change_percentage = ((this_month - last_month) / last_month) * 100
    else:
        change_percentage = 0
    
    recommendation = ""
    if top_category_month:
        category = top_category_month[0]
        amount = float(top_category_month[1])
        reduction = round(amount * 0.1)
        recommendation = f"{category} is your highest spending category. Reducing it by ₹{reduction}/month (about 10%) could help you reach your savings goals faster."

    cursor.execute("""
    SELECT AVG(month_total)
    FROM (
        SELECT SUM(amount) AS month_total
        FROM expenses
        WHERE user_id=%s
        GROUP BY DATE_FORMAT(expense_date, '%Y-%m')
    ) monthly_data
    """, (session['user_id'],))
    forecast = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM recurring_expenses WHERE user_id=%s", (session['user_id'],))
    recurring_count = cursor.fetchone()[0]

    hour = datetime.now().hour
    greeting = "Good Morning" if hour < 12 else "Good Afternoon" if hour < 17 else "Good Evening"
        
    # 11. Balances Extractions: Projected Balance Variables
    cursor.execute("SELECT IFNULL(SUM(amount), 0) FROM settlements WHERE user_id=%s AND amount > 0 AND status='active'", (session['user_id'],))
    people_owe_you = float(cursor.fetchone()[0])
    
    cursor.execute("SELECT IFNULL(SUM(ABS(amount)), 0) FROM settlements WHERE user_id=%s AND amount < 0 AND status='active'", (session['user_id'],))
    you_owe_others = float(cursor.fetchone()[0])

    # 12. Total income logged by this user
    cursor.execute("SELECT IFNULL(SUM(amount), 0) FROM income WHERE user_id=%s", (session['user_id'],))
    total_income = float(cursor.fetchone()[0])

    projected_position = total_income - float(total_spent) + people_owe_you - you_owe_others

    # "Net Balance" (projected_position, above) conflates two different
    # things: actual cash movement (income vs. spending) and *pending*
    # balances that haven't been settled yet. That's why settling a
    # receivable makes it drop even though nothing bad happened — the
    # receivable stops being "pending" but was never logged as income, so
    # the number just loses it. cash_balance and net_outstanding split
    # these back apart cleanly:
    #   cash_balance    = actual money in vs. out (unaffected by balances at all)
    #   net_outstanding = what's still pending across open balances
    # projected_position is left untouched above for backward compatibility
    # with anything already reading it; new/updated templates should prefer
    # cash_balance + net_outstanding instead of one blended figure.
    cash_balance = total_income - float(total_spent)
    net_outstanding = people_owe_you - you_owe_others

    # 13. Goal Allocation & Available Cash — money already earmarked in a
    # savings goal isn't really "free" cash anymore, even though it hasn't
    # left the user's account. We subtract it from the net balance so the
    # dashboard reflects what's actually available to spend, not just what's
    # technically still sitting in the account. goal_allocation is always
    # reconstructable as SUM(goal_contributions.amount) too — current_amount
    # here is just the cached total for cheap reads.
    cursor.execute("SELECT IFNULL(SUM(current_amount), 0) FROM savings_goals WHERE user_id=%s", (session['user_id'],))
    goal_allocation = float(cursor.fetchone()[0])
    available_cash = projected_position - goal_allocation

    # 14. Smart Insights — generated from real data (budget, trend, category,
    # savings, recurring, goals, unusual spending) rather than hardcoded copy.
    insights = build_smart_insights(
        cursor=cursor,
        user_id=session['user_id'],
        budget=budget,
        budget_percentage=budget_percentage,
        month_spent=month_spent,
        last_month=last_month,
        change_percentage=change_percentage,
        top_category_month=top_category_month,
        total_income=total_income,
        total_expenses=float(total_spent),
    )

    conn.commit()
    cursor.close()
    conn.close()
    
    return render_template(
        'dashboard/dashboard.html',
        greeting=greeting,
        username=session["username"],
        display_name=session.get('display_name', session['username']),
        active_page='dashboard',
        total_spent=total_spent,
        expense_count=expense_count,
        recent_expenses=recent_expenses,
        budget=budget,
        budget_left=budget_left,
        budget_percentage=budget_percentage,
        highest_expense=highest_expense,
        top_category=top_category,
        dates=dates,
        amounts=amounts,
        budget_status=budget_status,
        goals=goals,
        top_categories=top_categories,
        this_month=this_month,
        last_month=last_month,
        change_percentage=change_percentage,
        insights=insights,
        forecast=forecast,
        recommendation=recommendation,
        alert_message=alert_message,
        recurring_count=recurring_count,
        goal_allocation=goal_allocation,
        available_cash=available_cash,
        projected_position=projected_position,
        cash_balance=cash_balance,
        net_outstanding=net_outstanding,
        total_income=total_income
    )

@app.route('/add-expense', methods=['GET','POST'])
def add_expense():
    if 'user_id' not in session:
        return redirect('/login')
    if request.method == 'POST':
        amount = request.form.get('amount')
        category = request.form.get('category') or 'Other'
        description = request.form.get('description', '')
        expense_date = request.form.get('expense_date')
        if not amount or not expense_date:
            flash("Amount and date are required to add an expense.", "error")
            return redirect('/expenses')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO expenses (amount, category, description, expense_date, user_id) VALUES (%s, %s, %s, %s, %s)", (amount, category, description, expense_date, session['user_id']))
        conn.commit()
        cursor.close()
        conn.close()
        flash("Expense added successfully!", "success")
        return redirect('/expenses')
    conn = get_db_connection(); cursor = conn.cursor()
    categories = get_categories(cursor)
    cursor.close(); conn.close()
    return render_template('expenses/add_expense.html', categories=categories)

@app.route('/expenses') 
def expenses():
    if 'user_id' not in session:
        return redirect('/login')
    search = request.args.get('search')
    sort = request.args.get('sort')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    show_income = request.args.get('show_income', '0')  # default: expenses only
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT SUM(amount) FROM expenses WHERE user_id=%s", (session['user_id'],))
    total_spent = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s", (session['user_id'],))
    expense_count = cursor.fetchone()[0] or 0
    avg_expense = round(total_spent / expense_count) if expense_count > 0 else 0
    
    cursor.execute("SELECT amount, description FROM expenses WHERE user_id=%s ORDER BY amount DESC LIMIT 1", (session['user_id'],))
    highest_row = cursor.fetchone()
    highest_amount = highest_row[0] if highest_row else 0
    highest_desc = highest_row[1] if highest_row and highest_row[1] else "No Description"

    # Combined, filtered, sorted transaction list — shared with /export so the
    # two never drift out of sync. Each row includes recurring_id (last
    # column) so the template can disable editing on recurring-generated rows.
    combined = fetch_filtered_transactions(
        cursor, session['user_id'], search=search, sort=sort,
        start_date=start_date, end_date=end_date, show_income=(show_income == '1')
    )

    categories = get_categories(cursor)

    cursor.close(); conn.close()
    
    return render_template('expenses/expenses.html', expenses=combined, search=search, start_date=start_date, end_date=end_date, total_spent=total_spent, expense_count=expense_count, avg_expense=avg_expense, highest_amount=highest_amount, highest_desc=highest_desc, username=session["username"], display_name=session.get('display_name', session['username']), active_page='expenses', show_income=show_income, categories=categories)

@app.route('/summary')
def summary():
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT SUM(amount) FROM expenses WHERE user_id=%s", (session['user_id'],))
    total = cursor.fetchone()
    cursor.close(); conn.close()
    return render_template('reports/summary.html', total=total[0])

@app.route('/test-db')
def test_db():
    conn = get_db_connection()
    return "Database Connected!"

@app.route('/delete/<int:expense_id>', methods=['POST'])
def delete_expense(expense_id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT recurring_id FROM expenses WHERE expense_id=%s AND user_id=%s", (expense_id, session['user_id']))
    row = cursor.fetchone()
    if row and row[0]:
        cursor.close(); conn.close()
        flash("This transaction was generated from a recurring expense. Manage it from the Recurring page.", "error")
        return redirect('/expenses')
    cursor.execute("DELETE FROM expenses WHERE expense_id=%s AND user_id=%s", (expense_id, session['user_id']))
    # Clear any balance that pointed at this expense — whether it owned the
    # expense ("I owe them" + Counts as expense) or merely referenced it
    # ("They owe me" linked to a bill you already logged). Either way the
    # balance itself survives (the debt/receivable didn't stop existing
    # just because the expense record did); it just becomes unlinked.
    cursor.execute(
        "UPDATE settlements SET linked_expense_id=NULL, counts_as_expense=0 WHERE linked_expense_id=%s AND user_id=%s",
        (expense_id, session['user_id'])
    )
    conn.commit(); cursor.close(); conn.close()
    flash("Expense deleted.", "success")
    return redirect('/expenses')

@app.route('/edit/<int:expense_id>', methods=['GET','POST'])
def edit_expense(expense_id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()

    cursor.execute("SELECT recurring_id FROM expenses WHERE expense_id=%s AND user_id=%s", (expense_id, session['user_id']))
    existing = cursor.fetchone()
    if existing and existing[0]:
        cursor.close(); conn.close()
        flash("This transaction was generated from a recurring expense. Manage it from the Recurring page.", "error")
        return redirect('/expenses')

    if request.method == 'POST':
        amount = request.form.get('amount')
        category = request.form.get('category') or 'Other'
        description = request.form.get('description', '')
        expense_date = request.form.get('expense_date')
        if not amount or not expense_date:
            flash("Amount and date are required to update an expense.", "error")
            cursor.close(); conn.close()
            return redirect('/expenses')
        cursor.execute("UPDATE expenses SET amount=%s, category=%s, description=%s, expense_date=%s WHERE expense_id=%s AND user_id=%s", (amount, category, description, expense_date, expense_id, session['user_id']))
        # If this expense is OWNED by a balance (created via "I owe them" +
        # Counts as expense), keep that balance's amount/date in sync rather
        # than letting the two drift — this is the one place besides the
        # Balances page itself where this expense's numbers can change.
        cursor.execute(
            "SELECT settlement_id, amount FROM settlements WHERE linked_expense_id=%s AND user_id=%s AND counts_as_expense=1",
            (expense_id, session['user_id'])
        )
        owning_settlement = cursor.fetchone()
        if owning_settlement:
            settlement_id, old_signed = owning_settlement
            new_signed = -float(amount) if float(old_signed) < 0 else float(amount)
            cursor.execute(
                "UPDATE settlements SET amount=%s, balance_date=%s WHERE settlement_id=%s AND user_id=%s",
                (new_signed, expense_date, settlement_id, session['user_id'])
            )
        conn.commit(); cursor.close(); conn.close()
        flash("Expense updated successfully!", "success")
        return redirect('/expenses')
    cursor.execute("SELECT * FROM expenses WHERE expense_id=%s AND user_id=%s", (expense_id, session['user_id']))
    expense = cursor.fetchone()
    categories = get_categories(cursor)
    cursor.close(); conn.close()
    return render_template('expenses/edit_expense.html', expense=expense, categories=categories)

@app.route('/breakdown')
def breakdown():
    # Redirect old /breakdown URL to the analytics tab in Reports
    return redirect('/monthly-report?tab=analytics')

@app.route('/export')
def export_csv():
    if 'user_id' not in session:
        return redirect('/login')
    search = request.args.get('search')
    sort = request.args.get('sort')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    show_income = request.args.get('show_income', '0') == '1'

    conn = get_db_connection(); cursor = conn.cursor()
    # Exports exactly what's currently filtered/sorted on the Expenses page —
    # same helper, same rules, so the two can never disagree.
    combined = fetch_filtered_transactions(
        cursor, session['user_id'], search=search, sort=sort,
        start_date=start_date, end_date=end_date, show_income=show_income
    )
    cursor.close(); conn.close()

    csv_data = "ID,Type,Amount,Category,Description,Date\n"
    for row in combined:
        row_id, amount, category, description, _formatted_date, raw_date, row_type, _recurring_id = row
        csv_data += (
            f"{row_id},{row_type},{amount},"
            f"{csv_escape(category)},{csv_escape(description)},{raw_date}\n"
        )

    filename = "transactions" if show_income else "expenses"
    if search:
        filename += f"_{search}"
    if start_date or end_date:
        filename += f"_{start_date or 'start'}_to_{end_date or 'end'}"
    filename += ".csv"
    return Response(csv_data, mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

@app.route('/exports')
def exports_hub():
    if 'user_id' not in session:
        return redirect('/login')
    return redirect('/monthly-report?tab=exports')

def balance_expense_description(peer_name, reason):
    """Shared description text for an expense created from a balance, so
    it's recognizable in the Expenses list without adding a new column
    there just to mark provenance."""
    return f"{reason} (owed to {peer_name})" if reason else f"Owed to {peer_name}"


def build_settlements_summary(cursor, user_id):
    """Fetches the full Balances page state fresh from the DB — summary
    totals, open balances, and recent settled history. Shared by the page's
    initial render and every AJAX mutation (add/edit/delete/settle), so the
    numbers a fetch() response shows always match a full reload."""
    cursor.execute("SELECT IFNULL(SUM(amount), 0) FROM settlements WHERE user_id=%s AND amount > 0 AND status='active'", (user_id,))
    owed_to_you = float(cursor.fetchone()[0])
    cursor.execute("SELECT IFNULL(SUM(ABS(amount)), 0) FROM settlements WHERE user_id=%s AND amount < 0 AND status='active'", (user_id,))
    you_owe = float(cursor.fetchone()[0])
    net_position = owed_to_you - you_owe

    cursor.execute("""
        SELECT s.settlement_id, s.peer_name, s.amount, s.reason,
               DATE_FORMAT(s.created_at, '%d %b %Y'),
               DATE_FORMAT(s.balance_date, '%Y-%m-%d'),
               DATE_FORMAT(s.balance_date, '%d %b %Y'),
               DATEDIFF(CURDATE(), s.balance_date),
               s.counts_as_expense, s.linked_expense_id, e.category
        FROM settlements s
        LEFT JOIN expenses e ON e.expense_id = s.linked_expense_id AND s.counts_as_expense = 1
        WHERE s.user_id=%s AND s.status='active'
        ORDER BY s.updated_at DESC
    """, (user_id,))
    active = [{
        'id': r[0], 'peer_name': r[1], 'amount': float(r[2]), 'reason': r[3],
        'created_display': r[4], 'balance_date': r[5], 'balance_date_display': r[6],
        'days_pending': r[7], 'status': 'active',
        'counts_as_expense': bool(r[8]), 'linked_expense_id': r[9], 'expense_category': r[10],
    } for r in cursor.fetchall()]

    cursor.execute("""
        SELECT s.settlement_id, s.peer_name, s.amount, s.reason,
               DATE_FORMAT(s.balance_date, '%Y-%m-%d'),
               DATE_FORMAT(s.balance_date, '%d %b %Y'),
               DATE_FORMAT(s.updated_at, '%Y-%m-%d'),
               DATE_FORMAT(s.updated_at, '%b %d, %Y'),
               DATE_FORMAT(s.created_at, '%d %b %Y'),
               s.counts_as_expense, s.linked_expense_id, e.category
        FROM settlements s
        LEFT JOIN expenses e ON e.expense_id = s.linked_expense_id AND s.counts_as_expense = 1
        WHERE s.user_id=%s AND s.status='settled'
        ORDER BY s.updated_at DESC LIMIT 20
    """, (user_id,))
    history = [{
        'id': r[0], 'peer_name': r[1], 'amount': float(r[2]), 'reason': r[3],
        'balance_date': r[4], 'balance_date_display': r[5],
        'settled_date': r[6], 'settled_display': r[7],
        'created_display': r[8], 'status': 'settled',
        'counts_as_expense': bool(r[9]), 'linked_expense_id': r[10], 'expense_category': r[11],
    } for r in cursor.fetchall()]

    # Recent expenses, for the "Link to expense" picker on "They owe me"
    # balances — this is a reference list only, not something the balance
    # ever owns/mutates. Capped to a reasonable recent window so the picker
    # stays usable rather than listing years of history.
    cursor.execute("""
        SELECT expense_id, amount, category, description, DATE_FORMAT(expense_date, '%d %b %Y')
        FROM expenses WHERE user_id=%s
        ORDER BY expense_date DESC, expense_id DESC LIMIT 40
    """, (user_id,))
    recent_expenses = [{
        'id': r[0],
        'label': f"₹{float(r[1]):,.0f} · {(r[3] or r[2] or 'Expense')} · {r[4]}",
    } for r in cursor.fetchall()]

    categories = get_categories(cursor)

    return {
        'owed_to_you': owed_to_you, 'you_owe': you_owe, 'net_position': net_position,
        'active': active, 'history': history, 'recent_expenses': recent_expenses,
        'categories': [{'name': name, 'icon': icon} for name, icon in categories],
    }


@app.route('/settlements', methods=['GET', 'POST'])
def settlements():
    if 'user_id' not in session:
        return redirect('/login')
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    conn = get_db_connection(); cursor = conn.cursor()

    if request.method == 'POST':
        peer_name = request.form.get('peer_name', '').strip()
        amount_raw = request.form.get('amount')
        flow_direction = request.form.get('direction', 'they_owe_me')
        reason = (request.form.get('reason') or '').strip()[:255] or None
        balance_date = request.form.get('balance_date') or date.today().isoformat()

        if not peer_name or not amount_raw:
            cursor.close(); conn.close()
            if is_ajax:
                return {'success': False, 'error': 'Person and amount are required.'}, 400
            flash("Person and amount are required.", "error")
            return redirect('/settlements')

        try:
            amount = float(amount_raw)
        except ValueError:
            amount = 0
        if amount <= 0:
            cursor.close(); conn.close()
            if is_ajax:
                return {'success': False, 'error': 'Enter an amount greater than zero.'}, 400
            flash("Enter an amount greater than zero.", "error")
            return redirect('/settlements')

        if flow_direction == 'owe_them':
            amount = -abs(amount)

        # "Counts as expense" only applies to "I owe them" — the money
        # hasn't left your account yet, so this is the only case where a
        # *new*, settlement-owned expense makes sense to create.
        #
        # "They owe me" never creates an expense. If you paid for a shared
        # bill, you already logged that full amount as your own expense —
        # this balance is just a *reference* to it (linked_expense_id set,
        # counts_as_expense left at 0), never something Finora creates,
        # edits, or deletes on its own.
        counts_as_expense = (
            request.form.get('counts_as_expense') is not None
            and flow_direction == 'owe_them'
        )
        referenced_expense_id = None
        if flow_direction == 'they_owe_me':
            raw_ref = request.form.get('linked_expense_id')
            if raw_ref:
                cursor.execute(
                    "SELECT expense_id FROM expenses WHERE expense_id=%s AND user_id=%s",
                    (raw_ref, session['user_id'])
                )
                if cursor.fetchone():
                    referenced_expense_id = raw_ref

        cursor.execute(
            "INSERT INTO settlements (user_id, peer_name, amount, status, reason, created_at, balance_date, linked_expense_id) "
            "VALUES (%s, %s, %s, 'active', %s, NOW(), %s, %s)",
            (session['user_id'], peer_name, amount, reason, balance_date, referenced_expense_id)
        )
        new_settlement_id = cursor.lastrowid

        if counts_as_expense:
            expense_category = request.form.get('expense_category') or 'Other'
            description = balance_expense_description(peer_name, reason)
            cursor.execute(
                "INSERT INTO expenses (amount, category, description, expense_date, user_id) VALUES (%s, %s, %s, %s, %s)",
                (abs(amount), expense_category, description, balance_date, session['user_id'])
            )
            linked_expense_id = cursor.lastrowid
            cursor.execute(
                "UPDATE settlements SET counts_as_expense=1, linked_expense_id=%s WHERE settlement_id=%s AND user_id=%s",
                (linked_expense_id, new_settlement_id, session['user_id'])
            )

        conn.commit()
        message = f"Added balance for {peer_name}."

        if is_ajax:
            data = build_settlements_summary(cursor, session['user_id'])
            cursor.close(); conn.close()
            return {'success': True, 'message': message, 'data': data}

        cursor.close(); conn.close()
        flash(message, "success")
        return redirect('/settlements')

    data = build_settlements_summary(cursor, session['user_id'])
    cursor.close(); conn.close()
    return render_template(
        'balances/balances.html',
        owed_to_you=data['owed_to_you'], you_owe=data['you_owe'], net_position=data['net_position'],
        active_balances=data['active'], history=data['history'],
        active_page='balances',
        username=session["username"], display_name=session.get('display_name', session['username'])
    )


@app.route('/api/settlements/data')
def settlements_data():
    """JSON feed of the full Balances page state — used to refresh the
    summary cards + Open Balances + Settled History after any mutation
    without a full reload."""
    if 'user_id' not in session:
        return {'error': 'unauthorized'}, 401
    conn = get_db_connection(); cursor = conn.cursor()
    data = build_settlements_summary(cursor, session['user_id'])
    cursor.close(); conn.close()
    return data


# Backward-compat: /balances redirects to /settlements
@app.route('/balances', methods=['GET', 'POST'])
def balances_redirect():
    return redirect('/settlements')


@app.route('/settle/<int:settlement_id>', methods=['POST'])
def settle_transaction(settlement_id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if 'user_id' not in session:
        return ({'success': False, 'error': 'Please log in again.'}, 401) if is_ajax else redirect('/login')

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "UPDATE settlements SET status='settled' WHERE settlement_id=%s AND user_id=%s AND status='active'",
        (settlement_id, session['user_id'])
    )
    conn.commit()
    message = "Balance marked as settled."

    if is_ajax:
        data = build_settlements_summary(cursor, session['user_id'])
        cursor.close(); conn.close()
        return {'success': True, 'message': message, 'data': data}

    cursor.close(); conn.close()
    flash(message, "success")
    return redirect('/settlements')


@app.route('/api/settlements/<int:settlement_id>/edit', methods=['POST'])
def edit_settlement(settlement_id):
    """Edit a balance's person/direction/amount/balance_date/reason —
    works for both open and settled balances, since correcting a mistake
    shouldn't require deleting and recreating a settled record. Editing
    never changes status: a settled balance stays settled unless the user
    explicitly reopens it via the separate reopen endpoint. created_at is
    never touched here. For a settled record, an optional settled_date
    updates updated_at (the field that drives "Date Settled") — updated_at
    is explicitly carried forward to its existing value otherwise, so a
    plain edit of reason/amount on a settled row can't silently shift its
    settled date even if the column has an ON UPDATE CURRENT_TIMESTAMP
    trigger at the DB level. Also keeps any linked expense in sync (see
    counts_as_expense handling below) instead of ever creating a duplicate."""
    if 'user_id' not in session:
        return {'success': False, 'error': 'Please log in again.'}, 401

    peer_name = request.form.get('peer_name', '').strip()
    flow_direction = request.form.get('direction', 'they_owe_me')
    amount_raw = request.form.get('amount')
    reason = (request.form.get('reason') or '').strip()[:255] or None
    balance_date = request.form.get('balance_date') or None
    settled_date = request.form.get('settled_date') or None

    if not peer_name:
        return {'success': False, 'error': 'Enter a name.'}, 400
    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return {'success': False, 'error': 'Enter an amount greater than zero.'}, 400

    signed_amount = -amount if flow_direction == 'owe_them' else amount

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT status, updated_at, counts_as_expense, linked_expense_id, balance_date "
        "FROM settlements WHERE settlement_id=%s AND user_id=%s",
        (settlement_id, session['user_id'])
    )
    row = cursor.fetchone()
    if not row:
        cursor.close(); conn.close()
        return {'success': False, 'error': 'Balance not found.'}, 404

    current_status, existing_updated_at, old_counts_as_expense, old_linked_expense_id, existing_balance_date = row
    new_updated_at = settled_date if (current_status == 'settled' and settled_date) else existing_updated_at
    resolved_balance_date = balance_date or existing_balance_date

    # counts_as_expense=1 means this settlement OWNS the linked expense — it
    # created it and is responsible for keeping it in sync or deleting it.
    # counts_as_expense=0 with linked_expense_id set means the link is a
    # REFERENCE to an expense the user already had (the "they owe me"
    # case) — Finora must never create, update, or delete that expense.
    # Getting this branch wrong is exactly how a referenced expense would
    # get silently deleted, so every path below is explicit about which
    # case it's in.
    old_was_owned = bool(old_counts_as_expense) and old_linked_expense_id is not None
    description = balance_expense_description(peer_name, reason)
    new_linked_expense_id = None
    new_counts_as_expense = False

    if flow_direction == 'owe_them':
        counts_as_expense_requested = request.form.get('counts_as_expense') is not None
        expense_category = request.form.get('expense_category') or 'Other'
        if old_was_owned and counts_as_expense_requested:
            # Still owned, still on — sync the existing owned expense in place.
            cursor.execute(
                "UPDATE expenses SET amount=%s, category=%s, description=%s, expense_date=%s WHERE expense_id=%s AND user_id=%s",
                (amount, expense_category, description, resolved_balance_date, old_linked_expense_id, session['user_id'])
            )
            new_linked_expense_id = old_linked_expense_id
            new_counts_as_expense = True
        elif old_was_owned and not counts_as_expense_requested:
            # Toggled off — this settlement owns the expense, so it's the
            # one responsible for cleaning it up.
            cursor.execute(
                "DELETE FROM expenses WHERE expense_id=%s AND user_id=%s",
                (old_linked_expense_id, session['user_id'])
            )
        elif not old_was_owned and counts_as_expense_requested:
            # Newly turned on — create a fresh owned expense. Any old
            # linked_expense_id here was a they-owe-me *reference*, which
            # is simply left untouched (not deleted) since we never owned it.
            cursor.execute(
                "INSERT INTO expenses (amount, category, description, expense_date, user_id) VALUES (%s, %s, %s, %s, %s)",
                (amount, expense_category, description, resolved_balance_date, session['user_id'])
            )
            new_linked_expense_id = cursor.lastrowid
            new_counts_as_expense = True
        # else: wasn't owned, still not requested — nothing to do, stays unlinked.

    else:  # they_owe_me
        if old_was_owned:
            # Direction flipped away from "I owe them" — the accrued debt
            # expense this settlement created no longer applies, so it's
            # deleted (this settlement still owns it, so it's still ours to remove).
            cursor.execute(
                "DELETE FROM expenses WHERE expense_id=%s AND user_id=%s",
                (old_linked_expense_id, session['user_id'])
            )
        raw_ref = request.form.get('linked_expense_id')
        if raw_ref:
            cursor.execute(
                "SELECT expense_id FROM expenses WHERE expense_id=%s AND user_id=%s",
                (raw_ref, session['user_id'])
            )
            if cursor.fetchone():
                new_linked_expense_id = raw_ref
        # counts_as_expense stays False — this is always a reference, never owned.

    cursor.execute(
        "UPDATE settlements SET peer_name=%s, amount=%s, reason=%s, "
        "balance_date=COALESCE(%s, balance_date), updated_at=%s, "
        "counts_as_expense=%s, linked_expense_id=%s "
        "WHERE settlement_id=%s AND user_id=%s",
        (peer_name, signed_amount, reason, balance_date, new_updated_at,
         1 if new_counts_as_expense else 0, new_linked_expense_id,
         settlement_id, session['user_id'])
    )
    conn.commit()

    data = build_settlements_summary(cursor, session['user_id'])
    cursor.close(); conn.close()
    return {'success': True, 'message': f"Updated balance for {peer_name}.", 'data': data}


@app.route('/api/settlements/<int:settlement_id>/reopen', methods=['POST'])
def reopen_settlement(settlement_id):
    """Move a settled balance back to Open Balances. A distinct, explicit
    action from Edit — editing a settled record must never silently
    reopen it."""
    if 'user_id' not in session:
        return {'success': False, 'error': 'Please log in again.'}, 401

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "UPDATE settlements SET status='active' WHERE settlement_id=%s AND user_id=%s AND status='settled'",
        (settlement_id, session['user_id'])
    )
    if cursor.rowcount == 0:
        cursor.close(); conn.close()
        return {'success': False, 'error': 'Balance not found or not settled.'}, 404
    conn.commit()

    data = build_settlements_summary(cursor, session['user_id'])
    cursor.close(); conn.close()
    return {'success': True, 'message': 'Balance reopened.', 'data': data}


@app.route('/api/settlements/<int:settlement_id>/delete', methods=['POST'])
def delete_settlement(settlement_id):
    """Delete a balance outright — open or settled. Only cascade-deletes the
    linked expense when this settlement *owns* it (counts_as_expense=1, the
    "I owe them" case). A merely-referenced expense (the "they owe me"
    case) is the user's own independent record — deleting the balance must
    never delete that."""
    if 'user_id' not in session:
        return {'success': False, 'error': 'Please log in again.'}, 401

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT counts_as_expense, linked_expense_id FROM settlements WHERE settlement_id=%s AND user_id=%s",
        (settlement_id, session['user_id'])
    )
    row = cursor.fetchone()
    if not row:
        cursor.close(); conn.close()
        return {'success': False, 'error': 'Balance not found.'}, 404
    owns_linked_expense, linked_expense_id = row

    cursor.execute(
        "DELETE FROM settlements WHERE settlement_id=%s AND user_id=%s",
        (settlement_id, session['user_id'])
    )
    if owns_linked_expense and linked_expense_id:
        cursor.execute(
            "DELETE FROM expenses WHERE expense_id=%s AND user_id=%s",
            (linked_expense_id, session['user_id'])
        )
    conn.commit()

    data = build_settlements_summary(cursor, session['user_id'])
    cursor.close(); conn.close()
    return {'success': True, 'message': 'Balance deleted.', 'data': data}


@app.route('/monthly-report')
def monthly_report():
    if 'user_id' not in session:
        return redirect('/login')

    active_tab = request.args.get('tab', 'overview')

    conn = get_db_connection(); cursor = conn.cursor()

    cursor.execute("""
        SELECT DATE_FORMAT(expense_date, '%Y-%m'), SUM(amount)
        FROM expenses WHERE user_id=%s
        GROUP BY DATE_FORMAT(expense_date, '%Y-%m')
        ORDER BY DATE_FORMAT(expense_date, '%Y-%m') DESC
    """, (session['user_id'],))
    report_data = [(r[0], float(r[1])) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT category, SUM(amount) FROM expenses
        WHERE user_id=%s
          AND MONTH(expense_date)=MONTH(CURDATE())
          AND YEAR(expense_date)=YEAR(CURDATE())
        GROUP BY category ORDER BY SUM(amount) DESC
    """, (session['user_id'],))
    raw_breakdown = cursor.fetchall()
    category_breakdown = [(r[0], float(r[1])) for r in raw_breakdown]
    categories = [r[0] for r in category_breakdown]
    cat_amounts = [r[1] for r in category_breakdown]
    top_month_category = category_breakdown[0][0] if category_breakdown else None

    cursor.execute("""
        SELECT DATE(expense_date), SUM(amount)
        FROM expenses WHERE user_id=%s
        GROUP BY DATE(expense_date)
        ORDER BY DATE(expense_date)
    """, (session['user_id'],))
    trend_rows = cursor.fetchall()
    trend_dates   = [str(r[0]) for r in trend_rows]
    trend_amounts = [float(r[1]) for r in trend_rows]

    cursor.execute("SELECT IFNULL(SUM(amount),0) FROM income WHERE user_id=%s", (session['user_id'],))
    total_income = float(cursor.fetchone()[0])

    cursor.execute("SELECT IFNULL(SUM(amount),0) FROM expenses WHERE user_id=%s", (session['user_id'],))
    total_expenses = float(cursor.fetchone()[0])

    today = date.today()
    days_passed = today.day if today.day > 0 else 1
    days_in_month = calendar.monthrange(today.year, today.month)[1]  # actual days in this month (28-31), was hardcoded 30
    days_remaining = max(days_in_month - days_passed, 0)

    cursor.execute("""
        SELECT IFNULL(SUM(amount),0) FROM expenses
        WHERE user_id=%s
          AND MONTH(expense_date)=MONTH(CURDATE())
          AND YEAR(expense_date)=YEAR(CURDATE())
    """, (session['user_id'],))
    spent_this_month = float(cursor.fetchone()[0])

    avg_daily         = spent_this_month / days_passed
    forecast          = round(avg_daily * days_in_month)
    projected_savings = round(total_income - forecast)

    cursor.execute("""
        SELECT description, category, amount FROM expenses
        WHERE user_id=%s ORDER BY amount DESC LIMIT 5
    """, (session['user_id'],))
    largest_expenses = [(r[0], r[1], float(r[2])) for r in cursor.fetchall()]

    # Month-over-month comparison (same calc used on the dashboard) — the
    # report template expects this_month/last_month/change_percentage too.
    cursor.execute("""
        SELECT IFNULL(SUM(amount),0) FROM expenses
        WHERE MONTH(expense_date)=MONTH(CURDATE())
        AND YEAR(expense_date)=YEAR(CURDATE())
        AND user_id=%s
    """, (session['user_id'],))
    this_month = float(cursor.fetchone()[0])

    cursor.execute("""
        SELECT IFNULL(SUM(amount),0) FROM expenses
        WHERE MONTH(expense_date)=MONTH(CURDATE()-INTERVAL 1 MONTH)
        AND YEAR(expense_date)=YEAR(CURDATE()-INTERVAL 1 MONTH)
        AND user_id=%s
    """, (session['user_id'],))
    last_month = float(cursor.fetchone()[0])

    if last_month > 0:
        change_percentage = ((this_month - last_month) / last_month) * 100
    else:
        change_percentage = 0

    # Goal Allocation & Available Cash — same model as the dashboard, so the
    # two pages never disagree about how much of the user's net position is
    # actually free to spend versus already earmarked for a savings goal.
    cursor.execute("SELECT IFNULL(SUM(current_amount), 0) FROM savings_goals WHERE user_id=%s", (session['user_id'],))
    goal_allocation = float(cursor.fetchone()[0])
    available_cash = (total_income - total_expenses) - goal_allocation

    cursor.close(); conn.close()

    return render_template(
        'reports/monthly_report.html',
        active_tab=active_tab,
        active_page='reports',
        report_data=report_data,
        category_breakdown=category_breakdown,
        categories=categories,
        cat_amounts=cat_amounts,
        top_month_category=top_month_category,
        trend_dates=trend_dates,
        trend_amounts=trend_amounts,
        total_income=total_income,
        total_expenses=total_expenses,
        forecast=forecast,
        projected_savings=projected_savings,
        spent_this_month=spent_this_month,
        largest_expenses=largest_expenses,
        this_month=this_month,
        last_month=last_month,
        change_percentage=change_percentage,
        today_day=days_passed,
        avg_daily=avg_daily,
        days_remaining=days_remaining,
        goal_allocation=goal_allocation,
        available_cash=available_cash,
        username=session["username"],
        display_name=session.get('display_name', session['username']),
    )

GOAL_ICON_CHOICES = ['🎯', '✈️', '🏠', '💻', '🚗', '💍', '🎓', '👶', '🏥', '🎁', '📱', '🏖️']
GOAL_COLOR_CHOICES = [
    ('#4edea3', 'Green'), ('#adc6ff', 'Blue'), ('#facc15', 'Amber'),
    ('#f87171', 'Red'), ('#c084fc', 'Purple'), ('#5eead4', 'Teal'),
]


def compute_goal_display(current, target, target_date, closed_at):
    """The single source of truth for a goal's displayed state — always
    derived live, never stored. Precedence: Closed > Target Reached >
    Overdue > In Progress (a goal that reached its target is never shown
    as merely "overdue", even if its date has since passed)."""
    if closed_at:
        return 'closed'
    if target > 0 and current >= target:
        return 'target_reached'
    if target_date and target_date < datetime.now():
        return 'overdue'
    return 'in_progress'


def motivation_for_percent(percent, status):
    """Encouragement copy keyed off percent — single source shared by the
    initial page render and every AJAX summary response, so the message
    never disagrees with itself after a deposit/withdraw/edit."""
    if status == 'target_reached' or percent >= 100:
        return "Goal achieved 🎉", "text-amber-400"
    if percent >= 90:
        return "Almost there! Keep going 🔥", "text-primary"
    if percent >= 50:
        return "Halfway there 🎉", "text-primary"
    if percent >= 25:
        return "You're making solid progress 💪", "text-secondary"
    if percent > 0:
        return "You're almost halfway there!", "text-secondary"
    return "You're just getting started 🚀", "text-on-surface-variant"


def build_goal_summary(cursor, goal_id, user_id):
    """Fetches a goal fresh from the DB and returns the exact same computed
    shape used to render the Goal Details page. Used both for the initial
    page load and for every AJAX mutation (deposit/withdraw/edit/delete),
    so the ring, stat tiles, milestone, and status the user sees after an
    AJAX update are guaranteed to match what a full reload would show —
    there's only one place that computes a goal's displayed state."""
    cursor.execute(
        "SELECT goal_id, goal_name, target_amount, current_amount, "
        "DATE_FORMAT(target_date, '%Y-%m-%d'), description, icon, color, closed_at "
        "FROM savings_goals WHERE goal_id=%s AND user_id=%s",
        (goal_id, user_id)
    )
    row = cursor.fetchone()
    if not row:
        return None

    g_id, name, target, current, target_date, desc, icon, color, closed_at = row
    target = float(target); current = float(current)
    target_date_obj = datetime.strptime(target_date, '%Y-%m-%d') if target_date else None
    display_status = compute_goal_display(current, target, target_date_obj, closed_at)
    percent = round((current / target * 100), 1) if target > 0 else 0
    bar_width = min(percent, 100)

    pace_message = None
    monthly_needed = None
    if display_status == 'in_progress' and target_date_obj:
        months_left = max((target_date_obj.year - datetime.now().year) * 12 + (target_date_obj.month - datetime.now().month), 1)
        monthly_needed = round((target - current) / months_left)
        pace_message = f"Need ₹{monthly_needed:,.0f}/month to reach on time"

    next_milestone = None
    if display_status in ('in_progress', 'overdue') and target > 0 and percent < 100:
        for threshold in (25, 50, 75, 100):
            if percent < threshold:
                amount_needed = round((threshold / 100 * target) - current)
                next_milestone = {'pct': threshold, 'amount': max(amount_needed, 0)}
                break

    motivation_text, motivation_class = motivation_for_percent(percent, display_status)

    return {
        'id': g_id, 'name': name, 'target': target, 'current': current,
        'remaining': max(target - current, 0), 'percent': percent, 'bar_width': bar_width,
        'target_date': target_date, 'target_date_display': target_date_obj.strftime('%d %b %Y') if target_date_obj else None,
        'description': desc or '', 'icon': icon or '🎯', 'color': color or '#4edea3',
        'status': display_status, 'pace_message': pace_message, 'monthly_needed': monthly_needed,
        'next_milestone': next_milestone,
        'motivation_text': motivation_text, 'motivation_class': motivation_class,
        'is_closed': closed_at is not None,
    }


@app.route('/goals')
def goals():
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT goal_id, goal_name, target_amount, current_amount, target_date, "
        "description, icon, color, closed_at "
        "FROM savings_goals WHERE user_id=%s ORDER BY closed_at IS NOT NULL, goal_id DESC",
        (session['user_id'],)
    )
    raw_goals = cursor.fetchall()

    # Reserved for Goals / Available Balance / Overall Progress only ever
    # look at open goals' current_amount — a closed goal's current_amount
    # is always 0 by the time it's closed, so this needs no special-casing.
    total_saved_sum = sum(float(g[3]) for g in raw_goals if g[8] is None)
    total_target_sum = sum(float(g[2]) for g in raw_goals if g[8] is None)
    overall_progress_pct = round((total_saved_sum / total_target_sum * 100), 1) if total_target_sum > 0 else 0.0

    active_goals, closed_goals = [], []
    active_count = 0
    now = datetime.now()

    for row in raw_goals:
        g_id, name, target, current, target_date, desc, icon, color, closed_at = row
        target = float(target)
        current = float(current)
        target_date_obj = datetime.strptime(str(target_date), '%Y-%m-%d') if target_date else None

        display_status = compute_goal_display(current, target, target_date_obj, closed_at)
        percent = round((current / target * 100), 1) if target > 0 else 0
        bar_width = 100 if percent >= 100 else (max(percent, 2) if percent > 0 else 0)

        pace_message = None
        if display_status == 'in_progress' and target_date_obj:
            months_left = max((target_date_obj.year - now.year) * 12 + (target_date_obj.month - now.month), 1)
            pace_message = f"Need ₹{round((target - current) / months_left):,.0f}/month to reach on time"

        cursor.execute(
            "SELECT amount, note, DATE_FORMAT(created_at, '%b %d, %Y') FROM goal_contributions "
            "WHERE goal_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT 1",
            (g_id, session['user_id'])
        )
        last_row = cursor.fetchone()
        last_contribution = None
        if last_row:
            last_contribution = {
                'amount': float(last_row[0]), 'note': last_row[1], 'date': last_row[2],
                'is_withdrawal': float(last_row[0]) < 0,
            }

        goal_dict = {
            'id': g_id, 'name': name, 'target': target, 'current': current,
            'remaining': max(target - current, 0), 'percent': percent, 'bar_width': bar_width,
            'target_date': target_date_obj.strftime('%b %Y') if target_date_obj else None,
            'pace_message': pace_message, 'description': desc or '',
            'icon': icon or '🎯', 'color': color or '#4edea3',
            'status': display_status, 'last_contribution': last_contribution,
            'closed_at_display': closed_at.strftime('%b %d, %Y') if closed_at else None,
        }

        if display_status == 'closed':
            closed_goals.append(goal_dict)
        else:
            active_goals.append(goal_dict)
            active_count += 1

    cursor.execute("SELECT IFNULL(SUM(amount), 0) FROM income WHERE user_id=%s", (session['user_id'],))
    total_income = float(cursor.fetchone()[0])
    cursor.execute("SELECT IFNULL(SUM(amount), 0) FROM expenses WHERE user_id=%s", (session['user_id'],))
    total_expenses = float(cursor.fetchone()[0])
    total_balance = total_income - total_expenses
    reserved_for_goals = total_saved_sum
    available_balance = total_balance - reserved_for_goals

    cursor.close(); conn.close()
    return render_template(
        'goals/goals.html',
        goals=active_goals,
        closed_goals=closed_goals,
        active_count=active_count,
        total_target=total_target_sum,
        overall_progress=overall_progress_pct,
        total_balance=total_balance,
        reserved_for_goals=reserved_for_goals,
        available_balance=available_balance,
        active_page='goals',
        username=session["username"],
        display_name=session.get('display_name', session['username'])
    )


@app.route('/goals/<int:goal_id>')
def goal_details(goal_id):
    """The Goal Details page — deposit/withdraw/edit/close all live here now
    instead of a dashboard modal, per the finalized design."""
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    goal = build_goal_summary(cursor, goal_id, session['user_id'])
    cursor.close(); conn.close()

    if not goal:
        flash("Goal not found.", "error")
        return redirect('/goals')

    return render_template(
        'goals/goal_details.html',
        goal=goal,
        icon_choices=GOAL_ICON_CHOICES,
        color_choices=GOAL_COLOR_CHOICES,
        active_page='goals',
        username=session["username"],
        display_name=session.get('display_name', session['username'])
    )


@app.route('/add-goal', methods=['GET', 'POST'])
def add_goal():
    if 'user_id' not in session:
        return redirect('/login')
    if request.method == 'POST':
        goal_name = request.form['goal_name']
        target_amount = request.form['target_amount']
        current_amount = request.form.get('current_amount') or 0
        target_date = request.form.get('target_date') or None
        description = request.form.get('description') or None
        icon = request.form.get('icon') or '🎯'
        color = request.form.get('color') or '#4edea3'

        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount, target_date, description, icon, color) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (session['user_id'], goal_name, target_amount, current_amount, target_date, description, icon, color)
        )
        goal_id = cursor.lastrowid

        starting_amount = float(current_amount or 0)
        if starting_amount > 0:
            cursor.execute(
                "INSERT INTO goal_contributions (goal_id, user_id, amount, note) VALUES (%s, %s, %s, %s)",
                (goal_id, session['user_id'], starting_amount, "Starting balance")
            )

        conn.commit(); cursor.close(); conn.close()
        flash("Goal created!", "success")
        return redirect('/goals/' + str(goal_id))

    return render_template(
        'goals/goal_form.html',
        icon_choices=GOAL_ICON_CHOICES,
        color_choices=GOAL_COLOR_CHOICES,
        active_page='goals',
        username=session['username'],
        display_name=session.get('display_name', session['username'])
    )


@app.route('/edit-goal/<int:goal_id>', methods=['GET', 'POST'])
def edit_goal(goal_id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()

    cursor.execute(
        "SELECT goal_name, target_amount, DATE_FORMAT(target_date, '%Y-%m-%d'), description, icon, color, closed_at, current_amount "
        "FROM savings_goals WHERE goal_id=%s AND user_id=%s",
        (goal_id, session['user_id'])
    )
    row = cursor.fetchone()
    if not row:
        cursor.close(); conn.close()
        flash("Goal not found.", "error")
        return redirect('/goals')

    # Closed goals are read-only — editing requires restoring first.
    if row[6] is not None:
        cursor.close(); conn.close()
        flash("This goal is closed. Restore it before editing.", "error")
        return redirect('/goals/' + str(goal_id))

    if request.method == 'POST':
        goal_name = request.form['goal_name']
        target_amount = request.form['target_amount']
        target_date = request.form.get('target_date') or None
        description = request.form.get('description') or None
        icon = request.form.get('icon') or '🎯'
        color = request.form.get('color') or '#4edea3'

        old_target = float(row[1])
        new_target = float(target_amount or 0)
        current_amount = float(row[7])
        was_reached = old_target > 0 and current_amount >= old_target
        now_reached = new_target > 0 and current_amount >= new_target

        cursor.execute(
            "UPDATE savings_goals SET goal_name=%s, target_amount=%s, target_date=%s, description=%s, icon=%s, color=%s "
            "WHERE goal_id=%s AND user_id=%s",
            (goal_name, target_amount, target_date, description, icon, color, goal_id, session['user_id'])
        )
        conn.commit(); cursor.close(); conn.close()

        # Target-amount edits can silently flip the milestone state — always
        # say so explicitly rather than letting the badge just vanish/appear
        # with no explanation (and never treat a target-lowering "reach" as
        # an achievement worth celebrating, since no money was actually saved).
        if was_reached and not now_reached:
            flash("Target updated. This goal is back in progress.", "info")
        elif now_reached and not was_reached:
            flash("Target updated — this goal now shows Target Reached.", "info")
        else:
            flash("Goal updated!", "success")
        return redirect('/goals/' + str(goal_id))

    goal = {
        'id': goal_id, 'name': row[0], 'target': float(row[1]), 'target_date': row[2],
        'description': row[3] or '', 'icon': row[4] or '🎯', 'color': row[5] or '#4edea3',
        'current': float(row[7]),
    }
    cursor.close(); conn.close()
    return render_template(
        'goals/goal_form.html',
        goal=goal,
        icon_choices=GOAL_ICON_CHOICES,
        color_choices=GOAL_COLOR_CHOICES,
        active_page='goals',
        username=session['username'],
        display_name=session.get('display_name', session['username'])
    )


@app.route('/update-goal/<int:goal_id>', methods=['POST'])
def update_goal(goal_id):
    """Deposit into or withdraw from a goal. Every call writes a signed row
    to goal_contributions (the ledger) *and* updates current_amount (the
    cache) in the same transaction, so the cache is always independently
    reconstructable by summing the ledger. Status is never touched here —
    it's derived live from current_amount every time the goal is displayed.

    Responds with JSON when called via fetch (the normal path — Manage
    Savings submits through JS so the ring/stat tiles can animate in place
    instead of a full reload) and falls back to flash+redirect for a
    non-JS form submission."""
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if 'user_id' not in session:
        return ({'success': False, 'error': 'Please log in again.'}, 401) if is_ajax else redirect('/login')

    action_type = request.form.get('action_type')
    note = (request.form.get('note') or '').strip()[:255] or None

    try:
        raw_amount = float(request.form.get('added_amount') or 0)
    except ValueError:
        raw_amount = 0

    def fail(message, status=400):
        if is_ajax:
            return {'success': False, 'error': message}, status
        flash(message, "error")
        return redirect('/goals/' + str(goal_id))

    if raw_amount <= 0:
        return fail("Enter an amount greater than zero.")

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT current_amount, target_amount, closed_at, goal_name FROM savings_goals WHERE goal_id=%s AND user_id=%s",
        (goal_id, session['user_id'])
    )
    row = cursor.fetchone()
    if not row:
        cursor.close(); conn.close()
        return fail("Goal not found.", 404)
    if row[2] is not None:
        cursor.close(); conn.close()
        return fail("This goal is closed. Restore it before adding or withdrawing money.")

    current_amount = float(row[0])
    target_amount = float(row[1])
    goal_name = row[3]

    if action_type == 'withdraw':
        if raw_amount > current_amount:
            cursor.close(); conn.close()
            return fail(f"Only ₹{current_amount:,.0f} is currently available.")
    else:
        if target_amount > 0 and (current_amount + raw_amount) > target_amount:
            cursor.close(); conn.close()
            return fail("Amount exceeds the remaining target.")

    signed_amount = -raw_amount if action_type == 'withdraw' else raw_amount

    cursor.execute(
        "INSERT INTO goal_contributions (goal_id, user_id, amount, note) VALUES (%s, %s, %s, %s)",
        (goal_id, session['user_id'], signed_amount, note)
    )
    cursor.execute(
        "UPDATE savings_goals SET current_amount = current_amount + %s WHERE goal_id=%s AND user_id=%s",
        (signed_amount, goal_id, session['user_id'])
    )

    # Goal milestone notification — only deposits can "reach" a milestone
    # (withdrawing moves you further from one, never triggers this).
    # Compares the percent just before vs. just after this specific
    # deposit, so only a threshold actually crossed *by this action* fires
    # — not every threshold already passed on some earlier deposit.
    # dedup_key (e.g. 'goal-14-50pct') means the same goal+threshold can
    # never notify twice even if this code path runs again later.
    #
    # Wrapped in its own try/except: the deposit/withdrawal itself must
    # commit regardless of whether the notification step succeeds — a
    # notification hiccup must never block the actual accounting action.
    try:
        if action_type != 'withdraw' and target_amount > 0:
            old_percent = (current_amount / target_amount) * 100
            new_percent = ((current_amount + signed_amount) / target_amount) * 100
            for threshold in (25, 50, 75, 100):
                if old_percent < threshold <= new_percent:
                    prefs = get_notification_prefs(cursor, session['user_id'])
                    if prefs['goal_milestones']:
                        if threshold >= 100:
                            create_notification(
                                cursor, session['user_id'], icon='🎯',
                                title="Target reached! 🎉",
                                message=f"You've hit your target for \"{goal_name}\".",
                                link=f"/goals/{goal_id}",
                                dedup_key=f"goal-{goal_id}-100pct",
                            )
                        else:
                            create_notification(
                                cursor, session['user_id'], icon='🎯',
                                title=f"{threshold}% milestone reached",
                                message=f"You're now {threshold}% of the way to \"{goal_name}\".",
                                link=f"/goals/{goal_id}",
                                dedup_key=f"goal-{goal_id}-{threshold}pct",
                            )
    except Exception as e:
        print(f"[notifications] goal milestone check failed (user_id={session['user_id']}, goal_id={goal_id}): {e}")

    conn.commit()

    if action_type == 'withdraw':
        message = f"₹{raw_amount:,.0f} withdrawn — it's back in your Available Balance."
    else:
        message = f"₹{raw_amount:,.0f} added to your goal."

    if is_ajax:
        goal_summary = build_goal_summary(cursor, goal_id, session['user_id'])
        cursor.close(); conn.close()
        return {'success': True, 'message': message, 'goal': goal_summary}

    cursor.close(); conn.close()
    flash(message, "success")
    return redirect('/goals/' + str(goal_id))


@app.route('/api/goals/<int:goal_id>/contributions/<int:contribution_id>/edit', methods=['POST'])
def edit_goal_contribution(goal_id, contribution_id):
    """Edit a single ledger entry's amount and/or note. The deposit/withdraw
    *type* is intentionally immutable here — flipping the sign of a past
    transaction is a different transaction, not an edit of this one, so
    that's not exposed. Both the ledger row and the goal's cached
    current_amount are corrected in the same transaction, same invariant
    as every other write to this table."""
    if 'user_id' not in session:
        return {'success': False, 'error': 'Please log in again.'}, 401

    note = (request.form.get('note') or '').strip()[:255] or None
    try:
        new_magnitude = float(request.form.get('amount') or 0)
    except ValueError:
        new_magnitude = 0
    if new_magnitude <= 0:
        return {'success': False, 'error': 'Enter an amount greater than zero.'}, 400

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT amount FROM goal_contributions WHERE contribution_id=%s AND goal_id=%s AND user_id=%s",
        (contribution_id, goal_id, session['user_id'])
    )
    contrib_row = cursor.fetchone()
    if not contrib_row:
        cursor.close(); conn.close()
        return {'success': False, 'error': 'Transaction not found.'}, 404

    cursor.execute(
        "SELECT current_amount, target_amount, closed_at FROM savings_goals WHERE goal_id=%s AND user_id=%s",
        (goal_id, session['user_id'])
    )
    goal_row = cursor.fetchone()
    if not goal_row:
        cursor.close(); conn.close()
        return {'success': False, 'error': 'Goal not found.'}, 404
    if goal_row[2] is not None:
        cursor.close(); conn.close()
        return {'success': False, 'error': 'This goal is closed and read-only.'}, 400

    old_signed = float(contrib_row[0])
    current_amount = float(goal_row[0])
    target_amount = float(goal_row[1])
    is_deposit = old_signed >= 0
    new_signed = new_magnitude if is_deposit else -new_magnitude
    projected_current = current_amount - old_signed + new_signed

    if is_deposit:
        if target_amount > 0 and projected_current > target_amount:
            cursor.close(); conn.close()
            return {'success': False, 'error': 'Amount exceeds the remaining target.'}, 400
    else:
        available_before_this = current_amount - old_signed
        if new_magnitude > available_before_this:
            cursor.close(); conn.close()
            return {'success': False, 'error': f'Only ₹{available_before_this:,.0f} is currently available.'}, 400

    cursor.execute(
        "UPDATE goal_contributions SET amount=%s, note=%s WHERE contribution_id=%s AND user_id=%s",
        (new_signed, note, contribution_id, session['user_id'])
    )
    cursor.execute(
        "UPDATE savings_goals SET current_amount=%s WHERE goal_id=%s AND user_id=%s",
        (max(projected_current, 0), goal_id, session['user_id'])
    )
    conn.commit()

    goal_summary = build_goal_summary(cursor, goal_id, session['user_id'])
    cursor.close(); conn.close()
    return {'success': True, 'message': 'Transaction updated.', 'goal': goal_summary}


@app.route('/api/goals/<int:goal_id>/contributions/<int:contribution_id>/delete', methods=['POST'])
def delete_goal_contribution(goal_id, contribution_id):
    """Delete a single ledger entry and reverse its effect on the goal's
    cached current_amount in the same transaction."""
    if 'user_id' not in session:
        return {'success': False, 'error': 'Please log in again.'}, 401

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT amount FROM goal_contributions WHERE contribution_id=%s AND goal_id=%s AND user_id=%s",
        (contribution_id, goal_id, session['user_id'])
    )
    contrib_row = cursor.fetchone()
    if not contrib_row:
        cursor.close(); conn.close()
        return {'success': False, 'error': 'Transaction not found.'}, 404

    cursor.execute(
        "SELECT current_amount, closed_at FROM savings_goals WHERE goal_id=%s AND user_id=%s",
        (goal_id, session['user_id'])
    )
    goal_row = cursor.fetchone()
    if not goal_row:
        cursor.close(); conn.close()
        return {'success': False, 'error': 'Goal not found.'}, 404
    if goal_row[1] is not None:
        cursor.close(); conn.close()
        return {'success': False, 'error': 'This goal is closed and read-only.'}, 400

    old_signed = float(contrib_row[0])
    current_amount = float(goal_row[0])
    projected_current = max(current_amount - old_signed, 0)

    cursor.execute(
        "DELETE FROM goal_contributions WHERE contribution_id=%s AND user_id=%s",
        (contribution_id, session['user_id'])
    )
    cursor.execute(
        "UPDATE savings_goals SET current_amount=%s WHERE goal_id=%s AND user_id=%s",
        (projected_current, goal_id, session['user_id'])
    )
    conn.commit()

    goal_summary = build_goal_summary(cursor, goal_id, session['user_id'])
    cursor.close(); conn.close()
    return {'success': True, 'message': 'Transaction deleted.', 'goal': goal_summary}


@app.route('/close-goal/<int:goal_id>', methods=['POST'])
def close_goal(goal_id):
    """Closing is always a manual, explicit decision — never automatic.
    Any remaining reserved money is released (current_amount -> 0, logged
    as a ledger entry) in the same transaction as setting closed_at, so a
    goal can never end up closed with money still silently reserved."""
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT current_amount, closed_at FROM savings_goals WHERE goal_id=%s AND user_id=%s",
        (goal_id, session['user_id'])
    )
    row = cursor.fetchone()
    if not row:
        cursor.close(); conn.close()
        flash("Goal not found.", "error")
        return redirect('/goals')
    if row[1] is not None:
        cursor.close(); conn.close()
        return redirect('/goals')

    remaining = float(row[0])
    if remaining > 0:
        cursor.execute(
            "INSERT INTO goal_contributions (goal_id, user_id, amount, note) VALUES (%s, %s, %s, %s)",
            (goal_id, session['user_id'], -remaining, "Goal closed — returned to Available Balance")
        )
    cursor.execute(
        "UPDATE savings_goals SET current_amount = 0, closed_at = NOW() WHERE goal_id=%s AND user_id=%s",
        (goal_id, session['user_id'])
    )
    conn.commit(); cursor.close(); conn.close()
    flash("Goal closed.", "success")
    return redirect('/goals')


@app.route('/restore-goal/<int:goal_id>', methods=['POST'])
def restore_goal(goal_id):
    """Restoring only reopens the goal — it never brings back released
    money (that was already returned to Available Balance when closed).
    A restored goal always comes back at ₹0 reserved, per spec."""
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "UPDATE savings_goals SET closed_at = NULL, current_amount = 0 WHERE goal_id=%s AND user_id=%s",
        (goal_id, session['user_id'])
    )
    conn.commit(); cursor.close(); conn.close()
    flash("Goal restored — starting fresh at ₹0 reserved.", "success")
    return redirect('/goals')


@app.route('/goals/<int:goal_id>/history')
def goal_history(goal_id):
    """JSON feed of a single goal's contribution ledger, newest first.
    Powers the Activity Timeline on the Goal Details page."""
    if 'user_id' not in session:
        return {'error': 'unauthorized'}, 401

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT goal_id FROM savings_goals WHERE goal_id=%s AND user_id=%s",
        (goal_id, session['user_id'])
    )
    if not cursor.fetchone():
        cursor.close(); conn.close()
        return {'error': 'not found'}, 404

    cursor.execute("""
        SELECT contribution_id, amount, note, created_at, DATE_FORMAT(created_at, '%h:%i %p')
        FROM goal_contributions
        WHERE goal_id=%s AND user_id=%s
        ORDER BY created_at DESC
        LIMIT 50
    """, (goal_id, session['user_id']))
    rows = cursor.fetchall()
    cursor.close(); conn.close()

    # day_label buckets entries into "Today" / "Yesterday" / an actual date,
    # so consecutive same-day rows (already ordered newest-first) render
    # under one shared heading on the client instead of a full timestamp
    # per row.
    today = date.today()
    history = []
    for contribution_id, amount, note, created_at, time_str in rows:
        c_date = created_at.date() if hasattr(created_at, 'date') else created_at
        delta_days = (today - c_date).days
        if delta_days == 0:
            day_label = 'Today'
        elif delta_days == 1:
            day_label = 'Yesterday'
        else:
            day_label = f"{c_date.day} {c_date.strftime('%b %Y')}"
        history.append({
            'id': contribution_id, 'amount': float(amount), 'note': note,
            'day_label': day_label, 'time': time_str,
        })
    return {'history': history}


@app.route('/delete-goal/<int:goal_id>', methods=['POST'])
def delete_goal(goal_id):
    """Permanent delete is only allowed for closed goals — an active goal
    with money still reserved must be closed first, so reserved money can
    never disappear without an explicit close step."""
    if 'user_id' not in session:
        return redirect('/login')

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT closed_at FROM savings_goals WHERE goal_id=%s AND user_id=%s",
        (goal_id, session['user_id'])
    )
    row = cursor.fetchone()
    if not row:
        cursor.close(); conn.close()
        flash("Goal not found.", "error")
        return redirect('/goals')
    if row[0] is None:
        cursor.close(); conn.close()
        flash("Close this goal before deleting it.", "error")
        return redirect('/goals/' + str(goal_id))

    # Clean up the ledger explicitly rather than relying solely on the FK's
    # ON DELETE CASCADE, in case the DB isn't configured to enforce it.
    cursor.execute("DELETE FROM goal_contributions WHERE goal_id=%s AND user_id=%s", (goal_id, session['user_id']))
    cursor.execute("DELETE FROM savings_goals WHERE goal_id=%s AND user_id=%s", (goal_id, session['user_id']))
    conn.commit(); cursor.close(); conn.close()
    flash("Goal permanently deleted.", "success")
    return redirect('/goals')


    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT goal_id, user_id, goal_name, target_amount, current_amount, "
        "DATE_FORMAT(target_date, '%Y-%m-%d'), description, used_amount, "
        "DATE_FORMAT(completed_at, '%b %d, %Y'), DATE_FORMAT(archived_at, '%b %d, %Y') "
        "FROM savings_goals WHERE user_id=%s", (session['user_id'],)
    )
    raw_goals = cursor.fetchall()

    # Reserved for Goals / Available Balance / Overall Progress are always
    # driven by current_amount (money still reserved) only — used_amount
    # never factors in here, it's history, not state. An archived goal's
    # current_amount is 0 by definition, so summing across ALL goals
    # (active + archived) is already correct with no special-casing needed.
    total_saved_sum = sum(float(g[4]) for g in raw_goals)
    total_target_sum = sum(float(g[3]) for g in raw_goals)
    overall_progress_pct = round((total_saved_sum / total_target_sum * 100), 1) if total_target_sum > 0 else 0.0

    processed_goals = []
    archived_goals = []
    active_goals_count = 0
    current_date = datetime.now()

    for goal in raw_goals:
        g_id, u_id, name, target, current, date_str, desc, used, completed_at_display, archived_at_display = goal
        target = float(target)
        current = float(current)   # reserved amount
        used = float(used or 0)
        pace_message = None
        target_date_display = None

        # Status is ALWAYS derived live from current reserved money — never
        # stored, never set by an action. This is the fix for the earlier
        # bug: a goal can only be "done" (archived) once nothing is left
        # reserved, and using part of a completed goal's savings correctly
        # drops it back to In Progress rather than some terminal "Used" state.
        if current <= 0 and used > 0:
            db_status = 'archived'
        elif target > 0 and current >= target:
            db_status = 'completed'
        else:
            db_status = 'in_progress'

        status = db_status
        if db_status == 'in_progress':
            active_goals_count += 1

        if date_str:
            t_date = datetime.strptime(date_str, '%Y-%m-%d')
            target_date_display = t_date.strftime('%b %Y')

            if db_status == 'in_progress':
                months_left = (t_date.year - current_date.year) * 12 + (t_date.month - current_date.month)
                if months_left <= 0:
                    months_left = 1
                needed_per_month = round((target - current) / months_left)
                pace_message = f"Need ₹{needed_per_month}/month to reach on time"

                # On Track / Behind — compares elapsed time vs elapsed progress toward
                # the deadline. There's no "goal created" timestamp on savings_goals,
                # so we use the first ledger contribution as the effective start date
                # (falling back to "today" for a brand-new goal with no history yet,
                # which is harmless since there's nothing to be behind on).
                cursor.execute(
                    "SELECT MIN(created_at) FROM goal_contributions WHERE goal_id=%s AND user_id=%s",
                    (g_id, session['user_id'])
                )
                first_row = cursor.fetchone()
                start_dt = first_row[0] if first_row and first_row[0] else current_date
                total_days = (t_date - start_dt).days
                elapsed_days = (current_date - start_dt).days

                if total_days > 0:
                    expected_pct = max(0, min(100, (elapsed_days / total_days) * 100))
                    actual_pct = (current / target) * 100 if target > 0 else 100
                    # 5-point buffer so a goal isn't flagged "Behind" the day after creation
                    status = 'on_track' if actual_pct >= (expected_pct - 5) else 'behind'
                else:
                    status = 'on_track'

        # Last contribution — surfaced directly on the card, not just in the
        # modal ledger. Keeps the sign so deposits and withdrawals read
        # differently instead of both showing as "+₹X".
        cursor.execute(
            "SELECT amount, note, DATE_FORMAT(created_at, '%b %d, %Y') FROM goal_contributions "
            "WHERE goal_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT 1",
            (g_id, session['user_id'])
        )
        last_row = cursor.fetchone()
        last_contribution = None
        if last_row:
            last_contribution = {
                'amount': float(last_row[0]),
                'note': last_row[1],
                'date': last_row[2],
                'is_withdrawal': float(last_row[0]) < 0,
            }

        percent = round((current / target * 100), 1) if target > 0 else 0
        # Progress bars need a visible sliver even at 0.1–1% — otherwise a
        # freshly-started goal just looks broken/empty rather than "started".
        bar_width = 100 if percent >= 100 else (max(percent, 2) if percent > 0 else 0)

        goal_dict = {
            'id': g_id,
            'name': name,
            'target': target,
            'current': current,               # reserved amount
            'used': used,                      # lifetime used amount (history only)
            'total_lifetime': current + used,  # everything ever saved toward this goal
            'remaining': max(target - current, 0),
            'percent': percent,
            'bar_width': bar_width,
            'target_date': target_date_display,
            'pace_message': pace_message,
            'description': desc if desc else "",
            'db_status': db_status,         # 'in_progress' | 'completed' | 'archived' — always derived, never stored
            'status': status,               # db_status, or 'on_track' | 'behind' sub-state while in_progress
            'last_contribution': last_contribution,
            'completed_at': completed_at_display,
            'archived_at': archived_at_display,
        }

        if db_status == 'archived':
            archived_goals.append(goal_dict)
        else:
            processed_goals.append(goal_dict)

    # Total Balance / Reserved for Goals / Available Balance — same reservation
    # model as the Dashboard's Goal Allocation & Available Cash, surfaced here
    # with clearer labels since this is the page where "reserved" money is
    # actually managed. Deliberately income-minus-expenses only (no
    # settlements), matching how this page frames "your account balance".
    cursor.execute("SELECT IFNULL(SUM(amount), 0) FROM income WHERE user_id=%s", (session['user_id'],))
    total_income = float(cursor.fetchone()[0])
    cursor.execute("SELECT IFNULL(SUM(amount), 0) FROM expenses WHERE user_id=%s", (session['user_id'],))
    total_expenses = float(cursor.fetchone()[0])
    total_balance = total_income - total_expenses
    reserved_for_goals = total_saved_sum
    available_balance = total_balance - reserved_for_goals

    cursor.close(); conn.close()
    return render_template(
        'goals/goals.html',
        goals=processed_goals,
        archived_goals=archived_goals,
        active_count=active_goals_count,
        total_saved=total_saved_sum,
        total_target=total_target_sum,
        overall_progress=overall_progress_pct,
        total_balance=total_balance,
        reserved_for_goals=reserved_for_goals,
        available_balance=available_balance,
        username=session["username"],
        display_name=session.get('display_name', session['username'])
    )

# ---------------------------------------------------------------------------
# Auth: Register
# ---------------------------------------------------------------------------
# DB schema required:
#   users table must have columns: user_id, username, password, display_name, email
#   Run once if the email column doesn't exist yet:
#     ALTER TABLE users ADD COLUMN email VARCHAR(255) UNIQUE;
# ---------------------------------------------------------------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username         = request.form['username'].strip()
        email            = request.form['email'].strip().lower()
        password         = request.form['password']
        confirm_password = request.form['confirm_password']

        # Server-side password match check (client also validates, but never trust only the client)
        if password != confirm_password:
            flash("Passwords do not match. Please try again.", "error")
            return redirect(url_for('register'))

        conn = get_db_connection()
        cursor = conn.cursor()

        # Check username uniqueness
        cursor.execute("SELECT user_id FROM users WHERE username=%s", (username,))
        if cursor.fetchone():
            cursor.close(); conn.close()
            flash("Username already taken. Please choose another.", "error")
            return redirect(url_for('register'))

        # Check email uniqueness
        cursor.execute("SELECT user_id FROM users WHERE email=%s", (email,))
        if cursor.fetchone():
            cursor.close(); conn.close()
            flash("An account with that email already exists. Try logging in.", "error")
            return redirect(url_for('register'))

        cursor.execute(
            "INSERT INTO users (username, password, email) VALUES (%s, %s, %s)",
            (username, generate_password_hash(password), email)
        )
        conn.commit()
        cursor.close(); conn.close()
        flash("Account created! Please log in.", "success")
        return redirect(url_for('login'))

    return render_template('auth/register.html')

# ---------------------------------------------------------------------------
# Auth: Login  (email + password)
# ---------------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form['email'].strip().lower()
        password = request.form['password']

        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cursor.fetchone()
        cursor.close(); conn.close()

        if user and check_password_hash(user[2], password):
            session['user_id']     = user[0]
            session['username']    = user[1]
            session['display_name'] = user[3] if len(user) > 3 and user[3] else user[1]
            return redirect('/')
        else:
            flash("Invalid email or password. Please try again.", "error")
            return redirect(url_for('login'))

    return render_template('auth/login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect('/login')
    return render_template(
        'user/profile.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        active_page='profile'
    )

@app.route('/profile/update-name', methods=['POST'])
def profile_update_name():
    """Display name update from the Profile page — same validation as the
    Settings page's display-name field, just a distinct endpoint since the
    Profile page has its own dedicated form for it."""
    if 'user_id' not in session:
        return redirect('/login')
    new_name = request.form.get('display_name', '').strip()
    if not new_name:
        flash("Display name cannot be empty.", "error")
        return redirect('/profile')
    if len(new_name) > 40:
        flash("Display name must be 40 characters or fewer.", "error")
        return redirect('/profile')

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET display_name=%s WHERE user_id=%s",
        (new_name, session['user_id'])
    )
    conn.commit(); cursor.close(); conn.close()
    session['display_name'] = new_name
    flash("Display name updated successfully!", "success")
    return redirect('/profile')


@app.route('/profile/change-password', methods=['POST'])
def profile_change_password():
    """Change password — requires the current password to match, same
    'never trust only the client' principle used at registration."""
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

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT password FROM users WHERE user_id=%s", (session['user_id'],))
    row = cursor.fetchone()
    if not row or not check_password_hash(row[0], current_password):
        cursor.close(); conn.close()
        flash("Current password is incorrect.", "error")
        return redirect('/profile')

    cursor.execute(
        "UPDATE users SET password=%s WHERE user_id=%s",
        (generate_password_hash(new_password), session['user_id'])
    )
    conn.commit(); cursor.close(); conn.close()
    flash("Password updated successfully!", "success")
    return redirect('/profile')


@app.route('/profile/delete-account', methods=['POST'])
def profile_delete_account():
    """Permanently delete the account and every piece of data that belongs
    to it. The whole operation is one transaction — if any step fails,
    everything is rolled back and the session is left untouched, so a user
    is never left half-deleted or logged out of an account that wasn't
    actually removed. `budgets` is deliberately excluded: it has no
    user_id column and is shared across every account, so it must never be
    touched by a single user's deletion."""
    if 'user_id' not in session:
        return redirect('/login')

    confirm_username = request.form.get('confirm_username', '').strip()
    if confirm_username != session['username']:
        flash("Username confirmation didn't match. Account was not deleted.", "error")
        return redirect('/profile')

    user_id = session['user_id']
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        # Children before parents, to respect the one real FK in this schema
        # (expenses.recurring_id -> recurring_expenses) and to mirror the
        # same order delete_goal() already uses for its own ledger cleanup.
        cursor.execute("DELETE FROM goal_contributions WHERE user_id=%s", (user_id,))
        cursor.execute("DELETE FROM savings_goals WHERE user_id=%s", (user_id,))
        cursor.execute("DELETE FROM settlements WHERE user_id=%s", (user_id,))
        cursor.execute("DELETE FROM expenses WHERE user_id=%s", (user_id,))
        cursor.execute("DELETE FROM recurring_expenses WHERE user_id=%s", (user_id,))
        cursor.execute("DELETE FROM income WHERE user_id=%s", (user_id,))
        cursor.execute("DELETE FROM notifications WHERE user_id=%s", (user_id,))
        cursor.execute("DELETE FROM notification_preferences WHERE user_id=%s", (user_id,))
        cursor.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        cursor.close(); conn.close()
        flash("Something went wrong deleting your account. Nothing was changed — please try again.", "error")
        return redirect('/profile')

    cursor.close(); conn.close()
    # Only clear the session once the transaction has actually committed.
    session.clear()
    flash("Your account and all associated data have been permanently deleted.", "success")
    return redirect('/login')


@app.route('/settings', methods=['GET','POST'])
def settings():
    if 'user_id' not in session:
        return redirect('/login')

    conn = get_db_connection(); cursor = conn.cursor()

    if request.method == 'POST':
        if 'display_name' in request.form:
            new_name = request.form['display_name'].strip()
            if new_name:
                cursor.execute(
                    "UPDATE users SET display_name=%s WHERE user_id=%s",
                    (new_name, session['user_id'])
                )
                conn.commit()
                session['display_name'] = new_name
                flash("Display name updated successfully!", "success")
            else:
                flash("Display name cannot be empty.", "error")

        elif 'budget' in request.form:
            new_budget = request.form['budget']
            cursor.execute("SELECT COUNT(*) FROM budgets")
            exists = cursor.fetchone()[0]
            if exists:
                cursor.execute("UPDATE budgets SET monthly_limit=%s", (new_budget,))
            else:
                cursor.execute("INSERT INTO budgets (monthly_limit) VALUES (%s)", (new_budget,))
            conn.commit()
            flash("Budget updated successfully!", "success")

        elif 'notif_prefs_submitted' in request.form:
            budget_alerts = 1 if request.form.get('budget_alerts') else 0
            recurring_reminders = 1 if request.form.get('recurring_reminders') else 0
            goal_milestones = 1 if request.form.get('goal_milestones') else 0

            cursor.execute(
                "SELECT user_id FROM notification_preferences WHERE user_id=%s",
                (session['user_id'],)
            )
            if cursor.fetchone():
                cursor.execute(
                    "UPDATE notification_preferences "
                    "SET budget_alerts=%s, recurring_reminders=%s, goal_milestones=%s "
                    "WHERE user_id=%s",
                    (budget_alerts, recurring_reminders, goal_milestones, session['user_id'])
                )
            else:
                cursor.execute(
                    "INSERT INTO notification_preferences "
                    "(user_id, budget_alerts, recurring_reminders, goal_milestones) "
                    "VALUES (%s, %s, %s, %s)",
                    (session['user_id'], budget_alerts, recurring_reminders, goal_milestones)
                )
            conn.commit()
            flash("Notification settings saved!", "success")

        cursor.close(); conn.close()
        return redirect('/settings')

    # ---- GET ----
    cursor.execute(
        "SELECT budget_alerts, recurring_reminders, goal_milestones "
        "FROM notification_preferences WHERE user_id=%s",
        (session['user_id'],)
    )
    row = cursor.fetchone()
    if row:
        notif_prefs = {
            'budget_alerts': bool(row[0]),
            'recurring_reminders': bool(row[1]),
            'goal_milestones': bool(row[2]),
        }
    else:
        # No row yet for this user — default everything on
        notif_prefs = {'budget_alerts': True, 'recurring_reminders': True, 'goal_milestones': True}

    cursor.execute("SELECT monthly_limit FROM budgets LIMIT 1")
    budget_row = cursor.fetchone()
    current_budget = float(budget_row[0]) if budget_row and budget_row[0] is not None else None

    cursor.close(); conn.close()
    return render_template(
        'user/settings.html',
        current_budget=current_budget,
        notif_prefs=notif_prefs,
        username=session['username'],
        display_name=session.get('display_name', session.get('username', '')),
        active_page='settings'
    )
       

@app.route('/export-pdf') 
def export_pdf():
    if 'user_id' not in session:
        return redirect('/login')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    conn = get_db_connection(); cursor = conn.cursor()
    query = "SELECT * FROM expenses WHERE user_id=%s"
    params = [session['user_id']]
    if start_date:
        query += " AND expense_date >= %s"; params.append(start_date)
    if end_date:
        query += " AND expense_date <= %s"; params.append(end_date)
    cursor.execute(query, tuple(params))
    expenses = cursor.fetchall()
    cursor.close(); conn.close()

    title = "Expense Report"
    if start_date or end_date:
        title += f" ({start_date or 'start'} to {end_date or 'today'})"

    # Built entirely in memory rather than a shared fixed filename on disk —
    # a fixed filename meant two overlapping requests (different users, or
    # the same user twice) could race and one could be served the other's
    # report.
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer); styles = getSampleStyleSheet(); content = []
    content.append(Paragraph(title, styles['Title'])); content.append(Spacer(1,12))
    for expense in expenses:
        content.append(Paragraph(f"₹{expense[1]} | {expense[2]} | {expense[3]}", styles['Normal']))
    doc.build(content)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="expenses_report.pdf",
        mimetype="application/pdf",
    )

def process_due_auto_charges(user_id, cursor, conn):
    """For 'auto' recurring items (subscriptions, SIPs, digital gold): if their
    next_charge_date has arrived (or passed), post an expense row for each
    elapsed cycle and roll next_charge_date forward — same idea as the manual
    'Mark as Paid' flow, just automatic since these don't need confirmation.
    Also flashes a toast so the user notices it happened in the background."""
    cursor.execute(
        "SELECT recurring_id, title, amount, category, frequency, next_charge_date "
        "FROM recurring_expenses "
        "WHERE user_id=%s AND status='active' AND recurring_type='auto' AND next_charge_date IS NOT NULL "
        "AND next_charge_date <= CURDATE()",
        (user_id,)
    )
    due_items = cursor.fetchall()
    today = date.today()
    charged_names = []
    charged_total = 0.0
    recurring_prefs = get_notification_prefs(cursor, user_id) if due_items else None

    for recurring_id, title, amount, category, frequency, next_charge_date in due_items:
        charge_date = next_charge_date
        cycles = 0
        # Catch up on any missed cycles (e.g. app wasn't opened for a while)
        while charge_date is not None and charge_date <= today:
            cursor.execute(
                "INSERT INTO expenses (amount, category, description, expense_date, user_id, recurring_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (amount, category or 'Other', f"{title} (auto-charge)", charge_date, user_id, recurring_id)
            )
            charged_total += float(amount)
            cycles += 1
            # Wrapped in its own try/except: the expense row inserted just
            # above must still be committed regardless of whether the
            # notification step succeeds — a notification hiccup must
            # never block a real auto-charge from being recorded.
            if recurring_prefs and recurring_prefs['recurring_reminders']:
                try:
                    create_notification(
                        cursor, user_id, icon='🔄',
                        title=f"{title} auto-charged",
                        message=f"₹{float(amount):,.0f} was logged to your expenses.",
                        link='/recurring',
                        dedup_key=f"recurring-charged-{recurring_id}-{charge_date.isoformat()}",
                    )
                except Exception as e:
                    print(f"[notifications] auto-charge notification failed (user_id={user_id}, recurring_id={recurring_id}): {e}")
            charge_date = advance_recurring_date(charge_date, frequency)
        if cycles:
            charged_names.append(title if cycles == 1 else f"{title} ×{cycles}")
        cursor.execute(
            "UPDATE recurring_expenses SET next_charge_date=%s WHERE recurring_id=%s AND user_id=%s",
            (charge_date, recurring_id, user_id)
        )

    if due_items:
        conn.commit()

    if charged_names:
        names_str = ", ".join(charged_names)
        flash(f"Auto-charged: {names_str} — ₹{charged_total:,.0f} logged to Expenses.", "success")


@app.route('/recurring', methods=['GET', 'POST'])
def recurring():
    if 'user_id' not in session: return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()

    process_due_auto_charges(session['user_id'], cursor, conn)

    if request.method == 'POST':
        name = request.form['name']; amount = float(request.form['amount'])
        category = request.form['category']; frequency = request.form['repeats']
        icon = request.form.get('icon', '⚡')
        recurring_type = request.form.get('recurring_type', 'auto')
        
        next_date = request.form['next_charge_date']
        next_date = next_date if next_date != "" else None
        
        cursor.execute("INSERT INTO recurring_expenses (user_id, title, amount, category, frequency, next_charge_date, icon, recurring_type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                       (session['user_id'], name, amount, category, frequency, next_date, icon, recurring_type))
        conn.commit(); return redirect('/recurring')
        
    # NOTE: this route expects an extra column on recurring_expenses:
    #   ALTER TABLE recurring_expenses ADD COLUMN recurring_type VARCHAR(10) DEFAULT 'auto';
    # 'auto'   = automatic recurring (subscriptions, SIPs, digital gold) — no overdue tracking
    # 'manual' = needs manual confirmation (rent, bills, EMIs) — supports "Mark as Paid"
    cursor.execute("SELECT * FROM recurring_expenses WHERE user_id=%s", (session['user_id'],))
    items = cursor.fetchall()

    total_monthly  = sum(float(i[3])           for i in items if i[5] == 'Monthly' and (len(i) <= 8 or i[8] == 'active'))
    total_monthly += sum(float(i[3]) * 365/12   for i in items if i[5] == 'Daily'   and (len(i) <= 8 or i[8] == 'active'))
    total_monthly += sum(float(i[3]) * 52/12    for i in items if i[5] == 'Weekly'  and (len(i) <= 8 or i[8] == 'active'))
    total_monthly += sum(float(i[3]) / 12       for i in items if i[5] == 'Yearly'  and (len(i) <= 8 or i[8] == 'active'))

    processed = [{
        'id': i[0], 'name': i[2], 'amount': float(i[3]), 'category': normalize_category_name(i[4]) if i[4] else i[4],
        'repeats': i[5], 'next_date': i[6], 'icon': i[7] if len(i) > 7 else '⚡',
        'status': i[8] if len(i) > 8 else 'active',
        'recurring_type': i[11] if len(i) > 11 and i[11] else 'auto',
        'yearly': (float(i[3]) * 365 if i[5] == 'Daily' else
                   float(i[3]) * 52  if i[5] == 'Weekly' else
                   float(i[3]) * 12  if i[5] == 'Monthly' else
                   float(i[3]))
    } for i in items]

    active_count = sum(1 for p in processed if p['status'] == 'active')

    # Category-level "top spend" stat for the Smart Insight card — grouped by
    # category (not by individual subscription name), so the insight reads
    # as e.g. "Investments account for 60% of your recurring spending"
    # rather than naming one specific subscription.
    category_totals = {}
    for p in processed:
        if p['status'] != 'active':
            continue
        cat_name = p['category'] or 'Uncategorized'
        category_totals[cat_name] = category_totals.get(cat_name, 0) + p['yearly']
    active_annual_total = sum(category_totals.values())
    if category_totals:
        top_category_name = max(category_totals, key=category_totals.get)
        top_category_pct = round((category_totals[top_category_name] / active_annual_total) * 100) if active_annual_total > 0 else 0
    else:
        top_category_name = None
        top_category_pct = 0

    cursor.execute("SELECT monthly_limit FROM budgets LIMIT 1")
    budget_row = cursor.fetchone()
    budget_amount = float(budget_row[0]) if budget_row and budget_row[0] else 0
    budget_percentage = round((total_monthly / budget_amount) * 100) if budget_amount > 0 else 0

    categories = get_categories(cursor)
    
    cursor.close(); conn.close()
    return render_template(
        'recurring/recurring.html',
        items=processed,
        monthly_total=total_monthly,
        active_count=active_count,
        budget=budget_amount,
        budget_percentage=budget_percentage,
        categories=categories,
        top_category_name=top_category_name,
        top_category_pct=top_category_pct,
        username=session["username"],
        display_name=session.get('display_name', session['username']),
        active_page='recurring'
    )

@app.route('/delete-recurring/<int:id>', methods=['POST'])
def delete_recurring(id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM recurring_expenses WHERE recurring_id=%s AND user_id=%s", (id, session['user_id']))
    conn.commit(); cursor.close(); conn.close()
    return redirect('/recurring')

@app.route('/confirm-paid/<int:id>', methods=['POST'])
def confirm_paid(id):
    """For 'manual confirmation' recurring items (rent, bills, EMIs): mark this cycle
    as paid and advance the next_charge_date forward by one period. Subscriptions
    set to 'auto' don't use this — they just keep ticking on schedule."""
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT title, amount, category, frequency, next_charge_date FROM recurring_expenses WHERE recurring_id=%s AND user_id=%s",
        (id, session['user_id'])
    )
    row = cursor.fetchone()
    if row:
        title, amount, category, frequency, next_charge_date = row
        new_date = advance_recurring_date(next_charge_date, frequency)
        charge_date = next_charge_date or date.today()
        cursor.execute(
            "UPDATE recurring_expenses SET next_charge_date=%s WHERE recurring_id=%s AND user_id=%s",
            (new_date, id, session['user_id'])
        )
        # Log this payment as an actual expense so it shows up in totals/reports/balance
        cursor.execute(
            "INSERT INTO expenses (amount, category, description, expense_date, user_id, recurring_id) VALUES (%s, %s, %s, %s, %s, %s)",
            (amount, category or 'Other', f"{title} (recurring)", charge_date, session['user_id'], id)
        )

        # Notification — uses the exact same dedup_key scheme as the
        # auto-charge notification in process_due_auto_charges()
        # ('recurring-charged-{id}-{date}'), not a separate 'paid' key.
        # This route only ever applies to 'manual' recurring_type items and
        # auto-charging only ever applies to 'auto' ones, so in practice
        # the same item never goes through both paths — but sharing the
        # key means that guarantee is enforced by the database's unique
        # index rather than just assumed, so the same real-world payment
        # can never produce two notifications.
        #
        # Wrapped in its own try/except: the payment itself (the UPDATE +
        # expense INSERT above) must commit regardless of whether the
        # notification step succeeds — a notification hiccup must never
        # block the actual accounting action.
        try:
            prefs = get_notification_prefs(cursor, session['user_id'])
            if prefs['recurring_reminders']:
                create_notification(
                    cursor, session['user_id'], icon='🔄',
                    title=f"{title} marked as paid",
                    message=f"₹{float(amount):,.0f} was logged to your expenses.",
                    link='/recurring',
                    dedup_key=f"recurring-charged-{id}-{charge_date.isoformat()}",
                )
        except Exception as e:
            print(f"[notifications] confirm_paid notification failed (user_id={session['user_id']}, recurring_id={id}): {e}")

        conn.commit()
        flash("Marked as paid — logged as an expense and next due date updated.", "success")
    cursor.close(); conn.close()
    return redirect('/recurring')

@app.route('/toggle-recurring/<int:id>', methods=['POST'])
def toggle_recurring(id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT status FROM recurring_expenses WHERE recurring_id=%s AND user_id=%s", (id, session['user_id']))
    row = cursor.fetchone()
    if row:
        new_status = 'paused' if row[0] == 'active' else 'active'
        cursor.execute(
            "UPDATE recurring_expenses SET status=%s WHERE recurring_id=%s AND user_id=%s",
            (new_status, id, session['user_id'])
        )
        conn.commit()
        flash(f"Subscription {'paused' if new_status == 'paused' else 'resumed'}.", "success")
    cursor.close(); conn.close()
    return redirect('/recurring')

@app.route('/update-recurring/<int:id>', methods=['POST'])
def update_recurring(id):
    if 'user_id' not in session:
        return redirect('/login')
    name = request.form['name']; amount = float(request.form['amount'])
    category = request.form.get('category') or None
    frequency = request.form['repeats']
    icon = request.form.get('icon', '⚡')
    next_date = request.form.get('next_charge_date') or None
    recurring_type = request.form.get('recurring_type', 'auto')

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "UPDATE recurring_expenses "
        "SET title=%s, amount=%s, category=%s, frequency=%s, next_charge_date=%s, icon=%s, recurring_type=%s "
        "WHERE recurring_id=%s AND user_id=%s",
        (name, amount, category, frequency, next_date, icon, recurring_type, id, session['user_id'])
    )
    conn.commit(); cursor.close(); conn.close()
    flash("Subscription updated successfully!", "success")
    return redirect('/recurring')

@app.route('/set-budget', methods=['POST'])
def set_budget():
    if 'user_id' not in session:
        return redirect('/login')
    new_budget = request.form['monthly_budget']
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM budgets")
    exists = cursor.fetchone()[0]
    if exists:
        cursor.execute("UPDATE budgets SET monthly_limit=%s", (new_budget,))
    else:
        cursor.execute("INSERT INTO budgets (monthly_limit) VALUES (%s)", (new_budget,))
    conn.commit(); cursor.close(); conn.close()
    flash("Budget updated successfully!", "success")
    return redirect('/recurring')

# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------
@app.route('/login/google')
def login_google():
    google = oauth.create_client('google')
    redirect_uri = url_for('authorize_google', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize/google')
def authorize_google():
    google = oauth.create_client('google')
    token = google.authorize_access_token()
    user_info = token.get("userinfo")
    if user_info is None:
        user_info = google.userinfo()

    google_email = user_info['email']

    conn = get_db_connection(); cursor = conn.cursor()
    # Look up by email first, then fall back to username
    # (covers legacy rows created before the email column existed)
    cursor.execute("SELECT * FROM users WHERE email=%s OR username=%s", (google_email, google_email))
    user = cursor.fetchone()

    if not user:
        # Google-created accounts get a random, unusable password rather than
        # a fixed placeholder — the account can only ever be signed into via
        # Google, never guessed through the normal password-login form.
        cursor.execute(
            "INSERT INTO users (username, password, display_name, email) VALUES (%s, %s, %s, %s)",
            (
                google_email,
                generate_password_hash(secrets.token_hex(32)),
                user_info.get('name'),
                google_email,
            )
        )
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE email=%s", (google_email,))
        user = cursor.fetchone()
    elif user[4] is None:
        # Legacy row matched by username but email was never backfilled — fix it now
        cursor.execute("UPDATE users SET email=%s WHERE user_id=%s", (google_email, user[0]))
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE user_id=%s", (user[0],))
        user = cursor.fetchone()

    session['user_id']      = user[0]
    session['username']     = user[1]
    session['display_name'] = user[3] if user[3] else user[1]
    cursor.close(); conn.close()
    return redirect('/')

# ---------------------------------------------------------------------------
# Income
# ---------------------------------------------------------------------------
@app.route('/add-income')
def add_income_redirect():
    return redirect('/income')

def build_income_context(cursor, user_id, search=None, start_date=None, end_date=None, sort=None, page=1):
    """Builds every context variable income.html needs — summary cards, top
    source, and the filtered/paginated history list. Shared by both the main
    /income page and /edit-income/<id> (which reuses the same template), so
    opening the edit form never renders with missing stats."""
    cursor.execute("SELECT IFNULL(SUM(amount),0) FROM income WHERE user_id=%s", (user_id,))
    total_income = float(cursor.fetchone()[0])

    cursor.execute("SELECT COUNT(*) FROM income WHERE user_id=%s", (user_id,))
    income_count = cursor.fetchone()[0]
    avg_income = round(total_income / income_count) if income_count > 0 else 0

    cursor.execute("""
        SELECT source, SUM(amount) FROM income
        WHERE user_id=%s GROUP BY source ORDER BY SUM(amount) DESC LIMIT 1
    """, (user_id,))
    top_source_row = cursor.fetchone()
    top_source = top_source_row[0] if top_source_row else None
    top_source_amount = float(top_source_row[1]) if top_source_row else 0

    if page < 1:
        page = 1
    per_page = 10

    count_query = "SELECT COUNT(*) FROM income WHERE user_id=%s"
    count_params = [user_id]
    if search:
        count_query += " AND source=%s"; count_params.append(search)
    if start_date:
        count_query += " AND income_date >= %s"; count_params.append(start_date)
    if end_date:
        count_query += " AND income_date <= %s"; count_params.append(end_date)
    cursor.execute(count_query, tuple(count_params))
    filtered_count = cursor.fetchone()[0]
    total_pages = max((filtered_count + per_page - 1) // per_page, 1)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page

    list_query = (
        "SELECT income_id, DATE_FORMAT(income_date,'%b %d, %Y'), source, description, amount, income_date "
        "FROM income WHERE user_id=%s"
    )
    list_params = [user_id]
    if search:
        list_query += " AND source=%s"; list_params.append(search)
    if start_date:
        list_query += " AND income_date >= %s"; list_params.append(start_date)
    if end_date:
        list_query += " AND income_date <= %s"; list_params.append(end_date)

    if sort == 'amount_asc':
        list_query += " ORDER BY amount ASC"
    elif sort == 'amount_desc':
        list_query += " ORDER BY amount DESC"
    elif sort == 'date_asc':
        list_query += " ORDER BY income_date ASC"
    else:  # date_desc default
        list_query += " ORDER BY income_date DESC"

    list_query += " LIMIT %s OFFSET %s"
    list_params.extend([per_page, offset])

    cursor.execute(list_query, tuple(list_params))
    income_list = cursor.fetchall()

    return {
        'income_list': income_list,
        'total_income': total_income,
        'income_count': income_count,
        'avg_income': avg_income,
        'top_source': top_source,
        'top_source_amount': top_source_amount,
        'search': search,
        'start_date': start_date,
        'end_date': end_date,
        'sort': sort,
        'page': page,
        'total_pages': total_pages,
        'filtered_count': filtered_count,
    }


@app.route('/income', methods=['GET', 'POST'])
def income():
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()

    if request.method == 'POST':
        amount      = request.form['amount']
        source      = request.form['source']
        description = request.form.get('description', '')
        date_val    = request.form['date']
        cursor.execute(
            "INSERT INTO income (user_id, amount, source, description, income_date) VALUES (%s,%s,%s,%s,%s)",
            (session['user_id'], amount, source, description, date_val)
        )
        conn.commit()
        flash("Income added successfully!", "success")
        return redirect('/income')

    # ── Filters (mirrors the Expenses page: source dropdown, date range) ──
    search     = request.args.get('search')   # exact source value from the dropdown
    start_date = request.args.get('start_date')
    end_date   = request.args.get('end_date')
    sort       = request.args.get('sort')
    page       = request.args.get('page', 1, type=int)

    context = build_income_context(
        cursor, session['user_id'], search=search, start_date=start_date,
        end_date=end_date, sort=sort, page=page
    )

    cursor.close(); conn.close()
    return render_template(
        'expenses/income.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        active_page='income',
        **context
    )

@app.route('/edit-income/<int:income_id>', methods=['GET', 'POST'])
def edit_income(income_id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    if request.method == 'POST':
        amount      = request.form['amount']
        source      = request.form['source']
        description = request.form.get('description', '')
        date_val    = request.form['date']
        cursor.execute(
            "UPDATE income SET amount=%s, source=%s, description=%s, income_date=%s "
            "WHERE income_id=%s AND user_id=%s",
            (amount, source, description, date_val, income_id, session['user_id'])
        )
        conn.commit(); cursor.close(); conn.close()
        flash("Income updated successfully!", "success")
        return redirect('/income')
    cursor.execute(
        "SELECT income_id, DATE_FORMAT(income_date,'%Y-%m-%d'), source, description, amount, "
        "DATE_FORMAT(income_date,'%d %b %Y') "
        "FROM income WHERE income_id=%s AND user_id=%s",
        (income_id, session['user_id'])
    )
    entry = cursor.fetchone()
    if not entry:
        cursor.close(); conn.close()
        flash("Income entry not found.", "error")
        return redirect('/income')

    # Figure out which page of the (default-sorted, unfiltered) history list
    # this entry actually falls on, so the highlighted "editing" row below
    # the form is on a page that's actually shown — not always page 1.
    cursor.execute(
        "SELECT income_id FROM income WHERE user_id=%s ORDER BY income_date DESC, income_id DESC",
        (session['user_id'],)
    )
    ordered_ids = [row[0] for row in cursor.fetchall()]
    per_page = 10
    try:
        target_page = ordered_ids.index(income_id) // per_page + 1
    except ValueError:
        target_page = 1

    # Reuses income.html, so it needs the same summary/history context as
    # the main Income page — this is what was missing before, causing a
    # TypeError when the template tried to format an undefined total_income.
    context = build_income_context(cursor, session['user_id'], page=target_page)

    cursor.close(); conn.close()
    return render_template(
        'expenses/income.html',
        edit_entry=entry,
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        active_page='income',
        **context
    )

@app.route('/delete-income/<int:income_id>', methods=['POST'])
def delete_income(income_id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM income WHERE income_id=%s AND user_id=%s", (income_id, session['user_id']))
    conn.commit(); cursor.close(); conn.close()
    flash("Income entry deleted.", "success")
    return redirect('/income')


@app.route('/notifications')
def get_notifications():
    if 'user_id' not in session:
        return {'unread_count': 0, 'items': []}, 401

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id=%s AND is_read=0",
        (session['user_id'],)
    )
    unread_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT notification_id, icon, title, message, link, is_read,
               DATE_FORMAT(created_at, '%b %d, %h:%i %p')
        FROM notifications
        WHERE user_id=%s
        ORDER BY created_at DESC
        LIMIT 20
    """, (session['user_id'],))
    rows = cursor.fetchall()
    cursor.close(); conn.close()

    items = [{
        'id': r[0], 'icon': r[1] or '🔔', 'title': r[2],
        'message': r[3], 'link': r[4], 'is_read': bool(r[5]), 'created_at': r[6]
    } for r in rows]

    return {'unread_count': unread_count, 'items': items}

@app.route('/notifications/read/<int:notification_id>', methods=['POST'])
def mark_notification_read(notification_id):
    if 'user_id' not in session:
        return '', 401
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "UPDATE notifications SET is_read=1 WHERE notification_id=%s AND user_id=%s",
        (notification_id, session['user_id'])
    )
    conn.commit(); cursor.close(); conn.close()
    return '', 204

@app.route('/notifications/read-all', methods=['POST'])
def mark_all_notifications_read():
    if 'user_id' not in session:
        return '', 401
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE notifications SET is_read=1 WHERE user_id=%s", (session['user_id'],))
    conn.commit(); cursor.close(); conn.close()
    return '', 204

@app.route('/notifications/<int:notification_id>/delete', methods=['POST'])
def delete_notification(notification_id):
    """Remove a single notification from the list — 'mark as read' only
    ever changed is_read, it never gave a way to actually get rid of one."""
    if 'user_id' not in session:
        return '', 401
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM notifications WHERE notification_id=%s AND user_id=%s",
        (notification_id, session['user_id'])
    )
    conn.commit(); cursor.close(); conn.close()
    return '', 204

@app.route('/notifications/clear', methods=['POST'])
def clear_notifications():
    """Removes every notification for the current user, read or unread —
    the 'start fresh' action, distinct from mark-all-read which only
    changes read state and leaves the list populated."""
    if 'user_id' not in session:
        return '', 401
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM notifications WHERE user_id=%s", (session['user_id'],))
    conn.commit(); cursor.close(); conn.close()
    return '', 204


if __name__ == '__main__':
    app.run(debug=DEBUG)
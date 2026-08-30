from datetime import date, timedelta
from flask import flash
from services.account_service import get_default_account_id, adjust_account_on_expense_create

def advance_recurring_date(current_date, frequency):
    """Bump a recurring item's next_charge_date forward by one period."""
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

def process_due_auto_charges(user_id, cursor, conn, get_notification_prefs_fn, create_notification_fn):
    """Process due auto-recurring items and post expenses."""
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
    recurring_prefs = get_notification_prefs_fn(cursor, user_id) if due_items else None

    for recurring_id, title, amount, category, frequency, next_charge_date in due_items:
        charge_date = next_charge_date
        account_id = get_default_account_id(cursor, user_id)
        cycles = 0
        while charge_date is not None and charge_date <= today:
            cursor.execute(
                "SELECT expense_id FROM expenses WHERE recurring_id=%s AND expense_date=%s AND user_id=%s",
                (recurring_id, charge_date, user_id)
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO expenses (amount, category, description, expense_date, user_id, recurring_id, account_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (amount, category or 'Other', f"{title} (auto-charge)", charge_date, user_id, recurring_id, account_id)
                )
                adjust_account_on_expense_create(cursor, account_id, amount)
                charged_total += float(amount)
                cycles += 1
                if recurring_prefs and recurring_prefs.get('recurring_reminders'):
                    try:
                        create_notification_fn(
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

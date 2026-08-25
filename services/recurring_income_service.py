from datetime import date, datetime, timedelta
from services.account_service import get_default_account_id, adjust_account_on_income_create

def advance_income_date(current_date, frequency):
    if isinstance(current_date, str):
        current_date = datetime.strptime(current_date, '%Y-%m-%d').date()

    if frequency == 'Weekly':
        return current_date + timedelta(days=7)
    elif frequency == 'Monthly':
        month = current_date.month % 12 + 1
        year = current_date.year + (current_date.month // 12)
        day = min(current_date.day, 28)
        return date(year, month, day)
    elif frequency == 'Yearly':
        return date(current_date.year + 1, current_date.month, min(current_date.day, 28))
    return current_date + timedelta(days=30)

def get_user_recurring_income(cursor, user_id):
    """Fetches all recurring income setups for user."""
    cursor.execute(
        "SELECT recurring_income_id, title, amount, source, frequency, "
        "DATE_FORMAT(next_pay_date, '%%Y-%%m-%%d'), icon, status, account_id, "
        "DATE_FORMAT(next_pay_date, '%%d %%b %%Y') "
        "FROM recurring_income WHERE user_id=%s ORDER BY next_pay_date ASC",
        (user_id,)
    )
    rows = cursor.fetchall()
    return [{
        'id': r[0],
        'recurring_income_id': r[0],
        'title': r[1],
        'name': r[1],
        'amount': float(r[2] or 0),
        'source': r[3],
        'frequency': r[4],
        'next_pay_date': r[5],
        'icon': r[6] or '💼',
        'status': r[7],
        'account_id': r[8],
        'next_pay_fmt': r[9]
    } for r in rows]

def add_recurring_income(cursor, user_id, title, amount, source='Salary', frequency='Monthly', next_pay_date=None, icon='💼', account_id=None):
    if not next_pay_date:
        next_pay_date = date.today().isoformat()
    if not account_id:
        account_id = get_default_account_id(cursor, user_id)

    cursor.execute(
        "INSERT INTO recurring_income (user_id, title, amount, source, frequency, next_pay_date, icon, status, account_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s)",
        (user_id, title.strip(), float(amount), source, frequency, next_pay_date, icon, account_id)
    )
    return cursor.lastrowid

def process_due_recurring_income(user_id, cursor, conn):
    """Auto-executes due recurring income deposits and updates account balances."""
    today_str = date.today().isoformat()
    cursor.execute(
        "SELECT recurring_income_id, title, amount, source, frequency, next_pay_date, account_id "
        "FROM recurring_income WHERE user_id=%s AND status='active' AND next_pay_date <= %s",
        (user_id, today_str)
    )
    due_items = cursor.fetchall()

    for item in due_items:
        rec_id, title, amt, source, freq, next_d, acc_id = item
        amt = float(amt)
        target_acc_id = acc_id or get_default_account_id(cursor, user_id)

        # 1. Log income entry
        desc = f"[Auto] {title}"
        cursor.execute(
            "INSERT INTO income (user_id, amount, source, description, income_date, account_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, amt, source, desc, today_str, target_acc_id)
        )
        adjust_account_on_income_create(cursor, target_acc_id, amt)

        # 2. Advance next pay date
        new_next_date = advance_income_date(next_d, freq).isoformat()
        cursor.execute(
            "UPDATE recurring_income SET next_pay_date=%s WHERE recurring_income_id=%s",
            (new_next_date, rec_id)
        )

def toggle_recurring_income_status(cursor, user_id, rec_inc_id):
    cursor.execute(
        "UPDATE recurring_income SET status = IF(status='active', 'paused', 'active') "
        "WHERE recurring_income_id=%s AND user_id=%s",
        (rec_inc_id, user_id)
    )

def delete_recurring_income(cursor, user_id, rec_inc_id):
    cursor.execute(
        "DELETE FROM recurring_income WHERE recurring_income_id=%s AND user_id=%s",
        (rec_inc_id, user_id)
    )

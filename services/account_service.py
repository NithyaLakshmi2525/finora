from db import get_db

ACCOUNT_TYPES = [
    ('checking', 'Checking Account', '🏦', 'Everyday bank account'),
    ('savings', 'Savings Account', '💰', 'Money set aside for saving'),
    ('credit_card', 'Credit Card', '💳', 'Card balance / money you owe'),
    ('cash', 'Cash', '💵', 'Physical cash you currently hold'),
    ('wallet', 'Digital Wallet', '📱', 'UPI or digital wallet balance')
]

def get_user_accounts(cursor, user_id):
    cursor.execute(
        'SELECT account_id, name, account_type, balance, currency, is_active, created_at '
        'FROM accounts WHERE user_id=%s AND is_active=1 ORDER BY account_id ASC',
        (user_id,)
    )
    rows = cursor.fetchall()
    return [{
        'account_id': r[0],
        'id': r[0],
        'name': r[1],
        'account_type': r[2],
        'balance': float(r[3] or 0),
        'currency': r[4],
        'is_active': bool(r[5]),
        'created_at': r[6]
    } for r in rows]

def get_account_by_id(cursor, user_id, account_id):
    cursor.execute(
        'SELECT account_id, name, account_type, balance, currency, is_active, created_at '
        'FROM accounts WHERE account_id=%s AND user_id=%s',
        (account_id, user_id)
    )
    r = cursor.fetchone()
    if not r:
        return None
    return {
        'account_id': r[0],
        'id': r[0],
        'name': r[1],
        'account_type': r[2],
        'balance': float(r[3] or 0),
        'currency': r[4],
        'is_active': bool(r[5]),
        'created_at': r[6]
    }

def get_default_account_id(cursor, user_id):
    cursor.execute(
        'SELECT account_id FROM accounts WHERE user_id=%s AND is_active=1 ORDER BY account_id ASC LIMIT 1',
        (user_id,)
    )
    r = cursor.fetchone()
    if r:
        return r[0]

    cursor.execute(
        "INSERT INTO accounts (user_id, name, account_type, balance, currency) "
        "VALUES (%s, 'Main Account', 'checking', 0.00, 'INR')",
        (user_id,)
    )
    return cursor.lastrowid

def create_account(cursor, user_id, name, account_type='checking', initial_balance=0.0, currency='INR'):
    cursor.execute(
        'INSERT INTO accounts (user_id, name, account_type, balance, currency) '
        'VALUES (%s, %s, %s, %s, %s)',
        (user_id, name.strip(), account_type, float(initial_balance or 0), currency.upper())
    )
    return cursor.lastrowid

def update_account(cursor, user_id, account_id, name, account_type, currency='INR'):
    cursor.execute(
        'UPDATE accounts SET name=%s, account_type=%s, currency=%s '
        'WHERE account_id=%s AND user_id=%s',
        (name.strip(), account_type, currency.upper(), account_id, user_id)
    )

def archive_account(cursor, user_id, account_id):
    cursor.execute(
        'UPDATE accounts SET is_active=0 WHERE account_id=%s AND user_id=%s',
        (account_id, user_id)
    )

def check_account_linked_transactions(cursor, user_id, account_id):
    cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s AND account_id=%s", (user_id, account_id))
    exp_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM income WHERE user_id=%s AND account_id=%s", (user_id, account_id))
    inc_count = cursor.fetchone()[0]
    return exp_count + inc_count

def delete_account(cursor, user_id, account_id):
    linked_count = check_account_linked_transactions(cursor, user_id, account_id)
    if linked_count > 0:
        return False, f"This account has {linked_count} linked transaction{'s' if linked_count != 1 else ''} and cannot be deleted safely. Archive it instead."
    
    cursor.execute("DELETE FROM accounts WHERE account_id=%s AND user_id=%s", (account_id, user_id))
    return True, "Account deleted successfully."

def update_account_balance(cursor, account_id, delta):
    if not account_id or delta == 0:
        return
    cursor.execute(
        'UPDATE accounts SET balance = balance + %s WHERE account_id = %s',
        (float(delta), account_id)
    )

def adjust_account_on_expense_create(cursor, account_id, amount):
    update_account_balance(cursor, account_id, -abs(float(amount or 0)))

def adjust_account_on_expense_delete(cursor, account_id, amount):
    update_account_balance(cursor, account_id, abs(float(amount or 0)))

def adjust_account_on_expense_update(cursor, old_account_id, old_amount, new_account_id, new_amount):
    if old_account_id:
        update_account_balance(cursor, old_account_id, abs(float(old_amount or 0)))
    if new_account_id:
        update_account_balance(cursor, new_account_id, -abs(float(new_amount or 0)))

def adjust_account_on_income_create(cursor, account_id, amount):
    update_account_balance(cursor, account_id, abs(float(amount or 0)))

def adjust_account_on_income_delete(cursor, account_id, amount):
    update_account_balance(cursor, account_id, -abs(float(amount or 0)))

def adjust_account_on_income_update(cursor, old_account_id, old_amount, new_account_id, new_amount):
    if old_account_id:
        update_account_balance(cursor, old_account_id, -abs(float(old_amount or 0)))
    if new_account_id:
        update_account_balance(cursor, new_account_id, abs(float(new_amount or 0)))

def get_archived_accounts(cursor, user_id):
    cursor.execute(
        'SELECT account_id, name, account_type, balance, currency, is_active, created_at '
        'FROM accounts WHERE user_id=%s AND is_active=0 ORDER BY account_id ASC',
        (user_id,)
    )
    rows = cursor.fetchall()
    return [{
        'account_id': r[0],
        'id': r[0],
        'name': r[1],
        'account_type': r[2],
        'balance': float(r[3] or 0),
        'currency': r[4],
        'is_active': bool(r[5]),
        'created_at': r[6]
    } for r in rows]

def restore_account(cursor, user_id, account_id):
    cursor.execute(
        'UPDATE accounts SET is_active=1 WHERE account_id=%s AND user_id=%s',
        (account_id, user_id)
    )

def get_accounts_summary(cursor, user_id):
    accounts = get_user_accounts(cursor, user_id)
    archived_accounts = get_archived_accounts(cursor, user_id)
    net_worth = sum(a['balance'] for a in accounts)
    type_totals = {}
    for t_item in ACCOUNT_TYPES:
        t_key = t_item[0]
        type_totals[t_key] = sum(a['balance'] for a in accounts if a['account_type'] == t_key)

    return {
        'net_worth': net_worth,
        'total_accounts': len(accounts),
        'accounts': accounts,
        'archived_accounts': archived_accounts,
        'archived_count': len(archived_accounts),
        'type_totals': type_totals,
        'account_types': ACCOUNT_TYPES
    }

def reset_user_financial_data(cursor, user_id):
    """Safely resets all user-owned financial and application data while preserving the user account."""
    tables_to_clear = [
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
        'accounts'
    ]
    for table in tables_to_clear:
        cursor.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))

    # Re-initialize fresh defaults for user
    cursor.execute("INSERT INTO notification_preferences (user_id) VALUES (%s)", (user_id,))
    cursor.execute(
        "INSERT INTO accounts (user_id, name, account_type, balance, currency) "
        "VALUES (%s, 'Main Account', 'checking', 0.00, 'INR')",
        (user_id,)
    )

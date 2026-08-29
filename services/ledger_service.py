DEFAULT_CATEGORIES = [
    ('Food', '🍕'), ('Shopping', '🛍️'), ('Travel', '🚗'), ('Bills', '🧾'),
    ('Entertainment', '🎬'), ('Health', '🏥'), ('Education', '📚'), ('Repairs', '🔧'),
    ('Investment', '📈'), ('Rent', '🏠'), ('Subscription', '📱'), ('Transport', '🚌'),
    ('Other', '💰'),
]

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

class TransactionDict(dict):
    """
    A dictionary representation of a transaction supporting both key access (tx['type'])
    and attribute/positional indexing (tx[0]) for full backward compatibility.
    
    Positional Index Mapping:
    0: id
    1: amount (float)
    2: category
    3: description
    4: date_fmt / date
    5: account_id
    6: type ('expense' or 'income')
    7: recurring_id
    """
    def __getitem__(self, item):
        if isinstance(item, int):
            mapping = [
                self.get('id'),
                self.get('amount'),
                self.get('category'),
                self.get('description'),
                self.get('date_fmt') or self.get('date'),
                self.get('account_id'),
                self.get('type'),
                self.get('recurring_id')
            ]
            if 0 <= item < len(mapping):
                return mapping[item]
            raise IndexError("TransactionDict index out of range")
        return super().__getitem__(item)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'TransactionDict' object has no attribute '{name}'")

def normalize_category_name(name):
    if not name:
        return name
    stripped = name.strip()
    return CATEGORY_ALIASES.get(stripped.lower(), stripped)

def get_categories(cursor):
    """Retrieves unique categories with icons, preserving defaults."""
    try:
        cursor.execute("SELECT name, icon FROM categories ORDER BY name ASC")
        rows = cursor.fetchall()
    except Exception:
        rows = []
    
    cat_dict = {}
    for name, icon in DEFAULT_CATEGORIES:
        cat_dict[name] = icon or '💰'
    for name, icon in rows:
        norm = normalize_category_name(name)
        if norm and norm not in cat_dict:
            cat_dict[norm] = icon or '💰'
            
    return sorted([(k, v) for k, v in cat_dict.items()], key=lambda x: x[0])

def csv_escape(val):
    if val is None:
        return ""
    s = str(val)
    if any(c in s for c in [',', '"', '\n', '\r']):
        s = '"' + s.replace('"', '""') + '"'
    return s

def fetch_filtered_transactions(cursor, user_id, start_date=None, end_date=None, category=None, search=None, sort_order='desc', show_income=False, limit=None, offset=None):
    """
    Fetches filtered transactions for user_id.
    Returns a list of TransactionDict objects preserving transaction type ('expense' vs 'income').
    """
    sort_param = str(sort_order).lower()
    if sort_param in ('amount_asc', 'asc'):
        order_clause = "tx_date ASC, id ASC"
    elif sort_param in ('amount_desc',):
        order_clause = "amount DESC, tx_date DESC, id DESC"
    elif sort_param in ('date_asc',):
        order_clause = "tx_date ASC, id ASC"
    else:
        order_clause = "tx_date DESC, id DESC"

    if show_income:
        query = (
            "SELECT expense_id AS id, expense_date AS tx_date, category AS category, "
            "description, amount, account_id, 'expense' AS tx_type, recurring_id "
            "FROM expenses WHERE user_id=%s"
        )
        params = [user_id]
        if start_date:
            query += " AND expense_date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND expense_date <= %s"
            params.append(end_date)
        if category and category != 'all':
            query += " AND category = %s"
            params.append(category)
        if search:
            query += " AND (description LIKE %s OR category LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%"])

        query += (
            " UNION ALL "
            "SELECT income_id AS id, income_date AS tx_date, source AS category, "
            "description, amount, account_id, 'income' AS tx_type, NULL AS recurring_id "
            "FROM income WHERE user_id=%s"
        )
        params.append(user_id)
        if start_date:
            query += " AND income_date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND income_date <= %s"
            params.append(end_date)
        if category and category != 'all':
            query += " AND source = %s"
            params.append(category)
        if search:
            query += " AND (description LIKE %s OR source LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%"])

        query += f" ORDER BY {order_clause}"
        if limit is not None and offset is not None:
            query += " LIMIT %s OFFSET %s"
            params.extend([int(limit), int(offset)])
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
    else:
        query = (
            "SELECT expense_id AS id, expense_date AS tx_date, category, "
            "description, amount, account_id, 'expense' AS tx_type, recurring_id "
            "FROM expenses WHERE user_id=%s"
        )
        params = [user_id]
        if start_date:
            query += " AND expense_date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND expense_date <= %s"
            params.append(end_date)
        if category and category != 'all':
            query += " AND category = %s"
            params.append(category)
        if search:
            query += " AND (description LIKE %s OR category LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%"])

        query += f" ORDER BY {order_clause}"
        if limit is not None and offset is not None:
            query += " LIMIT %s OFFSET %s"
            params.extend([int(limit), int(offset)])
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

    results = []
    for r in rows:
        tx_id, tx_date, cat_val, desc_val, amt_val, acc_id, tx_type, rec_id = r
        amt_float = float(amt_val or 0)
        dt_str = str(tx_date) if tx_date else ''
        if hasattr(tx_date, 'strftime'):
            dt_fmt = tx_date.strftime('%d %b %Y')
            dt_iso = tx_date.strftime('%Y-%m-%d')
        else:
            dt_fmt = dt_str
            dt_iso = dt_str

        tx_dict = TransactionDict({
            'id': tx_id,
            'amount': amt_float,
            'category': cat_val or 'Other',
            'description': desc_val or '',
            'date': dt_iso,
            'date_fmt': dt_fmt,
            'account_id': acc_id,
            'type': str(tx_type).lower() if tx_type else 'expense',
            'recurring_id': rec_id,
            'is_income': str(tx_type).lower() == 'income',
            'is_recurring': bool(rec_id) and str(tx_type).lower() != 'income',
        })
        results.append(tx_dict)

    return results

def build_income_context(cursor, user_id, page=1, per_page=10):
    cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM income WHERE user_id=%s", (user_id,))
    total_income = float(cursor.fetchone()[0])
    
    cursor.execute("SELECT COUNT(*) FROM income WHERE user_id=%s", (user_id,))
    total_count = cursor.fetchone()[0]
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    
    cursor.execute(
        "SELECT income_id, DATE_FORMAT(income_date, '%Y-%m-%d'), source, description, amount, "
        "DATE_FORMAT(income_date, '%d %b %Y') "
        "FROM income WHERE user_id=%s ORDER BY income_date DESC, income_id DESC LIMIT %s OFFSET %s",
        (user_id, per_page, offset)
    )
    income_rows = cursor.fetchall()

    avg_income = (total_income / total_count) if total_count > 0 else 0.0

    cursor.execute(
        "SELECT source, SUM(amount) AS total FROM income WHERE user_id=%s "
        "GROUP BY source ORDER BY total DESC, source ASC LIMIT 1",
        (user_id,)
    )
    top_row = cursor.fetchone()
    top_source = top_row[0] if top_row else None
    top_source_amount = float(top_row[1]) if top_row else 0.0
    
    return {
        'total_income': total_income,
        'avg_income': avg_income,
        'this_month': total_income,
        'monthly_income': total_income,
        'income_rows': income_rows,
        'income_list': income_rows,
        'income_count': total_count,
        'total_count': total_count,
        'top_source': top_source,
        'top_source_amount': top_source_amount,
        'page': page,
        'total_pages': total_pages,
    }

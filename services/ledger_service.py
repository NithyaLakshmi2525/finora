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
    order_dir = 'ASC' if str(sort_order).lower() == 'asc' else 'DESC'

    if show_income:
        # Union expenses and income when show_income is enabled
        query = (
            "SELECT expense_id AS id, expense_date AS tx_date, category, description, amount, 'Expense' AS tx_type "
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

        query += " UNION ALL SELECT income_id AS id, income_date AS tx_date, source AS category, description, amount, 'Income' AS tx_type FROM income WHERE user_id=%s"
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

        query += f" ORDER BY tx_date {order_dir}, id {order_dir}"
        if limit is not None and offset is not None:
            query += " LIMIT %s OFFSET %s"
            params.extend([int(limit), int(offset)])
        cursor.execute(query, tuple(params))
        return cursor.fetchall()

    query = "SELECT expense_id, expense_date, category, description, amount FROM expenses WHERE user_id=%s"
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

    query += f" ORDER BY expense_date {order_dir}, expense_id {order_dir}"
    if limit is not None and offset is not None:
        query += " LIMIT %s OFFSET %s"
        params.extend([int(limit), int(offset)])
    cursor.execute(query, tuple(params))
    return cursor.fetchall()

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

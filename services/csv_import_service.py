import csv
import io
from datetime import datetime, date
from services.account_service import get_default_account_id, adjust_account_on_expense_create

DATE_FORMATS = [
    '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%d %b %Y', '%Y/%m/%d'
]

def parse_date(date_str):
    if not date_str:
        return None
    d_clean = date_str.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(d_clean, fmt).date().isoformat()
        except ValueError:
            pass
    return None

def parse_and_preview_csv(cursor, user_id, file_content_str, account_id=None):
    """Parses bank CSV stream, normalizes fields, and detects existing duplicate expenses."""
    f = io.StringIO(file_content_str)
    reader = csv.reader(f)
    
    rows = list(reader)
    if not rows or len(rows) < 2:
        return {'error': 'CSV file is empty or missing headers.', 'rows': []}

    headers = [h.strip().lower() for h in rows[0]]
    
    # Auto-detect column indices
    date_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ['date', 'time', 'txn_date'])), None)
    desc_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ['desc', 'payee', 'memo', 'particular', 'narration', 'detail'])), None)
    amt_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ['amount', 'debit', 'value', 'sum'])), None)
    cat_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ['cat', 'type', 'tag'])), None)

    if date_idx is None or amt_idx is None:
        return {'error': 'Could not auto-detect Date and Amount columns in CSV.', 'rows': []}

    parsed_rows = []
    seen_in_file = set()

    for idx, raw_row in enumerate(rows[1:], start=2):
        if not raw_row or all(not cell.strip() for cell in raw_row):
            continue

        raw_date = raw_row[date_idx].strip() if date_idx < len(raw_row) else ''
        raw_amt = raw_row[amt_idx].strip() if amt_idx < len(raw_row) else ''
        raw_desc = raw_row[desc_idx].strip() if (desc_idx is not None and desc_idx < len(raw_row)) else 'CSV Import'
        raw_cat = raw_row[cat_idx].strip() if (cat_idx is not None and cat_idx < len(raw_row)) else 'Other'

        parsed_date = parse_date(raw_date)
        try:
            clean_amt_str = raw_amt.replace('$', '').replace('₹', '').replace(',', '').replace('INR', '').strip()
            parsed_amt = abs(float(clean_amt_str))
        except ValueError:
            parsed_amt = None

        is_valid = (parsed_date is not None) and (parsed_amt is not None and parsed_amt > 0)
        error_msg = None
        if not parsed_date:
            error_msg = f"Invalid date format: '{raw_date}'"
        elif not parsed_amt:
            error_msg = f"Invalid amount: '{raw_amt}'"

        is_duplicate = False
        if is_valid:
            sig = (parsed_date, parsed_amt, (raw_desc or 'CSV Import').strip().lower())
            if sig in seen_in_file:
                is_duplicate = True
                error_msg = "Duplicate row within the uploaded CSV"
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM expenses WHERE user_id=%s AND expense_date=%s AND amount=%s AND description=%s",
                    (user_id, parsed_date, parsed_amt, raw_desc)
                )
                if cursor.fetchone()[0] > 0:
                    is_duplicate = True
                    error_msg = "Duplicate of existing transaction in database"
                else:
                    seen_in_file.add(sig)

        parsed_rows.append({
            'row_num': idx,
            'expense_date': parsed_date or raw_date,
            'description': raw_desc or 'CSV Import',
            'amount': parsed_amt if parsed_amt is not None else 0.0,
            'category': raw_cat if raw_cat else 'Other',
            'is_valid': is_valid,
            'is_duplicate': is_duplicate,
            'error': error_msg
        })

    return {'error': None, 'rows': parsed_rows}

def commit_imported_csv_rows(cursor, user_id, selected_rows, account_id=None):
    """Bulk inserts validated CSV rows into expenses and updates account balance."""
    if not account_id:
        account_id = get_default_account_id(cursor, user_id)

    imported_count = 0
    seen_in_batch = set()

    for r in selected_rows:
        amount = float(r.get('amount', 0))
        category = r.get('category', 'Other') or 'Other'
        description = r.get('description', 'CSV Import') or 'CSV Import'
        expense_date = r.get('expense_date')

        if amount > 0 and expense_date:
            sig = (expense_date, amount, description.strip().lower())
            if sig in seen_in_batch:
                continue

            # Prevent duplicate insertion against existing DB records
            cursor.execute(
                "SELECT COUNT(*) FROM expenses WHERE user_id=%s AND expense_date=%s AND amount=%s AND description=%s",
                (user_id, expense_date, amount, description)
            )
            if cursor.fetchone()[0] > 0:
                continue

            seen_in_batch.add(sig)
            cursor.execute(
                "INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, amount, category, description, expense_date, account_id)
            )
            adjust_account_on_expense_create(cursor, account_id, amount)
            imported_count += 1

    return imported_count

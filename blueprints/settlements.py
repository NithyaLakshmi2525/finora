from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from datetime import date
from db import get_db
from services.settlement_service import balance_expense_description, build_settlements_summary
from services.ledger_service import get_categories, parse_financial_amount
from services.account_service import (
    get_default_account_id,
    adjust_account_on_expense_create,
    adjust_account_on_expense_delete,
    adjust_account_on_income_create,
    adjust_account_on_income_delete
)

settlements_bp = Blueprint('settlements', __name__)

def build_balances_payload(cursor, user_id):
    summary_info = build_settlements_summary(cursor, user_id)
    cursor.execute(
        "SELECT settlement_id, peer_name, amount, status, created_at, updated_at, reason, balance_date, counts_as_expense, linked_expense_id "
        "FROM settlements WHERE user_id=%s ORDER BY status ASC, created_at DESC",
        (user_id,)
    )
    items = cursor.fetchall()
    active_items = []
    history_items = []
    for r in items:
        amt = float(r[2] or 0.0)
        item_dict = {
            'id': r[0], 'settlement_id': r[0], 'peer_name': r[1], 'amount': amt,
            'status': r[3], 'created_at': str(r[4]), 'updated_at': str(r[5]),
            'reason': r[6], 'balance_date': str(r[7]), 'balance_date_display': str(r[7]),
            'counts_as_expense': bool(r[8]), 'linked_expense_id': r[9]
        }
        if r[3] == 'active':
            active_items.append(item_dict)
        else:
            history_items.append(item_dict)

    cats = get_categories(cursor)
    categories = [{'name': c[0], 'icon': c[1]} for c in cats]

    cursor.execute(
        "SELECT expense_id, amount, category, description, DATE_FORMAT(expense_date, '%d %b %Y') "
        "FROM expenses WHERE user_id=%s ORDER BY expense_date DESC, expense_id DESC LIMIT 20",
        (user_id,)
    )
    recent_expenses = [{
        'id': r[0],
        'label': f"₹{float(r[1]):,.2f} • {r[2]} ({r[3] or 'No desc'}) • {r[4]}"
    } for r in cursor.fetchall()]

    return {
        'owed_to_you': summary_info['total_owed_to_you'],
        'you_owe': summary_info['total_you_owe'],
        'net_position': summary_info['net_balance'],
        'net_balance': summary_info['net_balance'],
        'active': active_items,
        'history': history_items,
        'items': active_items + history_items,
        'categories': categories,
        'recent_expenses': recent_expenses
    }

@settlements_bp.route('/settlements', methods=['GET', 'POST'])
def settlements():
    if 'user_id' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        if request.method == 'POST':
            peer_name = request.form.get('peer_name', '').strip()
            direction = request.form.get('direction', 'they_owe_me')
            raw_amount, amt_err = parse_financial_amount(request.form.get('amount'))

            if not peer_name or amt_err:
                err = amt_err or "Valid person name is required."
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
                    return jsonify({'error': err}), 400
                flash(err, "error")
                return redirect('/settlements')

            amount = raw_amount if direction == 'they_owe_me' else -abs(raw_amount)
            reason = request.form.get('reason', '').strip() or None
            # BUG 1 Enforcement: counts_as_expense is only valid for 'owe_them' (I owe them)
            counts_as_expense = 1 if (direction == 'owe_them' and request.form.get('counts_as_expense')) else 0
            txn_date = request.form.get('txn_date') or request.form.get('balance_date') or date.today().isoformat()
            expense_category = request.form.get('expense_category', 'Other').strip() or 'Other'
            linked_expense_id = request.form.get('linked_expense_id') or None
            if linked_expense_id:
                try:
                    linked_expense_id = int(linked_expense_id)
                except ValueError:
                    linked_expense_id = None

            valid_cats = [c[0] for c in get_categories(cursor)]
            if expense_category not in valid_cats:
                expense_category = 'Other'

            if counts_as_expense:
                exp_desc = balance_expense_description(peer_name, reason)
                cursor.execute(
                    "INSERT INTO expenses (amount, category, description, expense_date, user_id) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (abs(amount), expense_category, exp_desc, txn_date, user_id)
                )
                linked_expense_id = cursor.lastrowid

            cursor.execute(
                "INSERT INTO settlements (user_id, peer_name, amount, status, reason, balance_date, counts_as_expense, linked_expense_id) "
                "VALUES (%s, %s, %s, 'active', %s, %s, %s, %s)",
                (user_id, peer_name, amount, reason, txn_date, counts_as_expense, linked_expense_id)
            )

            msg = f"Balance with {peer_name} logged successfully!"
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
                payload = build_balances_payload(cursor, user_id)
                return jsonify({'success': True, 'message': msg, 'data': payload})

            flash(msg, "success")
            return redirect('/settlements')

        cursor.execute(
            "SELECT settlement_id, peer_name, amount, status, created_at, updated_at, reason, balance_date, counts_as_expense, linked_expense_id "
            "FROM settlements WHERE user_id=%s ORDER BY status ASC, created_at DESC",
            (user_id,)
        )
        items = cursor.fetchall()
        summary_info = build_settlements_summary(cursor, user_id)
        categories = get_categories(cursor)

        cursor.execute(
            "SELECT expense_id, amount, category, description, DATE_FORMAT(expense_date, '%d %b %Y') "
            "FROM expenses WHERE user_id=%s ORDER BY expense_date DESC, expense_id DESC LIMIT 20",
            (user_id,)
        )
        recent_expenses = [{
            'id': r[0],
            'label': f"₹{float(r[1]):,.2f} • {r[2]} ({r[3] or 'No desc'}) • {r[4]}"
        } for r in cursor.fetchall()]

    return render_template(
        'balances/balances.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        items=items,
        owed_to_you=summary_info['total_owed_to_you'],
        you_owe=summary_info['total_you_owe'],
        net_balance=summary_info['net_balance'],
        net_position=summary_info['net_balance'],
        categories=categories,
        recent_expenses=recent_expenses,
        active_page='balances'
    )

@settlements_bp.route('/balances')
def balances_redirect():
    if 'user_id' not in session:
        return redirect('/login')
    return redirect('/settlements')

@settlements_bp.route('/api/settlements/data')
def settlements_data():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        payload = build_balances_payload(cursor, user_id)

    return jsonify(payload)

@settlements_bp.route('/settle/<int:settlement_id>', methods=['POST'])
def settle_transaction(settlement_id):
    if 'user_id' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/login')
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT peer_name, amount, status FROM settlements WHERE settlement_id=%s AND user_id=%s",
            (settlement_id, user_id)
        )
        item = cursor.fetchone()
        if not item:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
                return jsonify({'error': 'Settlement not found.'}), 404
            flash("Settlement not found.", "error")
            return redirect('/settlements')

        peer_name, amount, status = item
        if status == 'active':
            amt_val = float(amount or 0.0)
            account_id = get_default_account_id(cursor, user_id)
            if amt_val < 0:
                # Payable settled -> money leaves bank account
                adjust_account_on_expense_create(cursor, account_id, abs(amt_val))
            elif amt_val > 0:
                # Receivable settled -> money enters bank account
                adjust_account_on_income_create(cursor, account_id, abs(amt_val))

            cursor.execute(
                "UPDATE settlements SET status='settled', updated_at=CURRENT_TIMESTAMP WHERE settlement_id=%s AND user_id=%s",
                (settlement_id, user_id)
            )

        msg = "Balance marked as settled!"
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            payload = build_balances_payload(cursor, user_id)
            return jsonify({'success': True, 'message': msg, 'data': payload})

    flash(msg, "success")
    return redirect('/settlements')

@settlements_bp.route('/api/settlements/<int:settlement_id>/edit', methods=['POST'])
def edit_settlement(settlement_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = session['user_id']
    peer_name = request.form.get('peer_name', '').strip()
    direction = request.form.get('direction', 'they_owe_me')
    try:
        raw_amount = float(request.form.get('amount', 0))
    except ValueError:
        raw_amount = 0.0

    if not peer_name or raw_amount <= 0:
        return jsonify({'error': 'Valid person name and positive amount are required.'}), 400

    amount = raw_amount if direction == 'they_owe_me' else -abs(raw_amount)
    reason = request.form.get('reason', '').strip() or None
    counts_as_expense = 1 if (direction == 'owe_them' and request.form.get('counts_as_expense')) else 0
    txn_date = request.form.get('txn_date') or request.form.get('balance_date') or date.today().isoformat()
    expense_category = request.form.get('expense_category', 'Other').strip() or 'Other'

    with get_db() as (conn, cursor):
        valid_cats = [c[0] for c in get_categories(cursor)]
        if expense_category not in valid_cats:
            expense_category = 'Other'

        cursor.execute(
            "SELECT status, counts_as_expense, linked_expense_id, amount FROM settlements "
            "WHERE settlement_id=%s AND user_id=%s",
            (settlement_id, user_id)
        )
        existing = cursor.fetchone()
        if not existing:
            return jsonify({'error': 'Balance not found.'}), 404

        status, old_counts, linked_exp_id, existing_amount = existing[0], bool(existing[1]), existing[2], float(existing[3] or 0.0)

        # BUG 2 Server-side Enforcement: Settled settlements lock financial fields!
        if status == 'settled':
            existing_dir = 'they_owe_me' if existing_amount > 0 else 'owe_them'
            if (abs(amount - existing_amount) > 0.001) or (direction != existing_dir) or (counts_as_expense != int(old_counts)):
                return jsonify({'error': 'Financial details are locked because this balance is settled. Reopen balance to edit financial details.'}), 400

        if counts_as_expense:
            exp_desc = balance_expense_description(peer_name, reason)
            if linked_exp_id:
                cursor.execute(
                    "UPDATE expenses SET amount=%s, category=%s, description=%s, expense_date=%s "
                    "WHERE expense_id=%s AND user_id=%s",
                    (abs(amount), expense_category, exp_desc, txn_date, linked_exp_id, user_id)
                )
            else:
                cursor.execute(
                    "INSERT INTO expenses (amount, category, description, expense_date, user_id) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (abs(amount), expense_category, exp_desc, txn_date, user_id)
                )
                linked_exp_id = cursor.lastrowid
        elif old_counts and linked_exp_id:
            cursor.execute("DELETE FROM expenses WHERE expense_id=%s AND user_id=%s", (linked_exp_id, user_id))
            linked_exp_id = None

        cursor.execute(
            "UPDATE settlements SET peer_name=%s, amount=%s, reason=%s, balance_date=%s, "
            "counts_as_expense=%s, linked_expense_id=%s WHERE settlement_id=%s AND user_id=%s",
            (peer_name, amount, reason, txn_date, counts_as_expense, linked_exp_id, settlement_id, user_id)
        )

        msg = "Balance updated successfully!"
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            payload = build_balances_payload(cursor, user_id)
            return jsonify({'success': True, 'message': msg, 'data': payload})

    flash("Balance updated successfully!", "success")
    return redirect('/settlements')

@settlements_bp.route('/api/settlements/<int:settlement_id>/reopen', methods=['POST'])
def reopen_settlement(settlement_id):
    if 'user_id' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/login')
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT peer_name, amount, status FROM settlements WHERE settlement_id=%s AND user_id=%s",
            (settlement_id, user_id)
        )
        item = cursor.fetchone()
        if not item:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
                return jsonify({'error': 'Settlement not found.'}), 404
            flash("Settlement not found.", "error")
            return redirect('/settlements')

        peer_name, amount, status = item
        if status == 'settled':
            amt_val = float(amount or 0.0)
            account_id = get_default_account_id(cursor, user_id)
            if amt_val < 0:
                # Reopen payable -> reverse account reduction
                adjust_account_on_expense_delete(cursor, account_id, abs(amt_val))
            elif amt_val > 0:
                # Reopen receivable -> reverse account addition
                adjust_account_on_income_delete(cursor, account_id, abs(amt_val))

            cursor.execute(
                "UPDATE settlements SET status='active', updated_at=CURRENT_TIMESTAMP WHERE settlement_id=%s AND user_id=%s",
                (settlement_id, user_id)
            )

        msg = "Balance reopened!"
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            payload = build_balances_payload(cursor, user_id)
            return jsonify({'success': True, 'message': msg, 'data': payload})

    flash(msg, "success")
    return redirect('/settlements')

@settlements_bp.route('/api/settlements/<int:settlement_id>/delete', methods=['POST'])
def delete_settlement(settlement_id):
    if 'user_id' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/login')
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT amount, status, counts_as_expense, linked_expense_id FROM settlements "
            "WHERE settlement_id=%s AND user_id=%s",
            (settlement_id, user_id)
        )
        row = cursor.fetchone()
        if row:
            amt_val, status, counts, linked_exp_id = float(row[0] or 0.0), row[1], row[2], row[3]
            account_id = get_default_account_id(cursor, user_id)
            if status == 'settled':
                if amt_val < 0:
                    adjust_account_on_expense_delete(cursor, account_id, abs(amt_val))
                elif amt_val > 0:
                    adjust_account_on_income_delete(cursor, account_id, abs(amt_val))

            if linked_exp_id:
                cursor.execute("DELETE FROM expenses WHERE expense_id=%s AND user_id=%s", (linked_exp_id, user_id))
            cursor.execute("DELETE FROM settlements WHERE settlement_id=%s AND user_id=%s", (settlement_id, user_id))

        msg = "Balance deleted successfully!"
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            payload = build_balances_payload(cursor, user_id)
            return jsonify({'success': True, 'message': msg, 'data': payload})

    flash(msg, "success")
    return redirect('/settlements')

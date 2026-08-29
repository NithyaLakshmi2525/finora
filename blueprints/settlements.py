from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from datetime import date
from db import get_db
from services.settlement_service import balance_expense_description, build_settlements_summary

settlements_bp = Blueprint('settlements', __name__)

@settlements_bp.route('/settlements', methods=['GET', 'POST'])
def settlements():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        if request.method == 'POST':
            peer_name = request.form['peer_name'].strip()
            direction = request.form['direction']
            raw_amount = float(request.form['amount'])
            amount = raw_amount if direction == 'they_owe_me' else -abs(raw_amount)
            reason = request.form.get('reason', '').strip() or None
            counts_as_expense = 1 if request.form.get('counts_as_expense') else 0
            txn_date = request.form.get('txn_date') or date.today().isoformat()

            linked_expense_id = None
            if counts_as_expense:
                exp_desc = balance_expense_description(peer_name, reason)
                cursor.execute(
                    "INSERT INTO expenses (amount, category, description, expense_date, user_id) "
                    "VALUES (%s, 'Other', %s, %s, %s)",
                    (abs(amount), exp_desc, txn_date, user_id)
                )
                linked_expense_id = cursor.lastrowid

            cursor.execute(
                "INSERT INTO settlements (user_id, peer_name, amount, status, reason, balance_date, counts_as_expense, linked_expense_id) "
                "VALUES (%s, %s, %s, 'active', %s, %s, %s, %s)",
                (user_id, peer_name, amount, reason, txn_date, counts_as_expense, linked_expense_id)
            )
            flash(f"Balance with {peer_name} logged successfully!", "success")
            return redirect('/settlements')

        cursor.execute(
            "SELECT settlement_id, peer_name, amount, status, created_at, updated_at, reason, balance_date, counts_as_expense, linked_expense_id "
            "FROM settlements WHERE user_id=%s ORDER BY status ASC, created_at DESC",
            (user_id,)
        )
        items = cursor.fetchall()
        summary_info = build_settlements_summary(cursor, user_id)

    return render_template(
        'balances/balances.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        items=items,
        owed_to_you=summary_info['total_owed_to_you'],
        you_owe=summary_info['total_you_owe'],
        net_balance=summary_info['net_balance'],
        net_position=summary_info['net_balance'],
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

    return jsonify({
        'owed_to_you': summary_info['total_owed_to_you'],
        'you_owe': summary_info['total_you_owe'],
        'net_position': summary_info['net_balance'],
        'net_balance': summary_info['net_balance'],
        'active': active_items,
        'history': history_items,
        'items': active_items + history_items
    })

@settlements_bp.route('/settle/<int:settlement_id>', methods=['POST'])
def settle_transaction(settlement_id):
    if 'user_id' not in session:
        return redirect('/login')
    with get_db() as (conn, cursor):
        cursor.execute(
            "UPDATE settlements SET status='settled' WHERE settlement_id=%s AND user_id=%s AND status='active'",
            (settlement_id, session['user_id'])
        )
    flash("Settlement marked as settled!", "success")
    return redirect('/settlements')

@settlements_bp.route('/api/settlements/<int:settlement_id>/edit', methods=['POST'])
def edit_settlement(settlement_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = session['user_id']
    peer_name = request.form['peer_name'].strip()
    direction = request.form['direction']
    raw_amount = float(request.form['amount'])
    amount = raw_amount if direction == 'they_owe_me' else -abs(raw_amount)
    reason = request.form.get('reason', '').strip() or None
    counts_as_expense = 1 if request.form.get('counts_as_expense') else 0
    txn_date = request.form.get('txn_date') or date.today().isoformat()

    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT status, counts_as_expense, linked_expense_id FROM settlements "
            "WHERE settlement_id=%s AND user_id=%s",
            (settlement_id, user_id)
        )
        existing = cursor.fetchone()
        if not existing:
            return jsonify({'error': 'Not found'}), 404

        old_counts, linked_exp_id = bool(existing[1]), existing[2]

        if counts_as_expense:
            exp_desc = balance_expense_description(peer_name, reason)
            if linked_exp_id:
                cursor.execute(
                    "UPDATE expenses SET amount=%s, category='Other', description=%s, expense_date=%s "
                    "WHERE expense_id=%s AND user_id=%s",
                    (abs(amount), exp_desc, txn_date, linked_exp_id, user_id)
                )
            else:
                cursor.execute(
                    "INSERT INTO expenses (amount, category, description, expense_date, user_id) "
                    "VALUES (%s, 'Other', %s, %s, %s)",
                    (abs(amount), exp_desc, txn_date, user_id)
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

    flash("Settlement updated successfully!", "success")
    return redirect('/settlements')

@settlements_bp.route('/api/settlements/<int:settlement_id>/reopen', methods=['POST'])
def reopen_settlement(settlement_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as (conn, cursor):
        cursor.execute(
            "UPDATE settlements SET status='active' WHERE settlement_id=%s AND user_id=%s AND status='settled'",
            (settlement_id, session['user_id'])
        )
    flash("Settlement reopened!", "success")
    return redirect('/settlements')

@settlements_bp.route('/api/settlements/<int:settlement_id>/delete', methods=['POST'])
def delete_settlement(settlement_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT counts_as_expense, linked_expense_id FROM settlements "
            "WHERE settlement_id=%s AND user_id=%s",
            (settlement_id, user_id)
        )
        row = cursor.fetchone()
        if row and row[1]:
            cursor.execute("DELETE FROM expenses WHERE expense_id=%s AND user_id=%s", (row[1], user_id))
        cursor.execute("DELETE FROM settlements WHERE settlement_id=%s AND user_id=%s", (settlement_id, user_id))

    flash("Settlement deleted successfully!", "success")
    return redirect('/settlements')

from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from werkzeug.security import check_password_hash
from db import get_db
from services.account_service import (
    get_accounts_summary, create_account, update_account, archive_account, delete_account, ACCOUNT_TYPES
)

accounts_bp = Blueprint('accounts', __name__)

@accounts_bp.route('/accounts', methods=['GET'])
def accounts():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        summary = get_accounts_summary(cursor, user_id)

    return render_template(
        'accounts/accounts.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        summary=summary,
        accounts=summary['accounts'],
        net_worth=summary['net_worth'],
        account_types=ACCOUNT_TYPES,
        active_page='accounts'
    )

@accounts_bp.route('/add-account', methods=['POST'])
def add_account():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    name = request.form.get('name', '').strip()
    account_type = request.form.get('account_type', 'checking')
    initial_balance = float(request.form.get('initial_balance', 0.0) or 0.0)
    currency = request.form.get('currency', 'INR').strip() or 'INR'

    if not name:
        flash('Account name is required.', 'error')
        return redirect('/accounts')

    with get_db() as (conn, cursor):
        create_account(cursor, user_id, name, account_type, initial_balance, currency)

    flash(f"Account '{name}' created successfully!", 'success')
    return redirect('/accounts')

@accounts_bp.route('/edit-account/<int:account_id>', methods=['POST'])
def edit_account(account_id):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    name = request.form.get('name', '').strip()
    account_type = request.form.get('account_type', 'checking')
    currency = request.form.get('currency', 'INR').strip() or 'INR'

    if not name:
        flash('Account name is required.', 'error')
        return redirect('/accounts')

    with get_db() as (conn, cursor):
        update_account(cursor, user_id, account_id, name, account_type, currency)

    flash('Account updated successfully!', 'success')
    return redirect('/accounts')

@accounts_bp.route('/toggle-account/<int:account_id>', methods=['POST'])
def toggle_account(account_id):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        archive_account(cursor, user_id, account_id)

    flash('Account archived successfully!', 'success')
    return redirect('/accounts')

@accounts_bp.route('/delete-account/<int:account_id>', methods=['POST'])
def remove_account(account_id):
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    confirm_text = request.form.get('confirm_text', '').strip()
    password = request.form.get('password', '')

    with get_db() as (conn, cursor):
        cursor.execute("SELECT password, auth_provider FROM users WHERE user_id=%s", (user_id,))
        user_row = cursor.fetchone()
        if not user_row:
            flash("User not found.", "error")
            return redirect('/accounts')

        user_pw, auth_provider = user_row[0], user_row[1]

        if confirm_text != 'DELETE':
            flash("You must type DELETE to confirm deletion.", "error")
            return redirect('/accounts')

        if auth_provider != 'google':
            if not password or not check_password_hash(user_pw, password):
                flash("Incorrect password. Deletion cancelled.", "error")
                return redirect('/accounts')

        success, msg = delete_account(cursor, user_id, account_id)
        if success:
            flash(msg, "success")
        else:
            flash(msg, "error")

    return redirect('/accounts')

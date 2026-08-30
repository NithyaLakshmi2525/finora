from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from datetime import date, datetime, timedelta
from db import get_db
from services.goal_service import compute_goal_display, motivation_for_percent, build_goal_summary
from services.ledger_service import parse_financial_amount

goals_bp = Blueprint('goals', __name__)

def fetch_goal_by_id(cursor, user_id, goal_id):
    cursor.execute(
        "SELECT goal_id, user_id, goal_name, target_amount, current_amount, "
        "DATE_FORMAT(target_date, '%Y-%m-%d'), description, icon, color, closed_at, "
        "DATE_FORMAT(target_date, '%d %b %Y') "
        "FROM savings_goals WHERE goal_id=%s AND user_id=%s",
        (goal_id, user_id)
    )
    g = cursor.fetchone()
    if not g:
        return None

    disp = compute_goal_display(g[3], g[4], g[5], g[9])
    return {
        'goal_id': g[0], 'id': g[0], 'goal_name': g[2], 'name': g[2],
        'target_amount': float(g[3] or 0), 'target': float(g[3] or 0),
        'current_amount': float(g[4] or 0), 'current': float(g[4] or 0),
        'status': disp['status_key'], 'bar_width': disp['pct'],
        'target_date_iso': g[5], 'target_date_fmt': g[10],
        'target_date_display': g[10], 'description': g[6],
        'icon': g[7] or '🎯', 'color': g[8] or '#4edea3', 'closed_at': g[9],
        'is_closed': bool(g[9]),
        'motivation_text': motivation_for_percent(disp['pct_rounded']),
        'motivation_class': 'text-primary' if disp['pct_rounded'] >= 50 else 'text-on-surface-variant',
        **disp
    }

@goals_bp.route('/goals')
def goals():
    if 'user_id' not in session:
        return redirect('/login')
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT goal_id, user_id, goal_name, target_amount, current_amount, "
            "DATE_FORMAT(target_date, '%Y-%m-%d'), description, icon, color, closed_at, "
            "DATE_FORMAT(target_date, '%d %b %Y') "
            "FROM savings_goals WHERE user_id=%s ORDER BY closed_at ASC, goal_id DESC",
            (user_id,)
        )
        raw_goals = cursor.fetchall()
        processed_goals = []
        for g in raw_goals:
            disp = compute_goal_display(g[3], g[4], g[5], g[9])
            processed_goals.append({
                'goal_id': g[0], 'id': g[0], 'goal_name': g[2], 'name': g[2],
                'target_amount': float(g[3] or 0), 'target': float(g[3] or 0),
                'current_amount': float(g[4] or 0), 'current': float(g[4] or 0),
                'status': disp['status_key'], 'target_date_iso': g[5],
                'target_date_fmt': g[10], 'description': g[6], 'icon': g[7] or '🎯',
                'color': g[8] or '#4edea3', 'closed_at': g[9], **disp
            })
        active_goals = [g for g in processed_goals if not g.get('closed_at')]
        closed_goals = [g for g in processed_goals if g.get('closed_at')]

        goal_summary = build_goal_summary(cursor, user_id)
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM income "
            "WHERE user_id=%s AND DATE_FORMAT(income_date, '%Y-%m') = DATE_FORMAT(CURRENT_DATE(), '%Y-%m')",
            (user_id,)
        )
        monthly_inc = float(cursor.fetchone()[0])
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%Y-%m') = DATE_FORMAT(CURRENT_DATE(), '%Y-%m')",
            (user_id,)
        )
        monthly_exp = float(cursor.fetchone()[0])
        available_balance = monthly_inc - monthly_exp

    return render_template(
        'goals/goals.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        goals=active_goals,
        active_goals=active_goals,
        closed_goals=closed_goals,
        goal_summary=goal_summary,
        total_balance=goal_summary['total_reserved'],
        reserved_for_goals=goal_summary['total_reserved'],
        total_target=goal_summary['total_target'],
        available_balance=available_balance,
        active_count=len(active_goals),
        overall_progress=goal_summary['overall_pct_rounded'],
        active_page='goals'
    )

@goals_bp.route('/add-goal', methods=['GET', 'POST'])
def add_goal():
    if 'user_id' not in session:
        return redirect('/login')
    if request.method == 'POST':
        goal_name = request.form['goal_name'].strip()
        target_amount, err1 = parse_financial_amount(request.form.get('target_amount'))
        if err1:
            flash(err1, "error")
            return redirect('/add-goal')
        initial_amount, err2 = parse_financial_amount(request.form.get('current_amount', 0) or 0, allow_zero=True)
        if err2:
            flash(err2, "error")
            return redirect('/add-goal')

        target_date = request.form.get('target_date') or None
        description = request.form.get('description', '').strip()
        icon = request.form.get('icon', '🎯')
        color = request.form.get('color', '#4edea3')

        with get_db() as (conn, cursor):
            cursor.execute(
                "INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount, target_date, description, icon, color) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (session['user_id'], goal_name, target_amount, initial_amount, target_date, description, icon, color)
            )
        flash("Savings goal created successfully!", "success")
        return redirect('/goals')

    return render_template(
        'goals/goal_form.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        active_page='goals'
    )

@goals_bp.route('/goals/<int:goal_id>')
def goal_details(goal_id):
    if 'user_id' not in session:
        return redirect('/login')
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        goal_data = fetch_goal_by_id(cursor, user_id, goal_id)
        if not goal_data:
            flash("Goal not found.", "error")
            return redirect('/goals')

        cursor.execute(
            "SELECT contribution_id, amount, note, DATE_FORMAT(created_at, '%d %b %Y %h:%i %p') "
            "FROM goal_contributions WHERE goal_id=%s AND user_id=%s ORDER BY created_at DESC",
            (goal_id, user_id)
        )
        contributions = cursor.fetchall()

    return render_template(
        'goals/goal_details.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        goal=goal_data,
        contributions=contributions,
        motivation=motivation_for_percent(goal_data['pct']),
        active_page='goals'
    )

@goals_bp.route('/goals/<int:goal_id>/history')
def goal_history(goal_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT goal_id FROM savings_goals WHERE goal_id=%s AND user_id=%s",
            (goal_id, user_id)
        )
        if not cursor.fetchone():
            return jsonify({'error': 'Goal not found'}), 404

        cursor.execute(
            "SELECT contribution_id, amount, note, created_at "
            "FROM goal_contributions WHERE goal_id=%s AND user_id=%s ORDER BY created_at DESC, contribution_id DESC",
            (goal_id, user_id)
        )
        rows = cursor.fetchall()
        history = []
        now = datetime.now()
        for cid, amount, note, created_at in rows:
            amt = float(amount or 0)
            if isinstance(created_at, datetime):
                dt = created_at
            elif isinstance(created_at, str) and created_at.strip():
                try:
                    dt = datetime.fromisoformat(created_at.strip())
                except ValueError:
                    dt = now
            else:
                dt = now

            if dt.date() == now.date():
                day_label = 'Today'
            elif dt.date() == (now.date() - timedelta(days=1)):
                day_label = 'Yesterday'
            else:
                day_label = dt.strftime('%d %b %Y').lstrip('0')

            time_str = dt.strftime('%I:%M %p').lstrip('0')

            history.append({
                'id': cid,
                'amount': amt,
                'note': note or '',
                'day_label': day_label,
                'time': time_str,
                'created_at': dt.strftime('%Y-%m-%d %H:%M:%S')
            })

    return jsonify({'history': history})

@goals_bp.route('/update-goal/<int:goal_id>', methods=['POST'])
def update_goal(goal_id):
    if 'user_id' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/login')

    user_id = session['user_id']
    action_type = (request.form.get('action_type') or request.form.get('type') or 'deposit').strip().lower()
    raw_amount = request.form.get('amount') or request.form.get('added_amount')
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json

    amount_val, amt_err = parse_financial_amount(raw_amount)
    if amt_err:
        if is_ajax:
            return jsonify({'error': amt_err}), 400
        flash(amt_err, "error")
        return redirect(f"/goals/{goal_id}")

    if action_type in ('withdraw', 'withdrawal'):
        amount = -abs(amount_val)
    else:
        amount = abs(amount_val)

    note = request.form.get('note', '').strip() or None

    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT current_amount FROM savings_goals WHERE goal_id=%s AND user_id=%s",
            (goal_id, user_id)
        )
        g_row = cursor.fetchone()
        if not g_row:
            if is_ajax:
                return jsonify({'error': 'Goal not found'}), 404
            flash("Goal not found.", "error")
            return redirect('/goals')

        current_res = float(g_row[0] or 0)
        if amount < 0 and abs(amount) > current_res:
            err_msg = f"Cannot withdraw ₹{abs(amount):,.2f} — only ₹{current_res:,.2f} is reserved in this goal."
            if is_ajax:
                return jsonify({'error': err_msg}), 400
            flash(err_msg, "error")
            return redirect(f"/goals/{goal_id}")

        new_current = current_res + amount
        cursor.execute(
            "UPDATE savings_goals SET current_amount=%s WHERE goal_id=%s AND user_id=%s",
            (new_current, goal_id, user_id)
        )
        cursor.execute(
            "INSERT INTO goal_contributions (goal_id, user_id, amount, note) VALUES (%s, %s, %s, %s)",
            (goal_id, user_id, amount, note)
        )
        updated_goal = fetch_goal_by_id(cursor, user_id, goal_id)

    if is_ajax:
        action_name = 'Deposit' if amount > 0 else 'Withdrawal'
        return jsonify({
            'success': True,
            'message': f"{action_name} recorded successfully!",
            'goal': updated_goal
        })

    flash("Goal progress updated successfully!", "success")
    return redirect(f"/goals/{goal_id}")

@goals_bp.route('/api/goals/<int:goal_id>/contributions/<int:contribution_id>/edit', methods=['POST'])
def edit_contribution(goal_id, contribution_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    raw_amount = request.form.get('amount') or '0'
    try:
        new_amount = float(raw_amount)
    except ValueError:
        new_amount = 0.0

    note = request.form.get('note', '').strip() or None

    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT amount FROM goal_contributions WHERE contribution_id=%s AND goal_id=%s AND user_id=%s",
            (contribution_id, goal_id, user_id)
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Transaction not found'}), 404

        old_amount = float(row[0] or 0)
        if old_amount < 0 and new_amount > 0:
            new_amount = -new_amount
        elif old_amount >= 0 and new_amount < 0:
            new_amount = abs(new_amount)

        diff = new_amount - old_amount

        cursor.execute(
            "UPDATE goal_contributions SET amount=%s, note=%s WHERE contribution_id=%s AND goal_id=%s AND user_id=%s",
            (new_amount, note, contribution_id, goal_id, user_id)
        )

        cursor.execute(
            "UPDATE savings_goals SET current_amount = current_amount + %s WHERE goal_id=%s AND user_id=%s",
            (diff, goal_id, user_id)
        )
        updated_goal = fetch_goal_by_id(cursor, user_id, goal_id)

    return jsonify({
        'success': True,
        'message': 'Transaction updated successfully!',
        'goal': updated_goal
    })

@goals_bp.route('/api/goals/<int:goal_id>/contributions/<int:contribution_id>/delete', methods=['POST'])
def delete_contribution(goal_id, contribution_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']

    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT amount FROM goal_contributions WHERE contribution_id=%s AND goal_id=%s AND user_id=%s",
            (contribution_id, goal_id, user_id)
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Transaction not found'}), 404

        old_amount = float(row[0] or 0)

        cursor.execute(
            "DELETE FROM goal_contributions WHERE contribution_id=%s AND goal_id=%s AND user_id=%s",
            (contribution_id, goal_id, user_id)
        )

        cursor.execute(
            "UPDATE savings_goals SET current_amount = current_amount - %s WHERE goal_id=%s AND user_id=%s",
            (old_amount, goal_id, user_id)
        )
        updated_goal = fetch_goal_by_id(cursor, user_id, goal_id)

    return jsonify({
        'success': True,
        'message': 'Transaction deleted successfully!',
        'goal': updated_goal
    })

@goals_bp.route('/close-goal/<int:goal_id>', methods=['POST'])
def close_goal(goal_id):
    if 'user_id' not in session:
        return redirect('/login')
    with get_db() as (conn, cursor):
        cursor.execute(
            "UPDATE savings_goals SET closed_at=NOW() WHERE goal_id=%s AND user_id=%s",
            (goal_id, session['user_id'])
        )
    flash("Goal marked as closed.", "success")
    return redirect('/goals')

@goals_bp.route('/restore-goal/<int:goal_id>', methods=['POST'])
def restore_goal(goal_id):
    if 'user_id' not in session:
        return redirect('/login')
    with get_db() as (conn, cursor):
        cursor.execute(
            "UPDATE savings_goals SET closed_at=NULL WHERE goal_id=%s AND user_id=%s",
            (goal_id, session['user_id'])
        )
    flash("Goal reopened!", "success")
    return redirect(f"/goals/{goal_id}")

@goals_bp.route('/delete-goal/<int:goal_id>', methods=['POST'])
def delete_goal(goal_id):
    if 'user_id' not in session:
        return redirect('/login')
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM savings_goals WHERE goal_id=%s AND user_id=%s", (goal_id, session['user_id']))
    flash("Goal deleted successfully!", "success")
    return redirect('/goals')

from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from datetime import date
from db import get_db
from services.goal_service import compute_goal_display, motivation_for_percent, build_goal_summary

goals_bp = Blueprint('goals', __name__)

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
        goals=processed_goals,
        goal_summary=goal_summary,
        total_balance=goal_summary['total_reserved'],
        reserved_for_goals=goal_summary['total_reserved'],
        total_target=goal_summary['total_target'],
        available_balance=available_balance,
        active_count=goal_summary['total_goals'],
        overall_progress=goal_summary['overall_pct_rounded'],
        active_page='goals'
    )

@goals_bp.route('/add-goal', methods=['GET', 'POST'])
def add_goal():
    if 'user_id' not in session:
        return redirect('/login')
    if request.method == 'POST':
        goal_name = request.form['goal_name'].strip()
        target_amount = float(request.form['target_amount'])
        initial_amount = float(request.form.get('current_amount', 0) or 0)
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
        cursor.execute(
            "SELECT goal_id, user_id, goal_name, target_amount, current_amount, "
            "DATE_FORMAT(target_date, '%Y-%m-%d'), description, icon, color, closed_at, "
            "DATE_FORMAT(target_date, '%d %b %Y') "
            "FROM savings_goals WHERE goal_id=%s AND user_id=%s",
            (goal_id, user_id)
        )
        g = cursor.fetchone()
        if not g:
            flash("Goal not found.", "error")
            return redirect('/goals')

        disp = compute_goal_display(g[3], g[4], g[5], g[9])
        goal_data = {
            'goal_id': g[0], 'id': g[0], 'goal_name': g[2], 'name': g[2],
            'target_amount': float(g[3] or 0), 'target': float(g[3] or 0),
            'current_amount': float(g[4] or 0), 'current': float(g[4] or 0),
            'status': disp['status_key'], 'bar_width': min(100.0, disp['pct']),
            'target_date_iso': g[5], 'target_date_fmt': g[10], 'description': g[6],
            'icon': g[7] or '🎯', 'color': g[8] or '#4edea3', 'closed_at': g[9], **disp
        }

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

@goals_bp.route('/update-goal/<int:goal_id>', methods=['POST'])
def update_goal(goal_id):
    if 'user_id' not in session:
        return redirect('/login')
    amount = float(request.form['amount'])
    note = request.form.get('note', '').strip() or None
    user_id = session['user_id']

    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT current_amount FROM savings_goals WHERE goal_id=%s AND user_id=%s",
            (goal_id, user_id)
        )
        g_row = cursor.fetchone()
        if not g_row:
            flash("Goal not found.", "error")
            return redirect('/goals')

        new_current = float(g_row[0] or 0) + amount
        cursor.execute(
            "UPDATE savings_goals SET current_amount=%s WHERE goal_id=%s AND user_id=%s",
            (new_current, goal_id, user_id)
        )
        cursor.execute(
            "INSERT INTO goal_contributions (goal_id, user_id, amount, note) VALUES (%s, %s, %s, %s)",
            (goal_id, user_id, amount, note)
        )

    flash("Goal progress updated successfully!", "success")
    return redirect(f"/goals/{goal_id}")

@goals_bp.route('/close-goal/<int:goal_id>')
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

@goals_bp.route('/restore-goal/<int:goal_id>')
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

@goals_bp.route('/delete-goal/<int:goal_id>')
def delete_goal(goal_id):
    if 'user_id' not in session:
        return redirect('/login')
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM savings_goals WHERE goal_id=%s AND user_id=%s", (goal_id, session['user_id']))
    flash("Goal deleted successfully!", "success")
    return redirect('/goals')

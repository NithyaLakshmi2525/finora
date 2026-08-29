from flask import Blueprint, render_template, request, redirect, session, flash, Response, send_file, jsonify
from datetime import date
import io
from db import get_db
from services.insights_service import build_smart_insights
from services.ledger_service import fetch_filtered_transactions, csv_escape, get_categories
from services.notification_service import (
    mark_notification_read as svc_mark_read,
    mark_all_notifications_read as svc_mark_all_read,
    delete_notification as svc_delete_notif,
    clear_all_notifications as svc_clear_all
)

reports_bp = Blueprint('reports', __name__)

import calendar
from datetime import date, timedelta

@reports_bp.route('/monthly-report')
def monthly_report():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    active_tab = request.args.get('tab', 'overview')
    month_param = request.args.get('month')
    today = date.today()
    if month_param:
        try:
            year, m = map(int, month_param.split('-'))
            target_date = date(year, m, 1)
        except ValueError:
            target_date = date(today.year, today.month, 1)
    else:
        target_date = date(today.year, today.month, 1)

    month_str = target_date.strftime('%Y-%m')
    
    # Calculate prior month str
    first_of_target = date(target_date.year, target_date.month, 1)
    last_month_date = first_of_target - timedelta(days=1)
    last_month_str = last_month_date.strftime('%Y-%m')
    days_in_month = calendar.monthrange(target_date.year, target_date.month)[1]

    with get_db() as (conn, cursor):
        # Current month income
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM income WHERE user_id=%s AND DATE_FORMAT(income_date, '%%Y-%%m') = %s",
            (user_id, month_str)
        )
        total_income = float(cursor.fetchone()[0])

        # Current month expenses
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=%s AND DATE_FORMAT(expense_date, '%%Y-%%m') = %s",
            (user_id, month_str)
        )
        spent_this_month = float(cursor.fetchone()[0])
        total_expenses = spent_this_month

        # Prior month expenses
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=%s AND DATE_FORMAT(expense_date, '%%Y-%%m') = %s",
            (user_id, last_month_str)
        )
        last_month = float(cursor.fetchone()[0])

        change_percentage = ((spent_this_month - last_month) / last_month * 100.0) if last_month > 0 else 0.0
        net_cash_flow = total_income - total_expenses
        savings_rate = ((total_income - total_expenses) / total_income * 100.0) if total_income > 0 else 0.0

        # All-time monthly history report_data
        cursor.execute(
            "SELECT DATE_FORMAT(expense_date, '%%Y-%%m') AS ym, SUM(amount) "
            "FROM expenses WHERE user_id=%s GROUP BY ym ORDER BY ym DESC LIMIT 12",
            (user_id,)
        )
        report_rows = cursor.fetchall()
        report_data = [(r[0], float(r[1])) for r in report_rows]

        # Top 5 largest expenses
        cursor.execute(
            "SELECT description, category, amount FROM expenses WHERE user_id=%s ORDER BY amount DESC LIMIT 5",
            (user_id,)
        )
        largest_expenses = cursor.fetchall()

        # Category breakdown for current month (or fallback to all-time if empty)
        cursor.execute(
            "SELECT category, SUM(amount) FROM expenses "
            "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%%Y-%%m') = %s "
            "GROUP BY category ORDER BY SUM(amount) DESC",
            (user_id, month_str)
        )
        cat_rows = cursor.fetchall()
        if not cat_rows:
            cursor.execute(
                "SELECT category, SUM(amount) FROM expenses WHERE user_id=%s GROUP BY category ORDER BY SUM(amount) DESC",
                (user_id,)
            )
            cat_rows = cursor.fetchall()

        category_breakdown = [(r[0], float(r[1])) for r in cat_rows]
        cat_amounts = [float(r[1]) for r in cat_rows] if cat_rows else [0.0]
        categories = [r[0] for r in cat_rows] if cat_rows else ['No Expenses']
        top_month_category = category_breakdown[0][0] if category_breakdown else None

        # Forecast calculations
        current_day = today.day if (today.year == target_date.year and today.month == target_date.month) else days_in_month
        avg_daily = (spent_this_month / current_day) if current_day > 0 else 0.0
        days_remaining = max(0, days_in_month - current_day)
        forecast = spent_this_month + (avg_daily * days_remaining)
        projected_savings = total_income - forecast

        # Reserved for goals & available cash
        cursor.execute("SELECT COALESCE(SUM(current_amount), 0), COUNT(*) FROM savings_goals WHERE user_id=%s", (user_id,))
        g_row = cursor.fetchone()
        goal_allocation = float(g_row[0]) if g_row else 0.0
        goal_count = g_row[1] if g_row else 0

        cursor.execute("SELECT COALESCE(SUM(balance), 0) FROM accounts WHERE user_id=%s AND is_active=1", (user_id,))
        acc_total = float(cursor.fetchone()[0] or 0.0)
        available_cash = acc_total - goal_allocation

        # Trend dates & amounts for daily chart / heatmap
        cursor.execute(
            "SELECT DATE_FORMAT(expense_date, '%%Y-%%m-%%d') AS ed, SUM(amount) "
            "FROM expenses WHERE user_id=%s GROUP BY ed ORDER BY ed ASC",
            (user_id,)
        )
        trend_rows = cursor.fetchall()
        trend_dates = [r[0] for r in trend_rows] if trend_rows else [today.isoformat()]
        trend_amounts = [float(r[1]) for r in trend_rows] if trend_rows else [0.0]

        insights = build_smart_insights(cursor, user_id)

    return render_template(
        'reports/monthly_report.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        active_tab=active_tab,
        selected_month=month_str,
        month_label=target_date.strftime('%B %Y'),
        total_income=total_income,
        this_month=total_income,
        total_expenses=total_expenses,
        spent_this_month=spent_this_month,
        last_month=last_month,
        change_percentage=change_percentage,
        net_cash_flow=net_cash_flow,
        savings_rate=savings_rate,
        report_data=report_data,
        largest_expenses=largest_expenses,
        category_breakdown=category_breakdown,
        cat_amounts=cat_amounts,
        categories=categories,
        top_month_category=top_month_category,
        forecast=forecast,
        avg_daily=avg_daily,
        days_remaining=days_remaining,
        today_day=current_day,
        projected_savings=projected_savings,
        goal_allocation=goal_allocation,
        goal_count=goal_count,
        available_cash=available_cash,
        trend_dates=trend_dates,
        trend_amounts=trend_amounts,
        insights=insights,
        active_page='reports'
    )

@reports_bp.route('/export')
def export_csv():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    start_date = request.args.get('start') or request.args.get('start_date')
    end_date = request.args.get('end') or request.args.get('end_date')
    category = request.args.get('category') or request.args.get('current_category')
    search = request.args.get('search') or request.args.get('q') or request.args.get('search_query')
    show_income_raw = request.args.get('show_income') or request.args.get('income') or request.args.get('type')
    show_income = str(show_income_raw).lower() in ('true', '1', 'yes', 'income', 'all') if show_income_raw else False
    sort_order = request.args.get('sort', 'desc').lower()

    with get_db() as (conn, cursor):
        rows = fetch_filtered_transactions(
            cursor, user_id, start_date=start_date, end_date=end_date, category=category,
            search=search, sort_order=sort_order, show_income=show_income
        )

    csv_data = "ID,Date,Category,Description,Amount,Type\n" if show_income else "ID,Date,Category,Description,Amount\n"
    for r in rows:
        tx_type_title = "Income" if r['type'] == 'income' else "Expense"
        amt_str = f"{r['amount']:.2f}"
        if show_income:
            csv_data += f"{r['id']},{r['date']},{csv_escape(r['category'])},{csv_escape(r['description'])},{amt_str},{tx_type_title}\n"
        else:
            csv_data += f"{r['id']},{r['date']},{csv_escape(r['category'])},{csv_escape(r['description'])},{amt_str}\n"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=finora_expenses.csv"}
    )

@reports_bp.route('/exports')
def exports_hub():
    if 'user_id' not in session:
        return redirect('/login')
    with get_db() as (conn, cursor):
        categories = get_categories(cursor)

    return render_template(
        'reports/monthly_report.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        categories=categories,
        is_exports_hub=True,
        active_page='reports'
    )

@reports_bp.route('/export-pdf')
def export_pdf():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    with get_db() as (conn, cursor):
        cursor.execute("SELECT expense_date, category, description, amount FROM expenses WHERE user_id=%s ORDER BY expense_date DESC", (user_id,))
        expenses = cursor.fetchall()

    try:
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer)
        styles = getSampleStyleSheet()
        story = [Paragraph("Finora Expense Report", styles['Title']), Spacer(1, 12)]

        for exp in expenses:
            story.append(Paragraph(f"{exp[0]} | {exp[1]} | {exp[2]} | ₹{float(exp[3]):,.2f}", styles['Normal']))

        doc.build(story)
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name="finora_report.pdf", mimetype="application/pdf")
    except Exception as e:
        flash(f"PDF export failed: {e}", "error")
        return redirect('/monthly-report')

# ----------------- NOTIFICATIONS ROUTES -----------------

@reports_bp.route('/notifications')
def get_notifications():
    if 'user_id' not in session:
        return jsonify({'unread_count': 0, 'notifications': [], 'items': []})
    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM notifications WHERE user_id=%s AND is_read=0", (session['user_id'],))
        unread_count = cursor.fetchone()[0]

        cursor.execute(
            "SELECT notification_id, icon, title, message, link, is_read, DATE_FORMAT(created_at, '%d %b %h:%i %p') "
            "FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 20",
            (session['user_id'],)
        )
        rows = cursor.fetchall()

    items = [{
        'id': r[0], 'icon': r[1] or '🔔', 'title': r[2], 'message': r[3],
        'link': r[4], 'is_read': bool(r[5]), 'time': r[6], 'created_at': r[6]
    } for r in rows]

    return jsonify({
        'unread_count': unread_count,
        'notifications': items,
        'items': items
    })

@reports_bp.route('/notifications/read/<int:notification_id>', methods=['POST'])
def mark_notification_read(notification_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as (conn, cursor):
        svc_mark_read(cursor, session['user_id'], notification_id)
    return jsonify({'success': True})

@reports_bp.route('/notifications/read-all', methods=['POST'])
def mark_all_notifications_read():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as (conn, cursor):
        svc_mark_all_read(cursor, session['user_id'])
    return jsonify({'success': True})

@reports_bp.route('/notifications/<int:notification_id>/delete', methods=['POST'])
def delete_notification(notification_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as (conn, cursor):
        svc_delete_notif(cursor, session['user_id'], notification_id)
    return jsonify({'success': True})

@reports_bp.route('/notifications/clear', methods=['POST'])
def clear_notifications():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as (conn, cursor):
        svc_clear_all(cursor, session['user_id'])
    return jsonify({'success': True})

from flask import Blueprint, render_template, request, redirect, session, flash, Response, send_file, jsonify
from datetime import date
import io
from db import get_db
from services.insights_service import build_smart_insights
from services.ledger_service import fetch_filtered_transactions, csv_escape, get_categories

reports_bp = Blueprint('reports', __name__)

@reports_bp.route('/monthly-report')
def monthly_report():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
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

    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM income "
            "WHERE user_id=%s AND DATE_FORMAT(income_date, '%Y-%m') = %s",
            (user_id, month_str)
        )
        total_income = float(cursor.fetchone()[0])

        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%Y-%m') = %s",
            (user_id, month_str)
        )
        total_expenses = float(cursor.fetchone()[0])

        net_cash_flow = total_income - total_expenses
        savings_rate = ((total_income - total_expenses) / total_income * 100.0) if total_income > 0 else 0.0

        cursor.execute(
            "SELECT category, SUM(amount) FROM expenses "
            "WHERE user_id=%s AND DATE_FORMAT(expense_date, '%Y-%m') = %s "
            "GROUP BY category ORDER BY SUM(amount) DESC",
            (user_id, month_str)
        )
        cat_data = cursor.fetchall()
        cat_breakdown = [{'category': r[0], 'total': float(r[1]), 'pct': (float(r[1]) / total_expenses * 100.0) if total_expenses > 0 else 0} for r in cat_data]

        insights = build_smart_insights(cursor, user_id)

    return render_template(
        'reports/monthly_report.html',
        username=session['username'],
        display_name=session.get('display_name', session['username']),
        selected_month=month_str,
        month_label=target_date.strftime('%B %Y'),
        total_income=total_income,
        total_expenses=total_expenses,
        net_cash_flow=net_cash_flow,
        savings_rate=savings_rate,
        cat_breakdown=cat_breakdown,
        insights=insights,
        active_page='reports'
    )

@reports_bp.route('/export')
def export_csv():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    category = request.args.get('category')

    with get_db() as (conn, cursor):
        rows = fetch_filtered_transactions(cursor, user_id, start_date, end_date, category)

    csv_data = "ID,Date,Category,Description,Amount\n"
    for r in rows:
        csv_data += f"{r[0]},{r[1]},{csv_escape(r[2])},{csv_escape(r[3])},{r[4]}\n"

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
        return jsonify([])
    with get_db() as (conn, cursor):
        cursor.execute(
            "SELECT notification_id, icon, title, message, link, is_read, DATE_FORMAT(created_at, '%d %b %h:%i %p') "
            "FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 20",
            (session['user_id'],)
        )
        rows = cursor.fetchall()

    return jsonify([{
        'id': r[0], 'icon': r[1] or '🔔', 'title': r[2], 'message': r[3],
        'link': r[4], 'is_read': bool(r[5]), 'time': r[6]
    } for r in rows])

@reports_bp.route('/notifications/read/<int:notification_id>', methods=['POST'])
def mark_notification_read(notification_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as (conn, cursor):
        cursor.execute(
            "UPDATE notifications SET is_read=1 WHERE notification_id=%s AND user_id=%s",
            (notification_id, session['user_id'])
        )
    return jsonify({'success': True})

@reports_bp.route('/notifications/read-all', methods=['POST'])
def mark_all_notifications_read():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as (conn, cursor):
        cursor.execute("UPDATE notifications SET is_read=1 WHERE user_id=%s", (session['user_id'],))
    return jsonify({'success': True})

@reports_bp.route('/notifications/<int:notification_id>/delete', methods=['POST'])
def delete_notification(notification_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM notifications WHERE notification_id=%s AND user_id=%s", (notification_id, session['user_id']))
    return jsonify({'success': True})

@reports_bp.route('/notifications/clear', methods=['POST'])
def clear_notifications():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM notifications WHERE user_id=%s", (session['user_id'],))
    return jsonify({'success': True})

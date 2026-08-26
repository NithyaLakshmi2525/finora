from datetime import date, timedelta
from flask import session

def get_notification_prefs(cursor, user_id):
    cursor.execute(
        "SELECT budget_alerts, recurring_reminders, goal_milestones "
        "FROM notification_preferences WHERE user_id=%s",
        (user_id,)
    )
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "INSERT INTO notification_preferences (user_id) VALUES (%s)",
            (user_id,)
        )
        return {'budget_alerts': 1, 'recurring_reminders': 1, 'goal_milestones': 1}
    return {
        'budget_alerts': bool(row[0]),
        'recurring_reminders': bool(row[1]),
        'goal_milestones': bool(row[2]),
    }

def create_notification(cursor, user_id, icon, title, message, link, dedup_key=None):
    if dedup_key:
        cursor.execute(
            "INSERT IGNORE INTO notifications (user_id, icon, title, message, link, dedup_key) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, icon, title, message, link, dedup_key)
        )
    else:
        cursor.execute(
            "INSERT INTO notifications (user_id, icon, title, message, link) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user_id, icon, title, message, link)
        )

def generate_opportunistic_notifications(cursor, user_id):
    prefs = get_notification_prefs(cursor, user_id)
    today = date.today()
    first_of_month = date(today.year, today.month, 1)

    if prefs['budget_alerts']:
        cursor.execute("SELECT monthly_limit FROM budgets WHERE user_id=%s LIMIT 1", (user_id,))
        b_row = cursor.fetchone()
        if b_row and b_row[0] and float(b_row[0]) > 0:
            budget = float(b_row[0])
            cursor.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM expenses "
                "WHERE user_id=%s AND expense_date >= %s",
                (user_id, first_of_month)
            )
            spent = float(cursor.fetchone()[0])
            pct = (spent / budget) * 100
            dedup_prefix = f"budget-{today.year}-{today.month}"
            if pct >= 100:
                create_notification(
                    cursor, user_id, icon='🚨',
                    title="Monthly budget exceeded",
                    message=f"You've spent ₹{spent:,.0f} of your ₹{budget:,.0f} budget ({pct:.0f}%).",
                    link='/monthly-report',
                    dedup_key=f"{dedup_prefix}-100pct"
                )
            elif pct >= 80:
                create_notification(
                    cursor, user_id, icon='⚠️',
                    title="80% of budget reached",
                    message=f"You've spent ₹{spent:,.0f} of your ₹{budget:,.0f} budget.",
                    link='/monthly-report',
                    dedup_key=f"{dedup_prefix}-80pct"
                )

    if prefs['recurring_reminders']:
        horizon = today + timedelta(days=3)
        cursor.execute(
            "SELECT recurring_id, title, amount, next_charge_date "
            "FROM recurring_expenses "
            "WHERE user_id=%s AND status='active' AND next_charge_date IS NOT NULL "
            "AND next_charge_date >= %s AND next_charge_date <= %s",
            (user_id, today, horizon)
        )
        due_soon = cursor.fetchall()
        for rec_id, r_title, r_amount, r_date in due_soon:
            if r_date == today:
                due_msg = "is due today"
            elif r_date == today + timedelta(days=1):
                due_msg = "is due tomorrow"
            else:
                due_msg = f"is due in {(r_date - today).days} days"
            create_notification(
                cursor, user_id, icon='⏰',
                title=f"Upcoming charge: {r_title}",
                message=f"₹{float(r_amount):,.0f} {due_msg}.",
                link='/recurring',
                dedup_key=f"recurring-due-{rec_id}-{r_date.isoformat()}"
            )

    if prefs['goal_milestones']:
        cursor.execute(
            "SELECT goal_id, goal_name, target_amount, current_amount "
            "FROM savings_goals WHERE user_id=%s AND closed_at IS NULL",
            (user_id,)
        )
        goals = cursor.fetchall()
        for g_id, g_name, g_target, g_current in goals:
            if not g_target or float(g_target) <= 0:
                continue
            pct = (float(g_current) / float(g_target)) * 100
            if pct >= 100:
                create_notification(
                    cursor, user_id, icon='🎉',
                    title=f"Goal reached: {g_name}!",
                    message=f"You've hit 100% of your target (₹{float(g_current):,.0f} saved).",
                    link=f"/goals/{g_id}",
                    dedup_key=f"goal-{g_id}-100pct"
                )
            elif pct >= 50:
                create_notification(
                    cursor, user_id, icon='🎯',
                    title=f"Halfway there: {g_name}",
                    message=f"You've reached 50% of your target (₹{float(g_current):,.0f} saved).",
                    link=f"/goals/{g_id}",
                    dedup_key=f"goal-{g_id}-50pct"
                )

def check_opportunistic_notifications(cursor, user_id):
    """Run opportunistic notification triggers safely."""
    try:
        generate_opportunistic_notifications(cursor, user_id)
    except Exception as e:
        print(f"[notifications] background check skipped (user_id={user_id}): {e}")

def mark_notification_read(cursor, user_id, notification_id):
    """Marks a single notification as read for the user."""
    cursor.execute(
        "UPDATE notifications SET is_read=1 WHERE notification_id=%s AND user_id=%s",
        (notification_id, user_id)
    )

def mark_all_notifications_read(cursor, user_id):
    """Marks all notifications as read for the user without deleting them."""
    cursor.execute("UPDATE notifications SET is_read=1 WHERE user_id=%s", (user_id,))

def delete_notification(cursor, user_id, notification_id):
    """Deletes a single notification for the user."""
    cursor.execute("DELETE FROM notifications WHERE notification_id=%s AND user_id=%s", (notification_id, user_id))

def clear_all_notifications(cursor, user_id):
    """Deletes all notifications for the user."""
    cursor.execute("DELETE FROM notifications WHERE user_id=%s", (user_id,))


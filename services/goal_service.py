from datetime import date

def compute_goal_display(target_amount, current_amount, target_date, closed_at):
    """Computes target percentages and display status for savings goals."""
    target = float(target_amount or 0)
    current = float(current_amount or 0)
    pct = min(100.0, (current / target * 100.0)) if target > 0 else 0.0

    t_date = None
    if isinstance(target_date, date):
        t_date = target_date
    elif isinstance(target_date, str) and target_date.strip():
        try:
            t_date = date.fromisoformat(target_date.strip())
        except ValueError:
            t_date = None

    if closed_at:
        status_key = 'closed'
        status_label = 'Closed'
    elif target > 0 and current >= target:
        status_key = 'reached'
        status_label = 'Target Reached'
    elif t_date and t_date < date.today():
        status_key = 'overdue'
        status_label = 'Overdue'
    else:
        status_key = 'in_progress'
        status_label = 'In Progress'

    remaining = max(0.0, target - current)
    monthly_needed = None
    if t_date and t_date > date.today() and remaining > 0:
        months_left = max(1, (t_date.year - date.today().year) * 12 + (t_date.month - date.today().month))
        monthly_needed = remaining / months_left

    return {
        'pct': pct,
        'pct_rounded': round(pct, 1),
        'remaining': remaining,
        'monthly_needed': monthly_needed,
        'status_key': status_key,
        'status_label': status_label,
        'is_reached': current >= target if target > 0 else False,
    }

def motivation_for_percent(pct):
    if pct >= 100:
        return "Target reached! Excellent job hitting your savings goal."
    if pct >= 75:
        return "Final stretch! You're almost at the finish line."
    if pct >= 50:
        return "Halfway there! Keep up the consistent progress."
    if pct >= 25:
        return "Great momentum! Off to a solid start."
    return "Every step counts. Keep contributing to hit your goal!"

def build_goal_summary(cursor, user_id):
    """Computes total active goals, reserved amounts, and total targets."""
    cursor.execute(
        "SELECT target_amount, current_amount FROM savings_goals "
        "WHERE user_id=%s AND closed_at IS NULL",
        (user_id,)
    )
    rows = cursor.fetchall()
    total_goals = len(rows)
    total_target = sum(float(r[0] or 0) for r in rows)
    total_reserved = sum(float(r[1] or 0) for r in rows)
    overall_pct = (total_reserved / total_target * 100.0) if total_target > 0 else 0.0

    return {
        'total_goals': total_goals,
        'active_goals_count': total_goals,
        'active_count': total_goals,
        'total_target': total_target,
        'total_reserved': total_reserved,
        'overall_pct': min(100.0, overall_pct),
        'overall_pct_rounded': round(min(100.0, overall_pct), 1),
        'progress_pct': round(min(100.0, overall_pct), 1),
    }

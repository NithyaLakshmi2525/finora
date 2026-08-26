def balance_expense_description(peer_name, reason=None):
    if reason and reason.strip():
        return f"Settlement with {peer_name} — {reason.strip()}"
    return f"Settlement with {peer_name}"

def build_settlements_summary(cursor, user_id):
    """Calculates active peer settlement balances (owed to you vs owed by you)."""
    cursor.execute(
        "SELECT amount FROM settlements WHERE user_id=%s AND status='active'",
        (user_id,)
    )
    rows = cursor.fetchall()
    total_owed_to_you = 0.0
    total_you_owe = 0.0
    for (amt,) in rows:
        try:
            val = float(amt) if amt is not None else 0.0
        except (ValueError, TypeError):
            val = 0.0

        if val >= 0:
            total_owed_to_you += val
        else:
            total_you_owe += abs(val)

    net_val = total_owed_to_you - total_you_owe
    return {
        'total_owed_to_you': total_owed_to_you,
        'total_you_owe': total_you_owe,
        'net_balance': net_val,
        'net_position': net_val,
        'active_count': len(rows)
    }

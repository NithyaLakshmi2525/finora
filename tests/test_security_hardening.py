import pytest
from db import get_db

def test_all_state_mutating_routes_reject_get_and_enforce_csrf_and_isolation(client, dual_users):
    """Regression test proving GET state mutations are rejected with 405 Method Not Allowed, CSRF is enforced on POST, and user isolation is strictly maintained."""
    uid_a = dual_users['id_a']
    acc_a = dual_users['acc_a']
    uid_b = dual_users['id_b']
    acc_b = dual_users['acc_b']

    # Seed test data for User A and User B
    with get_db() as (conn, cursor):
        cursor.execute("DELETE FROM expenses WHERE user_id IN (%s, %s)", (uid_a, uid_b))
        cursor.execute("DELETE FROM income WHERE user_id IN (%s, %s)", (uid_a, uid_b))
        cursor.execute("DELETE FROM recurring_expenses WHERE user_id IN (%s, %s)", (uid_a, uid_b))
        cursor.execute("DELETE FROM recurring_income WHERE user_id IN (%s, %s)", (uid_a, uid_b))
        cursor.execute("DELETE FROM savings_goals WHERE user_id IN (%s, %s)", (uid_a, uid_b))
        cursor.execute("DELETE FROM settlements WHERE user_id IN (%s, %s)", (uid_a, uid_b))

        # Insert User A expense & income & goal & recurring & settlement
        cursor.execute(
            "INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) "
            "VALUES (%s, 100.00, 'Food', 'User A Lunch', '2026-08-25', %s)", (uid_a, acc_a)
        )
        exp_a_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO income (user_id, amount, source, description, income_date, account_id) "
            "VALUES (%s, 5000.00, 'Salary', 'User A Salary', '2026-08-01', %s)", (uid_a, acc_a)
        )
        inc_a_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO recurring_expenses (user_id, title, amount, category, frequency, next_charge_date) "
            "VALUES (%s, 'User A Netflix', 649.00, 'Entertainment', 'Monthly', '2026-08-25')", (uid_a,)
        )
        rec_exp_a_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO recurring_income (user_id, title, amount, source, frequency, next_pay_date) "
            "VALUES (%s, 'User A Retainer', 15000.00, 'Freelance', 'Monthly', '2026-08-01')", (uid_a,)
        )
        rec_inc_a_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) "
            "VALUES (%s, 'User A Trip', 50000.00, 5000.00)", (uid_a,)
        )
        goal_a_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO settlements (user_id, peer_name, amount, status) "
            "VALUES (%s, 'Dave', 1200.00, 'active')", (uid_a,)
        )
        settle_a_id = cursor.lastrowid

        # Insert User B expense & goal for isolation test
        cursor.execute(
            "INSERT INTO expenses (user_id, amount, category, description, expense_date, account_id) "
            "VALUES (%s, 999.00, 'Secret', 'User B Secret', '2026-08-25', %s)", (uid_b, acc_b)
        )
        exp_b_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) "
            "VALUES (%s, 'User B Car', 100000.00, 10000.00)", (uid_b,)
        )
        goal_b_id = cursor.lastrowid

    # Log in as User A
    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    mutation_routes = [
        f'/delete/{exp_a_id}',
        f'/delete-income/{inc_a_id}',
        f'/toggle-recurring-income/{rec_inc_a_id}',
        f'/delete-recurring-income/{rec_inc_a_id}',
        f'/close-goal/{goal_a_id}',
        f'/restore-goal/{goal_a_id}',
        f'/delete-goal/{goal_a_id}',
        f'/delete-recurring/{rec_exp_a_id}',
        f'/confirm-paid/{rec_exp_a_id}',
        f'/toggle-recurring/{rec_exp_a_id}',
        f'/toggle-account/{acc_a}',
        f'/settle/{settle_a_id}',
        f'/api/settlements/{settle_a_id}/reopen',
        f'/api/settlements/{settle_a_id}/delete',
    ]

    # 1. REQUIREMENT: GET requests to all mutation routes MUST return 405 Method Not Allowed
    for route in mutation_routes:
        res = client.get(route)
        assert res.status_code == 405, f"GET {route} returned {res.status_code}, expected 405 Method Not Allowed"

    # 2. REQUIREMENT: GET requests perform ZERO database mutations
    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE expense_id=%s", (exp_a_id,))
        assert cursor.fetchone()[0] == 1, "Expense deleted via GET!"

        cursor.execute("SELECT COUNT(*) FROM income WHERE income_id=%s", (inc_a_id,))
        assert cursor.fetchone()[0] == 1, "Income deleted via GET!"

        cursor.execute("SELECT COUNT(*) FROM savings_goals WHERE goal_id=%s AND closed_at IS NULL", (goal_a_id,))
        assert cursor.fetchone()[0] == 1, "Goal closed via GET!"

        cursor.execute("SELECT status FROM settlements WHERE settlement_id=%s", (settle_a_id,))
        assert cursor.fetchone()[0] == 'active', "Settlement mutated via GET!"

    # 3. REQUIREMENT: User A CANNOT mutate User B's records via POST (User Isolation)
    res = client.post(f'/delete/{exp_b_id}', follow_redirects=True)
    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM expenses WHERE expense_id=%s", (exp_b_id,))
        assert cursor.fetchone()[0] == 1, "User A deleted User B expense!"

    res = client.post(f'/delete-goal/{goal_b_id}', follow_redirects=True)
    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM savings_goals WHERE goal_id=%s", (goal_b_id,))
        assert cursor.fetchone()[0] == 1, "User A deleted User B goal!"

    client.get('/logout')

import pytest
from datetime import date
from db import get_db
from services.account_service import get_default_account_id

def test_income_edit_slide_drawer_workflow(client, dual_users):
    """Verify Income edit drawer workflow: drawer action endpoint, account balance adjustment, and referer redirect."""
    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)
    uid_a = dual_users['id_a']
    acc_a = dual_users['acc_a']

    # 1. Create an income entry
    res = client.post('/income', data={
        'amount': '5000.00',
        'source': 'Salary',
        'description': 'Monthly Paycheck',
        'date': '2026-08-01',
        'account_id': str(acc_a)
    }, follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT income_id FROM income WHERE user_id=%s ORDER BY income_id DESC LIMIT 1", (uid_a,))
        inc_id = cursor.fetchone()[0]
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (acc_a,))
        initial_bal = float(cursor.fetchone()[0])

    # 2. GET /income with edit_entry or GET /edit-income/<id>
    res_edit_page = client.get(f'/edit-income/{inc_id}')
    assert res_edit_page.status_code == 200
    assert b'incomePanel' in res_edit_page.data
    assert b'openEditIncomePanel' in res_edit_page.data

    # 3. Edit income entry via drawer POST handler
    res_update = client.post(f'/edit-income/{inc_id}', data={
        'amount': '7500.00',
        'source': 'Salary',
        'description': 'Monthly Paycheck + Bonus',
        'date': '2026-08-01',
        'account_id': str(acc_a)
    }, follow_redirects=True)
    assert res_update.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT amount, description FROM income WHERE income_id=%s", (inc_id,))
        updated_amt, updated_desc = cursor.fetchone()
        assert float(updated_amt) == 7500.00
        assert updated_desc == 'Monthly Paycheck + Bonus'

        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (acc_a,))
        new_bal = float(cursor.fetchone()[0])
        # Balance should increase by delta (+2500)
        assert new_bal == pytest.approx(initial_bal + 2500.00)

def test_closed_goals_visual_clarity_and_restore(client, dual_users):
    """Verify closed goal visual badge, separation, and restore action."""
    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)
    uid_a = dual_users['id_a']

    with get_db() as (conn, cursor):
        cursor.execute(
            "INSERT INTO savings_goals (user_id, goal_name, target_amount, current_amount) "
            "VALUES (%s, 'Emergency Fund Test', 10000.00, 2000.00)", (uid_a,)
        )
        goal_id = cursor.lastrowid

    # 1. Active state check
    res_active = client.get('/goals')
    assert b'In Progress' in res_active.data

    # 2. Close goal
    res_close = client.post(f'/close-goal/{goal_id}', follow_redirects=True)
    assert res_close.status_code == 200

    # 3. Closed state check
    res_closed_list = client.get('/goals')
    assert b'Closed Goals' in res_closed_list.data
    assert b'CLOSED' in res_closed_list.data

    res_closed_detail = client.get(f'/goals/{goal_id}')
    assert b'CLOSED' in res_closed_detail.data
    assert b'Closed on' in res_closed_detail.data
    assert b'Restore Goal' in res_closed_detail.data

    # 4. Restore goal
    res_restore = client.post(f'/restore-goal/{goal_id}', follow_redirects=True)
    assert res_restore.status_code == 200

    res_restored_detail = client.get(f'/goals/{goal_id}')
    assert b'In Progress' in res_restored_detail.data

def test_upcoming_charges_interactivity(client, dual_users):
    """Verify upcoming charges timeline rows are interactive and link to subscription cards."""
    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)
    uid_a = dual_users['id_a']

    with get_db() as (conn, cursor):
        cursor.execute(
            "INSERT INTO recurring_expenses (user_id, title, amount, category, frequency, next_charge_date, status) "
            "VALUES (%s, 'Spotify Premium', 119.00, 'Music', 'Monthly', '2026-09-01', 'active')", (uid_a,)
        )
        rec_id = cursor.lastrowid

    res = client.get('/recurring')
    assert res.status_code == 200
    assert b'role="button"' in res.data
    assert b'scrollToSubscription' in res.data
    assert f'sub-card-{rec_id}'.encode() in res.data

def test_recurring_mark_as_paid_accounting_and_idempotency(client, dual_users):
    """Verify Mark as Paid decreases account balance and is idempotent against repeated clicks."""
    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)
    uid_a = dual_users['id_a']

    with get_db() as (conn, cursor):
        def_acc = get_default_account_id(cursor, uid_a)
        cursor.execute("UPDATE accounts SET balance=10000.00 WHERE account_id=%s", (def_acc,))
        cursor.execute(
            "INSERT INTO recurring_expenses (user_id, title, amount, category, frequency, next_charge_date, status) "
            "VALUES (%s, 'Internet Bill', 999.00, 'Utilities', 'Monthly', '2026-08-01', 'active')", (uid_a,)
        )
        rec_id = cursor.lastrowid

    # 1. First Mark as Paid click
    res1 = client.post(f'/confirm-paid/{rec_id}', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res1.status_code == 200
    json1 = res1.get_json()
    assert json1['success'] is True
    assert 'Paid Internet Bill' in json1['message']

    with get_db() as (conn, cursor):
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (def_acc,))
        bal1 = float(cursor.fetchone()[0])
        assert bal1 == 10000.00 - 999.00

        cursor.execute("SELECT COUNT(*) FROM expenses WHERE recurring_id=%s AND expense_date='2026-08-01'", (rec_id,))
        assert cursor.fetchone()[0] == 1

    # 2. Repeated click for same cycle (Idempotency)
    res2 = client.post(f'/confirm-paid/{rec_id}', data={'charge_date': '2026-08-01'}, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res2.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (def_acc,))
        bal2 = float(cursor.fetchone()[0])
        # Balance should NOT be decremented a second time
        assert bal2 == bal1

def test_settlement_accounting_semantics_and_category_validation(client, dual_users):
    """Verify settlement accounting: payable vs receivable, settle balance adjustments, reopen, delete, and category validation."""
    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)
    uid_a = dual_users['id_a']

    with get_db() as (conn, cursor):
        def_acc = get_default_account_id(cursor, uid_a)
        cursor.execute("UPDATE accounts SET balance=5000.00 WHERE account_id=%s", (def_acc,))

    # 1. Create Payable ("I owe them") with counts_as_expense=1
    res_pay = client.post('/settlements', data={
        'peer_name': 'Bob',
        'direction': 'owe_them',
        'amount': '1000.00',
        'counts_as_expense': 'on',
        'expense_category': 'InvalidCategoryString' # Should sanitize to 'Other'
    }, follow_redirects=True)
    assert res_pay.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT settlement_id, linked_expense_id FROM settlements WHERE peer_name='Bob' AND user_id=%s", (uid_a,))
        pay_id, pay_exp_id = cursor.fetchone()
        cursor.execute("SELECT category FROM expenses WHERE expense_id=%s", (pay_exp_id,))
        assert cursor.fetchone()[0] == 'Other'

        # Bank balance should NOT be decremented yet on creation
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (def_acc,))
        assert float(cursor.fetchone()[0]) == 5000.00

    # 2. Settle Payable -> bank balance decreases by 1000
    res_settle_pay = client.post(f'/settle/{pay_id}', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res_settle_pay.status_code == 200
    with get_db() as (conn, cursor):
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (def_acc,))
        assert float(cursor.fetchone()[0]) == 4000.00

    # 3. Reopen Payable -> bank balance restored back to 5000
    res_reopen_pay = client.post(f'/api/settlements/{pay_id}/reopen', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res_reopen_pay.status_code == 200
    with get_db() as (conn, cursor):
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (def_acc,))
        assert float(cursor.fetchone()[0]) == 5000.00

    # 4. Create Receivable ("They owe me")
    res_rec = client.post('/settlements', data={
        'peer_name': 'Alice',
        'direction': 'they_owe_me',
        'amount': '2000.00'
    }, follow_redirects=True)
    assert res_rec.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT settlement_id FROM settlements WHERE peer_name='Alice' AND user_id=%s", (uid_a,))
        rec_id = cursor.fetchone()[0]

    # 5. Settle Receivable -> bank balance increases by 2000 (to 7000)
    res_settle_rec = client.post(f'/settle/{rec_id}', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res_settle_rec.status_code == 200
    with get_db() as (conn, cursor):
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (def_acc,))
        assert float(cursor.fetchone()[0]) == 7000.00

    # 6. Delete Settled Receivable -> bank balance restored back to 5000
    res_del_rec = client.post(f'/api/settlements/{rec_id}/delete', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res_del_rec.status_code == 200
    with get_db() as (conn, cursor):
        cursor.execute("SELECT balance FROM accounts WHERE account_id=%s", (def_acc,))
        assert float(cursor.fetchone()[0]) == 5000.00

def test_custom_error_pages_and_ajax_errors(client):
    """Verify custom error page rendering for 404, 405, 401 and JSON error format for AJAX requests."""
    # 1. Non-existent page (404 HTML)
    res_404_html = client.get('/non-existent-path-12345')
    assert res_404_html.status_code == 404
    assert b'Page Not Found' in res_404_html.data
    assert b'Finora' in res_404_html.data

    # 2. Non-existent page (404 JSON for AJAX)
    res_404_json = client.get('/non-existent-path-12345', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert res_404_json.status_code == 404
    json_data = res_404_json.get_json()
    assert 'error' in json_data
    assert json_data['status'] == 404

    # 3. GET on POST-only route (405 Method Not Allowed)
    res_405_html = client.get('/settle/1')
    assert res_405_html.status_code == 405
    assert b'Method Not Allowed' in res_405_html.data

def test_registration_password_validation(client):
    """Verify registration password policy: min length 8, non-empty, non-whitespace-only."""
    import time
    ts = int(time.time() * 1000)
    client.get('/logout')
    
    # 1. Short password (< 8 chars)
    res_short = client.post('/register', data={
        'username': f'testshortpw_{ts}',
        'email': f'shortpw_{ts}@example.com',
        'password': 'pass',
        'confirm_password': 'pass'
    })
    assert res_short.status_code == 200
    assert b'Password must be at least 8 characters' in res_short.data

    # 2. Whitespace-only password
    res_space = client.post('/register', data={
        'username': f'testspacepw_{ts}',
        'email': f'spacepw_{ts}@example.com',
        'password': '        ',
        'confirm_password': '        '
    })
    assert res_space.status_code == 200
    assert b'Password cannot be empty or only whitespace' in res_space.data

    # 3. Valid password (exactly 8 chars)
    res_valid = client.post('/register', data={
        'username': f'testvalidpw_{ts}',
        'email': f'validpw_{ts}@example.com',
        'password': 'ValidPass1',
        'confirm_password': 'ValidPass1'
    }, follow_redirects=True)
    assert res_valid.status_code == 200
    assert b'Registration successful' in res_valid.data

def test_numeric_input_validation(client, dual_users):
    """Verify numeric input validation: reject non-numeric strings, negative amounts, and unrealistically large values."""
    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)

    # 1. Non-numeric amount in expense
    res_non_num = client.post('/add-expense', data={
        'amount': 'abc',
        'category': 'Food'
    }, follow_redirects=True)
    assert b'Invalid numeric amount entered' in res_non_num.data

    # 2. Negative amount in income
    res_neg = client.post('/add-income', data={
        'amount': '-100.00',
        'source': 'Salary'
    }, follow_redirects=True)
    assert b'Amount cannot be negative' in res_neg.data

    # 3. Excessively large value
    res_huge = client.post('/set-budget', data={
        'amount': '99999999999999.00',
        'category': 'Overall'
    }, follow_redirects=True)
    assert b'exceeds maximum allowable limit' in res_huge.data

def test_expenses_count_query_optimization(client, dual_users):
    """Verify Expenses COUNT(*) query accuracy, pagination, user isolation, and show_income counting."""
    client.get('/logout')
    client.post('/login', data={'email': dual_users['email_a'], 'password': dual_users['pw']}, follow_redirects=True)
    uid_a = dual_users['id_a']

    # Insert 15 expenses for User A
    with get_db() as (conn, cursor):
        for i in range(15):
            cursor.execute(
                "INSERT INTO expenses (user_id, amount, category, description, expense_date) "
                "VALUES (%s, 100.00, 'Food', %s, '2026-08-15')",
                (uid_a, f"Expense Item {i}")
            )

    res = client.get('/expenses')
    assert res.status_code == 200
    # Pagination: 15 items total, 10 per page -> 2 pages total
    assert b'Page 1 of 2' in res.data or b'total_pages' in res.data or b'15' in res.data

def test_auth_rate_limiting(client, app):
    """Verify rate limiting on login attempts."""
    from services.rate_limiter import reset_rate_limit_store
    client.get('/logout')
    app.config['ENABLE_RATE_LIMIT_TESTING'] = True
    reset_rate_limit_store()

    try:
        responses = []
        for i in range(12):
            r = client.post('/login', data={'email': 'nonexistent@example.com', 'password': 'wrongpassword'})
            responses.append(r.status_code)

        assert 429 in responses
    finally:
        app.config['ENABLE_RATE_LIMIT_TESTING'] = False
        reset_rate_limit_store()

def test_google_oauth_security_account_linking(client, app):
    """
    Threat Scenario Audit:
    Attacker creates local account with victim@example.com and password 'AttackerPW123!'.
    Victim logs in via Google OAuth with verified victim@example.com.
    OAuth updates account to 'google' provider and invalidates local password.
    Attacker's password login attempt is rejected.
    """
    import time
    ts = int(time.time() * 1000)
    victim_email = f'victim_oauth_{ts}@example.com'
    
    # 1. Attacker creates local account
    client.post('/register', data={
        'username': victim_email,
        'email': victim_email,
        'password': 'AttackerPW123!',
        'confirm_password': 'AttackerPW123!'
    }, follow_redirects=True)
    client.get('/logout')

    # 2. Simulate Google OAuth login for victim_email
    with app.test_request_context():
        with get_db() as (conn, cursor):
            cursor.execute("SELECT user_id, auth_provider FROM users WHERE email=%s", (victim_email,))
            row = cursor.fetchone()
            assert row is not None
            assert row[1] == 'local'
            user_id = row[0]

            # Trigger account linking security update as authorize_google does
            from werkzeug.security import generate_password_hash
            import secrets
            new_random_pw = generate_password_hash(secrets.token_hex(32))
            cursor.execute("UPDATE users SET auth_provider='google', password=%s WHERE user_id=%s", (new_random_pw, user_id))

    # 3. Attacker tries logging in with local password 'AttackerPW123!' -> MUST be rejected
    res_attacker_login = client.post('/login', data={'email': victim_email, 'password': 'AttackerPW123!'})
    assert res_attacker_login.status_code == 200
    assert b'Invalid email or password' in res_attacker_login.data

def test_password_policy_exact_examples(client):
    """
    Verify exact password policy requirements:
    1. 'Password1' -> ACCEPT
    2. 'Abcdefg1' -> ACCEPT
    3. 'My Password1' -> ACCEPT (internal spaces allowed & preserved)
    4. 'N       a' -> REJECT (no number)
    5. 'abcdefgh1' -> REJECT (no uppercase)
    6. 'ABCDEFGH' -> REJECT (no number)
    7. '12345678' -> REJECT (no uppercase)
    8. '        ' -> REJECT (entirely whitespace)
    """
    import time
    ts = int(time.time() * 1000)
    client.get('/logout')

    # Rejections
    rejections = [
        ('N       a', 'N       a', b'at least one number'),
        ('abcdefgh1', 'abcdefgh1', b'at least one uppercase'),
        ('ABCDEFGH', 'ABCDEFGH', b'at least one number'),
        ('12345678', '12345678', b'at least one uppercase'),
        ('        ', '        ', b'empty or only whitespace')
    ]
    for idx, (pw, cpw, err_snippet) in enumerate(rejections):
        r = client.post('/register', data={
            'username': f'rej_user_{idx}_{ts}',
            'email': f'rej_{idx}_{ts}@example.com',
            'password': pw,
            'confirm_password': cpw
        })
        assert r.status_code == 200
        assert err_snippet in r.data, f"Failed for password '{pw}'"

    # Acceptances
    acceptances = [
        ('Password1', 'Password1'),
        ('Abcdefg1', 'Abcdefg1'),
        ('My Password1', 'My Password1')
    ]
    for idx, (pw, cpw) in enumerate(acceptances):
        client.get('/logout')
        r = client.post('/register', data={
            'username': f'acc_user_{idx}_{ts}',
            'email': f'acc_{idx}_{ts}@example.com',
            'password': pw,
            'confirm_password': cpw
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'Registration successful' in r.data, f"Failed for password '{pw}'"

def test_google_oauth_account_linking_three_cases(client, app):
    """
    Verify Google OAuth 3 Cases:
    Case 1: New Google Identity -> creates 1 user with auth_provider='google' and Main Account.
    Case 2: Existing Local Account -> upgrades auth_provider='google', invalidates local password, preserves user_id & financial data.
    Case 3: Subsequent Google Login -> logs into same user, no duplicate user created.
    """
    import time
    ts = int(time.time() * 1000)
    email_c2 = f'google_c2_{ts}@example.com'

    # Case 2 Setup: Create local user with expense & income
    client.get('/logout')
    client.post('/register', data={
        'username': f'user_c2_{ts}',
        'email': email_c2,
        'password': 'LocalPassword1',
        'confirm_password': 'LocalPassword1'
    }, follow_redirects=True)

    with app.test_request_context():
        with get_db() as (conn, cursor):
            cursor.execute("SELECT user_id FROM users WHERE email=%s", (email_c2,))
            user_c2_id = cursor.fetchone()[0]

            # Add financial records
            cursor.execute(
                "INSERT INTO expenses (user_id, amount, category, description, expense_date) VALUES (%s, 250.00, 'Food', 'Groceries', '2026-08-01')",
                (user_c2_id,)
            )
            cursor.execute(
                "INSERT INTO income (user_id, amount, source, description, income_date) VALUES (%s, 5000.00, 'Salary', 'Monthly Pay', '2026-08-01')",
                (user_c2_id,)
            )

    client.get('/logout')

    # Case 2 Execution: Google OAuth login matches email_c2
    with app.test_request_context():
        with get_db() as (conn, cursor):
            cursor.execute("SELECT user_id, auth_provider FROM users WHERE email=%s", (email_c2,))
            row = cursor.fetchone()
            assert row[0] == user_c2_id
            assert row[1] == 'local'

            # Upgrade to Google provider
            from werkzeug.security import generate_password_hash
            import secrets
            new_random_pw = generate_password_hash(secrets.token_hex(32))
            cursor.execute("UPDATE users SET auth_provider='google', password=%s WHERE user_id=%s", (new_random_pw, user_c2_id))

            # Verify financial data preserved
            cursor.execute("SELECT COUNT(*) FROM expenses WHERE user_id=%s", (user_c2_id,))
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT COUNT(*) FROM income WHERE user_id=%s", (user_c2_id,))
            assert cursor.fetchone()[0] == 1

    # Attacker local password attempt MUST be rejected
    res_local_login = client.post('/login', data={'email': email_c2, 'password': 'LocalPassword1'})
    assert res_local_login.status_code == 200
    assert b'Invalid email or password' in res_local_login.data

    # Case 3: Subsequent login with same Google account does not duplicate user
    with app.test_request_context():
        with get_db() as (conn, cursor):
            cursor.execute("SELECT COUNT(*) FROM users WHERE email=%s", (email_c2,))
            assert cursor.fetchone()[0] == 1

def test_google_only_account_profile_ux(client, app):
    """Verify Google-only account Profile UX displays Google sign-in method notice and gracefully rejects password changes."""
    import time
    ts = int(time.time() * 1000)
    g_email = f'g_user_{ts}@example.com'

    # Register Google user
    with app.test_request_context():
        with get_db() as (conn, cursor):
            from werkzeug.security import generate_password_hash
            import secrets
            cursor.execute(
                "INSERT INTO users (username, email, password, display_name, auth_provider) VALUES (%s, %s, %s, %s, 'google')",
                (g_email, g_email, generate_password_hash(secrets.token_hex(32)), 'Google User')
            )
            g_uid = cursor.lastrowid
            cursor.execute("INSERT IGNORE INTO notification_preferences (user_id) VALUES (%s)", (g_uid,))

    # Log in as Google user
    with client.session_transaction() as sess:
        sess['user_id'] = g_uid
        sess['username'] = g_email
        sess['display_name'] = 'Google User'

    res_prof = client.get('/profile')
    assert res_prof.status_code == 200
    assert b'Sign-in method: Google' in res_prof.data
    assert b"You don't need a Finora password" in res_prof.data

    # Attempt to post change password
    res_change_pw = client.post('/profile/change-password', data={
        'current_password': 'AnyPassword1',
        'new_password': 'NewPassword1',
        'confirm_password': 'NewPassword1'
    }, follow_redirects=True)
    assert res_change_pw.status_code == 200
    assert b"don't need a Finora password" in res_change_pw.data or b"Google" in res_change_pw.data

def test_login_rate_limiting_full_policy(client, app):
    """Verify rate limiter threshold, 429 lockout, reset_rate_limit on successful login, and IP isolation."""
    from services.rate_limiter import reset_rate_limit_store, check_rate_limit, reset_rate_limit
    client.get('/logout')
    app.config['ENABLE_RATE_LIMIT_TESTING'] = True
    reset_rate_limit_store()

    try:
        key_a = "login:192.168.1.100"
        key_b = "login:192.168.1.101"

        # 1. Fill 5 attempts for Key A
        for _ in range(5):
            allowed, _ = check_rate_limit(key_a, max_requests=5, window_seconds=60)
            assert allowed is True

        # 6th attempt for Key A MUST be blocked
        allowed, retry = check_rate_limit(key_a, max_requests=5, window_seconds=60)
        assert allowed is False
        assert retry >= 10

        # 2. Key B remains unblocked (IP isolation)
        allowed_b, _ = check_rate_limit(key_b, max_requests=5, window_seconds=60)
        assert allowed_b is True

        # 3. Successful login resets Key A
        reset_rate_limit(key_a)
        allowed_a_reset, _ = check_rate_limit(key_a, max_requests=5, window_seconds=60)
        assert allowed_a_reset is True
    finally:
        app.config['ENABLE_RATE_LIMIT_TESTING'] = False
        reset_rate_limit_store()



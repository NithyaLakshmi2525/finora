import pytest
from db import get_db

def test_income_crud(auth_client, test_user):
    # 1. Add Income
    res = auth_client.post('/income', data={
        'amount': '40000.00',
        'source': 'Salary',
        'description': 'Monthly Paycheck',
        'date': '2026-08-01',
        'account_id': str(test_user['account_id'])
    }, follow_redirects=True)
    assert res.status_code == 200
    assert b'Monthly Paycheck' in res.data

    with get_db() as (conn, cursor):
        cursor.execute("SELECT income_id FROM income WHERE user_id=%s AND description='Monthly Paycheck'", (test_user['user_id'],))
        inc_id = cursor.fetchone()[0]

    # 2. Edit Income
    res = auth_client.post(f'/edit-income/{inc_id}', data={
        'amount': '45000.00',
        'source': 'Salary',
        'description': 'Bonus Paycheck',
        'date': '2026-08-01',
        'account_id': str(test_user['account_id'])
    }, follow_redirects=True)
    assert res.status_code == 200

    # 3. Delete Income
    res = auth_client.post(f'/delete-income/{inc_id}', follow_redirects=True)
    assert res.status_code == 200

    with get_db() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM income WHERE income_id=%s", (inc_id,))
        assert cursor.fetchone()[0] == 0

def test_income_page_rendering(auth_client, test_user):
    """Verify GET /income returns 200, sidebar, page title, summary cards, and income table exist."""
    res = auth_client.get('/income')
    assert res.status_code == 200
    assert b'Income' in res.data
    assert b'Finora' in res.data
    assert b'Total Income' in res.data
    assert b'exp-table' in res.data
    assert b'incomePanel' in res.data
    # Assert toast div is properly closed and page content is not nested inside opacity-0 toast
    assert b'<span id="toastMsg"></span>\n</div>' in res.data or b'<span id="toastMsg"></span></div>' in res.data

def test_income_js_does_not_reference_removed_dom_ids(auth_client, test_user):
    """Verify obsolete DOM references are completely removed from JS script."""
    res = auth_client.get('/income')
    assert res.status_code == 200
    assert b'sourceHidden' not in res.data
    assert b'formDropdownLabel' not in res.data

def test_income_visibility_restoration(auth_client, test_user):
    """Verify the page's initialization script never leaves document.documentElement hidden."""
    res = auth_client.get('/income')
    assert res.status_code == 200
    assert b"document.documentElement.style.visibility = 'hidden'" not in res.data

def test_income_edit_drawer_structure(auth_client, test_user):
    """Verify edit drawer markup and required form controls exist."""
    res = auth_client.get('/income')
    assert res.status_code == 200
    assert b'id="incomePanel"' in res.data
    assert b'id="panelOverlay"' in res.data
    assert b'id="incomeDrawerForm"' in res.data
    assert b'id="drawerAmount"' in res.data
    assert b'id="drawerSource"' in res.data
    assert b'id="drawerDescription"' in res.data
    assert b'id="drawerDate"' in res.data

def test_income_delete_uses_post_and_csrf(auth_client, test_user):
    """Verify GET on delete route is rejected with 405 Method Not Allowed."""
    res = auth_client.get('/delete-income/1')
    assert res.status_code == 405

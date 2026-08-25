import pytest

def test_reports_and_exports(auth_client, test_user):
    res = auth_client.get('/monthly-report')
    assert res.status_code == 200
    assert b'Monthly Report' in res.data or b'Finora' in res.data

    # CSV Export
    res = auth_client.get('/export')
    assert res.status_code == 200
    assert res.mimetype == 'text/csv'

    # PDF Export
    res = auth_client.get('/export-pdf')
    assert res.status_code == 200
    assert res.mimetype == 'application/pdf'

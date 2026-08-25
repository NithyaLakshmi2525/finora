import pytest

def test_notifications_feed_and_api(auth_client, test_user):
    res = auth_client.get('/notifications')
    assert res.status_code == 200
    data = res.get_json()
    assert isinstance(data, list)

    res = auth_client.post('/notifications/read-all')
    assert res.status_code == 200

    res = auth_client.post('/notifications/clear')
    assert res.status_code == 200

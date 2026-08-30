import time
from collections import defaultdict
from flask import request

_rate_limit_store = defaultdict(list)

def reset_rate_limit_store():
    """Clears all in-memory rate limit counters (useful for testing)."""
    global _rate_limit_store
    _rate_limit_store.clear()

def check_rate_limit(key, max_requests=10, window_seconds=60):
    """
    In-memory sliding window rate limiter.
    Returns (is_allowed, retry_after_seconds).
    """
    try:
        from flask import current_app
        if current_app and current_app.config.get('TESTING') and not current_app.config.get('ENABLE_RATE_LIMIT_TESTING'):
            return True, 0
    except RuntimeError:
        pass

    now = time.time()
    timestamps = _rate_limit_store[key]

    # Filter out timestamps outside the window
    _rate_limit_store[key] = [ts for ts in timestamps if now - ts < window_seconds]
    timestamps = _rate_limit_store[key]

    if len(timestamps) >= max_requests:
        oldest = timestamps[0]
        retry_after = max(1, int(window_seconds - (now - oldest)))
        return False, retry_after

    _rate_limit_store[key].append(now)
    return True, 0

def get_client_ip():
    """Extracts client IP address safely from request headers or remote_addr."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'

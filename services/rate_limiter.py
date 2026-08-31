"""
In-memory sliding window rate limiter service.

Note on Architecture & Scalability:
This in-memory store is suitable for single-instance application deployments.
For multi-instance or distributed production environments, counters should be
stored in a shared data store (e.g. Redis) so rate limit counters are shared across nodes.
"""
import time
from collections import defaultdict
from flask import request

_rate_limit_store = defaultdict(list)

def reset_rate_limit_store():
    """Clears all in-memory rate limit counters (useful for testing)."""
    global _rate_limit_store
    _rate_limit_store.clear()

def reset_rate_limit(key):
    """Clears failure rate limit timestamps for a specific key (e.g. on successful authentication)."""
    global _rate_limit_store
    if key in _rate_limit_store:
        _rate_limit_store[key].clear()

def check_rate_limit(key, max_requests=5, window_seconds=60):
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
        base_retry = window_seconds - (now - oldest)
        retry_after = max(10, int(base_retry))
        return False, retry_after

    _rate_limit_store[key].append(now)
    return True, 0

def get_client_ip():
    """Extracts client IP address safely from request headers or remote_addr."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'

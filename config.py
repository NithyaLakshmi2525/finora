import os
from datetime import timedelta
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

class Config:
    # Debug mode
    DEBUG = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")

    # Secret Key for sessions
    SECRET_KEY = os.environ.get("SECRET_KEY", "default-dev-secret-key-change-in-production")

    # Session Security Flags
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # SESSION_COOKIE_SECURE enabled in production (when FLASK_DEBUG is False or SESSION_COOKIE_SECURE=1)
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true", "yes")
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    # Database Settings
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "expense_tracker")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_POOL_NAME = "finora_db_pool"
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))

    # OAuth Settings
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

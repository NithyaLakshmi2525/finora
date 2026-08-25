import os
from contextlib import contextmanager
import mysql.connector
from mysql.connector import pooling
from config import Config

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_connection_pool = None

def init_db_pool():
    global _connection_pool
    if _connection_pool is None:
        try:
            _connection_pool = pooling.MySQLConnectionPool(
                pool_name=Config.DB_POOL_NAME,
                pool_size=Config.DB_POOL_SIZE,
                pool_reset_session=True,
                host=Config.DB_HOST,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                database=Config.DB_NAME,
                port=Config.DB_PORT
            )
        except Exception as e:
            # Fallback if pooling fails
            print(f"[db_pool] Connection pool initialization warning: {e}")
            _connection_pool = None

def get_db_connection():
    """Returns a connection from the pool, or falls back to direct connection if pool is full/uninitialized."""
    global _connection_pool
    if _connection_pool is None:
        init_db_pool()
    
    if _connection_pool is not None:
        try:
            return _connection_pool.get_connection()
        except mysql.connector.Error:
            pass # Fallback to direct connection on pool exhaustion

    return mysql.connector.connect(
        host=Config.DB_HOST,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME,
        port=Config.DB_PORT
    )

@contextmanager
def get_db():
    """Context manager for automatic connection checkout, commit/rollback, and releasing back to pool."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        yield conn, cursor
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
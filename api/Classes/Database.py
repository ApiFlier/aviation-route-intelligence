"""
Database connection manager for FlightConn API
"""

import os
from decimal import Decimal
import MySQLdb
from MySQLdb.cursors import DictCursor
from contextlib import contextmanager


def convert_row(row):
    """Convert Decimal and other types to JSON-serializable types."""
    if row is None:
        return None
    if isinstance(row, dict):
        return {k: convert_value(v) for k, v in row.items()}
    return row


def convert_value(val):
    """Convert a single value to JSON-serializable type."""
    if isinstance(val, Decimal):
        return float(val)
    return val


class Database:
    """MySQL database connection manager."""
    
    _instance = None
    
    def __init__(self):
        self.config = {
            'host': os.getenv('MYSQL_HOST', 'localhost'),
            'port': int(os.getenv('MYSQL_PORT', 3306)),
            'user': os.getenv('MYSQL_USER', 'flightconn'),
            'passwd': os.getenv('MYSQL_PASSWORD', 'flightconn'),
            'db': os.getenv('MYSQL_DATABASE', 'flightconn'),
            'charset': 'utf8mb4',
            'use_unicode': True,
        }
    
    @classmethod
    def get_instance(cls):
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def get_connection(self):
        """Get a new database connection."""
        return MySQLdb.connect(**self.config)
    
    @contextmanager
    def cursor(self, dict_cursor=True):
        """Context manager for database cursor."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor(DictCursor if dict_cursor else None)
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()
    
    def execute(self, query, params=None):
        """Execute a query and return all results."""
        with self.cursor() as cur:
            cur.execute(query, params or ())
            results = cur.fetchall()
            return [convert_row(row) for row in results]
    
    def execute_one(self, query, params=None):
        """Execute a query and return single result."""
        with self.cursor() as cur:
            cur.execute(query, params or ())
            return convert_row(cur.fetchone())
    
    def execute_many(self, query, params_list):
        """Execute a query with multiple parameter sets."""
        with self.cursor() as cur:
            cur.executemany(query, params_list)
            return cur.rowcount
    
    def execute_write(self, query, params=None):
        """Execute a write query and return affected rows."""
        with self.cursor() as cur:
            cur.execute(query, params or ())
            return cur.rowcount
    
    def get_last_insert_id(self):
        """Get last auto-increment ID."""
        result = self.execute_one("SELECT LAST_INSERT_ID() as id")
        return result['id'] if result else None
    
    def truncate_tables(self):
        """Truncate all data tables (for reprocessing)."""
        tables = ['route_carriers', 'routes', 'airports', 'carriers', 'aircraft', 'stats']
        with self.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in tables:
                cur.execute(f"TRUNCATE TABLE {table}")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        print("[DB] All tables truncated")


# Convenience function
def get_db():
    """Get database instance."""
    return Database.get_instance()
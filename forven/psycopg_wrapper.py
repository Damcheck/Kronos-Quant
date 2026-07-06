"""
Translation layer to replace sqlite3 with psycopg3 for Postgres migration.
"""

import os
import re
import psycopg
from psycopg.rows import dict_row

class PsycopgCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query: str, parameters=None):
        query = self._translate_query(query)
        if parameters is not None:
            self._cursor.execute(query, parameters)
        else:
            self._cursor.execute(query)
        return self

    def executemany(self, query: str, seq_of_parameters):
        query = self._translate_query(query)
        self._cursor.executemany(query, seq_of_parameters)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()
        
    def fetchmany(self, size=None):
        if size is None:
            return self._cursor.fetchmany()
        return self._cursor.fetchmany(size)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        # sqlite3 lastrowid equivalent in postgres. 
        # Usually requires RETURNING id, but we can't magically rewrite all inserts.
        # So we'll try to get it if returning was used, else None.
        try:
            return self._cursor.fetchone()['id']
        except Exception:
            return None

    def close(self):
        self._cursor.close()

    def _translate_query(self, query: str) -> str:
        if query.strip().upper().startswith("PRAGMA"):
            return "SELECT 1"
        if query.strip().upper() == "BEGIN IMMEDIATE":
            return "BEGIN"
        # Translate ? to %s
        q = query.replace('?', '%s')
        # Translate json_extract(col, '$.path') to (col::jsonb ->> 'path')
        q = re.sub(r"json_extract\(([^,]+),\s*'\$\.([^']+)'\)", r"(\1::jsonb ->> '\2')", q)
        # Translate json_valid
        q = re.sub(r"json_valid\(([^)]+)\)", r"(\1 IS NOT NULL)", q)
        # Schema translation: AUTOINCREMENT -> SERIAL
        q = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY", q, flags=re.IGNORECASE)
        # Schema translation: JSON -> JSONB
        q = re.sub(r"\bJSON\b", "JSONB", q, flags=re.IGNORECASE)

        # Translate INSERT OR IGNORE
        if re.search(r"INSERT OR IGNORE INTO", q, re.IGNORECASE):
            q = re.sub(r"INSERT OR IGNORE INTO", "INSERT INTO", q, flags=re.IGNORECASE)
            if not q.strip().upper().endswith("ON CONFLICT DO NOTHING"):
                q += " ON CONFLICT DO NOTHING"
                
        # Translate generic INSERT OR REPLACE
        m = re.search(r"INSERT OR REPLACE INTO\s+([a-zA-Z_0-9]+)\s*\((.*?)\)", q, re.IGNORECASE)
        if m:
            table = m.group(1)
            cols = [c.strip() for c in m.group(2).split(',')]
            pk = cols[0]
            if len(cols) > 1:
                updates = [f"{c}=EXCLUDED.{c}" for c in cols[1:]]
                update_str = ", ".join(updates)
                q = re.sub(r"INSERT OR REPLACE INTO", "INSERT INTO", q, flags=re.IGNORECASE)
                q += f" ON CONFLICT ({pk}) DO UPDATE SET {update_str}"
            else:
                q = re.sub(r"INSERT OR REPLACE INTO", "INSERT INTO", q, flags=re.IGNORECASE)
                q += f" ON CONFLICT ({pk}) DO NOTHING"
            
        return q
        
    def __iter__(self):
        return iter(self._cursor)

class PsycopgConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn
        self.row_factory = None  # Mocking sqlite3.row_factory attribute
    
    def cursor(self):
        return PsycopgCursorWrapper(self._conn.cursor(row_factory=dict_row))

    def execute(self, query: str, parameters=None):
        cur = self.cursor()
        cur.execute(query, parameters)
        return cur

    def executemany(self, query: str, seq_of_parameters):
        cur = self.cursor()
        cur.executemany(query, seq_of_parameters)
        return cur

    def executescript(self, sql_script: str):
        cur = self.cursor()
        cur._cursor.execute(sql_script)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

def connect(*args, **kwargs):
    # Ignore sqlite specific args
    db_url = os.environ.get("FORVEN_DATABASE_URL", "postgresql://localhost/forven")
    # psycopg.connect supports connection strings natively
    conn = psycopg.connect(conninfo=db_url, autocommit=False)
    return PsycopgConnectionWrapper(conn)

class Row:
    pass

class OperationalError(psycopg.OperationalError):
    pass

class IntegrityError(psycopg.IntegrityError):
    pass

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


class Database:
    """
    Универсальный адаптер реляционной БД с поддержкой пула соединений PostgreSQL
    (с TCP keepalive и авто-переподключением) и SQLite (для локальных тестов).
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.is_postgres = database_url.startswith("postgres://") or database_url.startswith("postgresql://")
        self._lock = threading.RLock()
        self._pg_pool = None

        if not self.is_postgres:
            if database_url.startswith("sqlite:///"):
                db_path_str = database_url[len("sqlite:///"):]
            elif database_url == ":memory:":
                db_path_str = ":memory:"
            else:
                db_path_str = database_url
            self.sqlite_path = db_path_str
            if db_path_str != ":memory:":
                Path(db_path_str).parent.mkdir(parents=True, exist_ok=True)
            self._memory_conn: Optional[sqlite3.Connection] = None
            if db_path_str == ":memory:":
                self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._memory_conn.row_factory = sqlite3.Row

    def _get_pg_pool(self):
        if self._pg_pool is None:
            import psycopg2.pool
            self._pg_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=10,
                dsn=self.database_url,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
                connect_timeout=5,
            )
        return self._pg_pool

    def _adapt_sql(self, sql: str) -> str:
        if self.is_postgres:
            return sql.replace("?", "%s")
        return sql

    @contextmanager
    def connect(self):
        if self.is_postgres:
            import psycopg2
            pool = self._get_pg_pool()
            conn = pool.getconn()
            try:
                # Проверяем живость соединения из пула
                if conn.closed:
                    pool.putconn(conn, close=True)
                    conn = pool.getconn()
                yield conn
                conn.commit()
            except psycopg2.OperationalError:
                try:
                    pool.putconn(conn, close=True)
                except Exception:
                    pass
                conn = None
                raise
            except Exception:
                if conn and not conn.closed:
                    conn.rollback()
                raise
            finally:
                if conn is not None:
                    try:
                        pool.putconn(conn)
                    except Exception:
                        pass
        else:
            if self._memory_conn is not None:
                with self._lock:
                    try:
                        yield self._memory_conn
                        self._memory_conn.commit()
                    except Exception:
                        self._memory_conn.rollback()
                        raise
            else:
                conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        adapted = self._adapt_sql(sql)
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(adapted, tuple(params))
            return cur.rowcount

    def executescript(self, sql_script: str) -> None:
        with self.connect() as conn:
            if self.is_postgres:
                cur = conn.cursor()
                cur.execute(sql_script)
            else:
                conn.executescript(sql_script)

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        adapted = self._adapt_sql(sql)
        with self.connect() as conn:
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(adapted, tuple(params))
                row = cur.fetchone()
                return dict(row) if row is not None else None
            else:
                cur = conn.cursor()
                cur.execute(adapted, tuple(params))
                row = cur.fetchone()
                return dict(row) if row is not None else None

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        adapted = self._adapt_sql(sql)
        with self.connect() as conn:
            if self.is_postgres:
                import psycopg2.extras
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(adapted, tuple(params))
                rows = cur.fetchall()
                return [dict(r) for r in rows]
            else:
                cur = conn.cursor()
                cur.execute(adapted, tuple(params))
                rows = cur.fetchall()
                return [dict(r) for r in rows]

"""
Thin MySQL access layer.

Every query in this file (and everywhere else in the app) uses PyMySQL
parameter substitution (`%s` placeholders + a params tuple/list) rather than
manual string formatting or f-strings, so user input can never be interpreted
as SQL. This is the ONLY module that should import pymysql directly.
"""
import pymysql
import pymysql.cursors
from flask import current_app, g

from .dbconfig import ssl_arg


def get_connection():
    """Return a request-scoped MySQL connection (created once per request)."""
    if 'db_conn' not in g:
        g.db_conn = pymysql.connect(
            host=current_app.config['DB_HOST'],
            port=current_app.config['DB_PORT'],
            user=current_app.config['DB_USER'],
            password=current_app.config['DB_PASSWORD'],
            database=current_app.config['DB_NAME'],
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            charset='utf8mb4',
            ssl=ssl_arg(),
            connect_timeout=15,
        )
    return g.db_conn


def close_connection(e=None):
    conn = g.pop('db_conn', None)
    if conn is not None:
        conn.close()


def init_app(app):
    app.teardown_appcontext(close_connection)


def query(sql, params=None, fetchone=False):
    """SELECT helper. Always uses a parameterized query."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone() if fetchone else cur.fetchall()


def execute(sql, params=None, many=False):
    """
    INSERT/UPDATE/DELETE helper. Commits immediately and returns
    (lastrowid, rowcount). Raises the underlying exception on failure
    (callers should catch pymysql.err.IntegrityError for constraint
    violations such as duplicate keys).
    """
    conn = get_connection()
    with conn.cursor() as cur:
        if many:
            cur.executemany(sql, params or [])
        else:
            cur.execute(sql, params or ())
        conn.commit()
        return cur.lastrowid, cur.rowcount


def get_raw_connection_for_script(app):
    """Used by standalone scripts (migration/training) outside a request context."""
    return pymysql.connect(
        host=app['DB_HOST'], port=app['DB_PORT'], user=app['DB_USER'],
        password=app['DB_PASSWORD'], database=app['DB_NAME'],
        cursorclass=pymysql.cursors.DictCursor, autocommit=False, charset='utf8mb4',
        ssl=ssl_arg(),
    )

"""
One-command setup for a HOSTED MySQL database (Aiven, etc.).

    python scripts/setup_remote_db.py            # first-time setup
    python scripts/setup_remote_db.py --force    # wipe all WatchNext tables and start over

What it does, using the DB_* variables from your .env / environment:
  1. Runs watch.sql (tables, roles, seed accounts) -- WITHOUT the
     DROP DATABASE / CREATE DATABASE / USE lines, because hosted databases
     already give you a database and don't let you drop it.
  2. Loads data/movies.csv into MySQL (fast, batched).
  3. Replaces the well-known default passwords (Admin@123 ...) with strong
     random ones -- or with SEED_ADMIN_PASSWORD / SEED_MODERATOR_PASSWORD /
     SEED_USER_PASSWORD if you set those -- and prints them ONCE.
"""
import os
import re
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import pymysql
from dotenv import load_dotenv

load_dotenv()

SCHEMA_PATH = os.path.join(ROOT, 'watch.sql')
SEED_VARS = ('SEED_ADMIN_PASSWORD', 'SEED_MODERATOR_PASSWORD', 'SEED_USER_PASSWORD')


def split_statements(sql_text):
    """Split a SQL file into statements (this schema has no ';' inside strings)."""
    lines = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('--') or not stripped:
            continue
        lines.append(re.sub(r'\s--\s.*$', '', line))  # trailing "-- comment"
    return [s.strip() for s in '\n'.join(lines).split(';') if s.strip()]


def is_database_level(stmt):
    u = stmt.upper()
    return (u.startswith('DROP DATABASE') or u.startswith('CREATE DATABASE')
            or u.startswith('USE '))


def main():
    force = '--force' in sys.argv

    # Strong random seed passwords unless the person chose their own.
    generated = {}
    for var in SEED_VARS:
        if not os.environ.get(var):
            pw = secrets.token_urlsafe(9) + 'a1'
            os.environ[var] = pw
            generated[var] = pw

    from app.dbconfig import connect_kwargs
    print(f"Connecting to {os.environ.get('DB_HOST', 'localhost')}:{os.environ.get('DB_PORT', 3306)} "
          f"(db={os.environ.get('DB_NAME', 'watchnext')}, ssl={os.environ.get('DB_SSL', '0')}) ...")
    # Create the database (e.g. `watchnext`) first if it doesn't exist yet.
    kw = connect_kwargs()
    db_name = kw.pop('database')
    try:
        boot = pymysql.connect(autocommit=True, **kw)
        with boot.cursor() as cur:
            cur.execute(f'CREATE DATABASE IF NOT EXISTS `{db_name}` '
                        'CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
        boot.close()
    except pymysql.err.OperationalError as e:
        sys.exit(f'ERROR: could not connect: {e}\n'
                 'Check DB_HOST / DB_PORT / DB_USER / DB_PASSWORD and DB_SSL=1 in your .env.')
    except pymysql.err.MySQLError as e:
        print(f'Note: could not create database `{db_name}` ({e}); assuming it already exists.')

    try:
        conn = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                               **connect_kwargs())
    except pymysql.err.OperationalError as e:
        sys.exit(f'ERROR: could not connect: {e}\n'
                 'Check DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME and DB_SSL=1 in your .env.')

    with conn.cursor() as cur:
        cur.execute('SHOW TABLES')
        existing = [list(r.values())[0] for r in cur.fetchall()]
        if existing and not force:
            sys.exit('This database already has tables: ' + ', '.join(existing[:6]) +
                     (' ...' if len(existing) > 6 else '') +
                     '\nRe-run with --force to DELETE them and start fresh.')
        if existing and force:
            print('--force given: dropping existing tables ...')
            cur.execute('SET FOREIGN_KEY_CHECKS = 0')
            for t in existing:
                cur.execute(f'DROP TABLE IF EXISTS `{t}`')
            cur.execute('SET FOREIGN_KEY_CHECKS = 1')

        print('Creating tables from watch.sql ...')
        with open(SCHEMA_PATH, encoding='utf-8') as f:
            statements = split_statements(f.read())
        for stmt in statements:
            if is_database_level(stmt):
                continue
            cur.execute(stmt)
    conn.close()

    # Reuse the (now batched) loader; it also sets the seed account passwords.
    import migrate_data
    conn = pymysql.connect(**migrate_data.DB_CONFIG)
    try:
        migrate_data.migrate_movies(conn)
        migrate_data.migrate_seed_accounts(conn)
    finally:
        conn.close()

    print('\n==================== DONE ====================')
    if generated:
        print('Generated logins (SAVE THESE NOW -- they are not stored anywhere):')
        labels = {'SEED_ADMIN_PASSWORD': 'admin', 'SEED_MODERATOR_PASSWORD': 'moderator',
                  'SEED_USER_PASSWORD': 'ram'}
        for var, pw in generated.items():
            print(f'   username: {labels[var]:10s} password: {pw}')
    print('Recovery code for all three seed accounts is 1234 -- change it from each '
          "account's dashboard after logging in.")


if __name__ == '__main__':
    main()

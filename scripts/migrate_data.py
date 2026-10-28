import os
import sys
import csv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

from app.dbconfig import connect_kwargs

load_dotenv()

DB_CONFIG = dict(cursorclass=pymysql.cursors.DictCursor, **connect_kwargs())

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'movies.csv')

SEED_ACCOUNTS = [
    ('admin', 'System Admin', 'admin@watch.local', 'Admin',
     os.environ.get('SEED_ADMIN_PASSWORD', 'Admin@123')),
    ('moderator', 'Content Moderator', 'moderator@watch.local', 'Moderator',
     os.environ.get('SEED_MODERATOR_PASSWORD', 'Moderator@123')),
    ('ram', 'ram', 'ram@watch.local', 'User',
     os.environ.get('SEED_USER_PASSWORD', 'User@123')),
]


def migrate_seed_accounts(conn):
    print('--- Seeding initial accounts ---')
    with conn.cursor() as cur:
        for username, name, email, role, password in SEED_ACCOUNTS:
            pw_hash = generate_password_hash(password)
            cur.execute(
                """UPDATE users u JOIN roles r ON r.role_id = u.role_id
                   SET u.password_hash = %s
                   WHERE u.username = %s AND r.role_name = %s""",
                (pw_hash, username, role),
            )
            print(f'  {role:10s} username={username:12s} password={password}')
    conn.commit()
    print('  (change these passwords after first login)\n')


def _clean_row(row, row_num):
    """Validate one CSV row. Returns (clean_dict, None) or (None, (title, reason))."""
    MIN_YEAR, MAX_YEAR = 1888, 2100  # mirrors chk_movie_year in watch.sql

    title = (row.get('title') or '').strip()
    if not title:
        return None, (f'row {row_num}', 'blank title')

    year_raw = (row.get('year') or '').strip()
    try:
        year = int(float(year_raw)) if year_raw else None
    except ValueError:
        year = None
    if year is None or year < MIN_YEAR or year > MAX_YEAR:
        return None, (title, f'missing/invalid year ({year_raw!r})')

    try:
        rating = float(row.get('rating') or 0)
    except ValueError:
        rating = 0.0
    rating = max(0.0, min(10.0, rating))  # chk_movie_rating (0-10)

    runtime_raw = row.get('runtime') or ''
    try:
        runtime = str(int(float(runtime_raw))) if runtime_raw else ''
    except ValueError:
        runtime = runtime_raw

    genres = []
    for g in (row.get('genre') or '').split('|'):
        g = g.strip()
        if g and g not in genres:
            genres.append(g)

    return {
        'title': title, 'year': year, 'rating': rating, 'runtime': runtime,
        'description': row.get('description', ''), 'director': row.get('director', ''),
        'cast': row.get('cast', ''), 'language': row.get('language', ''),
        'poster_url': row.get('poster_url', ''), 'trailer_id': row.get('trailer_id', ''),
        'genres': genres,
    }, None


def migrate_movies(conn):
    """
    Load data/movies.csv into movies / genres / movie_genres.

    Uses BATCHED inserts (a handful of network round-trips in total instead of
    thousands), so it is fast even against a remote hosted database. The
    validation rules and skip-logging are the same as before.
    """
    if not os.path.exists(CSV_PATH):
        print(f'No dataset found at {CSV_PATH}; skipping movie import.')
        return 0

    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    print(f'--- Migrating {len(rows)} movies from {CSV_PATH} ---')

    skip_log = []
    clean = []
    with conn.cursor() as cur:
        cur.execute('SELECT title, year FROM movies')
        seen = {(r['title'], r['year']) for r in cur.fetchall()}

    for row_num, row in enumerate(rows, start=2):  # +2: header row + 1-indexing
        item, err = _clean_row(row, row_num)
        if err:
            skip_log.append(err)
            continue
        key = (item['title'], item['year'])
        if key in seen:
            skip_log.append((item['title'], f"duplicate (already in DB for year {item['year']})"))
            continue
        seen.add(key)
        clean.append(item)

    inserted = 0
    BATCH = 500
    with conn.cursor() as cur:
        for i in range(0, len(clean), BATCH):
            chunk = clean[i:i + BATCH]
            try:
                cur.executemany(
                    """INSERT INTO movies (title, description, year, rating, director, cast_list,
                                            runtime, language, poster_url, trailer_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(m['title'], m['description'], m['year'], m['rating'], m['director'],
                      m['cast'], m['runtime'], m['language'], m['poster_url'], m['trailer_id'])
                     for m in chunk],
                )
                inserted += len(chunk)
            except pymysql.err.IntegrityError:
                # A bad batch must not abort the run: retry its rows one by one so
                # only the genuinely bad row(s) are skipped.
                conn.rollback()
                for m in chunk:
                    try:
                        cur.execute(
                            """INSERT INTO movies (title, description, year, rating, director,
                                                    cast_list, runtime, language, poster_url, trailer_id)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (m['title'], m['description'], m['year'], m['rating'], m['director'],
                             m['cast'], m['runtime'], m['language'], m['poster_url'], m['trailer_id']))
                        inserted += 1
                    except pymysql.err.IntegrityError as e:
                        skip_log.append((m['title'], f'DB constraint rejected row ({e})'))
                        conn.rollback()

        # genres
        cur.execute('SELECT genre_id, genre_name FROM genres')
        genre_ids = {r['genre_name']: r['genre_id'] for r in cur.fetchall()}
        new_genres = sorted({g for m in clean for g in m['genres']} - set(genre_ids))
        if new_genres:
            cur.executemany('INSERT INTO genres (genre_name) VALUES (%s)', [(g,) for g in new_genres])
            cur.execute('SELECT genre_id, genre_name FROM genres')
            genre_ids = {r['genre_name']: r['genre_id'] for r in cur.fetchall()}

        # movie <-> genre links
        cur.execute('SELECT movie_id, title, year FROM movies')
        movie_ids = {(r['title'], r['year']): r['movie_id'] for r in cur.fetchall()}
        links = []
        for m in clean:
            mid = movie_ids.get((m['title'], m['year']))
            if mid is None:
                continue
            for g in m['genres']:
                links.append((mid, genre_ids[g]))
        for i in range(0, len(links), 2000):
            cur.executemany('INSERT IGNORE INTO movie_genres (movie_id, genre_id) VALUES (%s,%s)',
                            links[i:i + 2000])

    conn.commit()
    print(f'  Inserted: {inserted}, skipped: {len(skip_log)}\n')

    if skip_log:
        print('  --- Skipped rows (grouped by reason) ---')
        by_reason = {}
        for t, reason in skip_log:
            by_reason.setdefault(reason.split(' (')[0], []).append((t, reason))
        for group_reason, items in by_reason.items():
            print(f'  [{group_reason}] {len(items)} row(s)')
            for t, reason in items[:15]:
                print(f'      - {t}: {reason}')
            if len(items) > 15:
                print(f'      ... and {len(items) - 15} more')
        print()

    return inserted


def main():
    try:
        conn = pymysql.connect(**DB_CONFIG)
    except pymysql.err.OperationalError as e:
        print(f'ERROR: could not connect to MySQL ({e}). '
              f'Is XAMPP/MySQL running and has watch.sql been imported?')
        sys.exit(1)

    try:
        migrate_movies(conn)
        migrate_seed_accounts(conn)
        print('Migration complete.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()

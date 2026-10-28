"""
Ratings, reviews, watch history and thumbs up/down feedback.
Every write here is scoped to the acting user_id passed in by the route —
routes are responsible for always using session['user_id'], never a
client-supplied id, so a user can only ever modify their own data.
"""
from app import db


# ---------------------------------------------------------------- ratings --
def upsert_rating(user_id, movie_id, rating):
    db.execute(
        """INSERT INTO ratings (user_id, movie_id, rating)
           VALUES (%s, %s, %s)
           ON DUPLICATE KEY UPDATE rating = VALUES(rating)""",
        (user_id, movie_id, rating),
    )


def get_user_rating(user_id, movie_id):
    return db.query('SELECT rating FROM ratings WHERE user_id=%s AND movie_id=%s',
                     (user_id, movie_id), fetchone=True)


def get_user_ratings(user_id):
    """All of this user's own star ratings, for personalization scoring
    (feeds get_personalised_for_user / catalog reordering) -- never
    another user's ratings."""
    return db.query('SELECT movie_id, rating FROM ratings WHERE user_id = %s', (user_id,))


def get_movie_average_rating(movie_id):
    row = db.query('SELECT AVG(rating) AS avg_rating, COUNT(*) AS n FROM ratings WHERE movie_id=%s',
                    (movie_id,), fetchone=True)
    return row


# ---------------------------------------------------------------- reviews --
def add_review(user_id, movie_id, text):
    review_id, _ = db.execute(
        'INSERT INTO reviews (user_id, movie_id, review_text) VALUES (%s,%s,%s)',
        (user_id, movie_id, text),
    )
    return review_id


def get_reviews_for_movie(movie_id, include_hidden=False):
    if include_hidden:
        sql = """SELECT rv.*, u.username FROM reviews rv
                  JOIN users u ON u.user_id = rv.user_id
                  WHERE rv.movie_id = %s ORDER BY rv.created_at DESC"""
    else:
        sql = """SELECT rv.*, u.username FROM reviews rv
                  JOIN users u ON u.user_id = rv.user_id
                  WHERE rv.movie_id = %s AND rv.status = 'visible'
                  ORDER BY rv.created_at DESC"""
    return db.query(sql, (movie_id,))


def get_all_reviews_for_moderation():
    return db.query(
        """SELECT rv.review_id, rv.review_text, rv.status, rv.created_at,
                  u.username, m.title AS movie_title, m.movie_id
           FROM reviews rv
           JOIN users u ON u.user_id = rv.user_id
           JOIN movies m ON m.movie_id = rv.movie_id
           ORDER BY rv.created_at DESC"""
    )


def set_review_status(review_id, status):
    if status not in ('visible', 'hidden'):
        raise ValueError('Invalid status')
    db.execute('UPDATE reviews SET status=%s WHERE review_id=%s', (status, review_id))


def review_owner(review_id):
    row = db.query('SELECT user_id FROM reviews WHERE review_id=%s', (review_id,), fetchone=True)
    return row['user_id'] if row else None


def delete_review(review_id):
    db.execute('DELETE FROM reviews WHERE review_id=%s', (review_id,))


# ---------------------------------------------------------- watch history --
def add_watch(user_id, movie_id):
    db.execute('INSERT INTO watch_history (user_id, movie_id) VALUES (%s, %s)', (user_id, movie_id))


def get_watch_history(user_id, limit=50):
    return db.query(
        """SELECT wh.movie_id, wh.watched_at, m.title, m.poster_url, m.year
           FROM watch_history wh JOIN movies m ON m.movie_id = wh.movie_id
           WHERE wh.user_id = %s ORDER BY wh.watched_at DESC LIMIT %s""",
        (user_id, limit),
    )


def get_watch_history_ids(user_id, limit=50):
    rows = db.query(
        """SELECT DISTINCT movie_id, MAX(watched_at) AS last_watched
           FROM watch_history WHERE user_id = %s
           GROUP BY movie_id ORDER BY last_watched DESC LIMIT %s""",
        (user_id, limit),
    )
    return [r['movie_id'] for r in rows]


def clear_watch_history(user_id):
    db.execute('DELETE FROM watch_history WHERE user_id = %s', (user_id,))


def get_trending_movie_ids(top_n=10):
    rows = db.query(
        """SELECT movie_id, COUNT(*) AS views FROM watch_history
           GROUP BY movie_id ORDER BY views DESC LIMIT %s""",
        (top_n,),
    )
    return [r['movie_id'] for r in rows]


# --------------------------------------------------------------- feedback --
def set_feedback(user_id, movie_id, vote):
    if vote not in ('up', 'down'):
        raise ValueError('Invalid vote')
    db.execute(
        """INSERT INTO feedback (user_id, movie_id, vote) VALUES (%s,%s,%s)
           ON DUPLICATE KEY UPDATE vote = VALUES(vote)""",
        (user_id, movie_id, vote),
    )


def get_user_feedback(user_id):
    return db.query('SELECT movie_id, vote FROM feedback WHERE user_id=%s', (user_id,))


def count_user_feedback(user_id):
    rows = db.query(
        """SELECT vote, COUNT(*) AS c FROM feedback WHERE user_id=%s GROUP BY vote""",
        (user_id,),
    )
    counts = {'up': 0, 'down': 0}
    for r in rows:
        counts[r['vote']] = r['c']
    return counts


# ---------------------------------------------------------------------------
# Watchlist -- real per-user, DB-backed (previously this was pure browser
# localStorage under one single global key, meaning it wasn't tied to any
# account and could leak between users sharing a browser -- see watch.sql).
# ---------------------------------------------------------------------------
def toggle_watchlist(user_id, movie_id):
    """Adds the movie if it's not already on this user's watchlist, removes
    it if it is. Returns 'added' or 'removed'. Never touches any other
    user's watchlist -- scoped strictly by user_id."""
    existing = db.query('SELECT 1 FROM watchlist WHERE user_id=%s AND movie_id=%s',
                         (user_id, movie_id), fetchone=True)
    if existing:
        db.execute('DELETE FROM watchlist WHERE user_id=%s AND movie_id=%s', (user_id, movie_id))
        return 'removed'
    else:
        db.execute('INSERT INTO watchlist (user_id, movie_id) VALUES (%s, %s)', (user_id, movie_id))
        return 'added'


def get_watchlist(user_id):
    """This user's saved movies, most recently added first, joined with the
    movie's own title/poster so the frontend doesn't need a second lookup."""
    return db.query(
        """SELECT m.movie_id, m.title, m.poster_url, m.year, w.added_at
           FROM watchlist w JOIN movies m ON m.movie_id = w.movie_id
           WHERE w.user_id = %s ORDER BY w.added_at DESC""",
        (user_id,),
    )


def get_watchlist_ids(user_id):
    """Just the movie_ids, used to mark 'already saved' state on cards."""
    rows = db.query('SELECT movie_id FROM watchlist WHERE user_id=%s', (user_id,))
    return {r['movie_id'] for r in rows}

from app import db
import pymysql


def _genre_rows_to_pipe(genre_rows):
    return '|'.join(r['genre_name'] for r in genre_rows)


def get_all_genres():
    return db.query('SELECT genre_id, genre_name FROM genres ORDER BY genre_name')


def get_or_create_genre(genre_name):
    genre_name = genre_name.strip()
    row = db.query('SELECT genre_id FROM genres WHERE genre_name = %s', (genre_name,), fetchone=True)
    if row:
        return row['genre_id']
    genre_id, _ = db.execute('INSERT INTO genres (genre_name) VALUES (%s)', (genre_name,))
    return genre_id


def set_movie_genres(movie_id, genre_names):
    db.execute('DELETE FROM movie_genres WHERE movie_id = %s', (movie_id,))
    for g in genre_names:
        g = g.strip()
        if not g:
            continue
        genre_id = get_or_create_genre(g)
        db.execute(
            'INSERT IGNORE INTO movie_genres (movie_id, genre_id) VALUES (%s, %s)',
            (movie_id, genre_id),
        )


def _attach_genres(movies):
    if not movies:
        return movies
    ids = [m['movie_id'] for m in movies]
    placeholders = ','.join(['%s'] * len(ids))
    rows = db.query(
        f"""SELECT mg.movie_id, g.genre_name FROM movie_genres mg
            JOIN genres g ON g.genre_id = mg.genre_id
            WHERE mg.movie_id IN ({placeholders})
            ORDER BY g.genre_name""",
        ids,
    )
    by_movie = {}
    for r in rows:
        by_movie.setdefault(r['movie_id'], []).append(r['genre_name'])
    for m in movies:
        m['genre'] = '|'.join(by_movie.get(m['movie_id'], []))
        m['id'] = m['movie_id']  # template compatibility alias
        m['cast'] = m.get('cast_list', '')
    return movies


def get_all_movies():
    movies = db.query('SELECT * FROM movies ORDER BY movie_id')
    return _attach_genres(movies)


def get_movie(movie_id):
    m = db.query('SELECT * FROM movies WHERE movie_id = %s', (movie_id,), fetchone=True)
    if not m:
        return None
    return _attach_genres([m])[0]


def find_by_title_year(title, year, exclude_id=None):
    if exclude_id:
        row = db.query(
            'SELECT movie_id FROM movies WHERE LOWER(title) = LOWER(%s) AND year = %s AND movie_id != %s',
            (title.strip(), year, exclude_id), fetchone=True,
        )
    else:
        row = db.query(
            'SELECT movie_id FROM movies WHERE LOWER(title) = LOWER(%s) AND year = %s',
            (title.strip(), year), fetchone=True,
        )
    return row is not None


def create_movie(data, genre_names, created_by=None):
    movie_id, _ = db.execute(
        """INSERT INTO movies (title, description, year, rating, director, cast_list,
                                runtime, language, poster_url, trailer_id, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (data['title'], data.get('description', ''), data['year'], data.get('rating', 0),
         data.get('director', ''), data.get('cast', ''), data.get('runtime', ''),
         data.get('language', ''), data.get('poster_url', ''), data.get('trailer_id', ''),
         created_by),
    )
    set_movie_genres(movie_id, genre_names)
    return movie_id


def update_movie(movie_id, data, genre_names):
    db.execute(
        """UPDATE movies SET title=%s, description=%s, year=%s, rating=%s, director=%s,
               cast_list=%s, runtime=%s, language=%s, poster_url=%s, trailer_id=%s
           WHERE movie_id = %s""",
        (data['title'], data.get('description', ''), data['year'], data.get('rating', 0),
         data.get('director', ''), data.get('cast', ''), data.get('runtime', ''),
         data.get('language', ''), data.get('poster_url', ''), data.get('trailer_id', ''),
         movie_id),
    )
    set_movie_genres(movie_id, genre_names)


def delete_movie(movie_id):
    db.execute('DELETE FROM movies WHERE movie_id = %s', (movie_id,))


def search_movies(q=None, genre=None, sort=None):
    sql = 'SELECT DISTINCT m.* FROM movies m'
    conditions = []
    params = []
    order_params = []
    if genre:
        sql += ' JOIN movie_genres mg ON mg.movie_id = m.movie_id JOIN genres g ON g.genre_id = mg.genre_id'
        conditions.append('g.genre_name = %s')
        params.append(genre)
    if q:
        conditions.append('(m.title LIKE %s OR m.description LIKE %s OR m.cast_list LIKE %s OR m.director LIKE %s)')
        like = f'%{q}%'
        params.extend([like, like, like, like])
    if conditions:
        sql += ' WHERE ' + ' AND '.join(conditions)

    # Relevance: a title match should always outrank a movie that only
    # matched on cast/director/description. This applies even when a sort
    # is chosen, as a secondary tiebreaker, EXCEPT when the person explicitly
    # asked to sort by title -- that's already a title-first order.
    relevance_clause = None
    if q and sort != 'title':
        relevance_clause = 'CASE WHEN m.title LIKE %s THEN 0 ELSE 1 END'
        order_params.append(f'%{q}%')

    order_by = []
    if relevance_clause:
        order_by.append(relevance_clause)
    if sort == 'rating':
        order_by.append('m.rating DESC')
    elif sort == 'year':
        order_by.append('m.year DESC')
    elif sort == 'title':
        order_by.append('m.title ASC')
    else:
        order_by.append('m.movie_id')

    sql += ' ORDER BY ' + ', '.join(order_by)
    movies = db.query(sql, params + order_params)
    return _attach_genres(movies)


def stats():
    total_movies = db.query('SELECT COUNT(*) AS c FROM movies', fetchone=True)['c']
    total_genres = db.query('SELECT COUNT(*) AS c FROM genres', fetchone=True)['c']
    total_ratings = db.query('SELECT COUNT(*) AS c FROM ratings', fetchone=True)['c']
    total_reviews = db.query('SELECT COUNT(*) AS c FROM reviews', fetchone=True)['c']
    return {
        'total_movies': total_movies,
        'total_genres': total_genres,
        'total_ratings': total_ratings,
        'total_reviews': total_reviews,
    }

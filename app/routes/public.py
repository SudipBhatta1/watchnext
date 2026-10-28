from flask import Blueprint, render_template, request, jsonify, session, make_response

from app.models import movies as movies_repo
from app.models import interactions as interactions_repo
from app import recommender
from app.authz import current_user

public_bp = Blueprint('public', __name__)


@public_bp.route('/healthz')
def healthz():
    """Lightweight health check. Also touches the database, so pinging it every
    few minutes (UptimeRobot / Render health check) keeps a free hosted DB active."""
    from app import db
    try:
        db.query('SELECT 1 AS ok', fetchone=True)
    except Exception:
        return jsonify({'status': 'db_unreachable'}), 503
    return jsonify({'status': 'ok'}), 200


@public_bp.route('/')
def index():
    all_movies = movies_repo.get_all_movies()
    genres = sorted({g for m in all_movies for g in str(m.get('genre', '')).split('|') if g})
    user = current_user()

    for_you = []
    history_count = 0
    if user:
        history_count = len(interactions_repo.get_watch_history_ids(user['user_id'], limit=200))
        for_you = recommender.get_personalised_for_user(user['user_id'], top_n=10)
        # Full catalog, nothing hidden -- just re-ranked so "All Movies"
        # isn't identical for every logged-in user, the same way a
        # streaming homepage reorders its full catalog by your taste.
        all_movies = recommender.get_personalized_catalog_order(user['user_id'], movies=all_movies)

    resp = make_response(render_template(
        'index.html', movies=all_movies, genres=genres, for_you=for_you,
        username=user['username'] if user else None, history_count=history_count,
        is_guest_personalised=False,
    ))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp


@public_bp.route('/movie/<int:movie_id>')
def movie_detail(movie_id):
    movie = movies_repo.get_movie(movie_id)
    if not movie:
        return render_template('error.html', code=404, message='Movie not found.'), 404

    recs = recommender.get_similar_movies(movie_id, top_n=6)
    cast_list = [c.strip() for c in str(movie.get('cast_list', '')).split('|') if c.strip()]
    reviews = interactions_repo.get_reviews_for_movie(movie_id)
    avg = interactions_repo.get_movie_average_rating(movie_id)

    user = current_user()
    user_rating = None
    if user:
        interactions_repo.add_watch(user['user_id'], movie_id)
        r = interactions_repo.get_user_rating(user['user_id'], movie_id)
        user_rating = r['rating'] if r else None

    return render_template(
        'movie_detail.html', movie=movie, recommendations=recs, cast_list=cast_list,
        username=user['username'] if user else None, reviews=reviews, avg_rating=avg,
        user_rating=user_rating,
    )


@public_bp.route('/search')
def search():
    q = request.args.get('q', '').strip()
    genre = request.args.get('genre', '')
    sort = request.args.get('sort', '')
    user = current_user()

    results = movies_repo.search_movies(q=q or None, genre=genre or None, sort=sort)

    # "Default" sort (no explicit sort chosen) is the only one re-ranked by
    # personal taste -- Top Rated / Newest / A-Z stay literal/objective,
    # since picking one of those is an explicit request for that exact order.
    if not sort and user:
        results = recommender.get_personalized_catalog_order(user['user_id'], movies=results)

    not_found = bool(q) and not results
    suggestions = []
    if not_found:
        words = [w for w in q.lower().split() if len(w) >= 3]
        seen = set()
        for w in words:
            for m in movies_repo.search_movies(q=w, sort=None):
                if m['movie_id'] not in seen:
                    seen.add(m['movie_id'])
                    suggestions.append(m)
        suggestions = suggestions[:6]

    return jsonify({'movies': results, 'not_found': not_found, 'query': q, 'suggestions': suggestions})


@public_bp.route('/recommend', methods=['POST'])
def recommend():
    data = request.get_json(silent=True) or {}
    movie_id = data.get('movie_id')
    if not movie_id:
        return jsonify({'recommendations': [], 'error': 'movie_id required'}), 400
    recs = recommender.get_similar_movies(int(movie_id), top_n=6)
    return jsonify({'recommendations': recs})

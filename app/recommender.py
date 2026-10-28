"""
Prediction-time recommendation service.

This module NEVER trains a model. It only loads the artifacts already
produced by train_model.py (model/tfidf.pkl, model/cosine_sim.pkl,
model/movie_ids.pkl) and combines them with each user's live data pulled
from MySQL (watch history, thumbs up/down feedback) to produce:

  1. Item-to-item "more like this" recommendations (content-based,
     TF-IDF + cosine similarity over genre/description/title/director/cast).
  2. A personalised "For You" feed per logged-in user, built by scoring
     every movie against the genres/directors the user has watched or
     liked, exactly the heuristic the original project used — just reading
     from MySQL instead of history.json / feedback.json.
  3. A cold-start fallback (top-rated movies) for users with no history.

If the trained model files are missing, item-to-item similarity degrades
gracefully to "no recommendations" instead of crashing the app; the "For
You" feed and trending/top-rated fallbacks work independently of the model
because they only need genre/director data already in MySQL.
"""
import os
import pickle

from app.models import movies as movies_repo
from app.models import interactions as interactions_repo

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'model')

_cache = {'tfidf': None, 'cosine_sim': None, 'movie_ids': None, 'loaded': False}


def _shared_genres(genre_str_a, genre_str_b):
    a = {g.strip() for g in str(genre_str_a).split('|') if g.strip()}
    b = {g.strip() for g in str(genre_str_b).split('|') if g.strip()}
    return sorted(a & b)


def model_files_exist():
    return all(os.path.exists(os.path.join(MODEL_DIR, f))
               for f in ('cosine_sim.pkl', 'movie_ids.pkl'))


def load_model():
    """Load cached similarity matrix + movie_id index list. Returns (cosine_sim, movie_ids) or (None, None)."""
    if _cache['loaded']:
        return _cache['cosine_sim'], _cache['movie_ids']
    try:
        with open(os.path.join(MODEL_DIR, 'cosine_sim.pkl'), 'rb') as f:
            cosine_sim = pickle.load(f)
        with open(os.path.join(MODEL_DIR, 'movie_ids.pkl'), 'rb') as f:
            movie_ids = pickle.load(f)
        _cache.update(cosine_sim=cosine_sim, movie_ids=movie_ids, loaded=True)
        return cosine_sim, movie_ids
    except Exception:
        _cache.update(cosine_sim=None, movie_ids=None, loaded=True)
        return None, None


def reload_model():
    """Force a fresh read from disk next call (used right after retraining)."""
    _cache.update(tfidf=None, cosine_sim=None, movie_ids=None, loaded=False)


def get_similar_movies(movie_id, top_n=6):
    """Content-based 'more like this' for a single movie, with similarity % and match reasons."""
    cosine_sim, movie_ids = load_model()
    source = movies_repo.get_movie(movie_id)
    if not source or cosine_sim is None or movie_id not in movie_ids:
        return []

    idx = movie_ids.index(movie_id)
    sims = sorted(enumerate(cosine_sim[idx]), key=lambda x: x[1], reverse=True)[1:top_n + 1]

    results = []
    for i, score in sims:
        rec_id = movie_ids[i]
        rec = movies_repo.get_movie(rec_id)
        if not rec:
            continue
        rec['similarity_pct'] = round(float(score) * 100)
        reasons = []
        shared_g = _shared_genres(source.get('genre', ''), rec.get('genre', ''))
        if shared_g:
            reasons.append(f"Genre: {', '.join(shared_g)}")
        if source.get('director') and source['director'].lower() == str(rec.get('director', '')).lower():
            reasons.append(f"Director: {source['director']}")
        src_cast = {c.strip() for c in str(source.get('cast_list', '')).split('|') if c.strip()}
        rec_cast = {c.strip() for c in str(rec.get('cast_list', '')).split('|') if c.strip()}
        shared_cast = src_cast & rec_cast
        if shared_cast:
            reasons.append(f"Cast: {', '.join(list(shared_cast)[:2])}")
        try:
            if abs(int(source.get('year', 0)) - int(rec.get('year', 0))) <= 5:
                reasons.append(f"Same era ({rec.get('year')})")
        except (TypeError, ValueError):
            pass
        rec['match_reasons'] = reasons or ['Similar story & themes']
        results.append(rec)
    return results


def get_top_rated(top_n=10, exclude_ids=None):
    exclude_ids = exclude_ids or set()
    all_movies = movies_repo.get_all_movies()
    pool = [m for m in all_movies if m['movie_id'] not in exclude_ids]
    pool.sort(key=lambda m: float(m['rating']), reverse=True)
    return pool[:top_n]


def get_trending(top_n=10):
    ids = interactions_repo.get_trending_movie_ids(top_n)
    if not ids:
        return get_top_rated(top_n)
    out = []
    for mid in ids:
        m = movies_repo.get_movie(mid)
        if m:
            out.append(m)
    return out


def _compute_affinity(user_id):
    """
    Builds this user's genre/director affinity from 3 signals, combined:
      1. Watch history   -> genre/director counts (base signal)
      2. Thumbs up/down   -> boost/penalty on those genres/directors
      3. Star rating       -> boost/penalty SCALED by distance from neutral
                              (3 stars = neutral; 5 > 4 > neutral > 2 > 1),
                              combined additively with thumbs on the same
                              genre/director if the user did both.
    Everything here is scoped to this one user_id only -- never mixed with
    any other user's data.
    """
    hist_ids = interactions_repo.get_watch_history_ids(user_id, limit=30)
    feedback_rows = interactions_repo.get_user_feedback(user_id)
    liked_ids = {r['movie_id'] for r in feedback_rows if r['vote'] == 'up'}
    disliked_ids = {r['movie_id'] for r in feedback_rows if r['vote'] == 'down'}
    rating_rows = interactions_repo.get_user_ratings(user_id)
    rated_ids = {r['movie_id'] for r in rating_rows}

    all_movies = movies_repo.get_all_movies()
    by_id = {m['movie_id']: m for m in all_movies}

    genre_counts, dir_counts = {}, {}
    for mid in hist_ids:
        m = by_id.get(mid)
        if not m:
            continue
        for g in str(m.get('genre', '')).split('|'):
            g = g.strip()
            if g:
                genre_counts[g] = genre_counts.get(g, 0) + 1
        d = str(m.get('director', '')).strip()
        if d:
            dir_counts[d] = dir_counts.get(d, 0) + 1

    genre_boost, genre_penalty, dir_boost = {}, {}, {}

    for mid in liked_ids:
        m = by_id.get(mid)
        if not m:
            continue
        for g in str(m.get('genre', '')).split('|'):
            g = g.strip()
            if g:
                genre_boost[g] = genre_boost.get(g, 0) + 2
        d = str(m.get('director', '')).strip()
        if d:
            dir_boost[d] = dir_boost.get(d, 0) + 2

    for mid in disliked_ids:
        m = by_id.get(mid)
        if not m:
            continue
        for g in str(m.get('genre', '')).split('|'):
            g = g.strip()
            if g:
                genre_penalty[g] = genre_penalty.get(g, 0) + 1

    # Star ratings: scaled by distance from the neutral midpoint (3), and
    # combined into the SAME boost/penalty dicts as thumbs -- so a movie a
    # user both liked AND rated 5 stars gets both contributions stacked.
    for r in rating_rows:
        m = by_id.get(r['movie_id'])
        if not m:
            continue
        delta = r['rating'] - 3  # -2..+2
        if delta == 0:
            continue
        genres = [g.strip() for g in str(m.get('genre', '')).split('|') if g.strip()]
        direc = str(m.get('director', '')).strip()
        if delta > 0:
            for g in genres:
                genre_boost[g] = genre_boost.get(g, 0) + delta
            if direc:
                dir_boost[direc] = dir_boost.get(direc, 0) + delta
        else:
            for g in genres:
                genre_penalty[g] = genre_penalty.get(g, 0) + abs(delta)

    exclude_ids = set(hist_ids) | liked_ids | disliked_ids | rated_ids
    has_signal = bool(hist_ids or liked_ids or disliked_ids or rated_ids)

    return {
        'genre_counts': genre_counts, 'dir_counts': dir_counts,
        'genre_boost': genre_boost, 'dir_boost': dir_boost, 'genre_penalty': genre_penalty,
        'exclude_ids': exclude_ids, 'has_signal': has_signal, 'all_movies': all_movies,
    }


def _score_movie(m, affinity):
    genres = [g.strip() for g in str(m.get('genre', '')).split('|') if g.strip()]
    direc = str(m.get('director', '')).strip()
    score = 0.0
    for g in genres:
        score += affinity['genre_counts'].get(g, 0) * 3
    score += affinity['dir_counts'].get(direc, 0) * 2
    for g in genres:
        score += affinity['genre_boost'].get(g, 0) * 4
    score += affinity['dir_boost'].get(direc, 0) * 3
    for g in genres:
        score -= affinity['genre_penalty'].get(g, 0) * 3
    score += float(m.get('rating', 0)) * 0.3
    return score


def get_personalised_for_user(user_id, top_n=10):
    """
    The "For You" feed -- scores every not-yet-seen movie and returns the
    top N. Cold start (no history, no feedback, no ratings): top-rated
    movies instead of an empty/arbitrary list.
    """
    affinity = _compute_affinity(user_id)
    if not affinity['has_signal']:
        return get_top_rated(top_n)

    exclude_ids = affinity['exclude_ids']
    scored = [(_score_movie(m, affinity), m) for m in affinity['all_movies']
              if m['movie_id'] not in exclude_ids]
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [m for _, m in scored[:top_n]]

    if len(results) < top_n:
        results += get_top_rated(top_n - len(results), exclude_ids=exclude_ids | {m['movie_id'] for m in results})
    return results


def get_personalized_catalog_order(user_id, movies=None):
    """
    Re-ranks a FULL list of movies by this user's affinity -- unlike
    get_personalised_for_user, nothing is excluded or hidden, every movie
    passed in comes back out, just reordered so movies matching this
    user's taste surface first. Used for the "All Movies" grid's default
    sort so it isn't identical for every user while still showing the
    complete, unfiltered catalog. `movies` defaults to the full catalog;
    pass a pre-filtered subset (e.g. from a genre/search filter) to
    re-rank just that subset instead.

    A brand-new user with no history/feedback/ratings yet still gets a
    stable, sensible order (by each movie's own public average rating,
    via the base term in _score_movie) rather than raw DB/insertion order
    -- it just won't differ from another equally-new user until they
    start interacting, which is expected since there's no signal yet.
    """
    affinity = _compute_affinity(user_id)
    pool = movies if movies is not None else affinity['all_movies']
    # Python's sort is stable, so movies with equal scores keep their
    # original relative order instead of being shuffled on every call.
    return sorted(pool, key=lambda m: _score_movie(m, affinity), reverse=True)

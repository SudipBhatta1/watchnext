from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash

from app.models import movies as movies_repo
from app.models import interactions as interactions_repo
from app.models import users as users_repo
from app import recommender
from app.authz import login_required, RECOVERY_CODE_RE

user_bp = Blueprint('user', __name__, url_prefix='/user')


@user_bp.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    history_ids = interactions_repo.get_watch_history_ids(user_id, limit=50)
    history = [movies_repo.get_movie(mid) for mid in history_ids]
    history = [m for m in history if m]

    for_you = recommender.get_personalised_for_user(user_id, top_n=10)
    fb_counts = interactions_repo.count_user_feedback(user_id)

    genre_counts, dir_counts = {}, {}
    for m in history:
        for g in str(m.get('genre', '')).split('|'):
            g = g.strip()
            if g:
                genre_counts[g] = genre_counts.get(g, 0) + 1
        d = str(m.get('director', '')).strip()
        if d:
            dir_counts[d] = dir_counts.get(d, 0) + 1

    stats = {
        'watched': len(history),
        'fav_genre': max(genre_counts, key=genre_counts.get) if genre_counts else None,
        'fav_director': max(dir_counts, key=dir_counts.get) if dir_counts else None,
        'liked_count': fb_counts['up'],
        'disliked_count': fb_counts['down'],
    }

    return render_template('user_dashboard.html', history=history, for_you=for_you,
                            stats=stats, username=session.get('username'), name=session.get('name'))


@user_bp.route('/history/clear', methods=['POST'])
@login_required
def clear_history():
    interactions_repo.clear_watch_history(session['user_id'])
    flash('Watch history cleared.', 'success')
    return redirect(url_for('user.dashboard'))


@user_bp.route('/rate', methods=['POST'])
@login_required
def rate_movie():
    data = request.get_json(silent=True) or request.form
    movie_id = int(data.get('movie_id', 0))
    try:
        rating = int(data.get('rating', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Rating must be a number.'}), 400
    if not movie_id or not movies_repo.get_movie(movie_id):
        return jsonify({'status': 'error', 'message': 'Movie not found.'}), 404
    if rating < 1 or rating > 5:
        return jsonify({'status': 'error', 'message': 'Rating must be between 1 and 5.'}), 400

    interactions_repo.upsert_rating(session['user_id'], movie_id, rating)
    avg = interactions_repo.get_movie_average_rating(movie_id)
    return jsonify({'status': 'ok', 'average': float(avg['avg_rating'] or 0), 'count': avg['n']})


@user_bp.route('/review', methods=['POST'])
@login_required
def add_review():
    data = request.get_json(silent=True) or request.form
    movie_id = int(data.get('movie_id', 0))
    text = (data.get('review_text') or '').strip()

    if not movie_id or not movies_repo.get_movie(movie_id):
        return jsonify({'status': 'error', 'message': 'Movie not found.'}), 404
    if not text:
        return jsonify({'status': 'error', 'message': 'Review cannot be empty.'}), 400
    if len(text) > 2000:
        return jsonify({'status': 'error', 'message': 'Review is too long (max 2000 characters).'}), 400

    interactions_repo.add_review(session['user_id'], movie_id, text)
    return jsonify({'status': 'ok'})


@user_bp.route('/feedback', methods=['POST'])
@login_required
def feedback():
    data = request.get_json(silent=True) or {}
    movie_id = int(data.get('movie_id', 0))
    vote = data.get('vote', '')
    if not movie_id or vote not in ('up', 'down'):
        return jsonify({'status': 'error'}), 400

    interactions_repo.set_feedback(session['user_id'], movie_id, vote)
    counts = interactions_repo.count_user_feedback(session['user_id'])
    message = ('Your feed now boosts movies like this one.' if vote == 'up'
               else 'Got it. We will show fewer movies like this.')
    return jsonify({'status': 'ok', 'liked_count': counts['up'], 'disliked_count': counts['down'],
                     'message': message})


@user_bp.route('/change_recovery_code', methods=['POST'])
@login_required
def change_recovery_code():
    """Self-service recovery-code change, available to every role (User,
    Moderator, Admin) since they all share the same login/session. Requires
    the current password as confirmation -- changing this is a security-
    sensitive action, same bar as changing a password."""
    current_password = request.form.get('current_password', '')
    new_code = request.form.get('recovery_code', '').strip()
    confirm_code = request.form.get('recovery_code_confirm', '').strip()

    user = users_repo.find_by_id(session['user_id'])
    if not user or not users_repo.verify_password(current_password, user['password_hash']):
        flash('Current password is incorrect.', 'error')
        return redirect(request.referrer or url_for('public.index'))

    if not RECOVERY_CODE_RE.match(new_code):
        flash('Recovery code must be exactly 4 numbers.', 'error')
        return redirect(request.referrer or url_for('public.index'))

    if new_code != confirm_code:
        flash('Recovery codes do not match.', 'error')
        return redirect(request.referrer or url_for('public.index'))

    users_repo.set_recovery_code(session['user_id'], new_code)
    flash('Recovery code updated.', 'success')
    return redirect(request.referrer or url_for('public.index'))


@user_bp.route('/watchlist/toggle', methods=['POST'])
@login_required
def watchlist_toggle():
    """Adds/removes a movie from THIS user's watchlist only -- scoped by
    session['user_id'], never a client-supplied id, so one user can't
    touch another user's list."""
    data = request.get_json(silent=True) or {}
    movie_id = data.get('movie_id')
    if not movie_id:
        return jsonify({'status': 'error', 'message': 'movie_id required'}), 400

    action = interactions_repo.toggle_watchlist(session['user_id'], int(movie_id))
    return jsonify({'status': 'ok', 'action': action})


@user_bp.route('/watchlist')
@login_required
def watchlist_list():
    """This user's saved movies -- used to populate the watchlist drawer
    and to mark 'already saved' state on cards after a page load."""
    items = interactions_repo.get_watchlist(session['user_id'])
    return jsonify({'movies': [
        {'id': m['movie_id'], 'title': m['title'], 'poster_url': m['poster_url'], 'year': m['year']}
        for m in items
    ]})




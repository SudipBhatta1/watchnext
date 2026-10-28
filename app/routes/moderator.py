import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, jsonify, session, current_app)

from app.models import movies as movies_repo
from app.models import interactions as interactions_repo
from app.authz import admin_or_moderator_required
from app import auto_retrain

moderator_bp = Blueprint('moderator', __name__, url_prefix='/moderator')

CURRENT_YEAR = datetime.datetime.now().year


def _validate_movie_form(form):
    errors = []
    title = form.get('title', '').strip()
    year_raw = form.get('year', '').strip()
    genre = form.get('genre', '').strip()
    rating_raw = form.get('rating', '0').strip()
    description = form.get('description', '').strip()

    if not title or len(title) > 255:
        errors.append('Title is required (max 255 characters).')
    if not genre:
        errors.append('At least one genre is required.')
    if not description:
        errors.append('Description is required.')

    year = None
    try:
        year = int(year_raw)
        if year < 1888 or year > CURRENT_YEAR + 1:
            errors.append(f'Year must be between 1888 and {CURRENT_YEAR + 1}.')
            year = None
    except ValueError:
        errors.append('Year must be a number.')

    rating = None
    try:
        rating = float(rating_raw or 0)
        if rating < 0 or rating > 10:
            errors.append('Rating must be between 0 and 10.')
    except ValueError:
        errors.append('Rating must be a number.')

    return errors, {
        'title': title, 'year': year, 'rating': rating, 'description': description,
        'director': form.get('director', '').strip(),
        'cast': form.get('cast', '').strip(),
        'runtime': form.get('runtime', '').strip(),
        'language': form.get('language', '').strip(),
        'poster_url': form.get('poster_url', '').strip(),
        'trailer_id': form.get('trailer_id', '').strip(),
    }, [g.strip() for g in genre.split('|') if g.strip()]


@moderator_bp.route('/dashboard')
@admin_or_moderator_required
def dashboard():
    movies = movies_repo.get_all_movies()
    stats = movies_repo.stats()
    reviews = interactions_repo.get_all_reviews_for_moderation()[:20]
    return render_template('moderator_dashboard.html', movies=movies, stats=stats,
                            reviews=reviews, role=session.get('role'))


@moderator_bp.route('/movie/add', methods=['GET', 'POST'])
@admin_or_moderator_required
def add_movie():
    genres = movies_repo.get_all_genres()
    if request.method == 'POST':
        errors, data, genre_names = _validate_movie_form(request.form)
        if not errors and movies_repo.find_by_title_year(data['title'], data['year']):
            errors.append('A movie with this title and year already exists.')
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('admin_add_movie.html', genres=genres, form=request.form)

        movies_repo.create_movie(data, genre_names, created_by=session['user_id'])
        auto_retrain.trigger_retrain(current_app._get_current_object(),
                                      trained_by=session['user_id'], reason='movie added')
        flash(f"\"{data['title']}\" was added. Recommendations will update shortly "
              f"(model is retraining in the background).", 'success')
        return redirect(url_for('moderator.dashboard'))

    return render_template('admin_add_movie.html', genres=genres, form={})


@moderator_bp.route('/movie/edit/<int:movie_id>', methods=['GET', 'POST'])
@admin_or_moderator_required
def edit_movie(movie_id):
    movie = movies_repo.get_movie(movie_id)
    if not movie:
        flash('Movie not found.', 'error')
        return redirect(url_for('moderator.dashboard'))
    genres = movies_repo.get_all_genres()

    if request.method == 'POST':
        errors, data, genre_names = _validate_movie_form(request.form)
        if not errors and movies_repo.find_by_title_year(data['title'], data['year'], exclude_id=movie_id):
            errors.append('A movie with this title and year already exists.')
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('admin_edit_movie.html', movie=movie, genres=genres, form=request.form)

        movies_repo.update_movie(movie_id, data, genre_names)
        auto_retrain.trigger_retrain(current_app._get_current_object(),
                                      trained_by=session['user_id'], reason='movie edited')
        flash(f"\"{data['title']}\" was updated. Recommendations will update shortly "
              f"(model is retraining in the background).", 'success')
        return redirect(url_for('moderator.dashboard'))

    return render_template('admin_edit_movie.html', movie=movie, genres=genres, form=movie)


@moderator_bp.route('/movie/delete/<int:movie_id>', methods=['POST'])
@admin_or_moderator_required
def delete_movie(movie_id):
    movie = movies_repo.get_movie(movie_id)
    if movie:
        movies_repo.delete_movie(movie_id)
        auto_retrain.trigger_retrain(current_app._get_current_object(),
                                      trained_by=session['user_id'], reason='movie deleted')
        flash(f"\"{movie['title']}\" was deleted. Recommendations will update shortly "
              f"(model is retraining in the background).", 'success')
    return redirect(url_for('moderator.dashboard'))


@moderator_bp.route('/genres')
@admin_or_moderator_required
def genres():
    all_genres = movies_repo.get_all_genres()
    return render_template('moderator_genres.html', genres=all_genres, role=session.get('role'))


@moderator_bp.route('/genres/add', methods=['POST'])
@admin_or_moderator_required
def add_genre():
    name = request.form.get('genre_name', '').strip()
    if not name or len(name) > 50:
        flash('Genre name must be 1-50 characters.', 'error')
    else:
        movies_repo.get_or_create_genre(name)
        flash(f'Genre "{name}" saved.', 'success')
    return redirect(url_for('moderator.genres'))


@moderator_bp.route('/genres/<int:genre_id>/delete', methods=['POST'])
@admin_or_moderator_required
def delete_genre(genre_id):
    from app import db
    db.execute('DELETE FROM genres WHERE genre_id = %s', (genre_id,))
    flash('Genre deleted.', 'success')
    return redirect(url_for('moderator.genres'))


@moderator_bp.route('/reviews')
@admin_or_moderator_required
def reviews():
    all_reviews = interactions_repo.get_all_reviews_for_moderation()
    return render_template('moderator_reviews.html', reviews=all_reviews, role=session.get('role'))


@moderator_bp.route('/reviews/<int:review_id>/hide', methods=['POST'])
@admin_or_moderator_required
def hide_review(review_id):
    interactions_repo.set_review_status(review_id, 'hidden')
    flash('Review hidden.', 'success')
    return redirect(url_for('moderator.reviews'))


@moderator_bp.route('/reviews/<int:review_id>/show', methods=['POST'])
@admin_or_moderator_required
def show_review(review_id):
    interactions_repo.set_review_status(review_id, 'visible')
    flash('Review restored.', 'success')
    return redirect(url_for('moderator.reviews'))


@moderator_bp.route('/reviews/<int:review_id>/delete', methods=['POST'])
@admin_or_moderator_required
def delete_review(review_id):
    interactions_repo.delete_review(review_id)
    flash('Review deleted.', 'success')
    return redirect(url_for('moderator.reviews'))

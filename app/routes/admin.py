from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, session)
import pymysql

from app.models import users as users_repo
from app.models import movies as movies_repo
from app.authz import admin_required

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    role_counts = {r['role_name']: r['total'] for r in users_repo.count_by_role()}
    movie_stats = movies_repo.stats()
    return render_template('admin_dashboard.html', role_counts=role_counts,
                            movie_stats=movie_stats)


@admin_bp.route('/users')
@admin_required
def users():
    q = request.args.get('q', '').strip()
    all_users = users_repo.list_users(search=q or None)
    return render_template('admin_users.html', users=all_users, query=q)


@admin_bp.route('/users/add', methods=['POST'])
@admin_required
def add_user():
    username = request.form.get('username', '').strip()
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    role = request.form.get('role', users_repo.ROLE_USER)

    from app.authz import validate_registration
    errors = validate_registration(username, name, email, password, password)
    if role not in (users_repo.ROLE_ADMIN, users_repo.ROLE_MODERATOR, users_repo.ROLE_USER):
        errors.append('Invalid role.')

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('admin.users'))

    try:
        users_repo.create_user(username, password, name, email, role_name=role)
        flash(f'User "{username}" created as {role}.', 'success')
    except pymysql.err.IntegrityError:
        flash('Username or email already exists.', 'error')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/role', methods=['POST'])
@admin_required
def change_role(user_id):
    new_role = request.form.get('role')
    if new_role not in (users_repo.ROLE_ADMIN, users_repo.ROLE_MODERATOR, users_repo.ROLE_USER):
        flash('Invalid role.', 'error')
        return redirect(url_for('admin.users'))
    if user_id == session['user_id'] and new_role != users_repo.ROLE_ADMIN:
        flash('You cannot remove your own Admin role.', 'error')
        return redirect(url_for('admin.users'))
    users_repo.set_role(user_id, new_role)
    flash('Role updated.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/status', methods=['POST'])
@admin_required
def toggle_status(user_id):
    status = request.form.get('status')
    if status not in ('active', 'inactive'):
        flash('Invalid status.', 'error')
        return redirect(url_for('admin.users'))
    if user_id == session['user_id'] and status == 'inactive':
        flash('You cannot deactivate your own account.', 'error')
        return redirect(url_for('admin.users'))
    users_repo.set_status(user_id, status)
    flash('User status updated.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/reset_password', methods=['POST'])
@admin_required
def reset_password(user_id):
    new_password = request.form.get('password', '')
    if len(new_password) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('admin.users'))
    users_repo.reset_password(user_id, new_password)
    flash('Password reset.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    if user_id == session['user_id']:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin.users'))
    users_repo.delete_user(user_id)
    flash('User deleted.', 'success')
    return redirect(url_for('admin.users'))


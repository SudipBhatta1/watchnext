from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import pymysql

from app.models import users as users_repo
from app.authz import validate_registration, validate_new_password, current_user, USERNAME_RE, EMAIL_RE

auth_bp = Blueprint('auth', __name__)


def _redirect_for_role(role):
    if role == users_repo.ROLE_ADMIN:
        return redirect(url_for('admin.dashboard'))
    if role == users_repo.ROLE_MODERATOR:
        return redirect(url_for('moderator.dashboard'))
    # Regular users land on the WatchNext homepage itself (browse/search/For
    # You feed), not a separate dashboard -- "My List"/history stats are
    # still reachable from the nav, they're just not the landing page.
    return redirect(url_for('public.index'))


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user():
        return _redirect_for_role(session.get('role'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        recovery_code = request.form.get('recovery_code', '').strip()
        recovery_code_confirm = request.form.get('recovery_code_confirm', '').strip()

        errors = validate_registration(username, name, email, password, confirm,
                                        recovery_code, recovery_code_confirm)

        if not errors:
            if users_repo.find_by_username(username):
                errors.append('That username is already taken.')
            if users_repo.find_by_email(email):
                errors.append('An account with that email already exists.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('user_register.html', form=request.form)

        try:
            users_repo.create_user(username, password, name, email, role_name=users_repo.ROLE_USER,
                                    recovery_code=recovery_code)
        except pymysql.err.IntegrityError:
            flash('That username or email is already registered.', 'error')
            return render_template('user_register.html', form=request.form)

        flash('Account created. Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('user_register.html', form={})


@auth_bp.route('/check_availability')
def check_availability():
    """Used by the signup form to show 'already taken' inline, on blur,
    instead of only after the whole form is submitted. Deliberately doesn't
    validate format here (that's what pattern/minlength + server-side
    validate_registration are for) -- this only answers 'is it taken'."""
    field = request.args.get('field', '')
    value = request.args.get('value', '').strip()

    if field == 'username':
        if not value or not USERNAME_RE.match(value):
            return jsonify({'valid_format': False})
        taken = bool(users_repo.find_by_username(value))
        return jsonify({'valid_format': True, 'available': not taken})

    if field == 'email':
        value = value.lower()
        if not value or not EMAIL_RE.match(value):
            return jsonify({'valid_format': False})
        taken = bool(users_repo.find_by_email(value))
        return jsonify({'valid_format': True, 'available': not taken})

    return jsonify({'error': 'unknown field'}), 400


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user():
        return _redirect_for_role(session.get('role'))

    if request.method == 'POST':
        identifier = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = users_repo.find_by_username_or_email(identifier)

        # Checked before verifying the password so a locked-out account never
        # leaks whether the password was actually right. Message is
        # deliberately vague -- no attempt count, no time remaining -- so the
        # lockout window can't be inferred or timed around.
        if user and user['is_locked']:
            flash('Too many attempts. Please try again later.', 'error')
            return render_template('user_login.html')

        if not user or not users_repo.verify_password(password, user['password_hash']):
            if user:
                users_repo.register_failed_attempt(user['user_id'])
            flash('Invalid username/email or password.', 'error')
            return render_template('user_login.html')

        if user['status'] != 'active':
            flash('This account has been deactivated. Contact an administrator.', 'error')
            return render_template('user_login.html')

        users_repo.reset_failed_attempts(user['user_id'])

        session.clear()
        session['user_id'] = user['user_id']
        session['username'] = user['username']
        session['name'] = user['name']
        session['role'] = user['role_name']

        flash(f"Welcome back, {user['name']}!", 'success')
        next_url = request.args.get('next')
        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return _redirect_for_role(user['role_name'])

    return render_template('user_login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('public.index'))


# ---------------------------------------------------------------------------
# Forgot password -- three steps, gated by session state so a person can't
# jump straight to "set new password" without actually verifying the
# recovery code first. Works the same way for every role (User, Moderator,
# Admin) since it's all driven by the same users table.
# ---------------------------------------------------------------------------
@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user():
        return _redirect_for_role(session.get('role'))

    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        user = users_repo.find_by_username_or_email(identifier)

        if not user:
            flash('No account found with that username or email.', 'error')
            return render_template('forgot_password.html')

        if user['is_locked']:
            flash('Too many attempts. Please try again later.', 'error')
            return render_template('forgot_password.html')

        session['recovery_user_id'] = user['user_id']
        session.pop('recovery_verified', None)
        return redirect(url_for('auth.forgot_password_verify'))

    return render_template('forgot_password.html')


@auth_bp.route('/forgot-password/verify', methods=['GET', 'POST'])
def forgot_password_verify():
    if current_user():
        return _redirect_for_role(session.get('role'))

    user_id = session.get('recovery_user_id')
    if not user_id:
        flash('Please start over.', 'error')
        return redirect(url_for('auth.forgot_password'))

    user = users_repo.find_by_id(user_id)
    if not user:
        session.pop('recovery_user_id', None)
        flash('Please start over.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if user['is_locked']:
        session.pop('recovery_user_id', None)
        session.pop('recovery_verified', None)
        flash('Too many attempts. Please try again later.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        code = request.form.get('recovery_code', '').strip()

        if not users_repo.verify_recovery_code(user, code):
            users_repo.register_failed_attempt(user_id)
            flash('Incorrect recovery code.', 'error')
            return render_template('forgot_password_verify.html')

        users_repo.reset_failed_attempts(user_id)
        session['recovery_verified'] = True
        return redirect(url_for('auth.forgot_password_reset'))

    return render_template('forgot_password_verify.html')


@auth_bp.route('/forgot-password/reset', methods=['GET', 'POST'])
def forgot_password_reset():
    if current_user():
        return _redirect_for_role(session.get('role'))

    user_id = session.get('recovery_user_id')
    if not user_id or not session.get('recovery_verified'):
        flash('Please verify your account first.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        errors = validate_new_password(password, confirm)
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('forgot_password_reset.html')

        users_repo.reset_password(user_id, password)
        users_repo.reset_failed_attempts(user_id)
        session.pop('recovery_user_id', None)
        session.pop('recovery_verified', None)

        flash('Password reset. Please log in with your new password.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('forgot_password_reset.html')

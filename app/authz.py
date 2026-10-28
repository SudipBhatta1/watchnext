"""
Backend authorization. Every protected route uses one of these decorators —
buttons being hidden in a template is never treated as access control.
A user hitting a restricted URL directly always gets checked here.
"""
import re
from functools import wraps
from flask import session, redirect, url_for, flash, abort, request

from app.models.users import ROLE_ADMIN, ROLE_MODERATOR, ROLE_USER

USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,30}$')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
RECOVERY_CODE_RE = re.compile(r'^\d{4}$')


def current_user():
    if 'user_id' not in session:
        return None
    return {
        'user_id': session['user_id'],
        'username': session.get('username'),
        'name': session.get('name'),
        'role': session.get('role'),
    }


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return wrapped


def roles_required(*allowed_roles):
    """Reject (403) any user whose role is not in allowed_roles.
    Applying this to a route is what actually stops privilege escalation —
    it runs on every request regardless of how the URL was reached."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in to continue.', 'error')
                return redirect(url_for('auth.login', next=request.path))
            if session.get('role') not in allowed_roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def admin_required(f):
    return roles_required(ROLE_ADMIN)(f)


def moderator_required(f):
    return roles_required(ROLE_MODERATOR)(f)


def admin_or_moderator_required(f):
    return roles_required(ROLE_ADMIN, ROLE_MODERATOR)(f)


def validate_registration(username, name, email, password, confirm_password,
                           recovery_code=None, recovery_code_confirm=None):
    """Returns a list of error strings; empty list means valid.
    recovery_code/recovery_code_confirm are optional so this can still be
    called from contexts (like the admin "add user" form) that don't collect
    a recovery code -- only validated when actually passed in."""
    errors = []
    if not username or not USERNAME_RE.match(username):
        errors.append('Username must be 3-30 characters: letters, numbers, underscore only.')
    if not name or len(name.strip()) < 2 or len(name) > 100:
        errors.append('Name must be between 2 and 100 characters.')
    if not email or not EMAIL_RE.match(email) or len(email) > 120:
        errors.append('Please enter a valid email address.')
    if not password or len(password) < 8:
        errors.append('Password must be at least 8 characters.')
    elif not re.search(r'[A-Za-z]', password) or not re.search(r'[0-9]', password):
        errors.append('Password must contain at least one letter and one number.')
    if password != confirm_password:
        errors.append('Passwords do not match.')
    if recovery_code is not None or recovery_code_confirm is not None:
        if not recovery_code or not RECOVERY_CODE_RE.match(recovery_code):
            errors.append('Recovery code must be exactly 4 numbers.')
        elif recovery_code != recovery_code_confirm:
            errors.append('Recovery codes do not match.')
    return errors


def validate_new_password(password, confirm_password):
    """Same password rules as registration, reused by the forgot-password
    reset step (which doesn't need username/name/email/recovery-code
    checks)."""
    errors = []
    if not password or len(password) < 8:
        errors.append('Password must be at least 8 characters.')
    elif not re.search(r'[A-Za-z]', password) or not re.search(r'[0-9]', password):
        errors.append('Password must contain at least one letter and one number.')
    if password != confirm_password:
        errors.append('Passwords do not match.')
    return errors

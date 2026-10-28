from werkzeug.security import generate_password_hash, check_password_hash
from app import db

ROLE_ADMIN = 'Admin'
ROLE_MODERATOR = 'Moderator'
ROLE_USER = 'User'


def hash_password(plain_password):
    return generate_password_hash(plain_password)


def verify_password(plain_password, password_hash):
    return check_password_hash(password_hash, plain_password)


def get_role_id(role_name):
    row = db.query('SELECT role_id FROM roles WHERE role_name = %s', (role_name,), fetchone=True)
    return row['role_id'] if row else None


def find_by_username(username):
    return db.query(
        """SELECT u.*, r.role_name FROM users u
           JOIN roles r ON r.role_id = u.role_id
           WHERE u.username = %s""",
        (username,), fetchone=True,
    )


def find_by_email(email):
    return db.query('SELECT user_id FROM users WHERE email = %s', (email,), fetchone=True)


def find_by_username_or_email(identifier):
    """Used at login so a person can sign in with either their username or
    email in the same field. Returns the full row + role_name (unlike
    find_by_email, which only returns user_id for uniqueness checks), plus
    a computed is_locked flag so the caller doesn't need a second query."""
    return db.query(
        """SELECT u.*, r.role_name,
                  (u.locked_until IS NOT NULL AND u.locked_until > NOW()) AS is_locked
           FROM users u
           JOIN roles r ON r.role_id = u.role_id
           WHERE u.username = %s OR u.email = %s""",
        (identifier, identifier.lower()), fetchone=True,
    )


# ---------------------------------------------------------------------------
# Rate limiting (login + forgot-password recovery-code entry share the same
# per-account counter and lockout, so guessing across both still counts
# against the same 10 attempts). The lockout duration is intentionally never
# surfaced to the caller/user-facing message -- only whether they're
# currently locked out, so the exact 10-minute window can't be inferred.
# ---------------------------------------------------------------------------
MAX_FAILED_ATTEMPTS = 10
LOCKOUT_MINUTES = 10


def register_failed_attempt(user_id):
    """Increments the failed-attempt counter and locks the account for
    LOCKOUT_MINUTES once MAX_FAILED_ATTEMPTS is reached. Uses MySQL's own
    NOW() + INTERVAL for the lock expiry so it isn't affected by app-server
    clock skew.

    IMPORTANT: locked_until must be computed BEFORE failed_attempts is
    reassigned in this same statement. MySQL evaluates UPDATE SET
    assignments left-to-right, and a later expression that references an
    earlier-assigned column sees the NEW value, not the original -- so if
    failed_attempts were incremented first, the lock condition below would
    effectively read (already-incremented value) + 1, triggering the lock
    one attempt early (at 9 failures instead of 10)."""
    db.execute(
        """UPDATE users
           SET locked_until = CASE
                   WHEN failed_attempts + 1 >= %s THEN NOW() + INTERVAL %s MINUTE
                   ELSE locked_until
               END,
               failed_attempts = failed_attempts + 1
           WHERE user_id = %s""",
        (MAX_FAILED_ATTEMPTS, LOCKOUT_MINUTES, user_id),
    )


def reset_failed_attempts(user_id):
    """Called on any successful login or successful recovery-code entry."""
    db.execute(
        'UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE user_id = %s',
        (user_id,),
    )


def find_by_id(user_id):
    return db.query(
        """SELECT u.*, r.role_name,
                  (u.locked_until IS NOT NULL AND u.locked_until > NOW()) AS is_locked
           FROM users u
           JOIN roles r ON r.role_id = u.role_id
           WHERE u.user_id = %s""",
        (user_id,), fetchone=True,
    )


def create_user(username, password, name, email, role_name=ROLE_USER, status='active', recovery_code=None):
    role_id = get_role_id(role_name)
    if role_id is None:
        raise ValueError(f'Unknown role: {role_name}')
    pw_hash = hash_password(password)
    recovery_hash = hash_password(recovery_code) if recovery_code else None
    user_id, _ = db.execute(
        """INSERT INTO users (username, password_hash, name, email, role_id, status, recovery_code_hash)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (username, pw_hash, name, email, role_id, status, recovery_hash),
    )
    return user_id


def set_recovery_code(user_id, code):
    db.execute('UPDATE users SET recovery_code_hash = %s WHERE user_id = %s',
               (hash_password(code), user_id))


def verify_recovery_code(user, code):
    """Expects a row that includes recovery_code_hash (e.g. from
    find_by_username_or_email). Returns False if no code has been set yet."""
    stored = user.get('recovery_code_hash') if user else None
    if not stored:
        return False
    return check_password_hash(stored, code)


def list_users(search=None):
    if search:
        like = f'%{search}%'
        return db.query(
            """SELECT u.user_id, u.username, u.name, u.email, u.status, u.created_at,
                      r.role_name
               FROM users u JOIN roles r ON r.role_id = u.role_id
               WHERE u.username LIKE %s OR u.name LIKE %s OR u.email LIKE %s
               ORDER BY u.created_at DESC""",
            (like, like, like),
        )
    return db.query(
        """SELECT u.user_id, u.username, u.name, u.email, u.status, u.created_at,
                  r.role_name
           FROM users u JOIN roles r ON r.role_id = u.role_id
           ORDER BY u.created_at DESC"""
    )


def set_role(user_id, role_name):
    role_id = get_role_id(role_name)
    if role_id is None:
        raise ValueError(f'Unknown role: {role_name}')
    db.execute('UPDATE users SET role_id = %s WHERE user_id = %s', (role_id, user_id))


def set_status(user_id, status):
    if status not in ('active', 'inactive'):
        raise ValueError('Invalid status')
    db.execute('UPDATE users SET status = %s WHERE user_id = %s', (status, user_id))


def reset_password(user_id, new_password):
    db.execute('UPDATE users SET password_hash = %s WHERE user_id = %s',
               (hash_password(new_password), user_id))


def delete_user(user_id):
    db.execute('DELETE FROM users WHERE user_id = %s', (user_id,))


def count_by_role():
    return db.query(
        """SELECT r.role_name, COUNT(u.user_id) AS total
           FROM roles r LEFT JOIN users u ON u.role_id = r.role_id
           GROUP BY r.role_name"""
    )

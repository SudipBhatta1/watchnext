import os
from flask import Flask
from dotenv import load_dotenv

load_dotenv()


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get('SECRET_KEY', 'change-me-in-production')
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB upload limit
    app.config['DEBUG'] = os.environ.get('FLASK_DEBUG', '0') == '1'

    # --- Production / hosting settings (all optional, driven by env vars) ---
    if app.secret_key == 'change-me-in-production' and not app.config['DEBUG']:
        app.logger.warning('SECRET_KEY is not set -- sessions are NOT secure. '
                           'Set a long random SECRET_KEY in your hosting dashboard.')
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
    if os.environ.get('BEHIND_PROXY', '0') == '1':
        # Hosts like Render terminate HTTPS at a proxy; trust its forwarded headers.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.config['DB_HOST'] = os.environ.get('DB_HOST', 'localhost')
    app.config['DB_PORT'] = int(os.environ.get('DB_PORT', 3306))
    app.config['DB_NAME'] = os.environ.get('DB_NAME', 'watchnext')
    app.config['DB_USER'] = os.environ.get('DB_USER', 'root')
    app.config['DB_PASSWORD'] = os.environ.get('DB_PASSWORD', '')

    from . import db
    db.init_app(app)

    from .routes.public import public_bp
    from .routes.auth import auth_bp
    from .routes.user import user_bp
    from .routes.moderator import moderator_bp
    from .routes.admin import admin_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(moderator_bp)
    app.register_blueprint(admin_bp)

    # Makes login state available in EVERY template automatically, even ones
    # that don't explicitly pass `username` -- this is what lets base.html
    # expose a reliable window.IS_LOGGED_IN to every page's JS, so buttons
    # that require login (thumbs, watchlist) can check before attempting a
    # request instead of failing silently for guests.
    from flask import session

    @app.context_processor
    def inject_auth_state():
        return {
            'nav_username': session.get('username'),
            'nav_role': session.get('role'),
        }

    # Friendly error handlers — never leak stack traces to the browser.
    from flask import render_template, jsonify, request

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith('/api'):
            return jsonify({'error': 'not found'}), 404
        return render_template('error.html', code=404,
                                message='Page not found.'), 404

    @app.errorhandler(403)
    def forbidden(e):
        if request.path.startswith('/api'):
            return jsonify({'error': 'forbidden'}), 403
        return render_template('error.html', code=403,
                                message='You do not have permission to view this page.'), 403

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception('Unhandled server error')
        if request.path.startswith('/api'):
            return jsonify({'error': 'internal server error'}), 500
        return render_template('error.html', code=500,
                                message='Something went wrong on our end.'), 500

    return app

"""Production entry point:  gunicorn wsgi:app

(Not `app:app` -- the `app/` package shadows `app.py`, so gunicorn can't find it.)
"""
from app import create_app

app = create_app()

"""
app/auto_retrain.py

Fires an automatic model retrain in the background whenever a movie is
added, edited, or deleted (from either the Admin or Moderator side -- they
both go through the same routes in app/routes/moderator.py).

Design:
  - Reuses the exact same train_model.py subprocess pattern as the manual
    "Retrain Model Now" button in admin.py, so training always goes through
    one single, already-validated code path.
  - Runs in a background daemon thread so the person doesn't wait for
    training to finish before their add/edit/delete request returns.
  - A threading.Lock (non-blocking) makes sure two retrains never run at
    once: if a retrain is already in progress when another change comes in,
    the new trigger is skipped (the in-progress run will already pick up
    the latest data since it reads straight from MySQL).
  - Skips entirely if the catalog is below MIN_MOVIES_FOR_TRAINING, since a
    cosine-similarity matrix over 0-1 movies isn't meaningful and would
    just waste a training cycle.
  - Writes model/last_retrain_status.json after every attempt (success,
    failure, or skip) so the admin Model page can show what happened,
    without needing a DB schema change. This is intentionally file-based
    (not in-memory) so the status survives an app restart.
"""
import json
import os
import subprocess
import sys
import threading
import time

MIN_MOVIES_FOR_TRAINING = 5

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_PATH = os.path.join(PROJECT_ROOT, 'model', 'last_retrain_status.json')

_retrain_lock = threading.Lock()


def _write_status(status, message, trained_by=None, reason='change'):
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    payload = {
        'status': status,  # 'success' | 'failed' | 'skipped' | 'in_progress'
        'message': message,
        'trained_by': trained_by,
        'reason': reason,  # what triggered it, e.g. 'movie added', 'movie deleted'
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    try:
        with open(STATUS_PATH, 'w') as f:
            json.dump(payload, f, indent=2)
    except OSError:
        pass  # status display is best-effort; never let this break the request


def get_last_status():
    """Read the last retrain attempt's outcome, or None if never run."""
    try:
        with open(STATUS_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _run_retrain(app, trained_by, reason):
    from app.models import movies as movies_repo
    from app import recommender

    with app.app_context():
        movie_count = movies_repo.stats()['total_movies']
        if movie_count < MIN_MOVIES_FOR_TRAINING:
            _write_status('skipped',
                           f'Skipped: only {movie_count} movie(s) in the catalog '
                           f'(need at least {MIN_MOVIES_FOR_TRAINING}).',
                           trained_by, reason)
            return

        cmd = [sys.executable, os.path.join(PROJECT_ROOT, 'train_model.py')]
        if trained_by:
            cmd += ['--trained-by', str(trained_by)]

        try:
            result = subprocess.run(
                cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=600,
            )
        except subprocess.TimeoutExpired:
            app.logger.error('Auto-retrain timed out (trigger: %s)', reason)
            _write_status('failed', 'Training timed out after 10 minutes.', trained_by, reason)
            return

        if result.returncode != 0:
            app.logger.error('Auto-retrain failed (trigger: %s): %s', reason, result.stderr[-4000:])
            _write_status('failed',
                           'Training failed -- check server logs for details.',
                           trained_by, reason)
            return

        recommender.reload_model()
        _write_status('success', 'Model retrained successfully.', trained_by, reason)


def trigger_retrain(app, trained_by=None, reason='change'):
    """
    Kick off a background retrain. Safe to call from any add/edit/delete
    route. Non-blocking: returns immediately, training happens on a
    daemon thread. If a retrain is already running, this call is a no-op
    (the running one will already reflect the latest data).
    """
    if not _retrain_lock.acquire(blocking=False):
        return  # already retraining; the in-flight run covers this change too

    def _worker():
        try:
            _run_retrain(app, trained_by, reason)
        finally:
            _retrain_lock.release()

    threading.Thread(target=_worker, daemon=True).start()

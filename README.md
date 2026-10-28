# WatchNext — Movie Recommendation System

A movie recommendation system built on **MySQL (via XAMPP) + Flask + a
content-based ML recommender**, with **Admin / Moderator / User** roles,
real backend authorization, and a training pipeline that is fully
separated from the web application.

Movies, users, ratings, reviews, watch history and feedback all live in
MySQL. Setup is two steps, both quick: import `watch.sql` (schema,
roles, and the 3 seed accounts) through phpMyAdmin, then run
`scripts/migrate_data.py` once to load `data/movies.csv` into MySQL. The
movie catalog itself isn't baked into the SQL file — for a dataset that
can grow over time, keeping content in the CSV (with one small, versioned
loader script) scales much better than a multi-megabyte SQL file that
duplicates the same data and gets out of sync every time the CSV changes.


## 1. Technology stack

| Layer          | Technology                                              |
|-----------------|----------------------------------------------------------|
| Backend         | Python 3, Flask                                          |
| Database        | MySQL 8, run through **XAMPP**, managed via **phpMyAdmin** |
| ML              | pandas, numpy, scikit-learn (TF-IDF + cosine similarity, Logistic Regression genre classifier), joblib/pickle |
| Evaluation      | matplotlib, seaborn, scipy (train/test split charts — see §5.1) |
| Auth            | Werkzeug password hashing (scrypt), Flask sessions        |
| Frontend        | HTML, CSS, vanilla JavaScript (server-rendered + a few JSON endpoints) |

---

## 2. Project structure

```
watchnext/
├── app/
│   ├── __init__.py         # Flask app factory, error handlers
│   ├── db.py                # MySQL connection layer (parameterized queries only)
│   ├── authz.py             # login/role decorators + input validation
│   ├── recommender.py       # prediction-time recommendation service
│   ├── auto_retrain.py      # background auto-retrain trigger (see §5.2)
│   ├── models/               # repository layer (one module per entity)
│   │   ├── users.py           # includes recovery-code + rate-limit helpers
│   │   ├── movies.py
│   │   ├── interactions.py  # ratings, reviews, watch history, feedback
│   │   └── model_metadata.py
│   ├── routes/                # Flask blueprints
│   │   ├── auth.py            # register / login (username or email) / logout /
│   │   │                        forgot-password (3-step recovery-code flow)
│   │   ├── public.py          # browse, movie detail, search, /recommend
│   │   ├── user.py            # dashboard, rate, review, feedback, history,
│   │   │                        change recovery code
│   │   ├── moderator.py       # movie/genre CRUD, review moderation
│   │   └── admin.py           # user & role management, stats
│   ├── templates/             # Jinja2 templates
│   └── static/                # CSS/JS/images
├── data/
│   └── movies.csv             # raw source dataset -- the single source of truth for
│                                # the movie catalog; loaded into MySQL by migrate_data.py
├── model/                     # trained model artifacts (generated, not hand-written)
│   ├── tfidf_vectorizer.pkl
│   ├── cosine_sim.pkl
│   ├── movie_ids.pkl
│   ├── metadata.json
│   ├── last_retrain_status.json   # outcome of the most recent auto-retrain
│   └── charts/                     # evaluation charts (Figure1-10 PNGs, see §5.1)
├── scripts/
│   └── migrate_data.py        # REQUIRED once, after importing watch.sql and before
│                                # train_model.py: loads data/movies.csv into MySQL
│                                # (movies/genres/movie_genres). Also (re)sets the seed
│                                # account passwords -- safe to re-run any time the CSV changes.
│                                # Bad rows (blank title, invalid year, duplicates) are
│                                # skipped and logged instead of crashing the whole import.
├── model_evaluation.py        # train/test split + genre classifier + chart generation
│                                # (called by train_model.py; diagnostic only, never
│                                # affects the production recommender)
├── train_model.py             # the ONLY place the model is trained
├── app.py                     # entrypoint (python app.py)
├── watch.sql                   # ONE schema file: all tables/constraints, the 3 roles,
│                                # and the 3 seed accounts (real password hashes, default
│                                # recovery code 1234). Deliberately does NOT contain the
│                                # movie catalog -- see scripts/migrate_data.py.
│                                # Idempotent: safe to re-import even over a partially-set-up DB.
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## 3. Database schema (MySQL, `watchnext` database)

| Table            | Purpose                                                | Key constraints |
|-------------------|---------------------------------------------------------|------------------|
| `roles`           | Admin / Moderator / User                                | PK role_id, UNIQUE role_name |
| `users`           | Accounts (password hash, recovery-code hash, rate-limit state) | PK user_id, UNIQUE username/email, FK role_id → roles |
| `movies`          | Movie catalog                                           | PK movie_id, UNIQUE(title, year), CHECK year 1888-2100 / rating 0-10, FULLTEXT index for search |
| `genres`          | Genre list                                              | PK genre_id, UNIQUE genre_name |
| `movie_genres`    | Many-to-many movies ↔ genres                             | Composite PK, FKs cascade on delete |
| `ratings`         | 1 rating per user per movie (1-5 stars)                  | UNIQUE(user_id, movie_id), CHECK 1-5 |
| `reviews`         | Free-text reviews, moderatable                           | status ENUM('visible','hidden') |
| `watch_history`   | Every time a user opens a movie's detail page             | FK cascade delete |
| `feedback`        | Thumbs up/down signal used for personalization             | UNIQUE(user_id, movie_id) |
| `model_metadata`  | Audit trail of every training run (NOT the model binary)  | JSON columns for parameters/evaluation |

`users` also carries `recovery_code_hash` (hashed the same way as the
password, used by Forgot Password — see §4.1), plus `failed_attempts` and
`locked_until` (shared rate-limit state for both login and recovery-code
entry — see §4.2).

Relationships: `users.role_id → roles.role_id`, `movies.created_by → users.user_id`,
`movie_genres` resolves the movies↔genres many-to-many, `ratings`/`reviews`/
`watch_history`/`feedback` all reference `users` and `movies` with
`ON DELETE CASCADE` so removing a user or movie cleans up their
interactions. The schema targets 3NF: no repeating groups, genres are
normalized out of `movies` into their own table, and derived values
(average rating, trending) are computed with SQL aggregates rather than
stored redundantly.

Import `watch.sql` through phpMyAdmin, or from the command line:

```bash
mysql -u root -p < watch.sql
```

**If you use phpMyAdmin's SQL tab and see `#1050 - Table 'roles' already exists`:**
this means a table with that name already existed in the target database (e.g. from an
earlier partial import) and the file's own cleanup didn't reach it in time. The current
`watch.sql` explicitly drops every table before creating it, so this is safe to
re-run as many times as needed — just re-import the same file again and it will succeed.
Easiest path: phpMyAdmin's top-level **Import** tab (not the SQL query box) → Choose File
→ select `watch.sql` → Go.

---

## 4. Roles & permissions

| Capability                         | Admin | Moderator | User |
|--------------------------------------|:---:|:---:|:---:|
| Browse / search / rate / review movies | ✅ | ✅ | ✅ |
| Personalized recommendations          | ✅ | ✅ | ✅ |
| Add / edit / delete movies             | ✅ | ✅ | ❌ |
| Manage genres                          | ✅ | ✅ | ❌ |
| Hide / restore / delete reviews         | ✅ | ✅ | ❌ |
| Manage users (create, change role, activate/deactivate, delete, reset password) | ✅ | ❌ | ❌ |
| View system statistics                 | ✅ | ❌ | ❌ |

All of the above is enforced **server-side** with `@login_required`,
`@admin_required`, `@moderator_required`, and `@admin_or_moderator_required`
decorators in `app/authz.py` — hitting a restricted URL directly returns
`403`, regardless of what the UI shows. A Moderator can never grant
themselves (or anyone) the Admin role: `admin.change_role` is behind
`@admin_required`, and the endpoint additionally blocks an Admin from
demoting themselves out of the Admin role.

**After logging in:** Admin and Moderator land on their operational
dashboards (they need those tools immediately). A regular User lands on
the WatchNext **homepage** itself — browse/search/"For You" feed — not a
separate dashboard; "My Dashboard" (history/stats) is still one click
away in the nav whenever they want it.

### 4.1 Login and account recovery

Login accepts either a **username or email**, in the same field, checked
against both columns (`users.find_by_username_or_email`). This applies
to every role — Admin and Moderator log in through the exact same form
as a regular User.

Every account (including the 3 seed accounts) has a **4-digit recovery
code**, hashed the same way as the password (never stored in plaintext).
Regular users set their own at signup; the seed Admin/Moderator/User
accounts default to `1234`, changeable from their own dashboard's
"Change Recovery Code" section (requires the current password to
confirm — same bar as changing a password).

**Forgot password** is a 3-step, session-gated flow reachable from a link
on the login page, for every role:

1. `/forgot-password` — enter username or email.
2. `/forgot-password/verify` — enter the 4-digit recovery code.
3. `/forgot-password/reset` — set a new password + confirm (with
   show/hide "eye" buttons), only reachable after step 2 succeeds.

There is no email sending involved — this is a self-service flow gated
entirely by the recovery code, not a mailed reset link (see §8 for the
trade-off this implies).

### 4.2 Rate limiting

Both login and recovery-code entry share the same **per-account** limit:
**10 failed attempts** locks that account for **10 minutes**
(`users.register_failed_attempt` / `reset_failed_attempts`, backed by the
`failed_attempts`/`locked_until` columns and MySQL's own `NOW()`, so it
isn't affected by app-server clock skew). Guessing a password and
guessing a recovery code count against the *same* 10 — they're not
separate pools.

The lockout is scoped strictly to that one account: another user,
Moderator, or Admin can log in normally at the same time even if one
account is currently locked out. The message shown is deliberately vague
— *"Too many attempts. Please try again later."* — with no attempt count
or time remaining, so the 10-minute window can't be inferred or timed
around. The lock is also checked *before* the password/code is verified,
so a locked-out account never leaks whether the guess was actually
correct.

---

## 5. Machine learning pipeline

**Algorithm:** content-based filtering — TF-IDF over a combined text
feature (genre, director, cast, title, description, with genre and
director up-weighted) + cosine similarity between every pair of movies.

```
MySQL (movies + genres)
      │
      ▼
Data validation & cleaning   (train_model.py: drop blank titles, dedupe)
      │
      ▼
Train/test-split evaluation  (model_evaluation.py -- diagnostic only, see §5.1)
      │
      ▼
Feature engineering          (weighted text blob per movie, on the FULL catalog)
      │
      ▼
TF-IDF vectorization          (scikit-learn TfidfVectorizer)
      │
      ▼
Cosine similarity matrix      (scikit-learn cosine_similarity)
      │
      ▼
Saved to /model               (cosine_sim.pkl, movie_ids.pkl, tfidf_vectorizer.pkl)
      │
      ▼
model_metadata row in MySQL   (audit trail: who trained it, when, on how
                                many movies, with evaluation numbers + chart list)
      │
      ▼
app/recommender.py loads the artifacts at request time and combines them
with each user's live MySQL data (watch history + thumbs up/down) to
produce:
  • get_similar_movies()        "more like this" on a movie's page
  • get_personalised_for_user() the "For You" homepage feed
  • get_top_rated() / get_trending()   cold-start fallback
```

**Training vs. serving are fully separate.** `train_model.py` is a
standalone script — it is never imported or run inside a web request. The
web app only ever *reads* the `.pkl` files it produces. The **production
model is always trained on 100% of the catalog** — the 80/20 split
described below exists purely to measure quality, never to decide what
data the live recommender is built from.

### 5.1 Evaluation: train/test split, genre classifier & charts

Every time the model trains, `model_evaluation.py` also runs an **80/20
train/test split** to measure how good the TF-IDF/cosine-similarity
recommender actually is (do a movie's top-6 "similar" picks really share
a genre, compared against a random-guess baseline?), and trains a
separate **Logistic Regression genre classifier** (predicts a movie's
primary genre from its title + description) on a proper **70/10/20
train/validation/test split**, plus **5-fold cross-validation**.

This produces 9 charts, saved to `model/charts/` on disk. **Note:** the
admin "Recommendation Model" page that used to display these in the UI
has been removed (retraining is fully automatic — see §5.2 — so a manual
trigger/viewer page was redundant); the charts and `model_metadata` audit
rows are still generated on every train, just not currently surfaced
anywhere in the app itself. Open the PNGs directly from `model/charts/`
if you want to inspect them.

| Chart | Shows |
|---|---|
| Figure 1 | Dataset overview: genre/rating/runtime/decade distributions, split sizes |
| Figure 3 | Training vs. validation accuracy curve |
| Figure 4 | Training vs. validation loss curve |
| Figure 5 | Confusion matrix (raw + normalized) |
| Figure 6 | Precision / recall / F1 per genre |
| Figure 7 | 5-fold cross-validation results |
| Figure 8 | Precision-recall curve + AUC per genre |
| Figure 9 | Pearson correlation: rating/year/runtime vs. similarity score |
| Figure 10 | TF-IDF model accuracy vs. classifier vs. random baseline |

This is entirely **diagnostic** — if the catalog is too small for a
reliable split (e.g. under ~5 movies per genre), evaluation is skipped
gracefully and logged; it never blocks or changes the production model.

### 5.2 Automatic retraining

The model retrains **automatically in the background** whenever a movie
is added, edited, or deleted — by an Admin or a Moderator (they go
through the same routes). This is non-blocking: the request that
triggered it returns immediately, training happens on a background
thread, and `app/auto_retrain.py`:

- Skips retraining if the catalog has fewer than 5 movies (too small to
  be meaningful).
- Uses a lock so two retrains never run at once — if one is already in
  progress, a new trigger is a no-op (the in-flight run already covers
  the latest change).
- Writes the outcome (success / failed / skipped) to
  `model/last_retrain_status.json`. This was previously shown as a banner
  on the admin Model page; since that page was removed (see the note in
  §5.1), the status file is still written for anyone who wants to inspect
  it directly, but nothing in the UI currently displays it.

You can still trigger a retrain manually any time from the command line
with `python train_model.py` — it runs the exact same code path as the
automatic trigger, just invoked by hand instead of by a movie change.

**What actually gets learned from user feedback:** ratings, reviews,
watch history and thumbs up/down are all stored in MySQL as soon as a
user acts. The "For You" feed reads this live data on every request and
re-scores every movie against the user's genre/director history — so
personalization updates immediately, without retraining. The TF-IDF/
cosine-similarity model itself (the item-to-item similarity used by
"more like this") only changes when it retrains (automatically or
manually) on the current movie catalog; it does not "learn" from ratings
directly.

**Cold start:** a user with no watch history and no feedback gets
`get_top_rated()` — the highest-rated movies in the catalog — instead of
an empty page or an error.

---

## 6. Environment setup

### 6.1 XAMPP / MySQL

1. Install XAMPP, start the **MySQL** module (not MariaDB — this project targets MySQL 8/`mysql_native_password`/`caching_sha2_password` behavior as shipped with XAMPP).
2. Open **phpMyAdmin** → Import → choose `watch.sql` → Go.
   This creates the `watchnext` database with all tables and constraints, the
   three role rows (Admin/Moderator/User), and the three seed accounts
   with real, working password hashes (and a default recovery code of
   `1234` — see §4.1). `watch.sql` is a small schema file (~12 KB) — it
   does not contain the movie catalog itself; that comes from
   `data/movies.csv` in the next step.

### 6.2 Python environment

```bash
cd watchnext
python -m venv venv

# activate it
source venv/bin/activate        # macOS/Linux
venv\Scripts\activate            # Windows

pip install -r requirements.txt
```

`requirements.txt` uses version *ranges* (e.g. `numpy>=1.26.4,<2.0`)
rather than exact pins, so pip picks whichever compatible build has a
prebuilt wheel for your installed Python version. If you ever hit a
"no matching distribution" or wheel-build error, the fix is almost always
to raise the relevant upper bound in `requirements.txt` by one
minor/major version and re-run `pip install -r requirements.txt`.

### 6.3 Configure environment variables

```bash
cp .env.example .env
# edit .env if your MySQL credentials differ from the XAMPP defaults
# (host=localhost, port=3306, user=root, password=)
```

### 6.4 Load the movie catalog and train the model

```bash
python scripts/migrate_data.py   # loads data/movies.csv into MySQL (movies,
                                   # genres, movie_genres) and confirms the
                                   # seed account passwords. Bad rows (blank
                                   # title, invalid year, duplicates) are
                                   # skipped and logged, not fatal.
python train_model.py             # builds the TF-IDF + cosine-similarity model
                                    # from the movies now in MySQL, runs the
                                    # train/test-split evaluation, and saves
                                    # charts to model/charts/
```

The seed accounts (created by `watch.sql`, ready to log in with immediately):

| Role      | Username    | Password       | Recovery Code |
|------------|-------------|----------------|----------------|
| Admin      | `admin`      | `Admin@123`      | `1234` |
| Moderator  | `moderator`  | `Moderator@123`  | `1234` |
| User       | `ram`        | `User@123`       | `1234` |

**Change these after first login** — passwords via Admin → Users → Reset
Password (or your own account settings), and the recovery code via the
"Change Recovery Code" section on your own dashboard (see §4.1). You can
log in with either the username or the email shown in `watch.sql`
(`admin@watch.local`, etc.) — see §4.1.

### 6.5 Run the application

```bash
python app.py
```

Visit `http://localhost:5000`. From here, adding/editing/deleting a movie
as Admin or Moderator will automatically kick off a background retrain —
you don't need to re-run `train_model.py` by hand after every catalog
change, only for the very first build in §6.4.

---

## 7. Security

- **Authentication:** passwords are hashed with Werkzeug's `generate_password_hash` (scrypt), never stored or compared as plaintext. The forgot-password recovery code is hashed the exact same way (§4.1) — it is a second credential, not a plaintext-stored hint.
- **Authorization:** every protected route is checked server-side (see §4); RBAC is not a frontend-only concept.
- **SQL injection:** every single query in the codebase goes through `app/db.py`'s `query()`/`execute()` helpers, which use PyMySQL's `%s` parameter substitution — there is no string-concatenated SQL anywhere in the project.
- **Input validation:** registration/login validate username format, email format, password strength (8+ chars, letter+number), recovery-code format (exactly 4 digits), and matching confirmation (`app/authz.py::validate_registration`, `validate_new_password`); movie forms validate year range, rating range, and required fields (`app/routes/moderator.py::_validate_movie_form`); star ratings are range-checked 1-5 server-side even though the UI already constrains them.
- **Rate limiting:** 10 failed attempts (login or recovery-code entry, combined) locks that one account for 10 minutes, checked before password/code verification so a locked account never leaks whether a guess was correct — see §4.2.
- **IDOR protection:** rating/review/feedback/history writes always use `session['user_id']`, never a client-supplied id — a user cannot edit another user's rating or review.
- **Error handling:** 404/403/500 render a generic error page; stack traces are never shown to the browser (Flask debug mode is for local development only — turn `FLASK_DEBUG=0` off for anything resembling production use).
- **Model failure handling:** if `/model/*.pkl` files are missing, `app/recommender.py` degrades to top-rated-movie fallbacks instead of crashing the app. A failed background auto-retrain is logged to `model/last_retrain_status.json` rather than silently swallowed (see the note in §5.1 about this no longer being shown in the UI).

---

## 8. Known limitations

- **No automated test suite.** There's a `tests/` directory placeholder in the project layout, but nothing is populated yet — testing so far has been manual (direct route hits and scripted checks against a live MySQL instance).
- **Bulk CSV upload for movies** (present in an earlier single-file version of this project) was intentionally dropped in favor of a validated single-movie form in the UI; `data/movies.csv` is still the source of the *initial* catalog, loaded once via `python scripts/migrate_data.py` as part of setup (see §6.4).
- **Forgot password is recovery-code-based, not email-based** — there's no SMTP/email sending in this project, so the self-service reset flow (§4.1) is gated by a 4-digit code set at signup rather than a mailed reset link. This is weaker than a real emailed token (a code is easier to guess/observe than a random one-time link) but is mitigated by the shared 10-attempt/10-minute rate limit (§4.2). Admin can still directly reset any user's password from the Users page regardless.
- **Genre classifier accuracy is modest (~40% on this dataset)** — genre is inherently fuzzy to predict from text alone; this is reported honestly in the evaluation charts (§5.1) rather than hidden, and it does not affect the actual recommender, which scores far higher (~95%) on its own, easier "shares a genre" holdout metric.
- **Session security** uses Flask's signed-cookie sessions with the `SECRET_KEY` from `.env`; for a real deployment you'd want HTTPS-only cookies (`SESSION_COOKIE_SECURE=True`) and a production WSGI server instead of the Flask dev server used here.

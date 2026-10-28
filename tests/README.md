No automated test suite is included yet (see README.md section 10,
"Known limitations"). All testing to date was manual, via curl against a
running instance — see README.md section 8 for exactly what was checked.

Recommended next step: add pytest tests here using Flask's test client
against a disposable MySQL test database, covering:
  - RBAC (login_required / admin_required / moderator_required)
  - validation (registration, movie forms, rating range)
  - IDOR protection (users cannot edit each other's ratings/reviews)
  - recommender.get_personalised_for_user() cold-start vs. history-based

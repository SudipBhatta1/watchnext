# Hosting WatchNext for free (Render + Aiven)

* **Database:** Aiven free MySQL (always free, no credit card, 1 GB).
* **App:** Render free web service (sleeps after 15 min idle; first visit after that takes ~1 min).

## 1. Create the free MySQL database (Aiven)
1. Sign up at https://aiven.io -> create a service -> **MySQL** -> choose the **Free** plan -> Create.
2. Wait until it shows **Running**, then open the service page and copy: **Host, Port, User, Password, Database name**.

## 2. Load your data into it (from your own computer, once)
```bash
cd watchnext
python -m venv venv && venv\Scripts\activate      # Windows   (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env                               # Windows: copy .env.example .env
```
Edit `.env`: put the Aiven host, port, user and password in `DB_HOST / DB_PORT / DB_USER / DB_PASSWORD`, set `DB_NAME=watchnext` (the script creates it for you), and add `DB_SSL=1`. Then:
```bash
python scripts/setup_remote_db.py
```
This creates the tables, loads all movies, and prints **new random passwords** for admin / moderator / ram.
**Save them** -- they are shown only once. (To start over: `python scripts/setup_remote_db.py --force`.)

## 3. Push the code to GitHub
Replace the files in your repo with this folder (keep `.gitignore` so `.env` is never uploaded), commit, push.

## 4. Deploy on Render
1. https://render.com -> sign up with GitHub -> **New +** -> **Blueprint** -> pick your repo (it reads `render.yaml`).
2. When asked, enter `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` = `watchnext` (same values as your `.env`).
3. Deploy. The build installs packages and trains the recommender from your database automatically.
4. Open the `https://watchnext-xxxx.onrender.com` link. Check `/healthz` shows `{"status":"ok"}`.

## 5. Keep it awake (optional)
Free https://uptimerobot.com monitor -> HTTP(s) -> your `/healthz` URL -> every 5 minutes.
This avoids the 1-minute cold start and also counts as activity for the free Aiven database.

## Good to know
* Adding/editing movies as admin still triggers a background retrain, but the free server's disk is wiped on restart.
  To permanently refresh recommendations after catalog changes, press **Manual Deploy** on Render (it retrains from the DB).
* The evaluation charts are skipped on the server (`SKIP_EVALUATION=1`). Run `pip install -r requirements-dev.txt` and `python train_model.py` locally to get them.
* Locally with XAMPP nothing changes: leave `DB_SSL` unset.

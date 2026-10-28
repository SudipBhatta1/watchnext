import argparse
import json
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv()

from app.dbconfig import connect_kwargs

ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(ROOT, 'model')

DB_CONFIG = dict(cursorclass=pymysql.cursors.DictCursor, **connect_kwargs())


def log(msg):
    print(f'[train_model] {msg}', flush=True)


# --------------------------------------------------------------------------- #
# 1. LOAD
# --------------------------------------------------------------------------- #
def load_movies_from_db():
    try:
        conn = pymysql.connect(**DB_CONFIG)
    except pymysql.err.OperationalError as e:
        log(f'ERROR: could not connect to MySQL: {e}')
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT m.movie_id, m.title, m.description, m.year, m.rating,
                       m.director, m.cast_list, m.runtime, m.language
                FROM movies m
            """)
            movies = cur.fetchall()
            cur.execute("""
                SELECT mg.movie_id, g.genre_name
                FROM movie_genres mg JOIN genres g ON g.genre_id = mg.genre_id
            """)
            genre_rows = cur.fetchall()
    finally:
        conn.close()

    genres_by_movie = {}
    for r in genre_rows:
        genres_by_movie.setdefault(r['movie_id'], []).append(r['genre_name'])
    for m in movies:
        m['genre'] = '|'.join(genres_by_movie.get(m['movie_id'], []))

    return pd.DataFrame(movies)


# --------------------------------------------------------------------------- #
# 2. VALIDATE + CLEAN
# --------------------------------------------------------------------------- #
def validate_and_clean(df):
    if df.empty:
        log('ERROR: no movies found in the database. Import watch.sql and run '
            'scripts/migrate_data.py before training.')
        sys.exit(1)

    required_cols = {'movie_id', 'title', 'genre', 'description', 'director', 'cast_list'}
    missing = required_cols - set(df.columns)
    if missing:
        log(f'ERROR: missing required columns: {missing}')
        sys.exit(1)

    before = len(df)
    df = df.drop_duplicates(subset=['movie_id']).copy()
    df = df[df['title'].notna() & (df['title'].str.strip() != '')]

    for col in ('genre', 'description', 'director', 'cast_list', 'title'):
        df[col] = df[col].fillna('').astype(str)
    df['rating'] = pd.to_numeric(df['rating'], errors='coerce').fillna(0.0)
    df['year'] = pd.to_numeric(df['year'], errors='coerce').fillna(0).astype(int)

    dropped = before - len(df)
    if dropped:
        log(f'Cleaned data: dropped {dropped} row(s) (duplicates / missing title).')

    df = df.reset_index(drop=True)
    log(f'Loaded {len(df)} valid movies for training.')
    return df


# --------------------------------------------------------------------------- #
# 3. FEATURE ENGINEERING
# --------------------------------------------------------------------------- #
def build_text_features(df):
    def row_text(row):
        genre_txt = row['genre'].replace('|', ' ') * 3          # weight genre highest
        cast_txt = row['cast_list'].replace('|', ' ')
        director_txt = (row['director'] + ' ') * 2               # weight director
        return f"{genre_txt} {director_txt} {cast_txt} {row['title']} {row['description']}"

    return df.apply(row_text, axis=1)


# --------------------------------------------------------------------------- #
# 4. TRAIN
# --------------------------------------------------------------------------- #
def train_tfidf_cosine(text_features):
    vectorizer = TfidfVectorizer(stop_words='english', max_features=20000, min_df=1)
    tfidf_matrix = vectorizer.fit_transform(text_features)
    cosine_sim = cosine_similarity(tfidf_matrix, tfidf_matrix)
    return vectorizer, tfidf_matrix, cosine_sim


# --------------------------------------------------------------------------- #
# 5. EVALUATE
# --------------------------------------------------------------------------- #
def evaluate(cosine_sim, tfidf_matrix):
    n = cosine_sim.shape[0]
    off_diag = cosine_sim[~np.eye(n, dtype=bool)]
    nearest = np.sort(cosine_sim, axis=1)[:, -2]  # best match excluding self
    return {
        'num_items': int(n),
        'tfidf_vocabulary_size': int(tfidf_matrix.shape[1]),
        'tfidf_sparsity_pct': round(100 * (1 - tfidf_matrix.nnz / (tfidf_matrix.shape[0] * tfidf_matrix.shape[1])), 2),
        'mean_pairwise_similarity': round(float(off_diag.mean()), 4),
        'mean_best_match_similarity': round(float(nearest.mean()), 4),
        'median_best_match_similarity': round(float(np.median(nearest)), 4),
    }


# --------------------------------------------------------------------------- #
# 6. SAVE
# --------------------------------------------------------------------------- #
def save_artifacts(vectorizer, cosine_sim, movie_ids, metadata):
    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(os.path.join(MODEL_DIR, 'tfidf_vectorizer.pkl'), 'wb') as f:
        pickle.dump(vectorizer, f)
    with open(os.path.join(MODEL_DIR, 'cosine_sim.pkl'), 'wb') as f:
        pickle.dump(cosine_sim.astype(np.float32), f)  # float32 halves the file/RAM size
    with open(os.path.join(MODEL_DIR, 'movie_ids.pkl'), 'wb') as f:
        pickle.dump(movie_ids, f)
    with open(os.path.join(MODEL_DIR, 'metadata.json'), 'w') as f:
        json.dump(metadata, f, indent=2, default=str)
    log(f'Saved model artifacts to {MODEL_DIR}/')


def record_metadata_in_db(metadata, trained_by):
    try:
        conn = pymysql.connect(**DB_CONFIG)
    except pymysql.err.OperationalError as e:
        log(f'WARNING: could not record training run in MySQL: {e}')
        return
    try:
        with conn.cursor() as cur:
            cur.execute('UPDATE model_metadata SET is_active = FALSE')
            cur.execute(
                """INSERT INTO model_metadata
                       (model_type, trained_by, num_movies, num_features, parameters,
                        evaluation, artifact_path, is_active)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE)""",
                ('Content-Based (TF-IDF + Cosine Similarity)', trained_by,
                 metadata['evaluation']['num_items'],
                 metadata['evaluation']['tfidf_vocabulary_size'],
                 json.dumps(metadata['parameters']),
                 json.dumps(metadata['evaluation']),
                 'model/cosine_sim.pkl'),
            )
        conn.commit()
        log('Recorded training run in model_metadata table.')
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description='Train the WatchNext recommendation model.')
    parser.add_argument('--trained-by', type=int, default=None,
                         help='user_id of the admin who triggered this run (optional).')
    args = parser.parse_args()

    start = time.time()
    log('Starting training pipeline...')

    df = load_movies_from_db()
    df = validate_and_clean(df)

    eval_summary = {}
    if os.environ.get('SKIP_EVALUATION', '0') == '1':
        # Used on small free hosts: the evaluation needs matplotlib/scipy plus a lot
        # of RAM/CPU, and it is diagnostic only. The production model is unaffected.
        log('SKIP_EVALUATION=1 -> skipping train/test-split evaluation and charts.')
    else:
        log('Running train/test-split evaluation (genre classifier + charts)...')
        try:
            import model_evaluation  # lazy import: needs matplotlib/seaborn/scipy
            eval_summary = model_evaluation.run_full_evaluation(df)
        except Exception as e:
            # Evaluation/charts are diagnostic only -- must never block the
            # production model (used live by the app) from training and saving.
            log(f'WARNING: evaluation pipeline raised an unexpected error, skipping charts: {e}')
            eval_summary = {}
        if eval_summary:
            log(f'Evaluation summary: {eval_summary}')
        else:
            log('Evaluation produced no charts (likely too few movies/genres for a reliable split).')

    text_features = build_text_features(df)
    vectorizer, tfidf_matrix, cosine_sim = train_tfidf_cosine(text_features)
    evaluation = evaluate(cosine_sim, tfidf_matrix)
    evaluation.update(eval_summary)

    metadata = {
        'model_type': 'Content-Based Filtering (TF-IDF + Cosine Similarity)',
        'trained_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'parameters': {'stop_words': 'english', 'max_features': 20000, 'min_df': 1,
                        'feature_weights': 'genre x3, director x2, cast x1, title x1, description x1'},
        'evaluation': evaluation,
    }

    movie_ids = df['movie_id'].tolist()
    save_artifacts(vectorizer, cosine_sim, movie_ids, metadata)
    record_metadata_in_db(metadata, args.trained_by)

    elapsed = round(time.time() - start, 2)
    log(f'Training complete in {elapsed}s. Movies: {evaluation["num_items"]}, '
        f'vocabulary: {evaluation["tfidf_vocabulary_size"]}, '
        f'mean best-match similarity: {evaluation["mean_best_match_similarity"]}.')
    log('SUCCESS')


if __name__ == '__main__':
    main()

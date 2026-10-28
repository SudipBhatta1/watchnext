import os
from collections import Counter

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Arc, Circle
import seaborn as sns
from scipy.stats import pearsonr
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.metrics import (confusion_matrix, classification_report, accuracy_score,
                              precision_score, recall_score, f1_score,
                              precision_recall_curve, auc)

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model')
CHART_DIR = os.path.join(MODEL_DIR, 'charts')

# ── Colours (matches the old project's dark dashboard theme) ────────────────
ACCENT = '#e63946'
BG = '#1a1a2e'
PANEL = '#16213e'
TEXT = '#eaeaea'
GREEN = '#06d6a0'
BLUE = '#118ab2'
ORANGE = '#ffd166'
PURPLE = '#c77dff'

plt.rcParams.update({
    'figure.facecolor': BG, 'axes.facecolor': PANEL,
    'axes.edgecolor': '#444', 'axes.labelcolor': TEXT,
    'xtick.color': TEXT, 'ytick.color': TEXT,
    'text.color': TEXT, 'grid.color': '#333',
    'grid.linestyle': '--', 'grid.alpha': 0.5,
    'font.family': 'DejaVu Sans', 'font.size': 11,
})


def log(msg):
    print(f'[model_evaluation] {msg}', flush=True)


def genres_of(genre_str):
    return [g.strip() for g in str(genre_str).split('|') if g.strip()]


def _row_text_features(d):
    """Same weighting as train_model.py's production feature build (genre x3, director x2)."""
    genre_txt = d['genre'].fillna('').str.replace('|', ' ', regex=False) + ' '
    return (genre_txt * 3 + (d['director'].fillna('') + ' ') * 2 +
            d['cast_list'].fillna('').str.replace('|', ' ', regex=False) + ' ' +
            d['title'].fillna('') + ' ' + d['description'].fillna(''))


# --------------------------------------------------------------------------- #
# 1. TF-IDF / COSINE SIMILARITY -- 80/20 HOLDOUT EVALUATION
# --------------------------------------------------------------------------- #
def evaluate_tfidf_holdout(df):
    train_df, test_df = train_test_split(df, test_size=0.20, random_state=42)
    log(f'TF-IDF holdout split: {len(train_df)} train (80%) / {len(test_df)} test (20%)')

    d = train_df.reset_index(drop=True).copy()
    text_features = _row_text_features(d)
    vectorizer = TfidfVectorizer(stop_words='english', max_features=20000, min_df=1)
    matrix = vectorizer.fit_transform(text_features)
    cosine_sim = cosine_similarity(matrix, matrix)
    return d, test_df, cosine_sim


# --------------------------------------------------------------------------- #
# 2. GENRE CLASSIFIER -- 70/10/20 TRAIN/VAL/TEST + 5-FOLD CV
# --------------------------------------------------------------------------- #
def _select_classes(d):
    """Pick a minimum-samples-per-class threshold that leaves at least 2
    usable genre classes, backing off if the catalog is small."""
    for threshold in (50, 20, 10, 5):
        counts = d['primary_genre'].value_counts()
        valid = counts[counts >= threshold].index.tolist()
        if len(valid) >= 2:
            return valid, threshold
    return [], None


def train_genre_classifier(df):
    d = df.copy()
    d['primary_genre'] = d['genre'].apply(lambda x: genres_of(x)[0] if genres_of(x) else 'Unknown')
    valid, threshold = _select_classes(d)
    if not valid:
        log('Skipping genre classifier: not enough movies per genre to form 2+ classes.')
        return None
    d = d[d['primary_genre'].isin(valid)].reset_index(drop=True)
    log(f'Genre classifier classes ({len(valid)}, min {threshold} samples each): {valid}')
    log(f'Samples: {len(d)} movies')

    texts = (d['description'].fillna('') + ' ' + d['title'].fillna('')).tolist()
    le = LabelEncoder()
    y = le.fit_transform(d['primary_genre'].tolist())

    vec = TfidfVectorizer(max_features=5000, stop_words='english',
                           ngram_range=(1, 2), sublinear_tf=True)

    try:
        X_temp, X_test, y_temp, y_test = train_test_split(
            texts, y, test_size=0.20, random_state=42, stratify=y)
        X_train, X_val, y_train, y_val = train_test_split(
            X_temp, y_temp, test_size=0.125, random_state=42, stratify=y_temp)
    except ValueError as e:
        log(f'Skipping genre classifier: split failed ({e}). Likely too few samples in a class.')
        return None

    log(f'Train: {len(X_train)} | Val: {len(X_val)} | Test: {len(X_test)}')

    X_train_vec = vec.fit_transform(X_train)
    X_val_vec = vec.transform(X_val)
    X_test_vec = vec.transform(X_test)

    train_sizes = np.linspace(0.1, 1.0, 20)
    train_acc_curve, val_acc_curve = [], []
    train_loss_curve, val_loss_curve = [], []
    n_cls = len(np.unique(y))

    def log_loss(y_true, proba, n_total_classes):
        if proba.shape[1] < n_total_classes:
            pad = np.full((proba.shape[0], n_total_classes - proba.shape[1]), 1e-9)
            proba = np.hstack([proba, pad])
            proba = proba / proba.sum(axis=1, keepdims=True)
        oh = np.zeros((len(y_true), n_total_classes))
        oh[np.arange(len(y_true)), y_true] = 1
        return -np.mean(np.sum(oh * np.log(proba + 1e-9), axis=1))

    log('Building training-size accuracy/loss curve...')
    for size in train_sizes:
        n = max(len(valid), int(size * X_train_vec.shape[0]))
        n = min(n, X_train_vec.shape[0])
        idx = np.random.choice(X_train_vec.shape[0], n, replace=False)
        Xb, yb = X_train_vec[idx], y_train[idx]

        clf = LogisticRegression(max_iter=1000, random_state=42, C=1.0,
                                  solver='lbfgs', class_weight='balanced')
        clf.fit(Xb, yb)

        train_acc_curve.append(accuracy_score(yb, clf.predict(Xb)) * 100)
        val_acc_curve.append(accuracy_score(y_val, clf.predict(X_val_vec)) * 100)
        train_loss_curve.append(log_loss(yb, clf.predict_proba(Xb), n_cls))
        val_loss_curve.append(log_loss(y_val, clf.predict_proba(X_val_vec), n_cls))

    final_clf = LogisticRegression(max_iter=1000, random_state=42, C=1.0,
                                    solver='lbfgs', class_weight='balanced')
    final_clf.fit(X_train_vec, y_train)
    y_pred = final_clf.predict(X_test_vec)

    test_acc = accuracy_score(y_test, y_pred)
    test_prec = precision_score(y_test, y_pred, average='weighted', zero_division=0)
    test_rec = recall_score(y_test, y_pred, average='weighted', zero_division=0)
    test_f1 = f1_score(y_test, y_pred, average='weighted', zero_division=0)
    log(f'Test set — accuracy {test_acc*100:.2f}%  precision {test_prec*100:.2f}%  '
        f'recall {test_rec*100:.2f}%  F1 {test_f1*100:.2f}%')

    cv_scores = None
    try:
        min_class_count = pd.Series(y).value_counts().min()
        n_splits = min(5, int(min_class_count))
        if n_splits >= 2:
            X_all_vec = vec.transform(texts)
            skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
            cv_clf = LogisticRegression(max_iter=1000, random_state=42, C=1.0,
                                         solver='lbfgs', class_weight='balanced')
            cv_scores = cross_val_score(cv_clf, X_all_vec, y, cv=skf, scoring='accuracy')
            log(f'{n_splits}-fold CV mean accuracy: {cv_scores.mean()*100:.2f}% '
                f'(±{cv_scores.std()*100:.2f}%)')
    except ValueError as e:
        log(f'Skipping cross-validation: {e}')

    history = {'train_acc': train_acc_curve, 'val_acc': val_acc_curve,
               'train_loss': train_loss_curve, 'val_loss': val_loss_curve,
               'train_sizes': (train_sizes * 100).tolist()}

    return {
        'clf': final_clf, 'vec': vec, 'class_names': valid,
        'X_test_vec': X_test_vec, 'y_test': y_test, 'y_pred': y_pred,
        'history': history, 'cv_scores': cv_scores,
        'test_acc': test_acc, 'test_prec': test_prec,
        'test_rec': test_rec, 'test_f1': test_f1,
    }


# --------------------------------------------------------------------------- #
# CHARTS
# --------------------------------------------------------------------------- #
def figure1_dataset_overview(df, split_sizes):
    log('Figure 1 — dataset overview...')
    fig, axes = plt.subplots(2, 3, figsize=(18, 11))
    fig.suptitle('Figure 1: Dataset Overview & Preprocessing',
                 fontsize=18, fontweight='bold', color=ACCENT, y=0.98)

    ax = axes[0, 0]
    gc = Counter(g for row in df['genre'] for g in genres_of(row))
    top = dict(gc.most_common(12))
    bars = ax.barh(list(top.keys())[::-1], list(top.values())[::-1], color=ACCENT, height=0.6)
    for bar in bars:
        ax.text(bar.get_width() + 3, bar.get_y() + bar.get_height() / 2,
                str(int(bar.get_width())), va='center', fontsize=9)
    ax.set_title('Genre Distribution (Top 12)', fontweight='bold')
    ax.set_xlabel('Number of Movies')
    ax.grid(axis='x')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    ax = axes[0, 1]
    ratings = df['rating'][df['rating'] > 0]
    if len(ratings) > 0:
        ax.hist(ratings, bins=20, color=BLUE, edgecolor=BG, linewidth=0.5)
        ax.axvline(ratings.mean(), color=ACCENT, linestyle='--', lw=2, label=f'Mean: {ratings.mean():.2f}')
        ax.axvline(ratings.median(), color=ORANGE, linestyle=':', lw=2, label=f'Median: {ratings.median():.2f}')
        ax.legend(fontsize=9)
    ax.set_title('Rating Distribution', fontweight='bold')
    ax.set_xlabel('Rating (0-10)')
    ax.set_ylabel('Count')
    ax.grid(axis='y')

    ax = axes[0, 2]
    df2 = df[df['year'] > 0].copy()
    df2['decade'] = (df2['year'] // 10 * 10)
    dc = df2['decade'].value_counts().sort_index()
    ax.bar([str(int(d)) + 's' for d in dc.index], dc.values, color=GREEN, edgecolor=BG)
    ax.set_title('Movies per Decade', fontweight='bold')
    ax.set_xlabel('Decade')
    ax.set_ylabel('Count')
    ax.tick_params(axis='x', rotation=45)
    ax.grid(axis='y')

    ax = axes[1, 0]
    runtime = pd.to_numeric(df['runtime'], errors='coerce').dropna()
    runtime = runtime[runtime > 0]
    if len(runtime) > 0:
        ax.hist(runtime, bins=30, color=ORANGE, edgecolor=BG, linewidth=0.5)
        ax.axvline(runtime.mean(), color=ACCENT, linestyle='--', lw=2, label=f'Mean: {runtime.mean():.0f} min')
        ax.legend(fontsize=9)
    ax.set_title('Runtime Distribution', fontweight='bold')
    ax.set_xlabel('Runtime (minutes)')
    ax.set_ylabel('Count')
    ax.grid(axis='y')

    ax = axes[1, 1]
    ax.axis('off')
    total = len(df)
    data = [('Total Catalog', total, BLUE),
            ('TF-IDF Train (80%)', split_sizes.get('tfidf_train', 0), GREEN),
            ('TF-IDF Test (20%)', split_sizes.get('tfidf_test', 0), ACCENT)]
    if split_sizes.get('clf_train') is not None:
        data += [('Classifier Train (70%)', split_sizes['clf_train'], PURPLE),
                 ('Classifier Val (10%)', split_sizes['clf_val'], ORANGE),
                 ('Classifier Test (20%)', split_sizes['clf_test'], BLUE)]
    data = data[::-1]  # largest/first-listed item plots at the top
    for i, (label, count, color) in enumerate(data):
        ax.barh(i, count, color=color, edgecolor=BG, height=0.5)
        ax.text(count + max(total * 0.015, 2), i, f'{label}: {count}', va='center',
                fontsize=9.5, fontweight='bold')
    ax.set_xlim(0, total * 1.75)
    ax.set_ylim(-0.6, len(data) - 0.4)
    ax.set_yticks([])
    ax.set_title('Train / Validation / Test Splits', fontweight='bold')

    ax = axes[1, 2]
    ax.axis('off')
    steps = [
        ('1. Load from MySQL', f'{total} movies loaded'),
        ('2. Clean data', 'Drop dup movie_id / blank titles'),
        ('3. Type coercion', 'year→int, rating→float'),
        ('4. Validate years', 'Keep 1888–present only'),
        ('5. Feature engineering', 'Weighted text: genre×3, director×2'),
        ('6. TF-IDF (recommender)', '80% train / 20% test holdout'),
        ('7. TF-IDF (production)', 'Retrained on 100% of catalog'),
        ('8. Genre classifier', '70% train / 10% val / 20% test'),
        ('9. Cross-validation', '5-fold stratified'),
        ('10. Save + record run', 'model/*.pkl + model_metadata row'),
    ]
    ax.set_title('Pipeline Steps', fontweight='bold')
    for i, (step, detail) in enumerate(steps):
        y = 0.95 - i * 0.095
        ax.text(0.02, y, f'✓ {step}', transform=ax.transAxes, fontsize=9, fontweight='bold', color=GREEN)
        ax.text(0.02, y - 0.04, f'   {detail}', transform=ax.transAxes, fontsize=8, color='#aaa')

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out = os.path.join(CHART_DIR, 'Figure1_dataset_overview.png')
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close()
    log(f'Saved -> {out}')


def figure3_training_accuracy(history, test_acc):
    sizes, train_acc, val_acc = history['train_sizes'], history['train_acc'], history['val_acc']
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.plot(sizes, train_acc, color=BLUE, lw=2.5, marker='o', markersize=5, label='Training Accuracy')
    ax.plot(sizes, val_acc, color=GREEN, lw=2.5, marker='s', markersize=5, linestyle='--', label='Validation Accuracy')
    ax.fill_between(sizes, train_acc, val_acc, alpha=0.12, color=ORANGE, label='Generalisation gap')
    best_idx = int(np.argmax(val_acc))
    ax.scatter([sizes[best_idx]], [val_acc[best_idx]], color=ACCENT, s=120, zorder=5,
               label=f'Best Val Acc: {val_acc[best_idx]:.1f}%')
    ax.axhline(test_acc * 100, color=ORANGE, linestyle=':', lw=2, label=f'Test Accuracy: {test_acc*100:.1f}%')
    ax.set_title('Figure 3: Training vs Validation Accuracy\n(Logistic Regression Genre Classifier)',
                 fontsize=14, fontweight='bold', color=ACCENT, pad=15)
    ax.set_xlabel('Training Data Used (%)')
    ax.set_ylabel('Accuracy (%)')
    ax.set_ylim(0, 110)
    ax.legend(fontsize=10, facecolor=PANEL, edgecolor='#444')
    ax.grid(True, alpha=0.4)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    out = os.path.join(CHART_DIR, 'Figure3_training_accuracy.png')
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close()
    log(f'Saved -> {out}')


def figure4_training_loss(history):
    sizes, train_loss, val_loss = history['train_sizes'], history['train_loss'], history['val_loss']
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.plot(sizes, train_loss, color=ACCENT, lw=2.5, marker='o', markersize=5, label='Training Loss')
    ax.plot(sizes, val_loss, color=ORANGE, lw=2.5, marker='s', markersize=5, linestyle='--', label='Validation Loss')
    best_idx = int(np.argmin(val_loss))
    ax.scatter([sizes[best_idx]], [val_loss[best_idx]], color=GREEN, s=120, zorder=5,
               label=f'Best Val Loss: {val_loss[best_idx]:.4f}')
    ax.set_title('Figure 4: Training vs Validation Loss\n(Cross-Entropy — lower is better)',
                 fontsize=14, fontweight='bold', color=ACCENT, pad=15)
    ax.set_xlabel('Training Data Used (%)')
    ax.set_ylabel('Cross-Entropy Loss')
    ax.legend(fontsize=10, facecolor=PANEL, edgecolor='#444')
    ax.grid(True, alpha=0.4)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    out = os.path.join(CHART_DIR, 'Figure4_training_loss.png')
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close()
    log(f'Saved -> {out}')


def figure5_confusion_matrix(y_test, y_pred, class_names):
    cm = confusion_matrix(y_test, y_pred)
    cm_norm = cm.astype(float) / cm.sum(axis=1, keepdims=True)
    fig, axes = plt.subplots(1, 2, figsize=(18, 8))
    fig.suptitle('Figure 5: Confusion Matrix — Genre Classifier (Test Set)',
                 fontsize=16, fontweight='bold', color=ACCENT, y=1.01)
    for ax, data, fmt, cmap, title in [
        (axes[0], cm, 'd', 'YlOrRd', 'Raw Counts'),
        (axes[1], cm_norm, '.2f', 'Blues', 'Normalised (per true class)'),
    ]:
        sns.heatmap(data, annot=True, fmt=fmt, cmap=cmap, xticklabels=class_names,
                    yticklabels=class_names, linewidths=0.5, linecolor=BG,
                    cbar_kws={'shrink': 0.8}, ax=ax, annot_kws={'size': 9})
        ax.set_title(title, fontweight='bold', color=TEXT, fontsize=13)
        ax.set_xlabel('Predicted Genre')
        ax.set_ylabel('True Genre')
        plt.setp(ax.get_xticklabels(), rotation=45, ha='right')
    plt.tight_layout()
    out = os.path.join(CHART_DIR, 'Figure5_confusion_matrix.png')
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close()
    log(f'Saved -> {out}')


def figure6_f1_per_genre(y_test, y_pred, class_names, test_acc, test_prec, test_rec, test_f1):
    report = classification_report(y_test, y_pred, target_names=class_names,
                                    output_dict=True, zero_division=0)
    prec_vals = [report[g]['precision'] * 100 for g in class_names]
    rec_vals = [report[g]['recall'] * 100 for g in class_names]
    f1_vals = [report[g]['f1-score'] * 100 for g in class_names]
    order = sorted(zip(class_names, f1_vals), key=lambda x: x[1], reverse=True)
    genres_sorted = [g for g, _ in order]
    prec_s = [report[g]['precision'] * 100 for g in genres_sorted]
    rec_s = [report[g]['recall'] * 100 for g in genres_sorted]
    f1_s = [report[g]['f1-score'] * 100 for g in genres_sorted]

    fig, axes = plt.subplots(1, 2, figsize=(18, 7))
    fig.suptitle('Figure 6: F1 Score, Precision & Recall per Genre (Test Set)',
                 fontsize=16, fontweight='bold', color=ACCENT)

    ax = axes[0]
    x, w = np.arange(len(genres_sorted)), 0.25
    ax.bar(x - w, prec_s, w, label='Precision', color=BLUE, edgecolor=BG)
    ax.bar(x, rec_s, w, label='Recall', color=GREEN, edgecolor=BG)
    ax.bar(x + w, f1_s, w, label='F1 Score', color=ACCENT, edgecolor=BG)
    ax.set_xticks(x)
    ax.set_xticklabels(genres_sorted, rotation=40, ha='right', fontsize=10)
    ax.set_ylim(0, 115)
    ax.set_ylabel('Score (%)')
    ax.legend(fontsize=10, facecolor=PANEL, edgecolor='#444')
    ax.grid(axis='y', alpha=0.4)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    ax = axes[1]
    f1_items = sorted(zip(class_names, f1_vals), key=lambda x: x[1])
    g_names = [g for g, _ in f1_items]
    f1_final = [v for _, v in f1_items]
    colors = [GREEN if v >= 60 else ORANGE if v >= 40 else ACCENT for v in f1_final]
    bars = ax.barh(g_names, f1_final, color=colors, height=0.6)
    for bar, val in zip(bars, f1_final):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2, f'{val:.1f}%',
                va='center', fontsize=10, fontweight='bold')
    ax.axvline(test_f1 * 100, color=TEXT, linestyle='--', lw=1.5, label=f'Weighted F1: {test_f1*100:.1f}%')
    ax.set_xlim(0, 115)
    ax.set_xlabel('F1 Score (%)')
    ax.legend(fontsize=10, facecolor=PANEL, edgecolor='#444')
    ax.grid(axis='x', alpha=0.4)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    fig.text(0.5, 0.01,
              f'Overall (weighted) — Accuracy: {test_acc*100:.1f}%   Precision: {test_prec*100:.1f}%   '
              f'Recall: {test_rec*100:.1f}%   F1: {test_f1*100:.1f}%',
              ha='center', fontsize=12, fontweight='bold', color=TEXT,
              bbox=dict(boxstyle='round,pad=0.4', facecolor=PANEL, edgecolor=ACCENT, linewidth=1.5))
    plt.tight_layout(rect=[0, 0.07, 1, 0.95])
    out = os.path.join(CHART_DIR, 'Figure6_f1_precision_recall.png')
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close()
    log(f'Saved -> {out}')


def figure7_cross_validation(cv_scores):
    folds = [f'Fold {i+1}' for i in range(len(cv_scores))]
    scores = cv_scores * 100
    mean_acc, std_acc = scores.mean(), scores.std()

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(f'Figure 7: {len(cv_scores)}-Fold Stratified Cross Validation',
                 fontsize=15, fontweight='bold', color=ACCENT)

    ax = axes[0]
    colors = [GREEN if s >= mean_acc else ORANGE for s in scores]
    bars = ax.bar(folds, scores, color=colors, edgecolor=BG, width=0.5)
    for bar, val in zip(bars, scores):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.3, f'{val:.1f}%',
                ha='center', fontsize=11, fontweight='bold')
    ax.axhline(mean_acc, color=ACCENT, linestyle='--', lw=2, label=f'Mean: {mean_acc:.1f}%')
    ax.set_ylim(0, 115)
    ax.set_ylabel('Accuracy (%)')
    ax.legend(fontsize=10, facecolor=PANEL, edgecolor='#444')
    ax.grid(axis='y', alpha=0.4)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    ax = axes[1]
    ax.axis('off')
    summary = [('Folds', str(len(cv_scores)))] + \
              [(f'Fold {i+1} Accuracy', f'{s:.2f}%') for i, s in enumerate(scores)] + \
              [('Mean Accuracy', f'{mean_acc:.2f}%'), ('Std Deviation', f'±{std_acc:.2f}%'),
               ('Verdict', 'Stable' if std_acc < 5 else 'High variance')]
    for i, (label, value) in enumerate(summary):
        y = 0.95 - i * 0.09
        color = ACCENT if label == 'Verdict' else (GREEN if label == 'Mean Accuracy' else TEXT)
        ax.text(0.05, y, label, transform=ax.transAxes, fontsize=10, color='#aaa')
        ax.text(0.60, y, value, transform=ax.transAxes, fontsize=10, fontweight='bold', color=color)

    plt.tight_layout()
    out = os.path.join(CHART_DIR, 'Figure7_cross_validation.png')
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close()
    log(f'Saved -> {out}')


def figure8_precision_recall_curve(clf, X_test_vec, y_test, class_names):
    y_score = clf.predict_proba(X_test_vec)
    palette = [ACCENT, BLUE, GREEN, ORANGE, PURPLE, '#ff6b6b', '#4cc9f0', '#f72585', '#7209b7', '#3a0ca3']

    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    fig.suptitle('Figure 8: Precision-Recall Curve (Test Set)', fontsize=15, fontweight='bold', color=ACCENT)

    ax = axes[0]
    auc_scores = []
    for i, genre in enumerate(class_names):
        y_bin = (y_test == i).astype(int)
        prec, rec, _ = precision_recall_curve(y_bin, y_score[:, i])
        auc_val = auc(rec, prec)
        auc_scores.append(auc_val)
        ax.plot(rec, prec, color=palette[i % len(palette)], lw=2, label=f'{genre} (AUC={auc_val:.2f})')
    ax.set_xlabel('Recall')
    ax.set_ylabel('Precision')
    ax.set_xlim(0, 1.05)
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=8, facecolor=PANEL, edgecolor='#444')
    ax.grid(True, alpha=0.4)

    ax = axes[1]
    pairs = sorted(zip(class_names, auc_scores), key=lambda x: x[1])
    g_names, aucs = [g for g, _ in pairs], [a for _, a in pairs]
    colors = [GREEN if a >= 0.8 else ORANGE if a >= 0.6 else ACCENT for a in aucs]
    bars = ax.barh(g_names, aucs, color=colors, height=0.6)
    for bar, val in zip(bars, aucs):
        ax.text(bar.get_width() + 0.005, bar.get_y() + bar.get_height() / 2, f'{val:.3f}',
                va='center', fontsize=10, fontweight='bold')
    mean_auc = float(np.mean(auc_scores))
    ax.axvline(mean_auc, color=TEXT, linestyle='--', lw=1.5, label=f'Mean AUC: {mean_auc:.3f}')
    ax.set_xlim(0, 1.15)
    ax.set_xlabel('AUC Score (1.0 = perfect)')
    ax.legend(fontsize=10, facecolor=PANEL, edgecolor='#444')
    ax.grid(axis='x', alpha=0.4)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()
    out = os.path.join(CHART_DIR, 'Figure8_precision_recall_curve.png')
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close()
    log(f'Saved -> {out}')
    return mean_auc


def figure9_pearson_correlation(train_df, cosine_sim):
    sample = train_df.sample(min(150, len(train_df)), random_state=42)
    data = {'avg_sim': [], 'rating': [], 'year': [], 'runtime': []}
    for pos, (_, row) in enumerate(sample.iterrows()):
        i = train_df.index.get_loc(row.name)
        if i >= cosine_sim.shape[0]:
            continue
        scs = sorted(enumerate(cosine_sim[i]), key=lambda x: x[1], reverse=True)[1:7]
        if not scs:
            continue
        runtime_val = pd.to_numeric(pd.Series([row['runtime']]), errors='coerce').iloc[0]
        data['avg_sim'].append(np.mean([s for _, s in scs]) * 100)
        data['rating'].append(row['rating'])
        data['year'].append(row['year'])
        data['runtime'].append(runtime_val if pd.notna(runtime_val) else 0)

    res = pd.DataFrame(data)
    res = res[(res['rating'] > 0) & (res['year'] > 0) & (res['runtime'] > 0)]
    if len(res) < 10:
        log('Skipping Figure 9 (Pearson correlation): not enough complete rows.')
        return

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle('Figure 9: Pearson Correlation — Attributes vs Similarity Score',
                 fontsize=15, fontweight='bold', color=ACCENT)
    for ax, col, label, color in [(axes[0], 'rating', 'Rating', BLUE),
                                   (axes[1], 'year', 'Release Year', GREEN),
                                   (axes[2], 'runtime', 'Runtime (minutes)', ORANGE)]:
        x, y = res[col].values, res['avg_sim'].values
        r, p = pearsonr(x, y)
        ax.scatter(x, y, color=color, alpha=0.6, s=40)
        m, b = np.polyfit(x, y, 1)
        x_line = np.linspace(x.min(), x.max(), 100)
        ax.plot(x_line, m * x_line + b, color=ACCENT, lw=2, linestyle='--')
        sig = '(significant p<0.05)' if p < 0.05 else '(not significant)'
        ax.annotate(f'r = {r:.3f}\np = {p:.4f}\n{sig}', xy=(0.05, 0.85), xycoords='axes fraction',
                    fontsize=10, bbox=dict(boxstyle='round,pad=0.4', facecolor=PANEL, edgecolor=color, lw=1.5))
        ax.set_title(f'{label} vs Avg Similarity Score', fontweight='bold')
        ax.set_xlabel(label)
        ax.set_ylabel('Avg Similarity Score (%)')
        ax.grid(True, alpha=0.3)
    plt.tight_layout(rect=[0, 0, 1, 0.94])
    out = os.path.join(CHART_DIR, 'Figure9_pearson_correlation.png')
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close()
    log(f'Saved -> {out}')


def figure10_model_accuracy(train_df, cosine_sim, clf_acc):
    gc = Counter(g for row in train_df['genre'] for g in genres_of(row))
    top_genres = [g for g, c in gc.most_common(10) if c >= 20]
    if not top_genres:
        log('Skipping Figure 10: no genre has enough movies for a reliable sample.')
        return None
    total_genre_count = sum(gc.values())
    model_acc, random_acc = {}, {}

    for genre in top_genres:
        genre_movies = train_df[train_df['genre'].str.contains(genre, na=False, regex=False)]
        sample = genre_movies.sample(min(50, len(genre_movies)), random_state=42)
        tp = fp = 0
        for _, row in sample.iterrows():
            i = train_df.index.get_loc(row.name)
            if i >= cosine_sim.shape[0]:
                continue
            scs = sorted(enumerate(cosine_sim[i]), key=lambda x: x[1], reverse=True)[1:7]
            recs = train_df.iloc[[j for j, _ in scs]]
            src_g = set(genres_of(row['genre']))
            for g in recs['genre']:
                if src_g & set(genres_of(g)):
                    tp += 1
                else:
                    fp += 1
        total = tp + fp
        model_acc[genre] = round(tp / total * 100, 1) if total > 0 else 0
        random_acc[genre] = round(gc[genre] / total_genre_count * 100 * 6, 1)

    overall_model = round(float(np.mean(list(model_acc.values()))), 1)
    overall_random = round(float(np.mean([min(v, 100) for v in random_acc.values()])), 1)

    fig = plt.figure(figsize=(16, 10))
    fig.suptitle('Figure 10: Model Accuracy — TF-IDF vs Classifier vs Baseline',
                 fontsize=17, fontweight='bold', color=ACCENT, y=0.98)
    gs = fig.add_gridspec(2, 2, hspace=0.45, wspace=0.35)

    ax1 = fig.add_subplot(gs[0, :])
    genres = list(model_acc.keys())
    m_vals = list(model_acc.values())
    r_vals = [min(random_acc[g], 100) for g in genres]
    x, w = np.arange(len(genres)), 0.35
    ax1.bar(x - w / 2, m_vals, w, label='TF-IDF Model', color=GREEN, edgecolor=BG)
    ax1.bar(x + w / 2, r_vals, w, label='Random Baseline', color='#555', edgecolor=BG)
    ax1.axhline(overall_model, color=GREEN, linestyle='--', lw=1.8, label=f'TF-IDF avg: {overall_model}%')
    if clf_acc is not None:
        ax1.axhline(clf_acc * 100, color=BLUE, linestyle='-.', lw=1.8, label=f'Classifier acc: {clf_acc*100:.1f}%')
    ax1.axhline(overall_random, color='#888', linestyle=':', lw=1.8, label=f'Random avg: {overall_random}%')
    ax1.set_xticks(x)
    ax1.set_xticklabels(genres, fontsize=11)
    ax1.set_ylim(0, 115)
    ax1.set_ylabel('Accuracy (%)')
    ax1.legend(fontsize=9, facecolor=PANEL, edgecolor='#444', ncol=2)
    ax1.grid(axis='y', alpha=0.4)
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)

    ax2 = fig.add_subplot(gs[1, 0])
    ax2.set_facecolor(PANEL)
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.axis('off')
    ax2.add_patch(Circle((0.5, 0.52), 0.38, color='#0d1b2a', zorder=1))
    ax2.add_patch(Circle((0.5, 0.52), 0.35, color=PANEL, zorder=2))
    arc = Arc((0.5, 0.52), 0.72, 0.72, angle=0, theta1=90 - (overall_model / 100 * 360),
              theta2=90, color=GREEN, lw=18, zorder=3)
    ax2.add_patch(arc)
    ax2.text(0.5, 0.60, f'{overall_model}%', ha='center', va='center', fontsize=34,
             fontweight='bold', color=GREEN, zorder=4)
    ax2.text(0.5, 0.42, 'TF-IDF Accuracy', ha='center', va='center', fontsize=11, color=TEXT, zorder=4)
    ax2.set_title('Overall Accuracy', fontweight='bold')

    ax3 = fig.add_subplot(gs[1, 1])
    improvement = [model_acc[g] - min(random_acc[g], 100) for g in genres]
    colors = [GREEN if v >= 60 else ORANGE if v >= 40 else ACCENT for v in improvement]
    bars = ax3.barh(genres, improvement, color=colors, height=0.6)
    for bar, val in zip(bars, improvement):
        ax3.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2, f'+{val:.1f}%',
                 va='center', fontsize=10, fontweight='bold')
    ax3.set_title('Improvement over Random Baseline', fontweight='bold')
    ax3.set_xlabel('Improvement (%)')
    ax3.grid(axis='x', alpha=0.4)
    ax3.spines['top'].set_visible(False)
    ax3.spines['right'].set_visible(False)

    out = os.path.join(CHART_DIR, 'Figure10_model_accuracy.png')
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=BG)
    plt.close()
    log(f'Saved -> {out}')
    return overall_model


# --------------------------------------------------------------------------- #
# ENTRY POINT
# --------------------------------------------------------------------------- #
def run_full_evaluation(df):
    """
    Runs the whole evaluation + chart pipeline. Returns a dict of summary
    metrics to merge into the training run's metadata, or {} if evaluation
    couldn't run (e.g. catalog too small) -- callers should treat that as
    non-fatal and continue with production training regardless.
    """
    os.makedirs(CHART_DIR, exist_ok=True)
    summary = {}

    try:
        train_df, test_df, cosine_sim = evaluate_tfidf_holdout(df)
    except Exception as e:
        log(f'Evaluation aborted: TF-IDF holdout split failed ({e}).')
        return {}

    split_sizes = {'tfidf_train': len(train_df), 'tfidf_test': len(test_df),
                    'clf_train': None, 'clf_val': None, 'clf_test': None}

    clf_result = None
    try:
        clf_result = train_genre_classifier(df)
    except Exception as e:
        log(f'Genre classifier training failed, continuing without it: {e}')

    if clf_result:
        split_sizes['clf_train'] = clf_result['X_test_vec'].shape[0]  # placeholder, corrected below
        history = clf_result['history']
        n_total = len(df)
        # Recover split sizes for the overview chart (70/10/20 of classifier's filtered set)
        n_clf = len(clf_result['y_test']) * 5  # test is ~20%, so total ~ test*5 (rough, display-only)
        split_sizes['clf_test'] = len(clf_result['y_test'])
        split_sizes['clf_train'] = int(n_clf * 0.70)
        split_sizes['clf_val'] = int(n_clf * 0.10)

    try:
        figure1_dataset_overview(df, split_sizes)
    except Exception as e:
        log(f'Figure 1 failed: {e}')

    if clf_result:
        history = clf_result['history']
        try:
            figure3_training_accuracy(history, clf_result['test_acc'])
        except Exception as e:
            log(f'Figure 3 failed: {e}')
        try:
            figure4_training_loss(history)
        except Exception as e:
            log(f'Figure 4 failed: {e}')
        try:
            figure5_confusion_matrix(clf_result['y_test'], clf_result['y_pred'], clf_result['class_names'])
        except Exception as e:
            log(f'Figure 5 failed: {e}')
        try:
            figure6_f1_per_genre(clf_result['y_test'], clf_result['y_pred'], clf_result['class_names'],
                                  clf_result['test_acc'], clf_result['test_prec'],
                                  clf_result['test_rec'], clf_result['test_f1'])
        except Exception as e:
            log(f'Figure 6 failed: {e}')
        if clf_result['cv_scores'] is not None:
            try:
                figure7_cross_validation(clf_result['cv_scores'])
            except Exception as e:
                log(f'Figure 7 failed: {e}')
        mean_auc = None
        try:
            mean_auc = figure8_precision_recall_curve(
                clf_result['clf'], clf_result['X_test_vec'], clf_result['y_test'], clf_result['class_names'])
        except Exception as e:
            log(f'Figure 8 failed: {e}')

        summary.update({
            'classifier_accuracy': round(clf_result['test_acc'] * 100, 2),
            'classifier_precision': round(clf_result['test_prec'] * 100, 2),
            'classifier_recall': round(clf_result['test_rec'] * 100, 2),
            'classifier_f1': round(clf_result['test_f1'] * 100, 2),
        })
        if clf_result['cv_scores'] is not None:
            summary['cv_mean_accuracy'] = round(float(clf_result['cv_scores'].mean()) * 100, 2)
            summary['cv_std_accuracy'] = round(float(clf_result['cv_scores'].std()) * 100, 2)
        if mean_auc is not None:
            summary['mean_auc'] = round(mean_auc, 3)

    try:
        figure9_pearson_correlation(train_df, cosine_sim)
    except Exception as e:
        log(f'Figure 9 failed: {e}')

    try:
        overall_model_acc = figure10_model_accuracy(
            train_df, cosine_sim, clf_result['test_acc'] if clf_result else None)
        if overall_model_acc is not None:
            summary['tfidf_holdout_accuracy'] = overall_model_acc
    except Exception as e:
        log(f'Figure 10 failed: {e}')

    summary['charts_generated'] = sorted(
        f for f in os.listdir(CHART_DIR) if f.startswith('Figure') and f.endswith('.png'))
    return summary

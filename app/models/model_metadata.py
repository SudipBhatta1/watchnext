import json
from app import db


def record_training_run(model_type, num_movies, num_features, parameters, evaluation,
                          artifact_path, trained_by=None):
    db.execute('UPDATE model_metadata SET is_active = FALSE')
    version_id, _ = db.execute(
        """INSERT INTO model_metadata
               (model_type, trained_by, num_movies, num_features, parameters,
                evaluation, artifact_path, is_active)
           VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE)""",
        (model_type, trained_by, num_movies, num_features,
         json.dumps(parameters), json.dumps(evaluation), artifact_path),
    )
    return version_id


def get_active_model_info():
    return db.query('SELECT * FROM model_metadata WHERE is_active = TRUE ORDER BY model_version DESC LIMIT 1',
                     fetchone=True)


def get_history(limit=10):
    return db.query('SELECT * FROM model_metadata ORDER BY model_version DESC LIMIT %s', (limit,))

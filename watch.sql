DROP DATABASE IF EXISTS watchnext;
CREATE DATABASE watchnext CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE watchnext;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;


DROP TABLE IF EXISTS roles;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS genres;
DROP TABLE IF EXISTS movies;
DROP TABLE IF EXISTS movie_genres;
DROP TABLE IF EXISTS ratings;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS watch_history;
DROP TABLE IF EXISTS feedback;
DROP TABLE IF EXISTS model_metadata;


CREATE TABLE roles (
    role_id     INT AUTO_INCREMENT PRIMARY KEY,
    role_name   VARCHAR(20) NOT NULL UNIQUE,
    description VARCHAR(255) DEFAULT NULL
) ENGINE=InnoDB;


CREATE TABLE users (
    user_id          INT AUTO_INCREMENT PRIMARY KEY,
    username         VARCHAR(50)  NOT NULL UNIQUE,
    password_hash    VARCHAR(255) NOT NULL,
    name             VARCHAR(100) NOT NULL,
    email            VARCHAR(120) NOT NULL UNIQUE,
    role_id          INT NOT NULL,
    status           ENUM('active', 'inactive') NOT NULL DEFAULT 'active',

    recovery_code_hash VARCHAR(255) NULL,

    failed_attempts  INT NOT NULL DEFAULT 0,
    locked_until     TIMESTAMP NULL DEFAULT NULL,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_users_role FOREIGN KEY (role_id) REFERENCES roles(role_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT chk_username_len CHECK (CHAR_LENGTH(username) >= 3)
) ENGINE=InnoDB;

CREATE INDEX idx_users_role ON users(role_id);


CREATE TABLE genres (
    genre_id   INT AUTO_INCREMENT PRIMARY KEY,
    genre_name VARCHAR(50) NOT NULL UNIQUE
) ENGINE=InnoDB;


CREATE TABLE movies (
    movie_id    INT AUTO_INCREMENT PRIMARY KEY,
    title       VARCHAR(255) NOT NULL,
    description TEXT,
    year        SMALLINT NOT NULL,
    rating      DECIMAL(3,1) NOT NULL DEFAULT 0.0,
    director    VARCHAR(150) DEFAULT '',
    cast_list   TEXT,                      -- pipe-separated cast names (kept for TF-IDF features)
    runtime     VARCHAR(10) DEFAULT '',
    language    VARCHAR(50) DEFAULT '',
    poster_url  VARCHAR(500) DEFAULT '',
    trailer_id  VARCHAR(50) DEFAULT '',
    created_by  INT DEFAULT NULL,           -- user_id of admin/moderator who added it
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_movies_created_by FOREIGN KEY (created_by) REFERENCES users(user_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT chk_movie_year CHECK (year >= 1888 AND year <= 2100),
    CONSTRAINT chk_movie_rating CHECK (rating >= 0 AND rating <= 10),
    CONSTRAINT uq_title_year UNIQUE (title, year)
) ENGINE=InnoDB;

CREATE INDEX idx_movies_title ON movies(title);
CREATE INDEX idx_movies_year ON movies(year);
CREATE INDEX idx_movies_rating ON movies(rating);
CREATE FULLTEXT INDEX ftx_movies_search ON movies(title, description, director, cast_list);

CREATE TABLE movie_genres (
    movie_id INT NOT NULL,
    genre_id INT NOT NULL,
    PRIMARY KEY (movie_id, genre_id),
    CONSTRAINT fk_mg_movie FOREIGN KEY (movie_id) REFERENCES movies(movie_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_mg_genre FOREIGN KEY (genre_id) REFERENCES genres(genre_id)
        ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE INDEX idx_mg_genre ON movie_genres(genre_id);


CREATE TABLE ratings (
    rating_id  INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    movie_id   INT NOT NULL,
    rating     TINYINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_ratings_user  FOREIGN KEY (user_id)  REFERENCES users(user_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_ratings_movie FOREIGN KEY (movie_id) REFERENCES movies(movie_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT uq_user_movie_rating UNIQUE (user_id, movie_id),
    CONSTRAINT chk_rating_range CHECK (rating BETWEEN 1 AND 5)
) ENGINE=InnoDB;

CREATE INDEX idx_ratings_movie ON ratings(movie_id);


CREATE TABLE reviews (
    review_id   INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    movie_id    INT NOT NULL,
    review_text VARCHAR(2000) NOT NULL,
    status      ENUM('visible', 'hidden') NOT NULL DEFAULT 'visible',
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_reviews_user  FOREIGN KEY (user_id)  REFERENCES users(user_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_reviews_movie FOREIGN KEY (movie_id) REFERENCES movies(movie_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT chk_review_len CHECK (CHAR_LENGTH(review_text) >= 1)
) ENGINE=InnoDB;

CREATE INDEX idx_reviews_movie ON reviews(movie_id, status);
CREATE INDEX idx_reviews_user ON reviews(user_id);


CREATE TABLE watch_history (
    history_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    movie_id   INT NOT NULL,
    watched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_history_user  FOREIGN KEY (user_id)  REFERENCES users(user_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_history_movie FOREIGN KEY (movie_id) REFERENCES movies(movie_id)
        ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE INDEX idx_history_user ON watch_history(user_id, watched_at);


CREATE TABLE feedback (
    feedback_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    movie_id    INT NOT NULL,
    vote        ENUM('up', 'down') NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_feedback_user  FOREIGN KEY (user_id)  REFERENCES users(user_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_feedback_movie FOREIGN KEY (movie_id) REFERENCES movies(movie_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT uq_user_movie_feedback UNIQUE (user_id, movie_id)
) ENGINE=InnoDB;


CREATE TABLE watchlist (
    user_id     INT NOT NULL,
    movie_id    INT NOT NULL,
    added_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, movie_id),
    CONSTRAINT fk_watchlist_user  FOREIGN KEY (user_id)  REFERENCES users(user_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_watchlist_movie FOREIGN KEY (movie_id) REFERENCES movies(movie_id)
        ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB;


CREATE TABLE model_metadata (
    model_version   INT AUTO_INCREMENT PRIMARY KEY,
    model_type      VARCHAR(100) NOT NULL,
    trained_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trained_by      INT DEFAULT NULL,
    num_movies      INT NOT NULL,
    num_features    INT DEFAULT NULL,
    parameters      JSON DEFAULT NULL,
    evaluation      JSON DEFAULT NULL,
    artifact_path   VARCHAR(255) NOT NULL,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT fk_model_trained_by FOREIGN KEY (trained_by) REFERENCES users(user_id)
        ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB;

SET FOREIGN_KEY_CHECKS = 1;


INSERT INTO roles (role_name, description) VALUES
    ('Admin',     'Full system access: users, roles, movies, genres, statistics'),
    ('Moderator', 'Manages movies, genres and moderates reviews'),
    ('User',      'Browses movies, rates, reviews and receives recommendations');


INSERT INTO users (username, password_hash, name, email, role_id, status, recovery_code_hash) VALUES
    ('admin', 'scrypt:32768:8:1$fGhPVqmfwa1s72P3$89756a8ea20a808c0172afa3da59d8d180fb04858f5a4fcfecbcade7c812dcefa45b940ef84c4bfffd69995e1503187a6447b7a15a678416678fe4aa823a3c4c', 'System Admin', 'admin@watch.local',
        (SELECT role_id FROM roles WHERE role_name = 'Admin'), 'active',
        'scrypt:32768:8:1$wh6nEdojBh27lO6z$874dd805dcc010090ccb6c627fe9919e91176aa4e6f49842492b68af49d86e367d363f9ba716e942c9694c111e8f51c6defaff30e9d3d4e4a8fea003ccc48c90'),
    ('moderator', 'scrypt:32768:8:1$aVTcWzmVYYJisvlJ$ad5bd09b433fb4c58f8c0d4558348a80a849d1ff8945127f6fea4d27e2bdc6bf3a54061a20e63146146c22903ddbca607e1c674f4e01abc18b48ec6197d31979', 'Content Moderator', 'moderator@watch.local',
        (SELECT role_id FROM roles WHERE role_name = 'Moderator'), 'active',
        'scrypt:32768:8:1$wh6nEdojBh27lO6z$874dd805dcc010090ccb6c627fe9919e91176aa4e6f49842492b68af49d86e367d363f9ba716e942c9694c111e8f51c6defaff30e9d3d4e4a8fea003ccc48c90'),
    ('ram', 'scrypt:32768:8:1$beU0HRzlqgaKDOAB$1f7904db3e886e30b911ade62ab1381402d66f94e392922651c91fba2f8a06e2caeccbd4c35ccf85ac8b3c933d50a76096408bc6df5b51b70de2fab8ebf7e9f6', 'ram', 'ram@watch.local',
        (SELECT role_id FROM roles WHERE role_name = 'User'), 'active',
        'scrypt:32768:8:1$wh6nEdojBh27lO6z$874dd805dcc010090ccb6c627fe9919e91176aa4e6f49842492b68af49d86e367d363f9ba716e942c9694c111e8f51c6defaff30e9d3d4e4a8fea003ccc48c90');

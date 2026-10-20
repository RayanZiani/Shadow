"""Stockage du back-office — SQLite, sans dépendance externe.

Pourquoi SQLite et pas `db-postgres` ? Le back-office doit pouvoir démarrer et
se tester même si PostgreSQL n'est pas là, et il ne stocke que quelques
dizaines de lignes (utilisateurs, sessions, journal d'audit). Le registre des
outputs reste, lui, un chantier séparé (TODO §1.3). Le fichier vit dans le
volume Docker `bo-data` : il survit à un redémarrage du conteneur.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterable, Iterator, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    username          TEXT NOT NULL UNIQUE,
    password_hash     TEXT NOT NULL,
    role              TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'auditor')),
    active            INTEGER NOT NULL DEFAULT 1,
    face_embedding    BLOB,
    face_enrolled_at  REAL,
    created_at        REAL NOT NULL,
    last_login_at     REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    ip          TEXT,
    user_agent  TEXT,
    revoked     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attempts (
    username     TEXT PRIMARY KEY,
    failures     INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS challenges (
    id         TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    ip         TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    face_ok    INTEGER NOT NULL DEFAULT 0,
    used       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    username TEXT,
    event    TEXT NOT NULL,
    ip       TEXT,
    detail   TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_ts         ON audit(ts DESC);
CREATE INDEX IF NOT EXISTS idx_challenges_exp   ON challenges(expires_at);
"""


class Database:
    """Connexion unique + verrou.

    FastAPI exécute les routes synchrones dans un pool de threads : SQLite est
    ouvert en `check_same_thread=False` et TOUTES les opérations passent par le
    même verrou. C'est suffisant pour la charge d'un back-office de démo, et
    cela évite les erreurs `database is locked` d'un accès concurrent.
    """

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Transaction explicite (BEGIN IMMEDIATE) pour les écritures composées."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def query(self, sql: str, params: Sequence = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: Sequence = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence = ()):
        row = self.one(sql, params)
        return None if row is None else row[0]

    def run(self, sql: str, params: Sequence = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def run_many(self, sql: str, rows: Iterable[Sequence]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    # --- Entretien ---------------------------------------------------------

    def purge_expired(self, now: float | None = None) -> None:
        """Supprime les challenges et sessions périmés.

        Appelé à chaque étape d'authentification : le tableau reste petit, et
        un challenge expiré ne peut jamais être rejoué.
        """
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute("DELETE FROM challenges WHERE expires_at < ?", (now,))
            self._conn.execute(
                "DELETE FROM sessions WHERE expires_at < ? OR (revoked = 1 AND last_seen < ?)",
                (now, now - 86400),
            )
            self._conn.commit()

"""Persistance du registre d'outputs — PostgreSQL, avec repli mémoire explicite.

Pourquoi ce module existe : le registre vivait **uniquement en mémoire**. Un
`docker compose restart payment-api` en pleine démonstration effaçait les
outputs, donc la transaction de preuve, et le client recevait des 409 en série.
C'était le risque de démonstration n°1 du dossier (TODO §1.3).

Choix d'architecture : le dictionnaire en mémoire **reste la source de lecture**
(toutes les routes existantes fonctionnent sans modification et sans latence),
et PostgreSQL est un **journal d'écriture** rechargé au démarrage. C'est un
« write-through cache » :

    démarrage  : PostgreSQL → mémoire   (les outputs survivent)
    émission   : mémoire → PostgreSQL   (chaque output est écrit)
    dépense    : mémoire → PostgreSQL   (key image + output dépensé)

Pourquoi pas lire la base à chaque requête : `/tx/submit` est le chemin
critique mesuré à ~50 ms ; une requête SQL par leurre sur un anneau de 25
ajouterait 25 allers-retours. La base protège contre le redémarrage, pas contre
la latence.

**Repli explicite** : si `DATABASE_URL` est absent ou si la base ne répond pas,
le service démarre en mémoire et le DIT dans `/healthz`
(`registry: memory`, `registry_durable: false`) ainsi que dans une alerte
Prometheus. Un registre non persistant silencieux serait exactement la panne
qu'on cherche à supprimer.
"""
from __future__ import annotations

import logging
import os

LOG = logging.getLogger("payment-api.registry")

SCHEMA = """
CREATE TABLE IF NOT EXISTS outputs (
    id                      TEXT PRIMARY KEY,
    age_days                DOUBLE PRECISION NOT NULL,
    one_time_public_key_hex TEXT NOT NULL,
    tx_public_key_hex       TEXT NOT NULL,
    encrypted_amount_hex    TEXT NOT NULL,
    internal                BOOLEAN NOT NULL DEFAULT FALSE,
    spent                   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS key_images (
    key_image  TEXT PRIMARY KEY,
    spent_at   DOUBLE PRECISION NOT NULL
);

-- Portefeuille de démonstration : sans lui, un redémarrage tirait de
-- nouvelles clés et les outputs restaurés devenaient non dépensables.
CREATE TABLE IF NOT EXISTS wallet (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    view_private   TEXT NOT NULL,
    spend_private  TEXT NOT NULL,
    created_at     DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outputs_spent ON outputs(spent);
CREATE INDEX IF NOT EXISTS idx_outputs_internal ON outputs(internal);
"""


class RegistryStore:
    """Journal d'écriture PostgreSQL. Ne lève jamais : dégrade et le signale."""

    def __init__(self, url: str | None = None):
        self.url = (url or os.environ.get("DATABASE_URL", "")).strip()
        self.available = False
        self.last_error: str | None = None
        self._conn = None
        if not self.url:
            self.last_error = "DATABASE_URL absent : registre en mémoire"
            LOG.warning("registre NON persistant : %s", self.last_error)
            return
        if not self.url.startswith(("postgres://", "postgresql://")):
            self.last_error = "DATABASE_URL n'est pas PostgreSQL : registre en mémoire"
            LOG.warning("registre NON persistant : %s", self.last_error)
            return
        try:
            import psycopg

            self._conn = psycopg.connect(self.url, autocommit=True, connect_timeout=5)
            with self._conn.cursor() as cursor:
                cursor.execute(SCHEMA)
            self.available = True
            LOG.info("registre persistant : PostgreSQL")
        except Exception as exc:  # noqa: BLE001 - une panne de base ne doit pas tuer le service
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._conn = None
            LOG.error("registre NON persistant (%s)", self.last_error)

    # --- Rechargement au démarrage ---------------------------------------

    def load(self) -> tuple[dict, set, set, int]:
        """Retourne (outputs, key_images, spent_ids, dernier numéro d'ordre).

        Le dernier numéro d'ordre est extrait des identifiants `output_<n>` :
        sans lui, un redémarrage réémettrait `output_1` et écraserait un output
        existant — exactement le genre de collision qui casse la démonstration
        suivante.
        """
        outputs: dict[str, dict] = {}
        key_images: set[str] = set()
        spent_ids: set[str] = set()
        highest = 0
        if not self.available:
            return outputs, key_images, spent_ids, highest
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, age_days, one_time_public_key_hex, tx_public_key_hex, "
                    "encrypted_amount_hex, internal, spent FROM outputs"
                )
                for row in cursor.fetchall():
                    output = {
                        "id": row[0],
                        "age_days": row[1],
                        "one_time_public_key_hex": row[2],
                        "tx_public_key_hex": row[3],
                        "encrypted_amount_hex": row[4],
                    }
                    if row[5]:
                        output["internal"] = True
                    outputs[row[0]] = output
                    if row[6]:
                        spent_ids.add(row[0])
                    if row[0].startswith("output_"):
                        try:
                            highest = max(highest, int(row[0].split("_", 1)[1]))
                        except ValueError:
                            continue
                cursor.execute("SELECT key_image FROM key_images")
                key_images = {row[0] for row in cursor.fetchall()}
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"rechargement: {type(exc).__name__}: {exc}"
            self.available = False
            LOG.error("rechargement du registre impossible : %s", self.last_error)
        return outputs, key_images, spent_ids, highest

    # --- Écritures --------------------------------------------------------

    def save_outputs(self, outputs: list[dict]) -> None:
        if not self.available or not outputs:
            return
        rows = [
            (
                output["id"],
                float(output["age_days"]),
                output["one_time_public_key_hex"],
                output["tx_public_key_hex"],
                output["encrypted_amount_hex"],
                bool(output.get("internal")),
                False,
                float(output.get("created_at", 0.0)),
            )
            for output in outputs
        ]
        try:
            with self._conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO outputs(id, age_days, one_time_public_key_hex, "
                    "tx_public_key_hex, encrypted_amount_hex, internal, spent, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                    rows,
                )
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)

    def save_spent(self, output_id: str, key_image: str, spent_at: float) -> None:
        """Marque l'output dépensé ET enregistre la key image, ATOMIQUEMENT.

        Deux `execute` séparés en autocommit laissaient une fenêtre : si le
        processus mourait entre les deux, la base marquait l'output dépensé
        mais pas la key image, et un rejeu passait au redémarrage suivant.
        """
        if not self.available:
            return
        try:
            with self._conn.transaction():
                with self._conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE outputs SET spent = TRUE WHERE id = %s", (output_id,)
                    )
                    cursor.execute(
                        "INSERT INTO key_images(key_image, spent_at) VALUES (%s, %s) "
                        "ON CONFLICT (key_image) DO NOTHING",
                        (key_image, spent_at),
                    )
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)

    # --- Portefeuille ------------------------------------------------------

    def load_wallet(self) -> tuple[str, str] | None:
        """Clés privées persistées, ou None si aucune n'a encore été écrite.

        Sans cette persistance, `_bootstrap_wallet` tirait de nouvelles clés à
        chaque démarrage : les outputs restaurés devenaient non dépensables et
        `/tx/submit` répondait 409 une fois sur deux après un redémarrage.
        """
        if not self.available:
            return None
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "SELECT view_private, spend_private FROM wallet WHERE id = 1"
                )
                row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)
            return None
        if row is None:
            return None
        return str(row[0]), str(row[1])

    def save_wallet(self, view_private: str, spend_private: str, created_at: float) -> None:
        if not self.available:
            return
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO wallet(id, view_private, spend_private, created_at) "
                    "VALUES (1, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    (view_private, spend_private, created_at),
                )
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)

    def quarantine(self, output_ids: list[str]) -> None:
        if not self.available or not output_ids:
            return
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM outputs WHERE id = ANY(%s)", (list(output_ids),)
                )
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)

    def reset(self) -> None:
        """Efface le registre persistant (tests et remise à zéro explicite)."""
        if not self.available:
            return
        try:
            with self._conn.cursor() as cursor:
                cursor.execute("DELETE FROM key_images")
                cursor.execute("DELETE FROM outputs")
                cursor.execute("DELETE FROM wallet")
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)

    def _degrade(self, exc: Exception) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"
        self.available = False
        LOG.error("écriture dans le registre perdue : %s", self.last_error)

    def health(self) -> dict:
        return {
            "durable": self.available,
            "backend": "postgresql" if self.available else "memory",
            "last_error": self.last_error,
        }

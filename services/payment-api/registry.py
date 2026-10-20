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
import threading
import time

LOG = logging.getLogger("payment-api.registry")

# Délai de connexion borné : une base injoignable ne doit ni bloquer le
# démarrage ni retenir une tentative de reconnexion plus longtemps que ça.
CONNECT_TIMEOUT_SECONDS = 5
# Backoff de reconnexion : 1 s, 2 s, 4 s … plafonné à 30 s. Une tentative au
# plus par intervalle, et jamais depuis le chemin critique (thread dédié).
RECONNECT_MIN_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0


def _open_connection(url: str):
    """Ouvre une connexion psycopg — point d'injection unique pour les tests."""
    import psycopg

    return psycopg.connect(
        url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )


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

-- Idempotence de `/tx/pay` : une référence de commande ne produit qu'UN
-- paiement. Le résultat est conservé pour qu'un rejeu (timeout HTTP côté
-- boutique) renvoie la même key image au lieu d'émettre un second paiement.
--
-- Le nom est PRÉFIXÉ (`registry_payments`) : `shop-api` crée sa propre table
-- `payments` dans la MÊME base `shadow_settle`. Avec `CREATE TABLE IF NOT
-- EXISTS`, le premier service à démarrer imposait son schéma et l'autre
-- échouait à l'insertion (colonne inexistante). Constaté à l'audit du 20/10.
CREATE TABLE IF NOT EXISTS registry_payments (
    reference   TEXT PRIMARY KEY,
    key_image   TEXT NOT NULL,
    ring_size   INTEGER NOT NULL,
    amount      BIGINT NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outputs_spent ON outputs(spent);
CREATE INDEX IF NOT EXISTS idx_outputs_internal ON outputs(internal);
"""


class RegistryStore:
    """Journal d'écriture PostgreSQL. Ne lève jamais : dégrade, compte, réessaie.

    Après une coupure, `available` repasse à `True` dès qu'une reconnexion
    réussit (backoff exponentiel borné, tentative dans un thread séparé pour ne
    jamais bloquer le paiement). Les écritures refusées entre-temps sont
    comptées dans `lost_writes` : une perte silencieuse redevient visible.
    """

    # Valeurs par défaut au niveau classe : une instance construite par
    # `__new__` (tests) reste utilisable sans initialiser chaque champ.
    lost_writes = 0
    reconnect_attempts = 0
    _reconnect_delay = RECONNECT_MIN_DELAY
    _next_reconnect_at = 0.0
    _reconnecting = False
    _reconnect_guard = None

    def __init__(self, url: str | None = None):
        self.url = (url or os.environ.get("DATABASE_URL", "")).strip()
        self.available = False
        self.last_error: str | None = None
        self._conn = None
        self.lost_writes = 0
        self.reconnect_attempts = 0
        self._reconnect_delay = RECONNECT_MIN_DELAY
        self._next_reconnect_at = 0.0
        self._reconnecting = False
        self._reconnect_guard = threading.Lock()
        if not self.url:
            self.last_error = "DATABASE_URL absent : registre en mémoire"
            LOG.warning("registre NON persistant : %s", self.last_error)
            return
        if not self.url.startswith(("postgres://", "postgresql://")):
            self.last_error = "DATABASE_URL n'est pas PostgreSQL : registre en mémoire"
            LOG.warning("registre NON persistant : %s", self.last_error)
            return
        try:
            self._conn = _open_connection(self.url)
            with self._conn.cursor() as cursor:
                cursor.execute(SCHEMA)
            self.available = True
            LOG.info("registre persistant : PostgreSQL")
        except Exception as exc:  # noqa: BLE001 - une panne de base ne doit pas tuer le service
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._conn = None
            self._schedule_reconnect()
            LOG.error("registre NON persistant (%s)", self.last_error)

    # --- Reconnexion -------------------------------------------------------

    def _configured(self) -> bool:
        """Un journal PostgreSQL est-il *configuré* (même s'il est injoignable) ?"""
        return bool(self.url) and self.url.startswith(("postgres://", "postgresql://"))

    def _guard(self) -> threading.Lock:
        guard = self._reconnect_guard
        if guard is None:  # instance construite par `__new__`
            guard = threading.Lock()
            self._reconnect_guard = guard
        return guard

    def _schedule_reconnect(self) -> None:
        self._next_reconnect_at = time.monotonic() + self._reconnect_delay

    def _maybe_reconnect(self) -> bool:
        """Déclenche une tentative SI le backoff est écoulé. Ne bloque jamais.

        Retourne `True` si un thread de reconnexion a été lancé. Le chemin
        critique (écriture d'un paiement) ne paie donc jamais le coût d'une
        connexion PostgreSQL morte : il compte la perte et rend la main.
        """
        if self.available or not self._configured():
            return False
        if time.monotonic() < self._next_reconnect_at:
            return False
        with self._guard():
            if self._reconnecting or self.available:
                return False
            if time.monotonic() < self._next_reconnect_at:
                return False
            self._reconnecting = True
        self.reconnect_attempts += 1
        try:
            threading.Thread(
                target=self._reconnect, name="registry-reconnect", daemon=True
            ).start()
        except Exception as exc:  # noqa: BLE001 - ne jamais casser un paiement
            self._reconnecting = False
            self.last_error = f"thread de reconnexion: {type(exc).__name__}: {exc}"
            self._schedule_reconnect()
            return False
        return True

    def _reconnect(self) -> None:
        connection = None
        try:
            connection = _open_connection(self.url)
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA)
        except Exception as exc:  # noqa: BLE001 - on réessaiera
            if connection is not None:
                try:
                    connection.close()
                except Exception:  # noqa: BLE001
                    pass
            self.last_error = f"reconnexion: {type(exc).__name__}: {exc}"
            self._reconnect_delay = min(
                self._reconnect_delay * 2, RECONNECT_MAX_DELAY
            )
            self._schedule_reconnect()
            LOG.warning(
                "reconnexion au registre échouée (%s), nouvel essai dans %.0f s",
                self.last_error,
                self._reconnect_delay,
            )
            return
        else:
            # Tout l'état est publié AVANT de relâcher `_reconnecting` : aucun
            # autre thread ne peut lancer une seconde tentative dans la fenêtre.
            previous, self._conn = self._conn, connection
            if previous is not None and previous is not connection:
                try:
                    previous.close()
                except Exception:  # noqa: BLE001
                    pass
            self.available = True
            self.last_error = None
            self._reconnect_delay = RECONNECT_MIN_DELAY
            self._next_reconnect_at = 0.0
            LOG.info("registre reconnecté à PostgreSQL")
        finally:
            self._reconnecting = False

    def _refuse_write(self) -> None:
        """Compte une écriture perdue et arme une reconnexion.

        Le compteur ne bouge que si un journal PostgreSQL est *configuré* : en
        mode mémoire assumé (`DATABASE_URL` absent), il n'y a pas de panne
        transitoire à signaler — `durable: false` le dit déjà.
        """
        if not self._configured():
            return
        self.lost_writes += 1
        self._maybe_reconnect()

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
            self._maybe_reconnect()
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
            self._reconnect_delay = RECONNECT_MIN_DELAY
            self._schedule_reconnect()
            LOG.error("rechargement du registre impossible : %s", self.last_error)
        return outputs, key_images, spent_ids, highest

    # --- Écritures --------------------------------------------------------

    def save_outputs(self, outputs: list[dict]) -> None:
        if not outputs:
            return
        if not self.available:
            self._refuse_write()
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
            self._refuse_write()
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

    # --- Paiements idempotents --------------------------------------------

    def save_payment(
        self,
        reference: str,
        key_image: str,
        ring_size: int,
        amount: int,
        created_at: float,
    ) -> None:
        """Journalise le résultat d'une référence de commande.

        `ON CONFLICT DO NOTHING` : le premier paiement fait foi. Un rejeu ne
        doit jamais écraser la key image déjà émise.
        """
        if not reference:
            return
        if not self.available:
            self._refuse_write()
            return
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO registry_payments(reference, key_image, ring_size, amount, created_at) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (reference) DO NOTHING",
                    (reference, key_image, int(ring_size), int(amount), float(created_at)),
                )
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)

    def load_payment(self, reference: str) -> dict | None:
        """Résultat déjà journalisé pour cette référence, ou None."""
        if not reference:
            return None
        if not self.available:
            self._maybe_reconnect()
            return None
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "SELECT key_image, ring_size, amount, created_at FROM registry_payments "
                    "WHERE reference = %s",
                    (reference,),
                )
                row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc, lost_write=False)
            return None
        if row is None:
            return None
        return {
            "key_image": row[0],
            "ring_size": row[1],
            "amount": row[2],
            "created_at": row[3],
        }

    # --- Portefeuille ------------------------------------------------------

    def load_wallet(self) -> tuple[str, str] | None:
        """Clés privées persistées, ou None si aucune n'a encore été écrite.

        Sans cette persistance, `_bootstrap_wallet` tirait de nouvelles clés à
        chaque démarrage : les outputs restaurés devenaient non dépensables et
        `/tx/submit` répondait 409 une fois sur deux après un redémarrage.
        """
        if not self.available:
            self._maybe_reconnect()
            return None
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "SELECT view_private, spend_private FROM wallet WHERE id = 1"
                )
                row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc, lost_write=False)
            return None
        if row is None:
            return None
        return str(row[0]), str(row[1])

    def save_wallet(self, view_private: str, spend_private: str, created_at: float) -> None:
        if not self.available:
            self._refuse_write()
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
        if not output_ids:
            return
        if not self.available:
            self._refuse_write()
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
            self._refuse_write()
            return
        try:
            with self._conn.cursor() as cursor:
                cursor.execute("DELETE FROM key_images")
                cursor.execute("DELETE FROM outputs")
                cursor.execute("DELETE FROM wallet")
                cursor.execute("DELETE FROM registry_payments")
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)

    def _degrade(self, exc: Exception, lost_write: bool = True) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"
        self.available = False
        if lost_write:
            # L'écriture qui vient d'échouer est perdue, comme les suivantes.
            self.lost_writes += 1
        # Backoff remis à zéro : une nouvelle panne repart d'un essai rapide.
        self._reconnect_delay = RECONNECT_MIN_DELAY
        self._schedule_reconnect()
        if lost_write:
            LOG.error("écriture dans le registre perdue : %s", self.last_error)
        else:
            LOG.error("lecture du registre impossible : %s", self.last_error)

    def health(self) -> dict:
        # Un scrape de `/healthz` suffit à relancer la reconnexion, même si
        # aucune écriture n'a lieu après la panne.
        self._maybe_reconnect()
        return {
            "durable": self.available,
            "backend": "postgresql" if self.available else "memory",
            "last_error": self.last_error,
            "lost_writes": self.lost_writes,
            "reconnect_attempts": self.reconnect_attempts,
        }

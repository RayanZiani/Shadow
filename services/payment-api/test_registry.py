"""Tests du registre persistant — la durabilité doit être vérifiable.

    python -m pytest test_registry.py -v

Ce qui compte : un `docker compose restart payment-api` ne doit plus effacer
les outputs. On ne peut pas exiger un serveur PostgreSQL dans un test unitaire,
donc on vérifie :

1. le **repli explicite** quand la base est absente (jamais silencieux) ;
2. le **calcul du dernier identifiant** au rechargement — sans lui, un
   redémarrage réémettrait `output_1` et écraserait un output existant ;
3. l'**écriture groupée** (un seul aller-retour pour N outputs) ;
4. l'**idempotence** de la dépense (une key image déjà connue ne casse rien).
"""
from __future__ import annotations

import pathlib
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) in sys.path:
    sys.path.remove(str(HERE))
sys.path.insert(0, str(HERE))
for _name in ("registry", "main", "config", "svc_auth"):
    _module = sys.modules.get(_name)
    _file = getattr(_module, "__file__", None)
    if _file and pathlib.Path(_file).resolve().parent != HERE:
        del sys.modules[_name]

import registry as registry_module  # noqa: E402
from registry import RegistryStore  # noqa: E402


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows: list[tuple] = []
        self.description = None

    def execute(self, sql, params=None):
        self.connection.statements.append((sql, params))
        upper = sql.strip().upper()
        if "FROM REGISTRY_PAYMENTS" in upper:
            row = self.connection.payments.get(params[0])
            self._rows = [row] if row else []
        elif upper.startswith("INSERT INTO REGISTRY_PAYMENTS"):
            # `ON CONFLICT (reference) DO NOTHING` : le premier paiement fait foi.
            self.connection.payments.setdefault(params[0], tuple(params[1:]))
            self._rows = []
        elif upper.startswith("SELECT ID, AGE_DAYS"):
            self._rows = list(self.connection.output_rows)
        elif upper.startswith("SELECT KEY_IMAGE"):
            self._rows = [(image,) for image in self.connection.key_images]
        else:
            self._rows = []
        return self

    def executemany(self, sql, rows):
        self.connection.statements.append((sql, rows))
        self.connection.inserted.extend(rows)
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTransaction:
    """Context manager qui compte les transactions ouvertes (comme psycopg)."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.transactions.append("begin")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.connection.transactions.append("rollback" if exc_type else "commit")
        return False


class FakeConnection:
    def __init__(self, output_rows=None, key_images=None):
        self.statements: list[tuple] = []
        self.inserted: list[tuple] = []
        self.output_rows = output_rows or []
        self.key_images = key_images or []
        self.transactions: list[str] = []
        self.payments: dict[str, tuple] = {}

    def cursor(self):
        return FakeCursor(self)

    def transaction(self):
        return FakeTransaction(self)

    def close(self):
        return None


def _store_with(connection) -> RegistryStore:
    store = RegistryStore.__new__(RegistryStore)
    store.url = "postgresql://fake"
    store.available = True
    store.last_error = None
    store._conn = connection
    store.lost_writes = 0
    store.reconnect_attempts = 0
    store._reconnect_delay = registry_module.RECONNECT_MIN_DELAY
    # Pas de thread parasite dans les tests qui ne parlent pas de reconnexion.
    store._next_reconnect_at = float("inf")
    store._reconnecting = False
    store._reconnect_guard = threading.Lock()
    return store


# ---------------------------------------------------------------------------
# Repli explicite
# ---------------------------------------------------------------------------
def test_sans_database_url_le_repli_est_declare(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = RegistryStore()
    assert store.available is False
    health = store.health()
    assert health["durable"] is False
    assert health["backend"] == "memory"
    assert "DATABASE_URL" in (health["last_error"] or "")


def test_une_url_non_postgresql_est_refusee(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/x.db")
    store = RegistryStore()
    assert store.available is False
    assert "PostgreSQL" in (store.last_error or "")


def test_un_registre_non_durable_n_ecrit_rien_et_ne_leve_pas():
    store = RegistryStore.__new__(RegistryStore)
    store.url = ""
    store.available = False
    store.last_error = "absent"
    store._conn = None
    # Aucune exception : un journal en panne ne doit jamais casser un paiement.
    store.save_outputs([{"id": "output_1"}])
    store.save_spent("output_1", "aa" * 32, 1.0)
    store.quarantine(["output_1"])
    store.reset()
    assert store.load() == ({}, set(), set(), 0)


# ---------------------------------------------------------------------------
# Rechargement
# ---------------------------------------------------------------------------
def test_le_rechargement_restaure_outputs_key_images_et_depenses():
    connection = FakeConnection(
        output_rows=[
            ("output_7", 3.5, "aa" * 32, "bb" * 32, "cc" * 8, False, True),
            ("output_9", 0.01, "dd" * 32, "ee" * 32, "ff" * 8, True, False),
        ],
        key_images=["11" * 32],
    )
    outputs, key_images, spent, highest = _store_with(connection).load()

    assert set(outputs) == {"output_7", "output_9"}
    assert outputs["output_9"]["internal"] is True
    assert outputs["output_7"]["age_days"] == 3.5
    assert key_images == {"11" * 32}
    assert spent == {"output_7"}
    # Le point critique : le prochain identifiant repart à 10, pas à 1.
    assert highest == 9


def test_un_identifiant_malforme_n_empeche_pas_le_rechargement():
    connection = FakeConnection(
        output_rows=[
            ("output_abc", 1.0, "aa" * 32, "bb" * 32, "cc" * 8, False, False),
            ("output_4", 1.0, "aa" * 32, "bb" * 32, "cc" * 8, False, False),
        ]
    )
    _, _, _, highest = _store_with(connection).load()
    assert highest == 4


# ---------------------------------------------------------------------------
# Écritures
# ---------------------------------------------------------------------------
def test_l_ecriture_des_outputs_est_groupee():
    connection = FakeConnection()
    store = _store_with(connection)
    store.save_outputs(
        [
            {
                "id": "output_1",
                "age_days": 2.0,
                "one_time_public_key_hex": "aa",
                "tx_public_key_hex": "bb",
                "encrypted_amount_hex": "cc",
                "created_at": 1.0,
            },
            {
                "id": "output_2",
                "age_days": 4.0,
                "one_time_public_key_hex": "dd",
                "tx_public_key_hex": "ee",
                "encrypted_amount_hex": "ff",
                "created_at": 2.0,
            },
        ]
    )
    # Un seul `executemany`, donc un seul aller-retour pour N outputs.
    assert len(connection.statements) == 1
    assert len(connection.inserted) == 2
    assert connection.inserted[0][0] == "output_1"


def test_la_depense_marque_l_output_et_ajoute_la_key_image():
    connection = FakeConnection()
    store = _store_with(connection)
    store.save_spent("output_3", "ab" * 32, 123.0)

    sql = [statement[0] for statement in connection.statements]
    assert any("UPDATE outputs SET spent = TRUE" in query for query in sql)
    assert any("INSERT INTO key_images" in query for query in sql)
    # `ON CONFLICT DO NOTHING` : rejouer une dépense ne casse rien.
    assert any("ON CONFLICT (key_image) DO NOTHING" in query for query in sql)


def test_la_depense_est_atomique():
    """R2 : l'output dépensé ET la key image, ensemble ou pas du tout.

    Deux `execute` séparés en autocommit laissaient une fenêtre : si le
    processus mourait entre les deux, la base marquait l'output dépensé mais
    pas la key image. Au redémarrage, `spent_key_images` était incomplet et un
    rejeu de la même key image passait — la protection anti-double-dépense
    était perdue.
    """
    connection = FakeConnection()
    store = _store_with(connection)
    store.save_spent("output_3", "ab" * 32, 123.0)

    assert connection.transactions == ["begin", "commit"], connection.transactions
    # Les deux écritures sont dans la MÊME transaction : elles sont encadrées.
    assert len(connection.statements) == 2


def test_le_portefeuille_est_persiste_et_recharge():
    """C3 : sans persistance du portefeuille, les outputs restaurés ne sont
    plus dépensables après un redémarrage (409 une fois sur deux)."""

    class WalletConnection(FakeConnection):
        def __init__(self):
            super().__init__()
            self.wallet_row = None

        def cursor(self):
            connection = self

            class WalletCursor(FakeCursor):
                def execute(self, sql, params=None):
                    result = super().execute(sql, params)
                    if sql.strip().upper().startswith("SELECT VIEW_PRIVATE"):
                        self._rows = (
                            [connection.wallet_row] if connection.wallet_row else []
                        )
                    return result

            return WalletCursor(connection)

    connection = WalletConnection()
    store = _store_with(connection)

    # Aucun portefeuille : on en crée un.
    assert store.load_wallet() is None
    store.save_wallet("aa" * 32, "bb" * 32, 1.0)
    assert any("INSERT INTO wallet" in query for query, _ in connection.statements)

    # Un portefeuille existe : il est rechargé, donc les outputs restaurés
    # restent dépensables.
    connection.wallet_row = ("cc" * 32, "dd" * 32)
    assert store.load_wallet() == ("cc" * 32, "dd" * 32)


def test_la_restauration_conserve_les_key_images_sans_outputs():
    """R3 : une base dont tous les outputs sont en quarantaine doit quand même
    conserver ses key images, sinon l'anti-double-dépense repart en arrière."""
    connection = FakeConnection(output_rows=[], key_images=["11" * 32])
    outputs, key_images, spent, highest = _store_with(connection).load()

    assert outputs == {}
    assert key_images == {"11" * 32}
    assert highest == 0


def test_une_erreur_d_ecriture_degrade_sans_lever():
    class BrokenConnection:
        def cursor(self):
            raise RuntimeError("connexion perdue")

    store = _store_with(BrokenConnection())
    store.save_outputs(
        [
            {
                "id": "output_1",
                "age_days": 1.0,
                "one_time_public_key_hex": "aa",
                "tx_public_key_hex": "bb",
                "encrypted_amount_hex": "cc",
                "created_at": 1.0,
            }
        ]
    )
    assert store.available is False
    assert "connexion perdue" in (store.last_error or "")
    # §0.6.2 : la perte n'est plus silencieuse.
    assert store.lost_writes == 1


# ---------------------------------------------------------------------------
# §0.6.2 — reconnexion automatique et compteur de pertes
# ---------------------------------------------------------------------------
def _attendre(predicat, timeout=5.0):
    fin = time.monotonic() + timeout
    while time.monotonic() < fin:
        if predicat():
            return True
        time.sleep(0.01)
    return predicat()


def test_la_reconnexion_restaure_available_et_compte_les_pertes(monkeypatch):
    """Une coupure PostgreSQL ne doit plus être définitive.

    `_degrade()` laissait `available=False` pour toujours : les key images
    dépensées pendant la panne étaient perdues, et la double dépense redevenait
    possible après redémarrage. Ici la connexion échoue puis réussit.
    """
    connexion = FakeConnection()
    appels: list[str] = []

    def fake_open(url):
        appels.append(url)
        if len(appels) == 1:
            raise RuntimeError("base injoignable")
        return connexion

    monkeypatch.setattr(registry_module, "_open_connection", fake_open)
    store = RegistryStore(url="postgresql://fake")
    assert store.available is False
    assert store.last_error and "injoignable" in store.last_error

    # Deux écritures pendant l'indisponibilité : comptées, jamais levées.
    store.save_spent("output_1", "aa" * 32, 1.0)
    store.save_outputs(
        [
            {
                "id": "output_2",
                "age_days": 1.0,
                "one_time_public_key_hex": "aa",
                "tx_public_key_hex": "bb",
                "encrypted_amount_hex": "cc",
                "created_at": 1.0,
            }
        ]
    )
    assert store.lost_writes == 2
    assert store.reconnect_attempts == 0
    health = store.health()
    assert health["durable"] is False
    assert health["lost_writes"] == 2
    assert health["reconnect_attempts"] == 0

    # La fenêtre de backoff est écoulée : la tentative suivante réussit.
    store._next_reconnect_at = 0.0
    store.save_spent("output_3", "bb" * 32, 2.0)
    assert store.lost_writes == 3
    assert _attendre(lambda: store.available), "la reconnexion n'a jamais abouti"

    health = store.health()
    assert health["durable"] is True
    assert health["backend"] == "postgresql"
    assert health["reconnect_attempts"] == 1
    assert health["lost_writes"] == 3
    assert appels == ["postgresql://fake", "postgresql://fake"]
    assert store._conn is connexion


def test_une_reconnexion_ratee_ne_leve_pas_et_recule(monkeypatch):
    def fake_open(url):
        raise RuntimeError("toujours injoignable")

    monkeypatch.setattr(registry_module, "_open_connection", fake_open)
    store = RegistryStore(url="postgresql://fake")
    assert store.available is False
    delai_initial = store._reconnect_delay
    store._next_reconnect_at = 0.0

    store.save_spent("output_1", "aa" * 32, 1.0)  # déclenche une tentative
    # Le backoff n'est doublé qu'à la FIN de la tentative ratée : on attend
    # cette borne plutôt que le simple démarrage du thread (course).
    assert _attendre(lambda: store._reconnect_delay > delai_initial)
    assert store.reconnect_attempts >= 1
    assert store.available is False
    assert store.lost_writes == 1
    # Backoff exponentiel borné : le prochain essai est repoussé.
    assert store._next_reconnect_at > time.monotonic()


def test_le_mode_memoire_ne_compte_pas_de_pertes(monkeypatch):
    """Sans DATABASE_URL, il n'y a pas de panne transitoire : `durable: false`
    suffit, `lost_writes` ne doit pas crier au loup."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = RegistryStore()
    store.save_spent("output_1", "aa" * 32, 1.0)
    store.save_outputs(
        [
            {
                "id": "output_1",
                "age_days": 1.0,
                "one_time_public_key_hex": "aa",
                "tx_public_key_hex": "bb",
                "encrypted_amount_hex": "cc",
                "created_at": 1.0,
            }
        ]
    )
    assert store.lost_writes == 0
    assert store.health()["lost_writes"] == 0


# ---------------------------------------------------------------------------
# §0.6.1 — persistance des paiements idempotents
# ---------------------------------------------------------------------------
def test_les_paiements_sont_journalises_et_recharges():
    connexion = FakeConnection()
    store = _store_with(connexion)

    store.save_payment("cmd-1", "ab" * 32, 11, 42, 123.0)
    sql, params = connexion.statements[-1]
    assert "INSERT INTO registry_payments" in sql
    # Le premier paiement fait foi : un rejeu n'écrase pas la key image.
    assert "ON CONFLICT (reference) DO NOTHING" in sql
    assert params[0] == "cmd-1"

    assert store.load_payment("cmd-1") == {
        "key_image": "ab" * 32,
        "ring_size": 11,
        "amount": 42,
        "created_at": 123.0,
    }
    assert store.load_payment("inconnue") is None


def test_un_paiement_est_ecrit_meme_si_le_registre_est_degrade():
    """Une référence sans journal reste servie depuis la mémoire, mais la
    perte est comptée (jamais d'exception dans le paiement)."""
    class BrokenConnection:
        def cursor(self):
            raise RuntimeError("connexion perdue")

    store = _store_with(BrokenConnection())
    store.save_payment("cmd-2", "cd" * 32, 11, 42, 1.0)
    assert store.available is False
    assert store.lost_writes == 1

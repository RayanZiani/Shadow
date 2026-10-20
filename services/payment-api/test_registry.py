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

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) in sys.path:
    sys.path.remove(str(HERE))
sys.path.insert(0, str(HERE))
for _name in ("registry", "main", "config", "svc_auth"):
    _module = sys.modules.get(_name)
    _file = getattr(_module, "__file__", None)
    if _file and pathlib.Path(_file).resolve().parent != HERE:
        del sys.modules[_name]

from registry import RegistryStore  # noqa: E402


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows: list[tuple] = []
        self.description = None

    def execute(self, sql, params=None):
        self.connection.statements.append((sql, params))
        if sql.strip().upper().startswith("SELECT ID, AGE_DAYS"):
            self._rows = list(self.connection.output_rows)
        elif sql.strip().upper().startswith("SELECT KEY_IMAGE"):
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


class FakeConnection:
    def __init__(self, output_rows=None, key_images=None):
        self.statements: list[tuple] = []
        self.inserted: list[tuple] = []
        self.output_rows = output_rows or []
        self.key_images = key_images or []

    def cursor(self):
        return FakeCursor(self)


def _store_with(connection) -> RegistryStore:
    store = RegistryStore.__new__(RegistryStore)
    store.url = "postgresql://fake"
    store.available = True
    store.last_error = None
    store._conn = connection
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

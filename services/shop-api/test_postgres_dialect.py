"""Garde-fou C1 : aucun SQL de la boutique ne doit mélanger booléen et entier.

Le bug C1 était invisible en test parce que SQLite accepte `active = 1` sur une
colonne INTEGER. PostgreSQL, lui, refuse `integer -> boolean` : la boutique ne
démarrait pas.

On ne peut pas lancer PostgreSQL ici, mais on peut interdire le motif fautif :
c'est un test statique, et il vaut mieux qu'aucun test.

**La vraie preuve reste `docker compose up` avec `db-postgres`** — c'est écrit
dans le test, pas caché.
"""
import io
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent


def test_aucun_booleen_compare_a_un_entier():
    """`active = 1`, `active = 0` : interdits (PostgreSQL refuse).

    On autorise `TRUE` / `FALSE`, que SQLite comprend depuis 3.23 et que
    PostgreSQL comprend nativement.
    """
    source = io.open(HERE / "main.py", encoding="utf-8").read()
    offenders = [
        (number, line.strip())
        for number, line in enumerate(source.splitlines(), 1)
        if re.search(r"\bactive\s*=\s*[01]\b", line)
    ]
    assert not offenders, (
        "Comparaison booléen/entier détectée (casse PostgreSQL) :\n"
        + "\n".join(f"  ligne {n}: {text}" for n, text in offenders)
    )


def test_aucun_parametre_entier_pour_la_colonne_active():
    """Les INSERT doivent passer `True`/`False`, pas `1`/`0`.

    psycopg encode `1` en `int4` (OID 23) et PostgreSQL n'a aucun cast
    d'affectation `integer -> boolean`.
    """
    source = io.open(HERE / "main.py", encoding="utf-8").read()

    # Dans les tuples de paramètres des deux INSERT, la valeur qui suit
    # `product["stock"]` / `req.stock` doit être `True`, pas `1`.
    bad = re.findall(r"stock,\s*\n\s*1,\s*\n", source)
    assert not bad, (
        "Un INSERT passe encore `1` pour la colonne `active` : PostgreSQL "
        "refusera (integer -> boolean). Utiliser `True`."
    )


def test_le_schema_declare_bien_un_booleen_cote_postgres():
    """Le schéma PostgreSQL doit être BOOLEAN, pas INTEGER.

    Si quelqu'un « corrige » le bug en repassant la colonne en INTEGER côté
    PostgreSQL, il casse la sémantique au lieu de corriger le SQL.
    """
    source = io.open(HERE / "db.py", encoding="utf-8").read()
    postgres = source.split("SCHEMA_POSTGRES", 1)[1]
    assert "active        BOOLEAN NOT NULL DEFAULT TRUE" in postgres, (
        "Le schéma PostgreSQL doit déclarer `active BOOLEAN`."
    )


def test_les_deux_schemas_acceptent_true_et_false():
    """SQLite doit accepter les littéraux TRUE/FALSE utilisés par le SQL.

    SQLite les comprend depuis 3.23 (2018). Si la version embarquée est plus
    ancienne, le SQL échouerait — on le vérifie explicitement.
    """
    import sqlite3

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE t (active INTEGER)")
        connection.execute("INSERT INTO t VALUES (TRUE)")
        connection.execute("INSERT INTO t VALUES (FALSE)")
        assert connection.execute("SELECT COUNT(*) FROM t WHERE active = TRUE").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM t WHERE active = FALSE").fetchone()[0] == 1
    finally:
        connection.close()

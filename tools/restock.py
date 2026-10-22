#!/usr/bin/env python3
"""Remet le stock de la vitrine à sa valeur de départ.

    python tools/restock.py            # remet les 6 articles au stock du seed
    python tools/restock.py --dry-run  # montre ce qui serait fait
    python tools/restock.py --check    # ne modifie rien, dit si le stock a bougé

Pourquoi ce script existe : `python tools/attack.py shop` contient une attaque
de **survente** volontaire (cinq acheteurs simultanés sur le dernier article).
Elle est censée échouer — le stock ne devient jamais négatif — mais elle vide
réellement l'article ciblé. Après une répétition, la vitrine affiche « rupture »
devant le jury, et l'article n'est plus achetable.

Le stock de référence est lu dans `services/shop-api/main.py` (`CATALOG`), donc
il n'y a pas de deuxième liste à maintenir. Les articles mis à `0` dans le seed
(`AUDIT-J`) le restent : c'est un état de démonstration voulu, qui montre
l'affichage « rupture ».
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN = ROOT / "services" / "shop-api" / "main.py"
DB_SERVICE = "db-postgres"
DB_NAME = "shadow_settle"
DB_USER = "shadow"


def seed_stock() -> dict[str, int]:
    """Extrait {SKU: stock} du CATALOG de shop-api, sans importer le module."""
    source = MAIN.read_text(encoding="utf-8")
    marker = "CATALOG = ["
    start = source.index(marker) + len(marker) - 1
    depth = 0
    for offset, char in enumerate(source[start:], start=start):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = offset + 1
                break
    else:  # pragma: no cover - fichier tronqué
        raise SystemExit(f"CATALOG non terminé dans {MAIN}")
    catalog = ast.literal_eval(source[start:end])
    return {item["sku"]: int(item["stock"]) for item in catalog}


def psql(sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker", "compose", "exec", "-T", DB_SERVICE,
            "psql", "-U", DB_USER, "-d", DB_NAME, "-t", "-A", "-F", "|", "-c", sql,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="n'écrit rien")
    parser.add_argument("--check", action="store_true", help="code 1 si le stock a bougé")
    args = parser.parse_args()

    reference = seed_stock()
    current = psql("SELECT sku, stock FROM products ORDER BY id;")
    if current.returncode != 0:
        print("ECHEC : PostgreSQL injoignable.")
        print(current.stderr.strip()[:400])
        print("  → docker compose up -d db-postgres shop-api")
        return 1

    live: dict[str, int] = {}
    for line in current.stdout.splitlines():
        if "|" in line:
            sku, stock = line.split("|", 1)
            live[sku.strip()] = int(stock)

    drifted = {
        sku: (live[sku], expected)
        for sku, expected in reference.items()
        if sku in live and live[sku] != expected
    }

    if args.check:
        if not drifted:
            print("stock conforme au seed : rien à faire.")
            return 0
        for sku, (now, expected) in drifted.items():
            print(f"  {sku}: {now} en base, {expected} attendu")
        return 1

    if not drifted:
        print("stock conforme au seed : rien à faire.")
        return 0

    statements = [
        f"UPDATE products SET stock = {expected} WHERE sku = '{sku}';"
        for sku, (_, expected) in drifted.items()
    ]
    for sku, (now, expected) in drifted.items():
        print(f"  {sku}: {now} → {expected}")

    if args.dry_run:
        print("(dry-run : rien n'a été écrit)")
        return 0

    result = psql(" ".join(statements))
    if result.returncode != 0:
        print("ECHEC de la mise à jour :")
        print(result.stderr.strip()[:400])
        return 1
    print("stock réinitialisé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

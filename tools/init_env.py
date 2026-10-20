#!/usr/bin/env python3
"""Initialise `.env` avec des secrets ALÉATOIRES — à lancer une fois.

    python tools/init_env.py            # crée .env à partir de .env.example
    python tools/init_env.py --force    # régénère les secrets d'un .env existant

Pourquoi ce script : trois valeurs doivent être imprévisibles (jeton de service,
jeton d'enrôlement du back-office, jeton du bandeau LED). Les laisser vides
ferme les endpoints d'administration — sûr, mais la démonstration échoue ; y
mettre une valeur par défaut serait une faille. La bonne réponse est de les
GÉNÉRER, en une commande, sans les faire passer par un copier-coller.

Le mot de passe PostgreSQL est conservé s'il existe déjà : le changer après le
premier `docker compose up` rendrait le volume `pg-data` inutilisable.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import secrets
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"

# Valeur vide -> secret aléatoire. Le motif est ancré en début de ligne.
GENERATED = {
    "SERVICE_TOKEN": lambda: secrets.token_hex(32),
    "BO_BOOTSTRAP_TOKEN": lambda: secrets.token_urlsafe(24),
    "LED_DEVICE_TOKEN": lambda: secrets.token_hex(16),
    # PIN du terminal : 4 chiffres, mais ALÉATOIRE et jamais une constante du
    # code. L'ancien `1234` codé en dur était une faille volontaire.
    "TERMINAL_PIN": lambda: f"{secrets.randbelow(9000) + 1000}",
    # Généré seulement si absent : voir la note sur pg-data.
    "POSTGRES_PASSWORD": lambda: secrets.token_urlsafe(24),
    "GRAFANA_PASSWORD": lambda: secrets.token_urlsafe(18),
}

KEEP_IF_SET = {"POSTGRES_PASSWORD", "GRAFANA_PASSWORD"}


def set_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(f"{key}={value}", text, count=1)
    return text.rstrip("\n") + f"\n{key}={value}\n"


def get_value(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
    return None if match is None else match.group(1).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="régénère les secrets existants")
    args = parser.parse_args()

    if not EXAMPLE.exists():
        print(f".env.example introuvable : {EXAMPLE}")
        return 2

    if TARGET.exists() and not args.force:
        text = TARGET.read_text(encoding="utf-8")
        created = False
    else:
        if TARGET.exists() and args.force:
            backup = TARGET.with_suffix(".env.bak")
            shutil.copy2(TARGET, backup)
            print(f"sauvegarde : {backup.name}")
        text = EXAMPLE.read_text(encoding="utf-8")
        created = True

    changed = []
    for key, generator in GENERATED.items():
        current = get_value(text, key)
        has_value = bool(current)
        if has_value and (key in KEEP_IF_SET or not args.force):
            continue
        value = generator()
        text = set_value(text, key, value)
        changed.append(key)

    TARGET.write_text(text, encoding="utf-8", newline="")
    print(f"{'créé' if created else 'mis à jour'} : {TARGET.name}")
    if changed:
        print("secrets générés : " + ", ".join(changed))
    else:
        print("aucun secret à générer (déjà renseignés)")
    print("\nProchaines étapes :")
    print("  docker compose build && docker compose up -d")
    print("  python tools/backoffice_check.py")
    print("  ouvrir http://localhost:5173 pour enrôler l'administrateur")
    return 0


if __name__ == "__main__":
    sys.exit(main())

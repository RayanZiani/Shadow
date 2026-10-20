"""Lecture du jeton de service pour les outils de recette (hôte).

Les outils (`attack.py`, `e2e_demo.py`) appellent des endpoints d'administration
protégés par `SERVICE_TOKEN`. Ce jeton vit dans `.env`, que Docker lit mais que
Python ne lit pas automatiquement. Ce module évite d'obliger l'opérateur à
`export SERVICE_TOKEN=…` dans chaque terminal le jour de la démonstration.

Ordre de résolution : variable d'environnement, puis `.env` à la racine du
dépôt. Aucun jeton par défaut : s'il est absent, les appels échouent avec un
message explicite plutôt que de retomber sur une valeur connue.
"""
from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def service_token() -> str:
    token = os.environ.get("SERVICE_TOKEN", "").strip()
    if token:
        return token
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "SERVICE_TOKEN":
                return value.strip().strip('"').strip("'")
    return ""


def headers() -> dict[str, str]:
    token = service_token()
    return {"X-Service-Token": token} if token else {}

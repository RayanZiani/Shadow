"""Authentification service-à-service — les endpoints d'administration sont fermés.

Pourquoi ce fichier existe : les routes `/admin/*` de `payment-api` et du `waf`,
et `/incident` du moteur de remédiation, étaient **ouvertes**. N'importe qui
pouvait mettre 25 outputs en quarantaine, faire passer la taille d'anneau à 64,
armer la déception ou déclencher un playbook. Ce n'est pas une faille
« théorique » : c'est un déni de service à une requête, et le jury CYBER la
trouve en une minute.

Le jeton est partagé entre les services (variable `SERVICE_TOKEN`) et comparé à
temps constant. **Fail-closed** : si `SERVICE_TOKEN` n'est pas configuré, les
routes répondent 503 et `/healthz` le publie (`service_token_configured: false`).
Un défaut connu serait pire qu'une porte fermée : `tools/init_env.py` génère le
jeton au premier démarrage.

Ce module est dupliqué à l'identique dans chaque service : chaque image Docker
est autonome (aucun contexte de construction partagé), et 30 lignes dupliquées
valent mieux qu'un paquet commun qui casse une image sur deux.
"""
from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException

HEADER = "X-Service-Token"


def service_token_configured() -> bool:
    return bool(os.environ.get("SERVICE_TOKEN", "").strip())


def require_service_token(x_service_token: str | None = Header(default=None)) -> None:
    """Dépendance FastAPI : refuse tout appel sans le jeton de service."""
    expected = os.environ.get("SERVICE_TOKEN", "").strip()
    if not expected:
        # Fail-closed : pas de jeton configuré = pas d'administration possible.
        raise HTTPException(
            status_code=503,
            detail=(
                "SERVICE_TOKEN non configuré : les endpoints d'administration "
                "sont fermés. Renseigner SERVICE_TOKEN dans .env "
                "(python tools/init_env.py)."
            ),
        )
    # `.encode()` : `compare_digest` sur des `str` lève TypeError dès qu'un
    # caractère est non-ASCII (un en-tête peut en contenir). Comparer des
    # octets reste à temps constant et ne produit jamais de 500.
    if not x_service_token or not hmac.compare_digest(
        x_service_token.encode(), expected.encode()
    ):
        raise HTTPException(status_code=401, detail="jeton de service invalide")

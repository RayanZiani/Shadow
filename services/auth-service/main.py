"""auth-service — délivre des JWT aux terminaux de paiement.

La clé de signature vit dans Vault, jamais en clair dans le code ni dans un
`.env` livré. Deux modes, tracés dans `/healthz` :

  * `vault`    : clé lue depuis `secret/data/jwt` (KV v2) avec le token de
                 service `VAULT_TOKEN`. Mode attendu en démonstration.
  * `fallback` : `JWT_SIGNING_KEY_CACHE` (poste de dev sans Vault). Le service
                 le DIT au lieu de laisser croire que Vault est branché.

Bootstrap Vault (une fois, en dev) :

    docker compose exec vault sh -c '
      export VAULT_TOKEN=root &&
      vault kv put secret/jwt signing_key=$(openssl rand -hex 32)'

Puis `VAULT_TOKEN=<token> docker compose up -d auth-service`.
"""
import os
import time

import httpx
from fastapi import FastAPI, HTTPException, Response
from jose import jwt
from pydantic import BaseModel

app = FastAPI(title="auth-service")

VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://vault:8200")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN")
JWT_ALGO = "HS256"
# Une clé lue dans Vault est mise en cache : on ne fait pas un aller-retour Vault
# à chaque login, mais on expire le cache pour prendre en compte une rotation.
KEY_CACHE_SECONDS = float(os.environ.get("JWT_KEY_CACHE_SECONDS", "30"))

_key_cache: dict = {"value": None, "fetched_at": 0.0, "source": None}


class LoginRequest(BaseModel):
    merchant_id: str
    api_secret: str  # à vérifier contre Vault, pas une valeur en dur


def _fetch_signing_key() -> tuple[str, str]:
    """Retourne (clé, source). Lève 503 si Vault est configuré mais muet.

    Un fallback silencieux serait le pire des cas : la démo signerait avec une
    clé de dev sans que personne ne le sache, et le dossier technique
    prétendrait le contraire.
    """
    now = time.time()
    if _key_cache["value"] and now - _key_cache["fetched_at"] < KEY_CACHE_SECONDS:
        return _key_cache["value"], _key_cache["source"]

    if VAULT_TOKEN:
        try:
            response = httpx.get(
                f"{VAULT_ADDR}/v1/secret/data/jwt",
                headers={"X-Vault-Token": VAULT_TOKEN},
                timeout=3.0,
            )
            response.raise_for_status()
            key = response.json()["data"]["data"]["signing_key"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"clé JWT indisponible dans Vault ({type(exc).__name__})",
            ) from exc
        _key_cache.update(value=key, fetched_at=now, source="vault")
        return key, "vault"

    fallback = os.environ.get("JWT_SIGNING_KEY_CACHE", "dev-only-placeholder-rotate-me")
    _key_cache.update(value=fallback, fetched_at=now, source="fallback_env")
    return fallback, "fallback_env"


@app.post("/auth/login")
def login(req: LoginRequest):
    # TODO (vous) : vérifier req.api_secret contre la valeur stockée (hashée)
    # pour ce merchant_id, en base ou dans Vault.
    if not req.merchant_id:
        raise HTTPException(status_code=401, detail="merchant inconnu")

    key, _ = _fetch_signing_key()
    payload = {
        "sub": req.merchant_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    token = jwt.encode(payload, key, algorithm=JWT_ALGO)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/metrics")
def metrics():
    """Métriques Prometheus : d'où vient la clé de signature."""
    try:
        _, source = _fetch_signing_key()
        key_ok = True
    except HTTPException:
        source, key_ok = "unavailable", False
    lines = [
        "# HELP shadow_jwt_key_available 1 si la clé de signature est utilisable",
        "# TYPE shadow_jwt_key_available gauge",
        f"shadow_jwt_key_available {1 if key_ok else 0}",
        "# HELP shadow_jwt_key_from_vault 1 si la clé vient de Vault (0 = fallback)",
        "# TYPE shadow_jwt_key_from_vault gauge",
        f"shadow_jwt_key_from_vault {1 if source == 'vault' else 0}",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/healthz")
def healthz():
    try:
        _, source = _fetch_signing_key()
        key_ok = True
    except HTTPException as exc:
        source, key_ok = exc.detail, False
    return {
        "status": "ok" if key_ok else "degraded",
        "jwt_key_source": source,
        "vault_addr": VAULT_ADDR,
    }

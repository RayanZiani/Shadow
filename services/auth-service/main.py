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
import hashlib
import hmac
import json
import os
import time
from collections import defaultdict, deque

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from jose import jwt
from pydantic import BaseModel

app = FastAPI(title="auth-service")

VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://vault:8200")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN")
JWT_ALGO = "HS256"
# Une clé lue dans Vault est mise en cache : on ne fait pas un aller-retour Vault
# à chaque login, mais on expire le cache pour prendre en compte une rotation.
KEY_CACHE_SECONDS = float(os.environ.get("JWT_KEY_CACHE_SECONDS", "30"))

# Anti-bruteforce : 10 tentatives par minute et par IP. Sans cela, un secret de
# 32 octets reste hors de portée, mais un secret faible tomberait en quelques
# heures — et le service sert de porte d'entrée aux terminaux.
LOGIN_WINDOW_SECONDS = 60.0
LOGIN_MAX_ATTEMPTS = 10
_login_attempts: dict[str, deque[float]] = defaultdict(deque)

_key_cache: dict = {"value": None, "fetched_at": 0.0, "source": None}


class LoginRequest(BaseModel):
    merchant_id: str
    api_secret: str


def _merchant_secret_hashes() -> dict[str, str]:
    """Table `merchant_id -> sha256(secret)` lue dans l'environnement.

    Format : `MERCHANT_SECRETS='{"terminal-1": "<sha256 hex>"}'`.
    Un secret d'API est à haute entropie : SHA-256 suffit (pas de PBKDF2, qui
    est fait pour des mots de passe humains). La comparaison reste à temps
    constant.
    """
    raw = os.environ.get("MERCHANT_SECRETS", "").strip()
    if not raw:
        return {}
    try:
        table = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(table, dict):
        return {}
    return {str(key): str(value).lower() for key, value in table.items()}


def _rate_limited(client_ip: str) -> bool:
    now = time.time()
    window = _login_attempts[client_ip]
    while window and now - window[0] > LOGIN_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= LOGIN_MAX_ATTEMPTS:
        return True
    window.append(now)
    return False


def _verify_merchant(merchant_id: str, api_secret: str) -> bool:
    """Vérifie le secret du marchand. Fail-closed si rien n'est configuré.

    La version d'origine acceptait N'IMPORTE QUEL `api_secret` : le service
    délivrait un JWT valide à qui connaissait un simple identifiant. C'était une
    authentification décorative — corrigée ici, et couverte par un test.
    """
    table = _merchant_secret_hashes()
    if not table:
        raise HTTPException(
            status_code=503,
            detail=(
                "MERCHANT_SECRETS non configuré : aucun login possible. "
                "Renseigner la table des secrets (voir .env.example)."
            ),
        )
    expected = table.get(merchant_id)
    if expected is None:
        # Hachage factice : même temps de réponse pour un identifiant inconnu,
        # sinon on peut énumérer les marchands par chronométrage.
        hashlib.sha256(api_secret.encode("utf-8")).hexdigest()
        return False
    provided = hashlib.sha256((api_secret or "").encode("utf-8")).hexdigest()
    return hmac.compare_digest(provided, expected)


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
def login(req: LoginRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(client_ip):
        raise HTTPException(
            status_code=429,
            detail="trop de tentatives de connexion, réessayez dans une minute",
            headers={"Retry-After": str(int(LOGIN_WINDOW_SECONDS))},
        )

    if not req.merchant_id:
        raise HTTPException(status_code=401, detail="merchant inconnu")
    if not _verify_merchant(req.merchant_id, req.api_secret):
        raise HTTPException(status_code=401, detail="identifiants invalides")

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
        "merchants_configured": len(_merchant_secret_hashes()),
        "service_token_configured": bool(os.environ.get("SERVICE_TOKEN", "").strip()),
    }

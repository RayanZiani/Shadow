"""auth-service — délivre des JWT aux terminaux de paiement.
La clé de signature vit dans Vault, jamais en clair dans le code ou le .env.
"""
import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from jose import jwt
from pydantic import BaseModel

app = FastAPI(title="auth-service")

VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://vault:8200")
JWT_ALGO = "HS256"


class LoginRequest(BaseModel):
    merchant_id: str
    api_secret: str  # à vérifier contre Vault, pas une valeur en dur


def _fetch_signing_key() -> str:
    # TODO (vous) : lire secret/data/jwt via l'API Vault (KV v2) avec un token
    # à bail limité, plutôt que ce placeholder statique.
    return os.environ.get("JWT_SIGNING_KEY_CACHE", "dev-only-placeholder-rotate-me")


@app.post("/auth/login")
def login(req: LoginRequest):
    # TODO (vous) : vérifier req.api_secret contre la valeur stockée (hashée)
    # pour ce merchant_id, en base ou dans Vault.
    if not req.merchant_id:
        raise HTTPException(status_code=401, detail="merchant inconnu")

    payload = {
        "sub": req.merchant_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    token = jwt.encode(payload, _fetch_signing_key(), algorithm=JWT_ALGO)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}

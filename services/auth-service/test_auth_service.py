"""Tests de auth-service — la clé JWT ne doit jamais être un secret caché.

    python -m pytest test_auth_service.py -v

Ce qui compte pour la grille (CYBER : gestion des secrets) : le service doit
DIRE d'où vient sa clé. Un fallback silencieux ferait signer la démo avec une
clé de dev pendant que le dossier technique prétend que Vault est branché.
"""
import time

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def reset_cache():
    main._key_cache.update(value=None, fetched_at=0.0, source=None)
    yield
    main._key_cache.update(value=None, fetched_at=0.0, source=None)


@pytest.fixture()
def client():
    return TestClient(main.app)


def test_sans_token_le_service_annonce_le_fallback(client, monkeypatch):
    monkeypatch.setattr(main, "VAULT_TOKEN", None)
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["jwt_key_source"] == "fallback_env"


def test_login_renvoie_un_jwt_valide(client, monkeypatch):
    from jose import jwt

    monkeypatch.setattr(main, "VAULT_TOKEN", None)
    monkeypatch.setenv("JWT_SIGNING_KEY_CACHE", "cle-de-test")
    main._key_cache.update(value=None, fetched_at=0.0, source=None)

    token = client.post(
        "/auth/login", json={"merchant_id": "terminal-1", "api_secret": "x"}
    ).json()["access_token"]
    claims = jwt.decode(token, "cle-de-test", algorithms=["HS256"])
    assert claims["sub"] == "terminal-1"
    assert claims["exp"] - claims["iat"] == 300


def test_merchant_vide_refuse(client):
    assert client.post("/auth/login", json={"merchant_id": "", "api_secret": "x"}).status_code == 401


def test_vault_injoignable_produit_503_pas_un_fallback_silencieux(client, monkeypatch):
    """Vault configuré mais muet : on refuse de signer, on ne bascule pas."""
    monkeypatch.setattr(main, "VAULT_TOKEN", "token-de-test")
    monkeypatch.setattr(main, "VAULT_ADDR", "http://127.0.0.1:1")
    main._key_cache.update(value=None, fetched_at=0.0, source=None)

    response = client.post("/auth/login", json={"merchant_id": "m", "api_secret": "x"})
    assert response.status_code == 503
    assert "Vault" in response.json()["detail"]

    health = client.get("/healthz").json()
    assert health["status"] == "degraded"


def test_la_cle_est_mise_en_cache_puis_rafraichie(client, monkeypatch):
    calls: list[str] = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            calls.append("vault")
            return {"data": {"data": {"signing_key": "cle-vault"}}}

    monkeypatch.setattr(main, "VAULT_TOKEN", "token")
    monkeypatch.setattr(main.httpx, "get", lambda *a, **k: FakeResponse())
    main._key_cache.update(value=None, fetched_at=0.0, source=None)

    assert main._fetch_signing_key() == ("cle-vault", "vault")
    assert main._fetch_signing_key() == ("cle-vault", "vault")
    assert len(calls) == 1, "la clé a été relue alors que le cache était valide"

    # Après expiration du cache, une rotation est bien prise en compte.
    main._key_cache["fetched_at"] = time.time() - main.KEY_CACHE_SECONDS - 1
    main._fetch_signing_key()
    assert len(calls) == 2

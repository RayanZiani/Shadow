"""Tests du honeypot — il ne doit jamais éveiller les soupçons.

    python -m pytest test_shadow_api.py -v

Un honeypot qui répond 403 ou 500 est un honeypot repéré : l'attaquant
comprend qu'il a été déplacé. Ces tests verrouillent le comportement attendu
par la grille (CYBER : déception active) et par le playbook de remédiation.
"""
from fastapi.testclient import TestClient

import main


def _client() -> TestClient:
    main.intercepted_log.clear()
    return TestClient(main.app)


def test_decoys_renvoie_des_canaries_au_bon_format():
    client = _client()
    body = client.get("/decoys").json()
    assert len(body["decoys"]) == 10
    assert all(canary.startswith("canary_") for canary in body["decoys"])


def test_tx_submit_repond_toujours_confirme():
    """Jamais d'erreur visible : l'attaquant doit croire que ça marche."""
    client = _client()
    for payload in ({"amount": 1}, {}, {"decoy_ids": ["a"] * 10}):
        response = client.post("/tx/submit", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "confirmed"


def test_chaque_interception_est_journalisee():
    client = _client()
    client.get("/decoys")
    client.post("/tx/submit", json={"amount": 42})
    entries = client.get("/admin/interceptions").json()
    assert entries["count"] == 2
    assert entries["entries"][-1]["path"] == "/tx/submit"
    assert entries["entries"][-1]["attempted_tx"] == {"amount": 42}


def test_le_journal_est_borne_pour_un_flux_long():
    """Un attaquant qui tape 200 fois ne doit pas faire grossir la réponse."""
    client = _client()
    for _ in range(200):
        client.get("/decoys")
    body = client.get("/admin/interceptions").json()
    assert body["count"] == 200
    assert len(body["entries"]) == 50

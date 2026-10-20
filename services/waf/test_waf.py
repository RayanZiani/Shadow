"""Tests du WAF — la déception active doit être réelle, pas décorative.

    python -m pytest test_waf.py -v

Le point critique : le WAF ne doit JAMAIS dérouter une transaction légitime.
Une déception qui casse le paiement du client est pire que pas de déception.
"""
import json

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def reset_state():
    main.AGE_CACHE.clear()
    main.reset()
    yield
    main.reset()
    main.AGE_CACHE.clear()


@pytest.fixture()
def client():
    return TestClient(main.app)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.content = json.dumps(payload).encode()
        self.headers = {"content-type": "application/json"}

    def json(self):
        return json.loads(self.content)


@pytest.fixture()
def fake_upstream(monkeypatch):
    """Remplace httpx.AsyncClient : on teste le routage, pas le réseau."""
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            calls.append(url)
            if url.endswith("/outputs"):
                return FakeResponse(
                    {
                        "outputs": [
                            {"id": "recent_1", "age_days": 3.0},
                            {"id": "recent_2", "age_days": 4.0},
                            {"id": "old_1", "age_days": 30.0},
                            {"id": "old_2", "age_days": 25.0},
                            {"id": "old_3", "age_days": 40.0},
                        ]
                    }
                )
            if "8090" in url:
                return FakeResponse({"status": "confirmed", "key_image": "canary_ki_42"})
            return FakeResponse({"status": "confirmed", "key_image": "vraie_key_image"})

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    return calls


def test_age_median_ignore_les_outputs_inconnus():
    main.AGE_CACHE.update({"a": 3.0, "b": 4.0})
    assert main._decoy_median_age(["a", "b", "inconnu"]) is None


def test_leurres_recents_ne_sont_pas_suspects():
    main.AGE_CACHE.update({"a": 3.0, "b": 4.0, "c": 5.0})
    suspicious, median = main._is_suspicious({"decoy_ids": ["a", "b", "c"]})
    assert suspicious is False
    assert median == 4.0


def test_leurres_anciens_sont_suspects():
    main.AGE_CACHE.update({"a": 30.0, "b": 25.0, "c": 40.0})
    suspicious, median = main._is_suspicious({"decoy_ids": ["a", "b", "c"]})
    assert suspicious is True
    assert median == 30.0


def test_moins_de_trois_leurres_connus_ne_conclut_pas():
    """Mieux vaut laisser passer que fabriquer un faux positif au bord."""
    main.AGE_CACHE.update({"a": 30.0, "b": 40.0})
    suspicious, median = main._is_suspicious({"decoy_ids": ["a", "b", "c"]})
    assert suspicious is False and median is None


def test_le_waf_apprend_les_ages_en_proxyfiant_le_registre(client, fake_upstream):
    client.get("/outputs")
    assert main.AGE_CACHE["old_1"] == 30.0
    assert main.AGE_CACHE["recent_1"] == 3.0


def test_sans_deception_rien_n_est_deroute(client, fake_upstream):
    main.AGE_CACHE.update({"old_1": 30.0, "old_2": 25.0, "old_3": 40.0})
    response = client.post("/tx/submit", json={"decoy_ids": ["old_1", "old_2", "old_3"]})
    assert response.headers["X-Shadow-Route"] == "upstream"
    assert response.json()["key_image"] == "vraie_key_image"
    assert main.STATE["suspicious_requests"] == 1
    assert main.STATE["intercepted"] == 0


def test_avec_deception_l_attaquant_va_au_honeypot(client, fake_upstream):
    main.AGE_CACHE.update({"old_1": 30.0, "old_2": 25.0, "old_3": 40.0})
    client.post("/admin/redirect", json={"target": "shadow-api:8090", "reason": "test"})

    response = client.post("/tx/submit", json={"decoy_ids": ["old_1", "old_2", "old_3"]})
    assert response.headers["X-Shadow-Route"] == "honeypot"
    assert response.json()["key_image"].startswith("canary")
    assert main.STATE["intercepted"] == 1


def test_avec_deception_le_client_legitime_passe_toujours(client, fake_upstream):
    """LE test qui compte : la déception ne doit pas casser le paiement."""
    main.AGE_CACHE.update({"recent_1": 3.0, "recent_2": 4.0, "recent_3": 3.5})
    client.post("/admin/redirect", json={"target": "shadow-api:8090", "reason": "test"})

    response = client.post("/tx/submit", json={"decoy_ids": ["recent_1", "recent_2", "recent_3"]})
    assert response.headers["X-Shadow-Route"] == "upstream"
    assert response.json()["key_image"] == "vraie_key_image"
    assert main.STATE["intercepted"] == 0


def test_reset_remet_la_deception_a_zero(client):
    client.post("/admin/redirect", json={"target": "shadow-api:8090", "reason": "test"})
    assert client.get("/admin/state").json()["deception"] is True
    client.post("/admin/reset")
    body = client.get("/admin/state").json()
    assert body["deception"] is False
    assert body["intercepted"] == 0


def test_healthz_expose_la_topologie(client):
    body = client.get("/healthz").json()
    assert body["service"] == "waf"
    assert "payment-api" in body["upstream"]
    assert "shadow-api" in body["honeypot"]

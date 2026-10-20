"""Ingestion SIEM : une alerte de supervision doit AGIR, pas seulement s'afficher.

    python -m pytest test_alert_ingest.py -v

Nouvelle règle du jury : « l'IA doit gérer les logs ou alertes des outils de
supervision de sorte à agir elle-même, participer à la remédiation ». Ce fichier
vérifie le maillon manquant : Alertmanager pousse une alerte, le moteur décide
s'il doit ouvrir un incident, et il réutilise EXACTEMENT le chemin de l'incident
ML (même verrou, même playbook, même preuve).

Ce qui ne doit PAS arriver : qu'une alerte de confort (bandeau LED silencieux,
compte bloqué) déclenche un playbook. Sinon le premier avertissement bénin
lance une remédiation en pleine démonstration.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

import main

os.environ["SERVICE_TOKEN"] = "jeton-de-test"
HEADERS = {"X-Service-Token": "jeton-de-test"}


@pytest.fixture(autouse=True)
def stub_incident(monkeypatch):
    """Remplace le playbook : on teste le routage, pas Ansible."""
    calls: list[str] = []

    async def fake_run_incident(payload):
        calls.append(payload.diagnostic)

    monkeypatch.setattr(main, "_run_incident", fake_run_incident)
    main._last_alert_at.clear()
    main._alert_history.clear()
    yield calls
    main._last_alert_at.clear()
    main._alert_history.clear()


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
        headers=HEADERS,
    )


def _alert(name: str, status: str = "firing") -> dict:
    return {
        "status": status,
        "alerts": [
            {
                "status": status,
                "labels": {"alertname": name, "severity": "critical", "fingerprint": name},
                "annotations": {"summary": f"{name} levée"},
            }
        ],
    }


def test_une_alerte_actionnable_ouvre_un_incident(stub_incident):
    async def scenario():
        async with _client() as client:
            response = await client.post("/alert", json=_alert("NoyauCryptoIndisponible"))
            assert response.status_code == 200
            body = response.json()
            assert body["acted"] == 1
            await asyncio.sleep(0.01)  # laisse la tâche de fond démarrer

    asyncio.run(scenario())
    assert stub_incident == ["crypto_unavailable"]


def test_une_alerte_de_confort_est_journalisee_sans_agir(stub_incident):
    async def scenario():
        async with _client() as client:
            response = await client.post("/alert", json=_alert("BandeauLedSilencieux"))
            body = response.json()
            assert body["acted"] == 0
            assert body["ignored"] == 1
            assert body["details"][0]["reason"] == "non_actionnable"

    asyncio.run(scenario())
    assert stub_incident == []


def test_une_alerte_resolue_nouvre_pas_dincident(stub_incident):
    async def scenario():
        async with _client() as client:
            response = await client.post("/alert", json=_alert("WafIndisponible", "resolved"))
            assert response.json()["acted"] == 0

    asyncio.run(scenario())
    assert stub_incident == []


def test_les_doublons_sont_bornees(stub_incident):
    async def scenario():
        async with _client() as client:
            first = await client.post("/alert", json=_alert("SondeTransactionEnEchec"))
            second = await client.post("/alert", json=_alert("SondeTransactionEnEchec"))
            assert first.json()["acted"] == 1
            assert second.json()["acted"] == 0
            assert second.json()["details"][0]["reason"] == "anti_rebond"

    asyncio.run(scenario())
    assert stub_incident == ["probe_failed"]


def test_le_siem_exige_le_jeton_de_service():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            assert (await client.post("/alert", json=_alert("WafIndisponible"))).status_code == 401
            assert (await client.get("/alerts")).status_code == 401

    asyncio.run(scenario())


def test_le_journal_des_alertes_est_consultable(stub_incident):
    async def scenario():
        async with _client() as client:
            await client.post("/alert", json=_alert("ModeleMLIndisponible"))
            journal = await client.get("/alerts")
            assert journal.status_code == 200
            assert journal.json()["count"] >= 1

    asyncio.run(scenario())

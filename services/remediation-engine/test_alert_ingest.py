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
        try:
            calls.append(payload.diagnostic)
        finally:
            # Le vrai `_run_incident` libère `_busy` dans son `finally` : le
            # stub doit faire pareil, sinon le deuxième incident du test est
            # refusé pour une raison qui n'a rien à voir avec le routage.
            main._busy["value"] = False

    monkeypatch.setattr(main, "_run_incident", fake_run_incident)
    main._last_alert_at.clear()
    main._alert_history.clear()
    main._busy["value"] = False
    yield calls
    main._last_alert_at.clear()
    main._alert_history.clear()
    main._busy["value"] = False


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
            # Laisse la tâche de fond libérer `_busy` : sans cela, le second
            # serait refusé pour « remédiation déjà en cours » et non pour
            # anti-rebond — le test ne prouverait plus ce qu'il annonce.
            await asyncio.sleep(0.01)
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


def test_une_alerte_de_log_est_actionnable(stub_incident):
    """Les règles Loki poussent au même Alertmanager, donc au même moteur.

    Une erreur applicative (« paiement refusé ») n'a pas de métrique dédiée :
    sans cette route, elle resterait dans un fichier que personne ne lit.
    """
    async def scenario():
        async with _client() as client:
            response = await client.post("/alert", json=_alert("PaiementRefuse"))
            assert response.json()["acted"] == 1
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert stub_incident == ["payment_refused"]


def test_un_panic_dans_les_logs_ouvre_un_incident(stub_incident):
    async def scenario():
        async with _client() as client:
            await client.post("/alert", json=_alert("RemonteeDeBouclier"))
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert stub_incident == ["panic_in_logs"]


def test_deux_alertes_actionnables_ne_lancent_quun_playbook(stub_incident):
    """E2 : le verrou doit être réservé AVANT `create_task`.

    Avant le correctif, `if _lock.locked()` était testé dans le handler, mais
    la tâche n'avait pas encore pris le verrou : deux alertes actionnables dans
    le même payload lançaient deux playbooks. Alertmanager regroupe par
    `alertname`, donc ce cas reste possible avec deux alertes distinctes.
    """
    async def scenario():
        async with _client() as client:
            payload = {
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "NoyauCryptoIndisponible",
                            "severity": "critical",
                            "fingerprint": "crypto",
                        },
                        "annotations": {},
                    },
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "WafIndisponible",
                            "severity": "critical",
                            "fingerprint": "waf",
                        },
                        "annotations": {},
                    },
                ],
            }
            response = await client.post("/alert", json=payload)
            body = response.json()
            await asyncio.sleep(0.02)
            return body

    body = asyncio.run(scenario())
    assert body["acted"] == 1, body
    assert body["ignored"] == 1, body
    assert body["details"][1]["reason"] == "remediation_deja_en_cours"
    # Un seul playbook lancé, pas deux.
    assert len(stub_incident) == 1, stub_incident

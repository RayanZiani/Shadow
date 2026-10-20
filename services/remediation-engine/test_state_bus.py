"""Test de bout en bout (sans matériel) du contrat d'état
remediation-engine -> {dashboard, pont hardware}.

Lancement :
    py -m venv .venv && .venv\\Scripts\\pip install fastapi uvicorn
    .venv\\Scripts\\python -m pytest test_state_bus.py -v      (ou simplement ce fichier)

Ce test vérifie :
  * l'ordre UNDER_ATTACK -> REMEDIATING -> RECOVERED_TX pendant un incident ;
  * la diffusion SSE à un abonné et la monotonie de `seq` ;
  * qu'un rapport NORMAL_TX ne peut pas écraser un incident en cours ;
  * qu'un abonné qui disparaît ne bloque pas la remédiation ;
  * qu'une publication depuis un AUTRE thread (threadpool) reste sûre :
    `seq` strictement croissants et abonné réveillé sans attendre le ping SSE.
"""
import asyncio
import inspect
import json
import os
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import main
from main import StateBus, app

# /state/report exige le jeton de service (fail-closed sans lui).
os.environ["SERVICE_TOKEN"] = "jeton-de-test"


@pytest.fixture()
def bus():
    return StateBus()


def test_seq_strictement_croissant(bus):
    a = bus.publish("NORMAL_TX")
    b = bus.publish("UNDER_ATTACK")
    c = bus.publish("RECOVERED_TX")
    assert a["seq"] < b["seq"] < c["seq"]
    assert bus.current is c


def test_etat_inconnu_refuse(bus):
    with pytest.raises(ValueError):
        bus.publish("HACKED")


def test_abonne_recupere_etat_courant_a_l_inscription(bus):
    bus.publish("UNDER_ATTACK")

    async def scenario():
        queue = await bus.subscribe()
        first = queue.get_nowait()
        assert first["state"] == "UNDER_ATTACK"
        bus.publish("REMEDIATING")
        second = await queue.get()
        assert second["state"] == "REMEDIATING"

    asyncio.run(scenario())


def test_abonne_lent_ne_bloque_pas(bus):
    async def scenario():
        queue = await bus.subscribe()
        for index in range(200):  # dépasse largement maxsize=64
            bus.publish("REMEDIATING", {"i": index})
        # Aucune exception levée : les événements ont été jetés, tant pis.
        assert queue.qsize() <= 64

    asyncio.run(scenario())


@pytest.fixture()
def client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Service-Token": "jeton-de-test"},
    )


def test_rapport_normal_tx_refuse_pendant_incident():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Service-Token": "jeton-de-test"},
        ) as client:
            from main import bus as live_bus

            live_bus.publish("UNDER_ATTACK")
            response = await client.post("/state/report", json={"state": "NORMAL_TX"})
            assert response.status_code == 200
            body = response.json()
            assert body["accepted"] is False
            assert live_bus.current["state"] == "UNDER_ATTACK"

    asyncio.run(scenario())


def test_rapport_etat_interdit_refuse():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Service-Token": "jeton-de-test"},
        ) as client:
            response = await client.post("/state/report", json={"state": "RECOVERED_TX"})
            assert response.status_code == 400

    asyncio.run(scenario())


def test_healthz_expose_etat_et_seq():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/healthz")
            body = response.json()
            assert body["status"] == "ok"
            assert "state" in body and "seq" in body

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# §0.6.4 — StateBus manipulé depuis le threadpool
# ---------------------------------------------------------------------------
# `/state/report` était un endpoint SYNCHRONE : FastAPI l'exécutait dans le
# threadpool, donc `publish` faisait des `put_nowait` sur des `asyncio.Queue`
# hors boucle et incrémentait `_seq` sans verrou. Deux transactions simultanées
# pouvaient produire le même `seq`, et l'abonné SSE attendait le ping de 15 s.


def test_publish_depuis_un_thread_garde_des_seq_strictement_croissants():
    """20 publications depuis 4 threads : aucun `seq` perdu ni dupliqué."""
    bus = StateBus()

    async def scenario():
        bus.bind_loop()
        queue = await bus.subscribe()
        await queue.get()  # rejoue l'état courant à l'abonnement
        # `pool.map` bloque la boucle : les `call_soon_threadsafe` s'empilent
        # puis sont exécutés dès que l'on rend la main, comme en production.
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: bus.publish("REMEDIATING", {"i": i}), range(20)))
        return [
            await asyncio.wait_for(queue.get(), timeout=2.0) for _ in range(20)
        ]

    evenements = asyncio.run(scenario())
    seqs = [event["seq"] for event in evenements]

    assert len(seqs) == 20, seqs
    assert len(set(seqs)) == 20, seqs  # aucun doublon
    assert seqs == sorted(seqs), seqs  # strictement croissants dans l'ordre reçu
    assert bus.current["seq"] == max(seqs)  # le dernier état gagne, sans écrasement


def test_report_state_est_un_endpoint_asynchrone():
    """Le chemin nominal doit rester sur la boucle, pas dans le threadpool."""
    assert inspect.iscoroutinefunction(main.report_state)


def test_rapport_normal_tx_reveille_labonne_sans_attendre_le_ping():
    """Un rapport publié doit réveiller le SSE immédiatement (pas 15 s plus tard)."""

    async def scenario():
        from main import bus as live_bus

        live_bus.bind_loop()
        live_bus.publish("IDLE", {})  # sort de RECOVERED_TX / UNDER_ATTACK
        queue = await live_bus.subscribe()
        await queue.get()  # état courant rejoué
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Service-Token": "jeton-de-test"},
        ) as client:
            response = await client.post("/state/report", json={"state": "NORMAL_TX"})
            assert response.json()["accepted"] is True, response.json()
        try:
            return await asyncio.wait_for(queue.get(), timeout=2.0)
        finally:
            live_bus.unsubscribe(queue)

    event = asyncio.run(scenario())
    assert event["state"] == "NORMAL_TX", event


# ---------------------------------------------------------------------------
# §0.6.3 — /healthz publie l'incident EN COURS
# ---------------------------------------------------------------------------


def test_healthz_publie_lincident_courant():
    """Le commentaire l'annonçait, le champ manquait : on le vérifie."""

    async def scenario():
        main._current_incident = main.IncidentPayload(
            anomaly_score=1.83,
            features=[0.1, 2.0, 0.5, 0.02, 0.3, 4.0],
            detected_at=1760000000.0,
            diagnostic="poisoned_decoys_suspected",
        )
        main._current_alerts[:] = [
            {
                "alertname": "WafIndisponible",
                "severity": "critical",
                "summary": "WAF muet",
                "received_at": 1760000000.5,
                "fingerprint": "WafIndisponible",
                "diagnostic": "waf_unavailable",
            }
        ]
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/healthz")
                return response.json()
        finally:
            main._current_incident = None
            main._current_alerts.clear()

    body = asyncio.run(scenario())
    incident = body["current_incident"]
    assert incident is not None, body
    assert incident["diagnostic"] == "poisoned_decoys_suspected"
    assert incident["detected_at"] == 1760000000.0
    assert [a["alertname"] for a in incident["alerts"]] == ["WafIndisponible"]


def test_healthz_sans_incident_publie_null():
    """Entre deux transactions, aucun incident courant : le champ vaut None."""
    main._current_incident = None
    main._current_alerts.clear()

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return (await client.get("/healthz")).json()

    body = asyncio.run(scenario())
    assert "current_incident" in body
    assert body["current_incident"] is None


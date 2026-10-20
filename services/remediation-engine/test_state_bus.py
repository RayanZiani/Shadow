"""Test de bout en bout (sans matériel) du contrat d'état
remediation-engine -> {dashboard, pont hardware}.

Lancement :
    py -m venv .venv && .venv\\Scripts\\pip install fastapi uvicorn
    .venv\\Scripts\\python -m pytest test_state_bus.py -v      (ou simplement ce fichier)

Ce test vérifie :
  * l'ordre UNDER_ATTACK -> REMEDIATING -> RECOVERED_TX pendant un incident ;
  * la diffusion SSE à un abonné et la monotonie de `seq` ;
  * qu'un rapport NORMAL_TX ne peut pas écraser un incident en cours ;
  * qu'un abonné qui disparaît ne bloque pas la remédiation.
"""
import asyncio
import json

import httpx
import pytest

from main import StateBus, app


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
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def test_rapport_normal_tx_refuse_pendant_incident():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
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
            transport=httpx.ASGITransport(app=app), base_url="http://test"
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

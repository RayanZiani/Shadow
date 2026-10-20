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

Depuis §0.5.3, ce fichier couvre aussi la CORRÉLATION : une panne réelle produit
3 à 5 alertes en cascade. Elles doivent décrire UN incident (liste publiée dans
`detail.alerts`), et non cinq incidents concurrents dont quatre seraient jetés.
"""
from __future__ import annotations

import asyncio
import os
import subprocess

import httpx
import pytest

import main

os.environ["SERVICE_TOKEN"] = "jeton-de-test"
HEADERS = {"X-Service-Token": "jeton-de-test"}

# Le vrai `_run_incident`, capturé AVANT que la fixture autouse ne le remplace :
# les tests de corrélation ont besoin des états réellement publiés.
_REAL_RUN_INCIDENT = main._run_incident


def _reset_incident_state() -> None:
    """Remet les globals d'incident à zéro entre deux tests."""
    main._busy["value"] = False
    main._current_alerts.clear()
    main._incident_opened_at = None
    main._current_incident = None
    main._last_alert_at.clear()
    main._alert_history.clear()


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
    _reset_incident_state()
    yield calls
    _reset_incident_state()


@pytest.fixture()
def vrai_incident(stub_incident, monkeypatch):
    """Réinstalle le VRAI `_run_incident`, playbook simulé uniquement.

    La corrélation ne se voit que sur les états publiés : on a donc besoin du
    chemin complet (verrou, publication, /history), pas du stub de routage.
    La dépendance explicite envers `stub_incident` garantit l'ordre : le stub
    autouse est posé d'abord, puis écrasé ici.
    """
    monkeypatch.setattr(main, "_run_incident", _REAL_RUN_INCIDENT)
    return stub_incident


def _playbook_simule(appels: list[str], delai: float = 0.05):
    """Playbook simulé : compte les lancements et prend un peu de temps."""

    async def _fake_playbook(diagnostic: str):
        appels.append(diagnostic)
        await asyncio.sleep(delai)
        return subprocess.CompletedProcess(["ansible-playbook"], 0, "probe_ok=True", "")

    return _fake_playbook


async def _attendre_etat(cible: str, timeout: float = 5.0) -> str:
    """Attend que le bus atteigne un état (la remédiation est asynchrone)."""
    boucle = asyncio.get_running_loop()
    fin = boucle.time() + timeout
    while boucle.time() < fin:
        if main.bus.current["state"] == cible:
            return cible
        await asyncio.sleep(0.01)
    return main.bus.current["state"]


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


def test_deux_alertes_du_meme_payload_ne_lancent_quun_playbook(stub_incident):
    """E2 : le verrou doit être réservé AVANT `create_task`.

    Avant le correctif, `if _lock.locked()` était testé dans le handler, mais
    la tâche n'avait pas encore pris le verrou : deux alertes actionnables dans
    le même payload lançaient deux playbooks. Alertmanager regroupe par
    `alertname`, donc ce cas reste possible avec deux alertes distinctes.

    Depuis §0.5.3, la seconde alerte n'est plus jetée : elle est CORRÉLÉE au
    même incident. Un seul playbook, mais deux alertes comptées.
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
    assert body["acted"] == 2, body
    assert body["correlated"] == 1, body
    assert body["ignored"] == 0, body
    assert body["details"][1]["correlated"] is True, body["details"][1]
    # Un seul playbook lancé, pas deux.
    assert len(stub_incident) == 1, stub_incident


# ---------------------------------------------------------------------------
# Corrélation multi-alertes (§0.5.3)
# ---------------------------------------------------------------------------
# Une panne réelle (Vault arrêté, WAF coupé) fait tomber 3 à 5 sondes en
# cascade. Sans corrélation, la première ouvre l'incident et les autres sont
# jetées : le jury voit un incident « mono-alerte » alors que le SI en signale
# cinq. On vérifie ici qu'elles décrivent UN épisode, avec une liste lisible.

RAFALE = (
    "NoyauCryptoIndisponible",
    "WafIndisponible",
    "SondeTransactionEnEchec",
    "ModeleMLIndisponible",
)


def _rafale(noms=RAFALE) -> dict:
    return {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": nom,
                    "severity": "critical",
                    "fingerprint": nom,
                },
                "annotations": {"summary": f"{nom} levée"},
            }
            for nom in noms
        ],
    }


def test_une_rafale_dalertes_produit_un_seul_incident(vrai_incident, monkeypatch):
    """N alertes actionnables dans la fenêtre = 1 playbook, N entrées publiées."""
    monkeypatch.setattr(main, "ATTACK_HOLD_SECONDS", 0.02)
    appels: list[str] = []

    async def scenario():
        monkeypatch.setattr(main, "_run_playbook", _playbook_simule(appels, 0.05))
        queue = await main.bus.subscribe()
        await queue.get()  # rejoue l'état courant à l'abonnement
        evenements: list[dict] = []

        async def collecter():
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=5)
                evenements.append(event)
                if event["state"] in ("RECOVERED_TX", "REMEDIATION_FAILED"):
                    return

        tache = asyncio.create_task(collecter())
        async with _client() as client:
            reponse = await client.post("/alert", json=_rafale())
            corps = reponse.json()
        await asyncio.wait_for(tache, timeout=5)
        main.bus.unsubscribe(queue)
        return corps, evenements

    corps, evenements = asyncio.run(scenario())

    # Une seule remédiation lancée, avec le diagnostic de la PREMIÈRE alerte.
    assert appels == ["crypto_unavailable"], appels
    assert corps["received"] == 4, corps
    assert corps["acted"] == 4, corps
    assert corps["correlated"] == 3, corps
    assert corps["ignored"] == 0, corps

    under_attack = [e for e in evenements if e["state"] == "UNDER_ATTACK"]
    assert len(under_attack) == 1, [e["state"] for e in evenements]
    detail = under_attack[0]["detail"]
    assert detail["diagnostic"] == "crypto_unavailable", detail
    alertes = detail["alerts"]
    assert len(alertes) == 4, alertes
    assert [a["alertname"] for a in alertes] == list(RAFALE), alertes
    # Contrat stable, consommé par /state, /events et /history.
    assert set(alertes[0]) == {
        "alertname",
        "severity",
        "summary",
        "received_at",
        "fingerprint",
        "diagnostic",
    }, alertes[0]
    assert alertes[0]["severity"] == "critical"
    assert alertes[0]["summary"] == "NoyauCryptoIndisponible levée"
    assert alertes[0]["fingerprint"] == "NoyauCryptoIndisponible"

    remediating = [e for e in evenements if e["state"] == "REMEDIATING"]
    assert len(remediating) >= 1
    assert len(remediating[0]["detail"]["alerts"]) == 4, remediating[0]["detail"]
    # Le diagnostic principal n'a pas été écrasé par une alerte ultérieure.
    assert remediating[0]["detail"]["diagnostic"] == "crypto_unavailable"

    # /history porte la liste : l'incident est explicable après coup.
    assert len(main.remediation_log[-1]["alerts"]) == 4


def test_une_alerte_tardive_est_rattachee_a_lincident_courant(
    vrai_incident, monkeypatch
):
    """Pendant REMEDIATING, une alerte dans la fenêtre rejoint l'incident."""
    monkeypatch.setattr(main, "ATTACK_HOLD_SECONDS", 0.0)
    appels: list[str] = []

    async def scenario():
        monkeypatch.setattr(main, "_run_playbook", _playbook_simule(appels, 0.4))
        async with _client() as client:
            await client.post("/alert", json=_alert("NoyauCryptoIndisponible"))
            etat = await _attendre_etat("REMEDIATING")
            assert etat == "REMEDIATING", etat
            seq_avant = main.bus.current["seq"]

            reponse = await client.post("/alert", json=_alert("WafIndisponible"))
            corps = reponse.json()
            publie = (await client.get("/state")).json()

            await _attendre_etat("RECOVERED_TX")
            return corps, publie, seq_avant

    corps, publie, seq_avant = asyncio.run(scenario())

    # La réponse HTTP dit ce qui s'est passé : agi ET corrélé, pas ignoré.
    assert corps["acted"] == 1, corps
    assert corps["correlated"] == 1, corps
    assert corps["details"][0]["correlated"] is True, corps["details"][0]

    # L'état publié est republié tout de suite (nouveau seq, abonné SSE réveillé).
    assert publie["state"] == "REMEDIATING", publie
    assert publie["seq"] > seq_avant, (seq_avant, publie["seq"])
    assert [a["alertname"] for a in publie["detail"]["alerts"]] == [
        "NoyauCryptoIndisponible",
        "WafIndisponible",
    ], publie["detail"]
    assert publie["detail"]["diagnostic"] == "crypto_unavailable"

    # Toujours un seul playbook, et /history garde les deux alertes.
    assert appels == ["crypto_unavailable"], appels
    assert [a["alertname"] for a in main.remediation_log[-1]["alerts"]] == [
        "NoyauCryptoIndisponible",
        "WafIndisponible",
    ]


def test_une_alerte_hors_fenetre_est_ignoree(vrai_incident, monkeypatch):
    """Hors fenêtre : refus explicite, pas de rattachement silencieux."""
    monkeypatch.setattr(main, "ATTACK_HOLD_SECONDS", 0.0)
    monkeypatch.setattr(main, "CORRELATION_WINDOW_SECONDS", 0.0)
    appels: list[str] = []

    async def scenario():
        monkeypatch.setattr(main, "_run_playbook", _playbook_simule(appels, 0.4))
        async with _client() as client:
            await client.post("/alert", json=_alert("NoyauCryptoIndisponible"))
            assert await _attendre_etat("REMEDIATING") == "REMEDIATING"
            await asyncio.sleep(0.02)  # sort de la fenêtre (0 s)
            corps = (await client.post("/alert", json=_alert("WafIndisponible"))).json()
            alertes = list(main.bus.current["detail"]["alerts"])
            await _attendre_etat("RECOVERED_TX")
            return corps, alertes

    corps, alertes = asyncio.run(scenario())

    assert corps["acted"] == 0, corps
    assert corps["ignored"] == 1, corps
    assert corps["details"][0]["reason"] == "hors_fenetre_de_correlation", corps
    # La liste publiée n'a pas bougé : l'alerte n'appartient pas à l'épisode.
    assert len(alertes) == 1, alertes
    assert appels == ["crypto_unavailable"], appels


def test_une_alerte_non_actionnable_reste_ignoree_pendant_lincident(
    vrai_incident, monkeypatch
):
    """La corrélation ne doit pas ouvrir la porte aux alertes de confort."""
    monkeypatch.setattr(main, "ATTACK_HOLD_SECONDS", 0.0)
    appels: list[str] = []

    async def scenario():
        monkeypatch.setattr(main, "_run_playbook", _playbook_simule(appels, 0.4))
        async with _client() as client:
            await client.post("/alert", json=_alert("NoyauCryptoIndisponible"))
            assert await _attendre_etat("REMEDIATING") == "REMEDIATING"
            corps = (
                await client.post("/alert", json=_alert("BandeauLedSilencieux"))
            ).json()
            alertes = list(main.bus.current["detail"]["alerts"])
            await _attendre_etat("RECOVERED_TX")
            return corps, alertes

    corps, alertes = asyncio.run(scenario())

    assert corps["acted"] == 0, corps
    assert corps["correlated"] == 0, corps
    assert corps["details"][0]["reason"] == "non_actionnable", corps
    assert len(alertes) == 1, alertes
    assert appels == ["crypto_unavailable"], appels

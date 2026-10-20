"""Test d'intégration : séquence complète d'un incident, sans Ansible.

    python test_incident_flow.py

Vérifie le contrat que consomment le dashboard et le terminal :
  * /incident répond IMMÉDIATEMENT (202) et lance la remédiation en fond ;
  * le bus publie UNDER_ATTACK -> REMEDIATING -> RECOVERED_TX dans l'ordre ;
  * un échec de playbook publie REMEDIATION_FAILED ;
  * un second incident est refusé pendant qu'une remédiation tourne ;
  * un rapport NORMAL_TX ne peut pas masquer une attaque en cours.
"""
import asyncio
import os
import subprocess

import httpx

import main

# Les endpoints /incident et /state/report exigent le jeton de service
# (fail-closed : sans jeton, ils répondent 503). Le jeton est lu à chaque appel,
# donc le définir ici suffit pour tout le module.
os.environ["SERVICE_TOKEN"] = "jeton-de-test"
SERVICE_HEADERS = {"X-Service-Token": "jeton-de-test"}


def _stub(returncode: int, stdout: str):
    async def _fake_playbook(diagnostic: str):
        await asyncio.sleep(0.05)
        return subprocess.CompletedProcess(["ansible-playbook"], returncode, stdout, "")

    return _fake_playbook


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
        headers=SERVICE_HEADERS,
    )


INCIDENT = {
    "anomaly_score": 1.83,
    "features": [0.1, 2.0, 0.5, 0.02, 0.3, 4.0],
    "detected_at": 1760000000.0,
    "diagnostic": "poisoned_decoys_suspected",
}


async def wait_for_state(expected: str, timeout: float = 10.0) -> str:
    """Attend que le bus atteigne un état (la remédiation est asynchrone)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if main.bus.current["state"] == expected:
            return expected
        await asyncio.sleep(0.02)
    return main.bus.current["state"]


async def run_success():
    main._run_playbook = _stub(0, "probe_ok=True")
    async with _client() as client:
        response = await client.post("/incident", json=INCIDENT)
        assert response.status_code == 200, response.text
        body = response.json()
        # Réponse immédiate : indispensable, le détecteur a un timeout de 5 s.
        assert body["accepted"] is True, body
        assert body["status"] == "remediation_started", body

    state = await wait_for_state("RECOVERED_TX")
    assert state == "RECOVERED_TX", state
    assert main.remediation_log[-1]["probe_ok"] is True


async def run_failure():
    main._run_playbook = _stub(2, "probe_ok=False")
    async with _client() as client:
        await client.post("/incident", json=INCIDENT)
    state = await wait_for_state("REMEDIATION_FAILED")
    assert state == "REMEDIATION_FAILED", state


async def run_concurrency():
    """Pendant une remédiation, un second incident doit être refusé."""
    slow_started = asyncio.Event()

    async def slow_playbook(diagnostic: str):
        slow_started.set()
        await asyncio.sleep(1.0)
        return subprocess.CompletedProcess(["ansible-playbook"], 0, "probe_ok=True", "")

    main._run_playbook = slow_playbook
    async with _client() as client:
        first = await client.post("/incident", json=INCIDENT)
        assert first.json()["accepted"] is True
        await asyncio.wait_for(slow_started.wait(), timeout=5)
        second = await client.post("/incident", json=INCIDENT)
        assert second.json()["accepted"] is False, second.json()
    await wait_for_state("RECOVERED_TX")


async def run_ordering():
    """L'ordre des états publiés doit être stable : c'est ce que filme le clip."""
    main._run_playbook = _stub(0, "probe_ok=True")
    main.bus._seq = 0
    main.bus._current = main.bus._make("IDLE", {})
    seen: list[str] = []
    queue = await main.bus.subscribe()

    async def collect():
        # Le premier événement est l'état courant rejoué à l'abonnement :
        # c'est le comportement voulu (resynchronisation), on l'ignore ici.
        await queue.get()
        while len(seen) < 3:
            event = await queue.get()
            seen.append(event["state"])

    task = asyncio.create_task(collect())
    async with _client() as client:
        await client.post("/incident", json=INCIDENT)
    await asyncio.wait_for(task, timeout=10)
    main.bus.unsubscribe(queue)
    assert seen[:3] == ["UNDER_ATTACK", "REMEDIATING", "RECOVERED_TX"], seen


async def run_state_report_guard():
    main.bus.publish("UNDER_ATTACK", {"test": True})
    async with _client() as client:
        report = await client.post("/state/report", json={"state": "NORMAL_TX"})
    assert report.json()["accepted"] is False, report.json()
    assert main.bus.current["state"] == "UNDER_ATTACK"


async def main_async():
    await run_success()
    print("OK — incident nominal : UNDER_ATTACK -> REMEDIATING -> RECOVERED_TX")

    await run_failure()
    print("OK — échec de playbook : REMEDIATION_FAILED")

    await run_concurrency()
    print("OK — verrou anti-double-déclenchement")

    await run_ordering()
    print("OK — ordre des états stable")

    await run_state_report_guard()
    print("OK — un rapport NORMAL_TX ne masque pas une attaque")


if __name__ == "__main__":
    asyncio.run(main_async())


def test_une_erreur_du_playbook_publie_un_etat_terminal():
    """E1 : une erreur d'exécution ne doit jamais laisser l'état en REMEDIATING.

    `_run_playbook` ne capturait que FileNotFoundError et TimeoutExpired. Un
    PermissionError (binaire non exécutable) ou un IsADirectoryError remontait,
    la tâche de fond mourait, et le dashboard restait orange indéfiniment.
    """
    import asyncio
    import os
    import tempfile

    import main

    async def scenario():
        # `ANSIBLE_BIN` pointe un DOSSIER : subprocess.run lève une OSError
        # (PermissionError ou IsADirectoryError selon la plateforme).
        directory = tempfile.mkdtemp()
        previous = main.ANSIBLE_BIN
        main.ANSIBLE_BIN = directory
        try:
            result = await main._run_playbook("test")
        finally:
            main.ANSIBLE_BIN = previous
            os.rmdir(directory)
        return result

    result = asyncio.run(scenario())
    # Un code retour non nul explicite, pas une exception qui remonte.
    assert result.returncode != 0
    assert result.returncode in (126, 127, 124)
    assert result.stderr


def test_un_incident_qui_echoue_laisse_un_etat_terminal():
    """L'état ne doit jamais rester bloqué en REMEDIATING."""
    import asyncio

    import main

    async def scenario():
        async with _client() as client:
            main._busy["value"] = False
            response = await client.post("/incident", json=INCIDENT)
            assert response.status_code == 200
            for _ in range(200):
                await asyncio.sleep(0.05)
                if main.bus.current["state"] in (
                    "RECOVERED_TX",
                    "REMEDIATION_FAILED",
                ):
                    break
            return main.bus.current["state"]

    state = asyncio.run(scenario())
    assert state in ("RECOVERED_TX", "REMEDIATION_FAILED"), state

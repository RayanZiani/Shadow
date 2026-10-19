"""remediation-engine — reçoit le webhook de ml-detector et déclenche
remediation_playbook.yml. AUCUNE intervention humaine : toute la séquence
(honeypot, quarantaine, durcissement, rotation Vault, preuve de tx) tourne
seule. C'est l'axe le plus lourd de la grille (5 pts local / 25 pts national) —
testez-le en boucle (chaos engineering) bien avant le Jour 4.
"""
import asyncio
import os
import subprocess
import time

from fastapi import FastAPI
from pydantic import BaseModel

ANSIBLE_PLAYBOOK = os.environ.get("ANSIBLE_PLAYBOOK", "/ansible/remediation_playbook.yml")

app = FastAPI(title="remediation-engine")

remediation_log: list[dict] = []
_lock = asyncio.Lock()


class IncidentPayload(BaseModel):
    anomaly_score: float
    features: list[float]
    detected_at: float
    diagnostic: str


@app.post("/incident")
async def handle_incident(payload: IncidentPayload):
    async with _lock:  # une remédiation à la fois, pas de double déclenchement
        start = time.time()
        result = await _run_playbook(payload.diagnostic)
        entry = {
            "detected_at": payload.detected_at,
            "diagnostic": payload.diagnostic,
            "anomaly_score": payload.anomaly_score,
            "duration_seconds": time.time() - start,
            "ansible_rc": result.returncode,
            "ansible_stdout_tail": result.stdout[-2000:],
        }
        remediation_log.append(entry)
        return entry


async def _run_playbook(diagnostic: str) -> subprocess.CompletedProcess:
    # Le token Vault est récupéré par le playbook lui-même (rôle AppRole à
    # portée restreinte) — remediation-engine ne porte jamais de privilège
    # root ni de token large, cf. indicateur Axe 2 ("accès maîtrisés").
    return await asyncio.to_thread(
        subprocess.run,
        [
            "ansible-playbook",
            ANSIBLE_PLAYBOOK,
            "-e",
            f"diagnostic={diagnostic}",
        ],
        capture_output=True,
        text=True,
        timeout=15,  # objectif < 8s réel, cf. plan — le timeout est un garde-fou
    )


@app.get("/history")
def history():
    return {"count": len(remediation_log), "entries": remediation_log[-20:]}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}

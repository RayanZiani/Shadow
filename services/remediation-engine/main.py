"""remediation-engine — reçoit le webhook de ml-detector et déclenche
remediation_playbook.yml. AUCUNE intervention humaine : toute la séquence
(honeypot, quarantaine, durcissement, rotation Vault, preuve de tx) tourne
seule. C'est l'axe le plus lourd de la grille (5 pts local / 25 pts national) —
testez-le en boucle (chaos engineering) bien avant le Jour 4.

Ce service est aussi la SOURCE DE VÉRITÉ de l'état du système, consommée à
l'identique par le dashboard (SSE /events) et par l'objet physique via
hardware/bridge/hardware_bridge.py. Voir docs/hardware/CONCEPT-PHYSIQUE.md.

Contrat d'état (le même pour tous les rendus) :
    {"seq": 42, "state": "UNDER_ATTACK", "ts": 1760000000.0, "detail": {...}}
"""
import asyncio
import json
import os
import subprocess
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

ANSIBLE_PLAYBOOK = os.environ.get("ANSIBLE_PLAYBOOK", "/ansible/remediation_playbook.yml")
# Binaire Ansible. Configurable pour pouvoir rejouer la chaîne sur un poste sans
# Ansible installé (recette, CI) sans modifier le code.
ANSIBLE_BIN = os.environ.get("ANSIBLE_BIN", "ansible-playbook")

# États autorisés — doivent rester alignés avec le firmware ESP32
# (hardware/ring_signer_hsm/ring_signer_hsm.ino) et le dashboard.
STATES = (
    "IDLE",
    "NORMAL_TX",
    "UNDER_ATTACK",
    "REMEDIATING",
    "RECOVERED_TX",
    "REMEDIATION_FAILED",
)

# Durée minimale d'affichage de l'état UNDER_ATTACK avant de passer à
# REMEDIATING. Sans elle, la détection et le lancement d'Ansible s'enchaînent en
# quelques millisecondes : ni le terminal physique ni le dashboard n'ont le
# temps d'afficher le rouge, et le jury ne voit pas la détection. C'est un choix
# de mise en scène assumé, documenté dans le dossier technique.
ATTACK_HOLD_SECONDS = float(os.environ.get("ATTACK_HOLD_SECONDS", "3"))

# Durée pendant laquelle la preuve de reprise reste affichée avant que les
# transactions courantes ne reprennent la main. Assez long pour être lu et
# filmé, assez court pour ne pas figer le dashboard pendant la soutenance.
RECOVERED_HOLD_SECONDS = 10.0


class StateBus:
    """Bus d'états minimal : dernier état + diffusion aux abonnés SSE.

    Aucun abonné n'est obligatoire : si le dashboard et le pont hardware sont
    absents, publish() ne bloque jamais et ne lève jamais. C'est la garantie
    que l'objet physique ne peut pas casser la remédiation.
    """

    def __init__(self) -> None:
        self._seq = 0
        self._current: dict = self._make("IDLE", {})
        self._subscribers: set[asyncio.Queue] = set()

    def _make(self, state: str, detail: dict) -> dict:
        self._seq += 1
        return {"seq": self._seq, "state": state, "ts": time.time(), "detail": detail}

    @property
    def current(self) -> dict:
        return self._current

    def publish(self, state: str, detail: dict | None = None) -> dict:
        if state not in STATES:
            raise ValueError(f"état inconnu: {state!r} (attendu: {STATES})")
        event = self._make(state, detail or {})
        self._current = event
        # Copie de la liste : un abonné peut se désinscrire pendant l'itération.
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Abonné trop lent : on préfère perdre un événement que bloquer
                # la remédiation. Il resynchronisera via GET /state.
                pass
        return event

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        # Rejoue immédiatement l'état courant pour que l'abonné s'aligne.
        queue.put_nowait(self._current)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)


bus = StateBus()

remediation_log: list[dict] = []
_lock = asyncio.Lock()
_current_incident = None  # IncidentPayload en cours, pour /healthz


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Point d'entrée de l'état : le service démarre au repos.
    bus.publish("IDLE", {"reason": "startup"})
    yield


app = FastAPI(title="remediation-engine", lifespan=lifespan)

# Le dashboard (port 5173) consomme /events et /state en direct. Sans CORS, le
# navigateur bloque : la démo afficherait un écran vide. Origines restreintes
# au poste hôte et au conteneur front, pas de "*" en production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
    ).split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class IncidentPayload(BaseModel):
    anomaly_score: float
    features: list[float]
    detected_at: float
    diagnostic: str


class StateReport(BaseModel):
    """Rapport d'un fait constaté par un autre service (ex: tx confirmée).

    Ce n'est PAS un déclencheur de remédiation : seuls /incident et le webhook
    ml-detector peuvent lancer le playbook. Un rapport ne peut donc pas
    "réparer" le système, il ne fait que refléter un fait déjà vérifié.
    """

    state: str
    detail: dict = {}


@app.post("/incident")
async def handle_incident(payload: IncidentPayload):
    """Reçoit l'incident et lance la remédiation en TÂCHE DE FOND.

    Pourquoi ne pas attendre la fin du playbook dans la requête : un playbook
    réel prend 4 à 8 s. Le détecteur appelait ce webhook de façon synchrone
    avec un timeout de 5 s, et tombait en ReadTimeout alors que la remédiation
    fonctionnait — panne constatée en recette. On répond donc 202 tout de
    suite, et l'état est publié au fil de l'exécution via /events.
    """
    if _lock.locked():
        # Un incident est déjà en cours : on refuse explicitement plutôt que
        # de mettre en file. Le détecteur a son propre cooldown.
        return {"accepted": False, "reason": "remédiation déjà en cours"}

    _current_incident = payload
    asyncio.create_task(_run_incident(payload))
    return {"accepted": True, "status": "remediation_started"}


async def _run_incident(payload: IncidentPayload) -> None:
    global _current_incident
    async with _lock:  # une remédiation à la fois, pas de double déclenchement
        start = time.time()
        bus.publish(
            "UNDER_ATTACK",
            {
                "anomaly_score": payload.anomaly_score,
                "diagnostic": payload.diagnostic,
            },
        )
        # Laisse le temps aux rendus (dashboard, terminal) de montrer la
        # détection avant de basculer sur REMEDIATING. Voir ATTACK_HOLD_SECONDS.
        await asyncio.sleep(ATTACK_HOLD_SECONDS)
        bus.publish("REMEDIATING", {"diagnostic": payload.diagnostic})

        result = await _run_playbook(payload.diagnostic)
        probe_ok = _probe_ok(result)

        entry = {
            "detected_at": payload.detected_at,
            "diagnostic": payload.diagnostic,
            "anomaly_score": payload.anomaly_score,
            "duration_seconds": time.time() - start,
            "ansible_rc": result.returncode,
            "probe_ok": probe_ok,
            "ansible_stdout_tail": result.stdout[-2000:],
        }
        remediation_log.append(entry)

        if result.returncode == 0 and probe_ok:
            bus.publish("RECOVERED_TX", {"duration_seconds": entry["duration_seconds"]})
        else:
            bus.publish(
                "REMEDIATION_FAILED",
                {"ansible_rc": result.returncode, "probe_ok": probe_ok},
            )
        _current_incident = None


def _probe_ok(result: subprocess.CompletedProcess) -> bool:
    """La preuve de reprise est la sortie de l'étape 4 du playbook.

    Tant que le playbook utilise `ignore_errors`, le code retour d'Ansible vaut
    toujours 0 : on lit donc explicitement le statut de la sonde dans la sortie.
    Si l'information est absente (playbook pas encore durci), on retombe sur le
    code retour — et on l'assume dans le dossier technique.
    """
    stdout = result.stdout or ""
    if "probe_ok=True" in stdout:
        return True
    if "probe_ok=False" in stdout:
        return False
    return result.returncode == 0


async def _run_playbook(diagnostic: str) -> subprocess.CompletedProcess:
    # Le token Vault est récupéré par le playbook lui-même (rôle AppRole à
    # portée restreinte) — remediation-engine ne porte jamais de privilège
    # root ni de token large, cf. indicateur Axe 2 ("accès maîtrisés").
    try:
        return await asyncio.to_thread(
            subprocess.run,
            [
                ANSIBLE_BIN,
                ANSIBLE_PLAYBOOK,
                "-e",
                f"diagnostic={diagnostic}",
            ],
            capture_output=True,
            text=True,
            timeout=15,  # objectif < 8s réel, cf. plan — le timeout est un garde-fou
        )
    except FileNotFoundError:
        # Ansible absent (poste de dev, conteneur mal monté) : on publie un
        # échec explicite plutôt qu'une 500. Le terminal passe en rouge et le
        # dashboard garde la trace — c'est plus utile en recette.
        return subprocess.CompletedProcess(
            args=[ANSIBLE_BIN], returncode=127, stdout="", stderr="ansible-playbook introuvable"
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=[ANSIBLE_BIN],
            returncode=124,
            stdout=(exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr="timeout",
        )


@app.get("/state")
def state():
    """État courant — utilisé par le pont hardware à la (re)connexion."""
    return bus.current


@app.post("/state/report")
def report_state(report: StateReport):
    """Rapport d'un fait vérifié par un autre service (typiquement payment-api
    qui confirme une transaction). Volontairement limité à NORMAL_TX : un
    rapport ne doit jamais pouvoir masquer une attaque en cours ni simuler une
    reprise. Les états de remédiation restent pilotés par /incident seul.
    """
    if report.state != "NORMAL_TX":
        raise HTTPException(status_code=400, detail="seul NORMAL_TX peut être rapporté")
    if bus.current["state"] in ("UNDER_ATTACK", "REMEDIATING"):
        return {"accepted": False, "reason": "incident en cours", "state": bus.current}
    # On garde la preuve de reprise affichée quelques secondes (lisible au clip),
    # puis les transactions courantes reprennent la main normalement.
    if (
        bus.current["state"] == "RECOVERED_TX"
        and time.time() - bus.current["ts"] < RECOVERED_HOLD_SECONDS
    ):
        return {
            "accepted": False,
            "reason": "preuve de reprise en cours d'affichage",
            "state": bus.current,
        }
    return {"accepted": True, "state": bus.publish("NORMAL_TX", report.detail)}


@app.get("/events")
async def events():
    """Flux SSE consommé par le dashboard et par le pont hardware.

    Un commentaire `: ping` est émis toutes les 15 s : sans lui, les proxys et
    les sockets Windows ferment une connexion inactive, et le pont se
    reconnecte en boucle — donc rate des états pendant la démo.
    """

    async def stream():
        queue = await bus.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                payload = json.dumps(event, separators=(",", ":"))
                yield f"data: {payload}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/history")
def history():
    return {"count": len(remediation_log), "entries": remediation_log[-20:]}


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "state": bus.current["state"],
        "seq": bus.current["seq"],
        "remediation_in_progress": _lock.locked(),
        "last_incident": remediation_log[-1] if remediation_log else None,
    }

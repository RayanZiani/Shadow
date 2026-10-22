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
import logging
import os
import subprocess
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
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


def _put_nowait(queue: asyncio.Queue, event: dict) -> None:
    """Dépose un événement sans jamais bloquer (abonné trop lent = perdu).

    Un abonné qui ne suit pas perd l'événement : il se resynchronise via
    GET /state à la requête suivante. Mieux vaut perdre un état que figer une
    remédiation derrière un dashboard qui ne lit plus.
    """
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        pass


class StateBus:
    """Bus d'états minimal : dernier état + diffusion aux abonnés SSE.

    Aucun abonné n'est obligatoire : si le dashboard et le pont hardware sont
    absents, publish() ne bloque jamais et ne lève jamais. C'est la garantie
    que l'objet physique ne peut pas casser la remédiation.

    Thread-safety (§0.6.4) : `publish` peut être appelé depuis le threadpool
    (endpoint synchrone, tâche `asyncio.to_thread`) et pas seulement depuis la
    boucle. `asyncio.Queue` n'étant pas thread-safe, les `put_nowait` sont
    planifiés sur la boucle propriétaire des abonnés via
    `loop.call_soon_threadsafe` ; `_seq` et `_current` sont protégés par un
    verrou `threading`. Sans cela, deux publications simultanées produisaient
    le même `seq` et le réveil SSE attendait le ping de 15 s.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seq = 0
        self._current: dict = self._make("IDLE", {})
        self._subscribers: set[asyncio.Queue] = set()

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Mémorise la boucle propriétaire des abonnés (au démarrage).

        Sans elle, un `publish` venu d'un autre thread n'a aucun moyen de
        réveiller les `asyncio.Queue` : il devrait attendre le ping SSE de 15 s.
        """
        self._loop = loop or asyncio.get_running_loop()

    def _make(self, state: str, detail: dict) -> dict:
        # Incrémente `_seq` : à appeler verrou tenu (ou avant tout partage de
        # l'instance). `_make` est aussi utilisé par les tests pour remettre le
        # bus à zéro, d'où l'absence de prise de verrou ici (pas de réentrance).
        self._seq += 1
        return {"seq": self._seq, "state": state, "ts": time.time(), "detail": detail}

    @property
    def current(self) -> dict:
        return self._current

    def publish(self, state: str, detail: dict | None = None) -> dict:
        if state not in STATES:
            raise ValueError(f"état inconnu: {state!r} (attendu: {STATES})")
        with self._lock:
            event = self._make(state, detail or {})
            self._current = event
            # Diffusion SOUS le verrou : sans cela, un thread peut publier
            # `seq=5`, être préempté, et laisser `seq=6` arriver avant lui chez
            # l'abonné. Aucun appel bloquant ici : `put_nowait` et
            # `call_soon_threadsafe` rendent la main immédiatement.
            # Copie de la liste : un abonné peut se désinscrire pendant l'itération.
            self._dispatch(event, list(self._subscribers))
        return event

    def _dispatch(self, event: dict, subscribers: list[asyncio.Queue]) -> None:
        """Réveille les abonnés, y compris depuis un thread qui n'est pas la boucle."""
        if not subscribers:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            # Appel hors boucle (threadpool) : c'est justement le cas à couvrir.
            running = None
        loop = self._loop or running
        if loop is None:
            # Aucune boucle connue : les abonnés se resynchroniseront via
            # GET /state. On ne bloque pas, on ne lève pas.
            return
        if loop is running:
            # Chemin nominal (boucle du service) : dépôt direct, sans surcoût.
            for queue in subscribers:
                _put_nowait(queue, event)
            return
        for queue in subscribers:
            try:
                loop.call_soon_threadsafe(_put_nowait, queue, event)
            except RuntimeError:
                # Boucle fermée (service qui s'arrête) : on abandonne.
                break

    async def subscribe(self) -> asyncio.Queue:
        # S'abonner se fait forcément depuis la boucle qui consommera la file :
        # c'est le moment le plus fiable pour (re)mémoriser cette boucle.
        self.bind_loop()
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

# Alertes corrélées à l'incident en cours (§0.5.3). Une panne réelle produit 3 à
# 5 alertes en cascade : elles décrivent le MÊME incident, pas cinq. Cette liste
# est publiée dans `detail.alerts` des états UNDER_ATTACK / REMEDIATING et
# recopiée dans /history à la fin du playbook.
_current_alerts: list[dict] = []
# Horodatage d'ouverture de l'incident : borne la fenêtre de corrélation.
# `None` = aucun incident ouvert (hors fenêtre par définition).
_incident_opened_at: float | None = None


from svc_auth import require_service_token, service_token_configured  # noqa: E402


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Mémorise la boucle AVANT toute publication : /state/report et les tâches
    # lancées depuis le threadpool doivent pouvoir réveiller les abonnés SSE.
    bus.bind_loop()
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


@app.post("/incident", dependencies=[Depends(require_service_token)])
async def handle_incident(payload: IncidentPayload):
    """Reçoit l'incident et lance la remédiation en TÂCHE DE FOND.

    Pourquoi ne pas attendre la fin du playbook dans la requête : un playbook
    réel prend 4 à 8 s. Le détecteur appelait ce webhook de façon synchrone
    avec un timeout de 5 s, et tombait en ReadTimeout alors que la remédiation
    fonctionnait — panne constatée en recette. On répond donc 202 tout de
    suite, et l'état est publié au fil de l'exécution via /events.
    """
    # Réservation SYNCHRONE : deux appels simultanés ne peuvent pas réserver
    # tous les deux, contrairement à `_lock.locked()` qui laissait une fenêtre
    # entre le test et la prise du verrou par la tâche.
    if _busy["value"]:
        return {"accepted": False, "reason": "remédiation déjà en cours"}
    _busy["value"] = True

    global _current_incident, _incident_opened_at
    _current_incident = payload
    # Un incident ML n'est pas issu d'alertes : on repart d'une liste vide pour
    # ne pas hériter des alertes d'un incident précédent.
    _current_alerts.clear()
    # Horodatage d'OUVERTURE côté moteur (et non `detected_at` du détecteur :
    # une horloge décalée ou un rejeu fermerait la fenêtre de corrélation).
    _incident_opened_at = time.time()
    asyncio.create_task(_run_incident(payload))
    return {"accepted": True, "status": "remediation_started"}


# ---------------------------------------------------------------------------
# Ingestion SIEM : alertes Prometheus/Alertmanager → remédiation autonome
# ---------------------------------------------------------------------------
# C'est la réponse à « l'IA doit gérer les logs ou alertes des outils de
# supervision de sorte à agir elle-même ». Prometheus évalue ses règles,
# Alertmanager pousse ici, et le moteur déclenche le MÊME playbook que le
# détecteur ML. Une alerte n'est donc pas seulement affichée : elle agit.
#
# Ce qui déclenche une remédiation est une liste FERMÉE. Une alerte de confort
# (bandeau LED silencieux, compte bloqué) est journalisée mais n'ouvre pas un
# incident : sinon le premier avertissement bénin lancerait un playbook.

ACTIONABLE_ALERTS = {
    # --- métriques Prometheus ---
    "ModeleMLIndisponible": "model_unavailable",
    "WebhookRemediationCasse": "webhook_broken",
    "NoyauCryptoIndisponible": "crypto_unavailable",
    "SondeTransactionEnEchec": "probe_failed",
    "WafIndisponible": "waf_unavailable",
    "DeceptionNonArmee": "deception_not_armed",
    "ReconnaissanceFacialeIndisponible": "face_engine_unavailable",
    "RemediationEnEchec": "remediation_failed",
    # --- logs (Loki) : une erreur applicative n'a pas de métrique dédiée ---
    "PaiementRefuse": "payment_refused",
    "JetonServiceAbsentDansLesLogs": "service_token_missing",
    "RemonteeDeBouclier": "panic_in_logs",
}

# Alertmanager réémet la même alerte toutes les 4 h ; on borne les doublons.
ALERT_COOLDOWN_SECONDS = 60.0
# Fenêtre de corrélation (§0.5.3). Une panne réelle (Vault arrêté, WAF coupé)
# fait tomber 3 à 5 sondes en cascade en quelques secondes. Toutes les alertes
# actionnables reçues dans cette fenêtre après l'ouverture de l'incident sont
# rattachées au MÊME incident au lieu d'être jetées. Au-delà, on considère qu'il
# s'agit d'un nouvel épisode : l'alerte est ignorée avec une raison explicite
# (un incident ne s'étend pas indéfiniment, sinon une alerte oubliée de la
# veille se collerait à la démo du jour).
CORRELATION_WINDOW_SECONDS = float(os.environ.get("CORRELATION_WINDOW_SECONDS", "10"))
# Verrou « une remédiation à la fois ». `_lock.locked()` ne suffit pas : entre
# le test et `create_task`, la tâche n'a pas encore acquis le verrou, donc deux
# requêtes simultanées passent toutes les deux. `_busy` est positionné de
# façon SYNCHRONE dans le handler.
_busy = {"value": False}

_alert_history: deque[dict] = deque(maxlen=200)
_last_alert_at: dict[str, float] = {}


class AlertmanagerPayload(BaseModel):
    status: str = "firing"
    alerts: list[dict] = []


def _correlated_alert(entry: dict) -> dict:
    """Projette une alerte ingérée vers le contrat `detail.alerts`.

    Le contrat est consommé par le dashboard, le pont hardware et /history : les
    clés restent stables même si le format Alertmanager évolue.
    """
    return {
        "alertname": entry["alertname"],
        "severity": entry.get("severity"),
        "summary": entry.get("summary"),
        "received_at": entry["received_at"],
        "fingerprint": entry["fingerprint"],
        "diagnostic": entry.get("diagnostic"),
    }


def _alerts_snapshot() -> list[dict]:
    """Copie de la liste : un état publié ne doit jamais muter après coup."""
    return [dict(alert) for alert in _current_alerts]


def _within_correlation_window(received: float) -> bool:
    """Vrai si l'alerte appartient encore à l'épisode en cours."""
    if _incident_opened_at is None:
        return False
    return received - _incident_opened_at <= CORRELATION_WINDOW_SECONDS


def _republish_current_incident() -> dict | None:
    """Republie UNDER_ATTACK / REMEDIATING avec la liste d'alertes à jour.

    Sans cette republication, l'alerte corrélée resterait invisible : /state
    renverrait l'ancien `detail` et l'abonné SSE ne serait pas réveillé avant
    le ping de 15 s. Le `diagnostic` d'origine est conservé tel quel : c'est
    celui de la PREMIÈRE alerte qui pilote le playbook.
    """
    current = bus.current
    if current["state"] not in ("UNDER_ATTACK", "REMEDIATING"):
        return None
    detail = dict(current["detail"])
    detail["alerts"] = _alerts_snapshot()
    return bus.publish(current["state"], detail)


@app.post("/alert", dependencies=[Depends(require_service_token)])
async def handle_alert(payload: AlertmanagerPayload):
    """Reçoit un webhook Alertmanager et agit si l'alerte est actionnable.

    Corrélation multi-alertes (§0.5.3) : une panne réelle produit 3 à 5 alertes
    en cascade. La PREMIÈRE alerte actionnable ouvre l'incident et son
    diagnostic pilote le playbook ; les suivantes, reçues pendant la
    remédiation et dans `CORRELATION_WINDOW_SECONDS` après l'ouverture, sont
    rattachées au même incident (`detail.alerts`) et l'état est republié pour
    que /state, /events et /history les voient. Hors fenêtre, elles sont
    ignorées avec `reason: "hors_fenetre_de_correlation"` : on ne rattache pas
    indéfiniment à un incident déjà long.
    """
    global _incident_opened_at, _current_incident

    received = time.time()
    acted: list[dict] = []
    correlated: list[dict] = []
    ignored: list[dict] = []

    for alert in payload.alerts:
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        name = str(labels.get("alertname", ""))
        status = str(alert.get("status", payload.status))
        fingerprint = str(alert.get("fingerprint") or labels.get("fingerprint") or f"{name}:{status}")

        entry = {
            "alertname": name,
            "status": status,
            "severity": labels.get("severity"),
            "summary": annotations.get("summary"),
            "received_at": received,
            "fingerprint": fingerprint,
            "diagnostic": ACTIONABLE_ALERTS.get(name),
            "acted": False,
        }

        if status != "firing":
            entry["reason"] = "resolved"
            ignored.append(entry)
            continue
        if name not in ACTIONABLE_ALERTS:
            entry["reason"] = "non_actionnable"
            ignored.append(entry)
            continue
        if received - _last_alert_at.get(fingerprint, 0.0) < ALERT_COOLDOWN_SECONDS:
            entry["reason"] = "anti_rebond"
            ignored.append(entry)
            continue

        if _busy["value"]:
            # Remédiation en cours : deux cas, jamais de silence.
            if _within_correlation_window(received):
                _last_alert_at[fingerprint] = received
                entry["acted"] = True
                entry["correlated"] = True
                _current_alerts.append(_correlated_alert(entry))
                correlated.append(entry)
                acted.append(entry)
                # L'alerte doit être visible tout de suite, pas seulement à la
                # fin du playbook : /state, /events et le clip la montrent.
                _republish_current_incident()
            else:
                entry["reason"] = "hors_fenetre_de_correlation"
                ignored.append(entry)
            continue

        _last_alert_at[fingerprint] = received
        entry["acted"] = True
        entry["correlated"] = False
        acted.append(entry)
        # Réservation synchrone avant `create_task` : deux alertes actionnables
        # dans le même payload ne lancent plus deux playbooks.
        _busy["value"] = True

        incident = IncidentPayload(
            anomaly_score=0.0,
            features=[0.0] * 6,
            detected_at=received,
            diagnostic=ACTIONABLE_ALERTS[name],
        )
        # Ouvre l'épisode : c'est le diagnostic de CETTE alerte qui pilote le
        # playbook, et son horodatage qui borne la fenêtre de corrélation.
        _current_incident = incident
        _incident_opened_at = received
        _current_alerts.clear()
        _current_alerts.append(_correlated_alert(entry))

        # On réutilise exactement le chemin de l'incident ML : même verrou,
        # même playbook, même preuve de reprise. Un second chemin de
        # remédiation finirait par diverger du premier.
        asyncio.create_task(_run_incident(incident))

    _alert_history.extend(acted + ignored)
    return {
        "received": len(payload.alerts),
        "acted": len(acted),
        "correlated": len(correlated),
        "ignored": len(ignored),
        "details": acted + ignored,
    }


@app.get("/alerts", dependencies=[Depends(require_service_token)])
def list_alerts(limit: int = 50):
    """Journal des alertes ingérées — consultable dans le dashboard et en Q&A."""
    limit = max(1, min(int(limit), 200))
    entries = list(_alert_history)[-limit:]
    return {"count": len(_alert_history), "entries": entries}


async def _run_incident(payload: IncidentPayload) -> None:
    """Exécute un incident et publie TOUJOURS un état terminal.

    Le `try/except/finally` n'est pas décoratif : une exception dans le
    playbook laissait auparavant l'état bloqué en `REMEDIATING`, le dashboard
    orange, et `/state/report` refusait `NORMAL_TX` indéfiniment.
    """
    global _current_incident, _incident_opened_at
    try:
        async with _lock:  # une remédiation à la fois, pas de double déclenchement
            await _run_incident_locked(payload)
    except Exception as exc:  # noqa: BLE001 - un incident ne doit jamais bloquer le SI
        logging.getLogger("remediation-engine").exception(
            "incident interrompu : %s", exc
        )
        bus.publish(
            "REMEDIATION_FAILED",
            {
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "diagnostic": payload.diagnostic,
                "alerts": _alerts_snapshot(),
            },
        )
    finally:
        _current_incident = None
        _busy["value"] = False
        # L'épisode est terminé : la prochaine alerte ouvrira un nouvel incident.
        _current_alerts.clear()
        _incident_opened_at = None


async def _run_incident_locked(payload: IncidentPayload) -> None:
    global _current_incident
    if True:  # conserve l'indentation du corps d'origine
        start = time.time()
        bus.publish(
            "UNDER_ATTACK",
            {
                "anomaly_score": payload.anomaly_score,
                "diagnostic": payload.diagnostic,
                # Liste des alertes corrélées (§0.5.3) : le diagnostic reste
                # celui de la première alerte, les suivantes sont ici.
                "alerts": _alerts_snapshot(),
            },
        )
        # Laisse le temps aux rendus (dashboard, terminal) de montrer la
        # détection avant de basculer sur REMEDIATING. Voir ATTACK_HOLD_SECONDS.
        await asyncio.sleep(ATTACK_HOLD_SECONDS)
        bus.publish(
            "REMEDIATING",
            {"diagnostic": payload.diagnostic, "alerts": _alerts_snapshot()},
        )

        result = await _run_playbook(payload.diagnostic)
        probe_ok = _probe_ok(result)

        entry = {
            "detected_at": payload.detected_at,
            "diagnostic": payload.diagnostic,
            "anomaly_score": payload.anomaly_score,
            "duration_seconds": time.time() - start,
            "ansible_rc": result.returncode,
            "probe_ok": probe_ok,
            "alerts": _alerts_snapshot(),
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
    except (OSError, subprocess.SubprocessError) as exc:
        # PermissionError (binaire non exécutable), IsADirectoryError
        # (ANSIBLE_BIN pointant un dossier), etc. Auparavant l'exception
        # remontait et l'état restait bloqué en REMEDIATING.
        return subprocess.CompletedProcess(
            args=[ANSIBLE_BIN],
            returncode=126,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}"[:200],
        )


@app.get("/state")
def state():
    """État courant — utilisé par le pont hardware à la (re)connexion."""
    return bus.current


@app.post("/state/report", dependencies=[Depends(require_service_token)])
async def report_state(report: StateReport):
    """Rapport d'un fait vérifié par un autre service (typiquement payment-api
    qui confirme une transaction). Volontairement limité à NORMAL_TX : un
    rapport ne doit jamais pouvoir masquer une attaque en cours ni simuler une
    reprise. Les états de remédiation restent pilotés par /incident seul.

    `async def` (§0.6.4) : en endpoint synchrone, FastAPI l'exécutait dans le
    threadpool, donc `bus.publish` manipulait un `asyncio.Queue` hors boucle et
    incrémentait `_seq` sans verrou. Deux transactions simultanées pouvaient
    produire le même `seq`, et l'abonné SSE attendait le ping de 15 s. Le chemin
    nominal est désormais la boucle ; `publish` reste par ailleurs sûr depuis
    n'importe quel thread (verrou + `call_soon_threadsafe`).
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


@app.get("/metrics")
def metrics():
    """Métriques Prometheus : état courant, incidents, échecs de playbook."""
    last = remediation_log[-1] if remediation_log else {}
    failures = sum(1 for entry in remediation_log if entry.get("ansible_rc") != 0)
    lines = [
        "# HELP shadow_state_seq Numéro de séquence du dernier état publié",
        "# TYPE shadow_state_seq counter",
        f"shadow_state_seq {bus.current['seq']}",
        "# HELP shadow_remediation_in_progress 1 si un playbook tourne",
        "# TYPE shadow_remediation_in_progress gauge",
        f"shadow_remediation_in_progress {1 if _lock.locked() else 0}",
        "# HELP shadow_incidents_total Nombre d'incidents traités depuis le démarrage",
        "# TYPE shadow_incidents_total counter",
        f"shadow_incidents_total {len(remediation_log)}",
        "# HELP shadow_playbook_failures_total Nombre de playbooks en échec",
        "# TYPE shadow_playbook_failures_total counter",
        f"shadow_playbook_failures_total {failures}",
        "# HELP shadow_last_remediation_seconds Durée du dernier playbook",
        "# TYPE shadow_last_remediation_seconds gauge",
        f"shadow_last_remediation_seconds {float(last.get('duration_seconds') or 0)}",
        "# HELP shadow_sse_subscribers Nombre d'abonnés SSE (dashboard + terminal)",
        "# TYPE shadow_sse_subscribers gauge",
        f"shadow_sse_subscribers {len(bus._subscribers)}",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


def _current_incident_snapshot() -> dict | None:
    """Vue JSON de l'incident en cours, pour /healthz.

    `None` quand rien ne tourne : c'est le cas nominal entre deux transactions.
    """
    if _current_incident is None:
        return None
    return {
        "diagnostic": _current_incident.diagnostic,
        "detected_at": _current_incident.detected_at,
        "alerts": _alerts_snapshot(),
    }


@app.get("/healthz")
def healthz():
    """Sonde de vie + incident EN COURS (et non seulement le dernier terminé).

    `current_incident` était annoncé par un commentaire mais absent du JSON :
    la supervision savait qu'un incident tournait, pas lequel. On publie donc le
    diagnostic, l'horodatage de détection et les alertes corrélées — la même
    liste que `detail.alerts`, pour que la Q&A puisse expliquer l'épisode.
    """
    return {
        "status": "ok",
        "state": bus.current["state"],
        "seq": bus.current["seq"],
        "remediation_in_progress": _lock.locked(),
        "current_incident": _current_incident_snapshot(),
        "last_incident": remediation_log[-1] if remediation_log else None,
        # false = /incident et /state/report fermés (503) : renseigner
        # SERVICE_TOKEN, sinon le détecteur ne peut plus déclencher le playbook.
        "service_token_configured": service_token_configured(),
    }

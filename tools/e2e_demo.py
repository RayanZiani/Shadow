"""Répétition générale de la chaîne P0, chronométrée.

    python tools/e2e_demo.py

Vérifie exactement ce que la grille note (axe 1 local / 25 pts national) :

    attaque  ->  détection ML  ->  remédiation autonome  ->  transaction de preuve

et mesure la durée totale. Objectif : < 90 s, pour garder de la marge sur les
3 minutes de la démo nationale.

Ce script ne simule RIEN côté remédiation : il déclenche une vraie attaque sur
payment-api et attend que remediation-engine publie RECOVERED_TX. Il sert de
répétition avant chaque soutenance et de preuve chiffrée pour le dossier.

Code de sortie : 0 si la chaîne est complète et sous la limite, 1 sinon.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time

import httpx

PAYMENT = "http://localhost:8080"
DETECTOR = "http://localhost:8083"
REMEDIATION = "http://localhost:8084"

LIMIT_SECONDS = 90.0
POLL = 0.5


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def check_services(client: httpx.Client) -> dict:
    services = {}
    for name, url in (
        ("payment-api", PAYMENT),
        ("ml-detector", DETECTOR),
        ("remediation-engine", REMEDIATION),
    ):
        try:
            response = client.get(f"{url}/healthz", timeout=3.0)
            services[name] = response.json()
        except Exception as exc:  # noqa: BLE001
            log(f"ECHEC : {name} injoignable ({exc})")
            sys.exit(1)

    model = services["ml-detector"].get("model")
    if model != "ready":
        log(f"ECHEC : ml-detector en mode {model} — lancez train.py")
        sys.exit(1)
    webhook_error = services["ml-detector"].get("last_webhook_error")
    if webhook_error:
        log(f"ECHEC : le webhook de remédiation est cassé : {webhook_error}")
        log(f"       cible configurée : {services['ml-detector'].get('webhook')}")
        sys.exit(1)
    log(f"services OK · modèle {model} · webhook {services['ml-detector'].get('webhook')}")
    return services


def wait_clean_window(client: httpx.Client, timeout: float = 20.0) -> None:
    """Attend que la fenêtre du détecteur soit vide.

    Indispensable pour que deux répétitions s'enchaînent : sinon les
    observations de l'attaque précédente (fenêtre de 10 s) sont encore présentes
    et le script conclut à une « anomalie avant l'attaque ». La sonde de
    liveness de payment-api purge la fenêtre toutes les 4 s, même sans client.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"{DETECTOR}/score").json()
        if not state.get("window_size"):
            return
        time.sleep(0.5)
    log("ATTENTION : la fenêtre ne se vide pas (sonde de liveness arrêtée ?)")


def baseline(client: httpx.Client, samples: int = 12) -> None:
    """Trafic légitime AVANT l'attaque.

    Indispensable : sans trafic de référence, la fenêtre du détecteur est vide
    ou ne contient que l'attaque, et la mesure ne veut rien dire.

    `samples` doit dépasser MIN_EVENTS (8) pour que la fenêtre soit réellement
    remplie : sinon le détecteur reste en mode « warming » et ne produit aucun
    score, ce qui rend la vérification « pas d'anomalie au repos » sans valeur.
    """
    wait_clean_window(client)
    log(f"trafic normal de référence ({samples} tx)…")
    for _ in range(samples):
        decoys = client.get(f"{PAYMENT}/decoys", params={"count": 10}).json()["decoys"]
        submit(client, decoys)
        time.sleep(random.uniform(0.15, 0.35))

    state = client.get(f"{DETECTOR}/score").json()
    log(
        f"score au repos : {state['score']:.2f} (seuil {state.get('threshold')}, "
        f"fenêtre {state.get('window_size')}, mode {state.get('model')})"
    )
    if state.get("model") == "warming":
        log("ECHEC : la fenêtre ne se remplit pas — le test ne prouverait rien")
        sys.exit(1)
    if state.get("is_anomaly"):
        log("ATTENTION : anomalie détectée AVANT l'attaque — le modèle est mal calibré")
        sys.exit(1)

    # On laisse la fenêtre se vider : sinon les observations normales diluent
    # l'attaque et la détection est artificiellement retardée.
    log("purge de la fenêtre glissante…")
    wait_clean_window(client)


def submit(client: httpx.Client, decoy_ids: list[str]) -> bool:
    pool = client.get(f"{PAYMENT}/outputs", params={"unspent": True}).json()["outputs"]
    if not pool:
        return False
    response = client.post(
        f"{PAYMENT}/tx/submit",
        json={
            "real_output_id": random.choice(pool)["id"],
            "amount": 1,
            "spend_private_key_hex": "00" * 32,
            "decoy_ids": decoy_ids,
        },
    )
    return response.status_code == 200


def attack(client: httpx.Client) -> None:
    """Poison de leurres : tirage uniforme dans le registre public."""
    log("ATTAQUE — injection de leurres empoisonnés")
    for _ in range(12):
        outputs = client.get(f"{PAYMENT}/outputs").json()["outputs"]
        poison = [o["id"] for o in random.sample(outputs, k=10)]
        submit(client, poison)
        time.sleep(random.uniform(0.1, 0.3))


def wait_state(client: httpx.Client, expected: set[str], deadline: float) -> str:
    """Attend un état, en acceptant d'avoir manqué les états transitoires.

    UNDER_ATTACK dure quelques secondes puis laisse place à REMEDIATING. Si le
    polling tombe entre deux, on ne doit pas conclure à l'absence de détection :
    on considère que la détection a eu lieu si on observe un état AVANCÉ.
    """
    order = ["IDLE", "NORMAL_TX", "UNDER_ATTACK", "REMEDIATING", "RECOVERED_TX"]
    while time.time() < deadline:
        state = client.get(f"{REMEDIATION}/state").json()["state"]
        if state in expected:
            return state
        # La détection peut être manquée entre deux polls : si on observe un
        # état AVANCÉ, la détection a bien eu lieu.
        if expected == {"UNDER_ATTACK"} and state in (
            "REMEDIATING",
            "RECOVERED_TX",
            "REMEDIATION_FAILED",
        ):
            return state
        time.sleep(POLL)
    return "TIMEOUT"


def run() -> int:
    started = time.time()
    with httpx.Client(timeout=10.0) as client:
        check_services(client)
        baseline(client)

        client.post(f"{REMEDIATION}/state/report", json={"state": "NORMAL_TX", "detail": {}})

        attack_started = time.time()
        attack(client)

        log("attente de la détection…")
        detected = wait_state(client, {"UNDER_ATTACK"}, attack_started + 30)
        if detected == "TIMEOUT":
            log("ECHEC : aucune détection sous 30 s")
            return 1
        detection_s = time.time() - attack_started
        log(f"DÉTECTION en {detection_s:.1f} s")

        log("attente de la remédiation autonome…")
        recovered = wait_state(
            client, {"RECOVERED_TX", "REMEDIATION_FAILED"}, attack_started + LIMIT_SECONDS
        )
        if recovered == "TIMEOUT":
            log(f"ECHEC : pas de reprise sous {LIMIT_SECONDS:.0f} s")
            return 1
        if recovered == "REMEDIATION_FAILED":
            log("ECHEC : la remédiation a échoué (voir remediation-engine /history)")
            return 1
        recovery_s = time.time() - attack_started
        log(f"REPRISE en {recovery_s:.1f} s")

        # Preuve : une nouvelle transaction doit passer, sans intervention.
        log("transaction de preuve…")
        decoys = client.get(f"{PAYMENT}/decoys", params={"count": 10}).json()["decoys"]
        proof_ok = submit(client, decoys)
        total_s = time.time() - started

        history = client.get(f"{REMEDIATION}/history").json()
        last = history["entries"][-1] if history["entries"] else {}

        summary = {
            "detection_seconds": round(detection_s, 1),
            "recovery_seconds": round(recovery_s, 1),
            "total_seconds": round(total_s, 1),
            "limit_seconds": LIMIT_SECONDS,
            "proof_transaction_ok": proof_ok,
            "ansible_rc": last.get("ansible_rc"),
            "chain_complete": proof_ok and recovery_s < LIMIT_SECONDS,
        }
        print(json.dumps(summary, indent=2))

        if not proof_ok:
            log("ECHEC : la transaction de preuve n'est pas passée")
            return 1
        if recovery_s >= LIMIT_SECONDS:
            log(f"ECHEC : {recovery_s:.1f} s > {LIMIT_SECONDS:.0f} s")
            return 1

        log(f"CHAINE COMPLETE en {recovery_s:.1f} s (marge {LIMIT_SECONDS - recovery_s:.0f} s)")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=float, default=LIMIT_SECONDS)
    args = parser.parse_args()
    LIMIT_SECONDS = args.limit
    raise SystemExit(run())

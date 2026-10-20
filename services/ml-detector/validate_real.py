"""Validation du détecteur par le VRAI chemin de production.

    python validate_real.py --n 20

Contrairement à une validation qui injecterait des valeurs à la main dans
/ingest, ce script passe par payment-api comme le ferait un client :

  * trafic normal  : anneau tiré par GET /decoys (gamma, biais récents)
  * trafic attaque : anneau tiré UNIFORMÉMENT dans GET /outputs (poison)

puis lit le score exposé par /score. C'est le chiffre à citer dans le dossier
technique : il mesure la chaîne réelle, pas un montage de test.
"""
from __future__ import annotations

import argparse
import json
import random
import time

import httpx

PAYMENT = "http://localhost:8080"
DETECTOR = "http://localhost:8083"


def _submit(client: httpx.Client, decoy_ids: list[str]) -> tuple[bool, float]:
    pool = client.get(f"{PAYMENT}/outputs", params={"unspent": True}).json()["outputs"]
    if not pool:
        return False, 0.0
    response = client.post(
        f"{PAYMENT}/tx/submit",
        json={
            "real_output_id": random.choice(pool)["id"],
            # Montant déchiffré côté portefeuille, pas annoncé par le client.
            "decoy_ids": decoy_ids,
        },
    )
    if response.status_code != 200:
        return False, 0.0
    # payment-api envoie sa télémétrie en tâche de fond : sans cette attente,
    # on lit /score AVANT que l'observation ne soit ingérée (course observée
    # en recette, qui produisait des scores aberrants).
    time.sleep(0.25)
    return True, float(response.json().get("latency_ms", 0))


def _scored(state: dict, min_events: int) -> bool:
    """Le détecteur a-t-il RÉELLEMENT produit un score ?

    Tant que la fenêtre contient moins de MIN_EVENTS événements, le détecteur
    est en mode `warming` et renvoie 0,0 : ce n'est pas une mesure de trafic
    normal, c'est une absence de mesure. Les compter comme « score 0 » gonflait
    artificiellement les taux de non-détection (mesuré : `attack_min` à 0,0 et
    45 % de déclenchement au lieu de 100 %).
    """
    return (
        state.get("model") == "ready"
        and state.get("window_size", 0) >= min_events
    )


def run(n: int = 20) -> dict:
    rng = random.Random(7)
    normal_scores: list[float] = []
    attack_scores: list[float] = []
    normal_triggers = 0
    attack_triggers = 0
    threshold = 0.0
    streak_required = 3

    with httpx.Client(timeout=10.0) as client:
        threshold = client.get(f"{DETECTOR}/threshold").json().get("threshold") or 0.0
        health = client.get(f"{DETECTOR}/healthz").json()
        streak_required = health.get("streak_required", 3)
        min_events = health.get("min_events", 8)
        skipped_normal = 0
        skipped_attack = 0

        for _ in range(n):
            decoys = client.get(f"{PAYMENT}/decoys", params={"count": 10}).json()["decoys"]
            ok, _ = _submit(client, decoys)
            if ok:
                state = client.get(f"{DETECTOR}/score").json()
                if not _scored(state, min_events):
                    skipped_normal += 1
                    time.sleep(rng.uniform(0.2, 0.8))
                    continue
                normal_scores.append(float(state["score"]))
                # Ce qui compte réellement : un déclenchement de remédiation.
                if state.get("streak", 0) >= streak_required:
                    normal_triggers += 1
            time.sleep(rng.uniform(0.2, 0.8))

        # On laisse la fenêtre glissante se vider : sinon les observations
        # normales diluent l'attaque et le test est faussé en notre faveur.
        time.sleep(6)

        for _ in range(n):
            outputs = client.get(f"{PAYMENT}/outputs").json()["outputs"]
            # Tirage UNIFORME = ce que fait un attaquant qui empoisonne les
            # leurres. Aucun biais vers les outputs récents.
            poison = [o["id"] for o in rng.sample(outputs, k=10)]
            ok, _ = _submit(client, poison)
            if ok:
                state = client.get(f"{DETECTOR}/score").json()
                if not _scored(state, min_events):
                    skipped_attack += 1
                    time.sleep(rng.uniform(0.2, 0.8))
                    continue
                attack_scores.append(float(state["score"]))
                if state.get("streak", 0) >= streak_required:
                    attack_triggers += 1
            time.sleep(rng.uniform(0.2, 0.8))

    if not normal_scores or not attack_scores:
        return {"error": "pas assez d'échantillons", "normal": len(normal_scores), "attack": len(attack_scores)}

    return {
        "threshold": round(threshold, 3),
        "normal_mean": round(sum(normal_scores) / len(normal_scores), 3),
        "normal_max": round(max(normal_scores), 3),
        "attack_mean": round(sum(attack_scores) / len(attack_scores), 3),
        "attack_min": round(min(attack_scores), 3),
        "false_positive_rate": round(
            sum(1 for s in normal_scores if s > threshold) / len(normal_scores), 3
        ),
        "attack_detection_rate": round(
            sum(1 for s in attack_scores if s > threshold) / len(attack_scores), 3
        ),
        "false_trigger_rate": round(normal_triggers / len(normal_scores), 3),
        "attack_trigger_rate": round(attack_triggers / len(attack_scores), 3),
        "streak_required": streak_required,
        "n_normal": len(normal_scores),
        "n_attack": len(attack_scores),
        "skipped_warming_normal": skipped_normal,
        "skipped_warming_attack": skipped_attack,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(run(args.n), indent=2))

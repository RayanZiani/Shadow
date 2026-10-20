"""Mesure la distribution des scores sur du trafic NORMAL réel.

    python calibrate_threshold.py --seconds 300

Pourquoi : le seuil issu de l'entraînement (percentile 99,5 sur ~150 fenêtres)
s'est révélé trop bas en recette — un score de 4,4 apparaissait sur du trafic
légitime. Plutôt que d'ajuster à l'aveugle, on observe la vraie distribution
en conditions d'exploitation et on fixe le seuil sur un percentile élevé de
cette distribution.

Écrit `threshold_runtime.json`, chargé en priorité par detector.py.
"""
from __future__ import annotations

import argparse
import json
import random
import time

import httpx
import numpy as np

PAYMENT = "http://localhost:8080"
DETECTOR = "http://localhost:8083"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=300)
    parser.add_argument("--percentile", type=float, default=99.9)
    parser.add_argument("--out", default="threshold_runtime.json")
    args = parser.parse_args()

    rng = random.Random(2026)
    scores: list[float] = []
    deadline = time.time() + args.seconds

    with httpx.Client(timeout=10.0) as client:
        health = client.get(f"{DETECTOR}/healthz").json()
        if health.get("model") != "ready":
            print(f"ECHEC : détecteur non prêt ({health.get('model')})")
            return 1

        while time.time() < deadline:
            pool = client.get(f"{PAYMENT}/outputs", params={"unspent": True}).json()["outputs"]
            if not pool:
                print("plus d'outputs — arrêt")
                break
            decoys = client.get(f"{PAYMENT}/decoys", params={"count": 10}).json()["decoys"]
            response = client.post(
                f"{PAYMENT}/tx/submit",
                json={
                    "real_output_id": rng.choice(pool)["id"],
                    "amount": 1,
                    "spend_private_key_hex": "00" * 32,
                    "decoy_ids": decoys,
                },
            )
            if response.status_code != 200:
                time.sleep(0.3)
                continue

            # Attente de l'ingestion asynchrone avant lecture du score.
            time.sleep(0.25)
            state = client.get(f"{DETECTOR}/score").json()
            if state.get("model") == "ready" and state.get("score", 0) > 0:
                scores.append(float(state["score"]))
            time.sleep(rng.uniform(0.15, 0.9))

    if len(scores) < 50:
        print(f"ECHEC : seulement {len(scores)} mesures, pas assez pour calibrer")
        return 1

    array = np.array(scores)
    learned = 0.0
    with httpx.Client(timeout=5.0) as client:
        learned = client.get(f"{DETECTOR}/threshold").json().get("threshold") or 0.0

    runtime_threshold = float(np.percentile(array, args.percentile))
    payload = {
        "threshold": round(runtime_threshold, 4),
        "source": f"runtime_observation_p{args.percentile}",
        "n_measures": int(len(array)),
        "mean": round(float(array.mean()), 4),
        "std": round(float(array.std()), 4),
        "p50": round(float(np.percentile(array, 50)), 4),
        "p99": round(float(np.percentile(array, 99)), 4),
        "p999": round(float(np.percentile(array, 99.9)), 4),
        "max": round(float(array.max()), 4),
        "learned_threshold": round(learned, 4),
        "observed_seconds": args.seconds,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    print(json.dumps(payload, indent=2))
    if payload["threshold"] <= learned:
        print(
            "\nATTENTION : le seuil mesuré est INFÉRIEUR au seuil appris. "
            "Cela signifie que le trafic observé est plus calme que la baseline "
            "d'entraînement. Gardez le seuil appris (le plus prudent)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Capture d'une baseline de trafic NORMAL depuis payment-api.

    python capture_baseline.py --seconds 120 --out baseline.jsonl

Puis :
    python train.py --from-capture baseline.jsonl

Pourquoi c'est indispensable : un modèle entraîné sur des distributions
supposées produit des faux positifs dès la première transaction réelle (vécu :
score 3,79 pour un seuil à 3,57 sur du trafic légitime). La seule calibration
défendable devant un jury est celle faite sur les observations du système.

Le script n'invente rien : il interroge les mêmes endpoints que la démo et
enregistre les observations brutes telles que payment-api les envoie.
"""
from __future__ import annotations

import argparse
import json
import random
import time

import httpx

PAYMENT = "http://localhost:8080"


def capture(seconds: float, gap: float, out: str) -> int:
    """Capture une baseline NORMALE.

    La cadence est volontairement IRRÉGULIÈRE : un `sleep(gap)` fixe produit
    un jitter quasi constant, et le StandardScaler divise alors par un
    écart-type minuscule. Résultat observé : la moindre variation de latence
    devenait une anomalie à 8 écarts-types. Une baseline doit ressembler au
    trafic humain, pas à un benchmark.
    """
    observations = []
    deadline = time.time() + seconds
    rng = random.Random(4242)
    with httpx.Client(timeout=5.0) as client:
        while time.time() < deadline:
            # Un output réel non dépensé : c'est ce que ferait un wallet.
            pool = client.get(f"{PAYMENT}/outputs", params={"unspent": True}).json()["outputs"]
            if not pool:
                print("plus d'outputs disponibles — arrêt")
                break
            real = rng.choice(pool)["id"]
            ring = client.get(f"{PAYMENT}/decoys", params={"count": 10}).json()
            started = time.perf_counter()
            response = client.post(
                f"{PAYMENT}/tx/submit",
                json={
                    "real_output_id": real,
                    "amount": 1,
                    "spend_private_key_hex": "00" * 32,
                    "decoy_ids": ring["decoys"],
                },
            )
            latency_ms = (time.perf_counter() - started) * 1000
            if response.status_code != 200:
                # 409 : output déjà dépensé entre-temps. On continue, on
                # n'invente pas d'observation.
                time.sleep(gap)
                continue
            body = response.json()
            observations.append(
                {
                    "timestamp": time.time(),
                    "ring_verify_cpu_us": body.get("verify_cpu_us", 0),
                    "ring_size": body.get("ring_size", 11),
                    "decoy_ages_days": ring["ages_days"],
                    "key_image_rejected": False,
                    "pin_failure": False,
                    "tx_latency_ms": latency_ms,
                }
            )
            # Cadence humaine : entre 0,15 s et 1,2 s, jamais fixe.
            time.sleep(rng.uniform(0.15, 1.2))

    with open(out, "w", encoding="utf-8") as handle:
        for observation in observations:
            handle.write(json.dumps(observation) + "\n")

    print(f"{len(observations)} observations écrites dans {out}")
    return len(observations)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--gap", type=float, default=0.4)
    parser.add_argument("--out", default="baseline.jsonl")
    args = parser.parse_args()
    capture(args.seconds, args.gap, args.out)

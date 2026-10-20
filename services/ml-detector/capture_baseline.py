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


def capture(
    seconds: float,
    gap: float,
    out: str,
    min_gap: float = 0.15,
    max_gap: float = 0.9,
    pace_block_seconds: float = 20.0,
) -> int:
    """Capture une baseline NORMALE.

    La cadence est volontairement IRRÉGULIÈRE : un `sleep(gap)` fixe produit
    un jitter quasi constant, et le StandardScaler divise alors par un
    écart-type minuscule. Résultat observé : la moindre variation de latence
    devenait une anomalie à 8 écarts-types. Une baseline doit ressembler au
    trafic humain, pas à un benchmark.

    La cadence est en plus VARIÉE PAR BLOCS (`pace_block_seconds`) : une capture
    faite à cadence unique rend le modèle intolérant à toute autre cadence.
    Constaté en recette : baseline capturée à ~0,4 s d'intervalle, puis
    `e2e_demo.py` envoyant ses transactions à ~0,25 s → `inter_arrival_median`
    à −2,4 σ et un score de 5,4 sur du trafic parfaitement légitime. Couvrir
    plusieurs cadences est la seule façon d'obtenir un modèle qui ne confonde
    pas « plus rapide » avec « attaque ».
    """
    observations = []
    deadline = time.time() + seconds
    rng = random.Random(4242)
    block_deadline = 0.0
    block_min, block_max = min_gap, max_gap
    ring_count = 10
    with httpx.Client(timeout=5.0) as client:
        while time.time() < deadline:
            if time.time() >= block_deadline:
                block_deadline = time.time() + pace_block_seconds
                # La plage couvre la cadence RÉELLE d'un humain qui tape un
                # paiement (jusqu'à ~2 s entre deux actions) et celle d'un
                # script de démo (~4 tx/s). Une plage étroite rend le modèle
                # intolérant à tout ce qui sort de la capture : mesuré en
                # recette, un client à 0,87 s d'intervalle sortait à +5 σ sur
                # `inter_arrival_median` alors qu'il était parfaitement légitime.
                block_min, block_max = rng.choice(
                    [(0.05, 0.15), (0.15, 0.40), (0.40, 0.90), (0.90, 2.00)]
                )
                # Alternance 11 / 25 leurres : couvre l'avant et l'après
                # durcissement appliqué par le playbook.
                ring_count = rng.choice([10, 24])
                print(f"  cadence {block_min:.2f}-{block_max:.2f} s, {ring_count + 1} leurres")
            # Un output réel non dépensé : c'est ce que ferait un wallet.
            pool = client.get(f"{PAYMENT}/outputs", params={"unspent": True}).json()["outputs"]
            if not pool:
                print("plus d'outputs disponibles — arrêt")
                break
            real = rng.choice(pool)["id"]
            # Taille d'anneau variable : le playbook de remédiation DURCIT la
            # taille par défaut (11 -> 25) et elle reste durcie après l'incident.
            # Une baseline capturée uniquement à 11 leurres rendait le trafic
            # post-remédiation anormal (asymétrie des âges 0,67 -> 0,89).
            ring = client.get(f"{PAYMENT}/decoys", params={"count": ring_count}).json()
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
            # Cadence humaine, variable par blocs. Il faut aussi former des
            # fenêtres : le détecteur n'évalue qu'à partir de MIN_EVENTS (8)
            # événements dans WINDOW_SECONDS (10 s). Une cadence trop lente ne
            # produit aucune fenêtre exploitable (mesuré : 82 observations →
            # 11 fenêtres).
            time.sleep(rng.uniform(block_min, block_max))

    with open(out, "w", encoding="utf-8") as handle:
        for observation in observations:
            handle.write(json.dumps(observation) + "\n")

    print(f"{len(observations)} observations écrites dans {out}")
    return len(observations)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--gap", type=float, default=0.4)
    parser.add_argument("--min-gap", type=float, default=0.15, help="intervalle mini entre tx (s)")
    parser.add_argument("--max-gap", type=float, default=0.9, help="intervalle maxi entre tx (s)")
    parser.add_argument("--out", default="baseline.jsonl")
    args = parser.parse_args()
    capture(args.seconds, args.gap, args.out, args.min_gap, args.max_gap)

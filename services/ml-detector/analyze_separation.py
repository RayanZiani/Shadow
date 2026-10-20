"""Analyse de séparation : quelle feature porte réellement le signal d'attaque ?

    python analyze_separation.py

Construit deux jeux de fenêtres avec le MÊME code de features :
  * normal : fenêtres réelles issues de baseline.jsonl
  * attaque : mêmes horodatages, mais les leurres sont tirés UNIFORMÉMENT dans
    le registre d'outputs (ce que fait l'attaquant), au lieu de la gamma.

Sortie : moyenne/écart-type par classe et AUC univariée par feature. Sert à
justifier dans le dossier technique quelles features sont conservées et
pourquoi les autres sont du bruit de mesure.
"""
from __future__ import annotations

import json
import random

import numpy as np

from features import FEATURE_NAMES, extract_features

WINDOW = 5.0


def _windows(events: list[dict]) -> list[dict]:
    """Découpe en fenêtres glissantes de 5 s (même logique qu'en production)."""
    out = []
    current: list[dict] = []
    for event in events:
        current.append(event)
        current = [e for e in current if event["timestamp"] - e["timestamp"] <= WINDOW]
        if len(current) >= 3:
            out.append(list(current))
    return out


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """AUC par comptage de paires (pas de sklearn pour rester lisible)."""
    pos = scores[labels == 1]
    neg = scores[labels == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    wins = 0.0
    for value in pos:
        wins += float((neg < value).sum()) + 0.5 * float((neg == value).sum())
    return wins / (len(pos) * len(neg))


def main() -> None:
    events = [json.loads(line) for line in open("baseline.jsonl", encoding="utf-8")]
    normal_windows = _windows(events)
    if not normal_windows:
        raise SystemExit("baseline.jsonl trop courte")

    # Registre d'outputs réel, récupéré via l'API publique.
    import httpx

    outputs = httpx.get("http://localhost:8080/outputs", params={"limit": 500}).json()["outputs"]
    ages_pool = [o["age_days"] for o in outputs]
    rng = random.Random(99)

    attack_windows = []
    for window in normal_windows:
        forged = []
        for event in window:
            # Même nombre de leurres, mais tirage uniforme : distribution plate.
            forged.append({**event, "decoy_ages_days": rng.sample(ages_pool, k=len(event["decoy_ages_days"]) or 10)})
        attack_windows.append(forged)

    X_normal = np.vstack([extract_features(w) for w in normal_windows])
    X_attack = np.vstack([extract_features(w) for w in attack_windows])
    y = np.concatenate([np.zeros(len(X_normal)), np.ones(len(X_attack))])
    X = np.vstack([X_normal, X_attack])

    print(f"fenêtres normales : {len(X_normal)}   fenêtres attaque : {len(X_attack)}\n")
    print(f"{'feature':<28}{'normal':>18}{'attaque':>18}{'AUC':>8}")
    print("-" * 72)
    for index, name in enumerate(FEATURE_NAMES):
        n_mean, n_std = X_normal[:, index].mean(), X_normal[:, index].std()
        a_mean, a_std = X_attack[:, index].mean(), X_attack[:, index].std()
        score = auc(X[:, index], y)
        # AUC < 0,5 = la feature sépare dans l'autre sens, ce qui est utilisable.
        quality = max(score, 1 - score)
        print(
            f"{name:<28}{n_mean:>10.3f}±{n_std:<7.3f}{a_mean:>10.3f}±{a_std:<7.3f}"
            f"{quality:>8.3f}"
        )

    print("\nAUC > 0,90 = feature porteuse ; proche de 0,50 = bruit de mesure.")


if __name__ == "__main__":
    main()

"""Extraction des features — MODULE PARTAGÉ entre train.py et detector.py.

L'entraînement et l'inférence passent par EXACTEMENT la même fonction. Le
squelette d'origine entraînait sur des distributions inventées (cpu=4000 µs,
entropie=3,2) alors que l'inférence produisait des zéros : le modèle ne pouvait
pas fonctionner, quel que soit le seuil.

Les 6 features :

  0. decoy_median_age_days     jours, âge MÉDIAN des leurres (feature porteuse)
  1. ring_verify_cpu_us        µs, temps de vérification cryptographique
  2. key_image_rejection_ratio ratio de rejets (double-dépense, PIN)
  3. inter_arrival_median      s, intervalle médian entre requêtes
  4. ring_age_skewness         asymétrie des âges des leurres
  5. tx_latency_p95            ms, p95 de la latence de bout en bout

Pourquoi `decoy_median_age_days` et non l'entropie : un wallet Monero tire ses
leurres selon une gamma biaisée vers les outputs récents (âge médian ≈ 3,5 j
mesuré sur ce système). Un attaquant qui empoisonne les leurres les tire
uniformément dans le registre (âge médian ≈ 31 j). Mesuré sur le trafic réel :

    normal  : 3,56 j ± 0,67   (max observé 5,84)
    attaque : 31,1 j ± 12,7   (min observé 8,71)

Aucun chevauchement : c'est la feature qui sépare réellement l'attaque
principale. L'entropie de Shannon avait été essayée d'abord, mais elle sature
quand une transaction ne porte que 10 leurres (AUC 0,56) — elle mesurait la
taille de l'échantillon plus que la distribution. Ce constat est documenté dans
le dossier technique, avec les mesures.
"""
from __future__ import annotations

import numpy as np

FEATURE_NAMES = (
    "decoy_median_age_days",
    "ring_verify_cpu_us",
    "key_image_rejection_ratio",
    "inter_arrival_median",
    "ring_age_skewness",
    "tx_latency_p95",
)

N_FEATURES = len(FEATURE_NAMES)

# Paramètres de fenêtrage — PARTAGÉS entre l'entraînement et l'inférence.
#
# 10 s et non 5 s : à cadence humaine (~1 tx/s), une fenêtre de 5 s contient
# 5 événements, insuffisant pour que les statistiques soient stables. Constaté
# en recette : le détecteur restait en mode « warming » presque en permanence.
WINDOW_SECONDS = 10.0
MIN_EVENTS = 8

# Bornes de sécurité : une valeur aberrante (NaN, négatif, division par zéro)
# ne doit jamais produire un score absurde ni faire planter le service.
CLIPS = np.array(
    [
        (0.0, 3650.0),       # jours
        (0.0, 5_000_000.0),  # µs
        (0.0, 1.0),
        (0.0, 3600.0),       # s
        (-20.0, 20.0),
        (0.0, 600_000.0),    # ms
    ]
)

# Plancher d'échelle par feature : en dessous de cette valeur, l'écart observé
# est du bruit de mesure, pas un signal. Sans ce plancher, une feature quasi
# constante dans la baseline transforme une variation infime en anomalie à
# plusieurs écarts-types. Constaté en recette, corrigé ici.
SCALE_FLOORS = np.array(
    [
        0.30,      # jours
        50.0,      # µs
        0.02,      # ratio
        0.05,      # s
        0.15,      # sans unité
        15.0,      # ms
    ]
)


def skewness(values: list[float]) -> float:
    """Asymétrie de Fisher, implémentée sans scipy."""
    if len(values) < 3:
        return 0.0
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 3:
        return 0.0
    mean = x.mean()
    std = x.std()
    if std < 1e-9:
        return 0.0
    return float(np.mean(((x - mean) / std) ** 3))


def extract_features(events: list[dict]) -> np.ndarray:
    """Vecteur de features d'une fenêtre glissante.

    `events` : événements de télémétrie bruts envoyés par payment-api.
    Retourne un vecteur de N_FEATURES, toujours fini et dans les bornes.

    POINT CRITIQUE — invariance au volume de trafic.

    Les statistiques sur les âges sont calculées PAR ÉVÉNEMENT puis moyennées
    sur la fenêtre. Ce n'est pas un détail : mesuré sur une même loi de tirage,
    l'entropie des âges poolés passe de 3,0 bits (10 échantillons) à 5,4 bits
    (160 échantillons). Une feature calculée sur le pool dépend donc du NOMBRE
    de transactions dans la fenêtre, pas de la distribution des leurres : un
    simple changement de cadence ressemblait à une attaque.

    Comme chaque transaction porte le même nombre de leurres (taille d'anneau
    constante), la mesure par événement est comparable d'une fenêtre à l'autre ;
    la moyenne sur k événements réduit en plus le bruit de tirage en sqrt(k).
    """
    if not events:
        return np.zeros(N_FEATURES)

    timestamps = sorted(float(e["timestamp"]) for e in events)

    cpu = [float(e.get("ring_verify_cpu_us") or 0.0) for e in events]
    latency = [float(e.get("tx_latency_ms") or 0.0) for e in events]

    per_event_median_age: list[float] = []
    per_event_skew: list[float] = []
    for event in events:
        ages = event.get("decoy_ages_days") or []
        if len(ages) >= 2:
            per_event_median_age.append(float(np.median(ages)))
            per_event_skew.append(skewness(ages))
        # Une valeur explicitement fournie par l'appelant prime (utile pour les
        # tests et pour un futur client qui calculerait côté wallet).
        if event.get("decoy_median_age_days") is not None:
            per_event_median_age.append(float(event["decoy_median_age_days"]))

    median_age = (
        float(np.mean(per_event_median_age)) if per_event_median_age else 0.0
    )
    skew = float(np.mean(per_event_skew)) if per_event_skew else 0.0

    rejections = sum(
        1 for e in events if e.get("key_image_rejected") or e.get("pin_failure")
    )

    diffs = np.diff(timestamps) if len(timestamps) > 1 else np.array([0.0])
    # MÉDIANE et non écart-type. L'écart-type des intervalles explose au
    # moindre trou dans le trafic : une pause de 6 s suffisait à produire un
    # score de 6,2 sur du trafic légitime (mesuré en recette). La médiane est
    # insensible aux pauses et garde ce qu'on veut détecter : une rafale
    # automatisée a une médiane très basse (~0,05 s) contre ~0,7 s en usage
    # humain.
    inter_arrival = float(np.median(diffs)) if len(diffs) > 0 else 0.0

    vector = np.array(
        [
            median_age,
            float(np.mean(cpu)),
            rejections / len(events),
            inter_arrival,
            skew,
            float(np.percentile(latency, 95)) if latency else 0.0,
        ],
        dtype=float,
    )

    # Nettoyage : NaN/inf -> 0, puis bornes physiques.
    vector = np.nan_to_num(vector, nan=0.0, posinf=CLIPS[:, 1], neginf=CLIPS[:, 0])
    return np.clip(vector, CLIPS[:, 0], CLIPS[:, 1])


def describe(vector: np.ndarray) -> dict:
    """Version lisible pour les logs, le dashboard et le dossier technique."""
    return {name: round(float(value), 4) for name, value in zip(FEATURE_NAMES, vector)}

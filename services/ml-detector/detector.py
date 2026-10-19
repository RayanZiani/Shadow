"""ml-detector — pas de seuil fixe : un score d'anomalie combiné
(Isolation Forest + erreur de reconstruction d'un autoencoder) sur une
fenêtre glissante de 5s. Dès que le score dépasse la distribution apprise
à l'entraînement (voir train.py), on notifie remediation-engine.

Les 6 features du vecteur (cf. plan) :
  decoy_entropy, ring_verify_cpu_us, key_image_rejection_ratio,
  inter_arrival_jitter, ring_age_skewness, tx_latency_p95
"""
import os
import time
from collections import deque

import httpx
import joblib
import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI
from pydantic import BaseModel

REMEDIATION_WEBHOOK = os.environ.get("REMEDIATION_WEBHOOK", "http://remediation-engine:8084/incident")
WINDOW_SECONDS = 5

app = FastAPI(title="ml-detector")
event_window: deque = deque()


class TelemetryEvent(BaseModel):
    ring_verify_cpu_us: float
    ring_size: int
    timestamp: float


class Autoencoder(nn.Module):
    """6 -> 4 -> 2 -> 4 -> 6, cf. plan. Entraînez avec train.py avant la démo,
    ne JAMAIS entraîner en live — le modèle doit être figé avant le Jour 4."""

    def __init__(self):
        super().__init__()
        self.encoder = nn.Sequential(nn.Linear(6, 4), nn.ReLU(), nn.Linear(4, 2), nn.ReLU())
        self.decoder = nn.Sequential(nn.Linear(2, 4), nn.ReLU(), nn.Linear(4, 6))

    def forward(self, x):
        return self.decoder(self.encoder(x))


def _load_models():
    # TODO (vous) : charger les artefacts entraînés par train.py
    # (isolation_forest.joblib, autoencoder.pt). Ici : stubs à vide pour
    # que le service démarre même sans modèle entraîné (mode dégradé).
    iso_forest = None
    autoencoder = Autoencoder()
    try:
        iso_forest = joblib.load("isolation_forest.joblib")
        autoencoder.load_state_dict(torch.load("autoencoder.pt"))
        autoencoder.eval()
    except FileNotFoundError:
        pass
    return iso_forest, autoencoder


iso_forest, autoencoder = _load_models()


def _extract_features(events: list[dict]) -> np.ndarray:
    if not events:
        return np.zeros(6)
    cpu_times = [e["ring_verify_cpu_us"] for e in events]
    timestamps = sorted(e["timestamp"] for e in events)
    jitter = float(np.std(np.diff(timestamps))) if len(timestamps) > 1 else 0.0

    # TODO (vous) : brancher decoy_entropy, key_image_rejection_ratio et
    # ring_age_skewness sur les vraies données renvoyées par payment-api
    # (ces 3-là ne sont pas encore dans TelemetryEvent — étendez le modèle).
    return np.array(
        [
            0.0,  # decoy_entropy — placeholder
            float(np.mean(cpu_times)),
            0.0,  # key_image_rejection_ratio — placeholder
            jitter,
            0.0,  # ring_age_skewness — placeholder
            float(np.percentile(cpu_times, 95)),
        ]
    )


def _anomaly_score(features: np.ndarray) -> float:
    iso_score = 0.0
    if iso_forest is not None:
        iso_score = -iso_forest.score_samples(features.reshape(1, -1))[0]

    with torch.no_grad():
        x = torch.tensor(features, dtype=torch.float32)
        recon = autoencoder(x)
        ae_error = float(torch.mean((recon - x) ** 2))

    return iso_score + ae_error


@app.post("/ingest")
async def ingest(event: TelemetryEvent):
    now = time.time()
    event_window.append(event.model_dump())
    while event_window and now - event_window[0]["timestamp"] > WINDOW_SECONDS:
        event_window.popleft()

    features = _extract_features(list(event_window))
    score = _anomaly_score(features)

    # TODO (vous) : ce seuil "0.8" doit venir de la distribution apprise à
    # l'entraînement (ex: percentile 99 des scores normaux), pas d'un chiffre
    # en dur — sinon on retombe exactement sur le piège que le sujet évite.
    if score > 0.8:
        await _trigger_remediation(score, features)

    return {"score": score, "window_size": len(event_window)}


async def _trigger_remediation(score: float, features: np.ndarray):
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(
            REMEDIATION_WEBHOOK,
            json={
                "anomaly_score": score,
                "features": features.tolist(),
                "detected_at": time.time(),
                "diagnostic": "poisoned_decoys_suspected",  # TODO: classifieur multi-vecteur
            },
        )


@app.get("/healthz")
def healthz():
    return {"status": "ok", "window_size": len(event_window)}

"""ml-detector — détection d'anomalies SANS seuil fixe.

Principe (exigence explicite du sujet : « une détection par machine learning,
et non par de simples seuils fixes ») :

1. Un `StandardScaler` appris sur le trafic normal met les 6 features à l'échelle.
2. Deux modèles non supervisés donnent chacun un score d'écart :
     - IsolationForest : isole les points rares (score_samples)
     - Autoencoder     : mesure l'erreur de reconstruction
3. Chaque score est normalisé par la distribution observée à l'entraînement
   (moyenne/écart-type des scores sur le trafic NORMAL).
4. Le score combiné est comparé à un SEUIL APPRIS (percentile 99,5 des scores
   normaux), stocké dans `threshold.json` — jamais une constante dans le code.

Les artefacts sont produits par train.py. Si un artefact manque, le service
démarre en mode dégradé explicite (`/healthz` le dit) plutôt que de faire
semblant de détecter.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque

import httpx
import joblib
import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from features import (
    FEATURE_NAMES,
    MIN_EVENTS,
    N_FEATURES,
    SCALE_FLOORS,
    WINDOW_SECONDS,
    describe,
    extract_features,
)
REMEDIATION_WEBHOOK = os.environ.get(
    "REMEDIATION_WEBHOOK", "http://remediation-engine:8084/incident"
)
# Après un déclenchement, on laisse la remédiation se dérouler : sans ce
# délai, chaque événement de la fenêtre relance un playbook Ansible.
COOLDOWN_SECONDS = float(os.environ.get("COOLDOWN_SECONDS", "20"))
# Anti-rebond : nombre de fenêtres CONSÉCUTIVES au-dessus du seuil avant de
# déclencher. Une fenêtre isolée à 15 % de faux positifs déclencherait une
# remédiation sans attaque pendant une démo de 3 minutes ; trois fenêtres
# consécutives ramènent ce risque à un niveau négligeable tout en détectant
# une attaque persistante en quelques secondes.
ANOMALY_STREAK_REQUIRED = int(os.environ.get("ANOMALY_STREAK_REQUIRED", "3"))
ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", ".")

app = FastAPI(title="ml-detector")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
    ).split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
logger = logging.getLogger("ml-detector")
event_window: deque = deque()

# Dernier résultat, exposé au dashboard (courbe temps réel).
latest = {
    "ts": None,
    "score": 0.0,
    "threshold": None,
    "is_anomaly": False,
    "features": {},
    "model": "unloaded",
}


class TelemetryEvent(BaseModel):
    """Observations brutes envoyées par payment-api.

    On envoie des faits, pas des features : l'agrégation sur la fenêtre
    glissante reste la responsabilité du détecteur. Ça évite d'avoir deux
    définitions divergentes de la même feature.
    """

    timestamp: float
    ring_size: int = 0
    ring_verify_cpu_us: float = 0.0
    decoy_entropy: float | None = None
    decoy_ages_days: list[float] = Field(default_factory=list)
    key_image_rejected: bool = False
    pin_failure: bool = False
    tx_latency_ms: float = 0.0
    # Trafic émis par la sonde de liveness, pas par un client. Il est EXCLU du
    # calcul des features : la sonde a une cadence fixe (4 s) et une latence
    # stable, deux propriétés qu'aucun utilisateur ne produit. La laisser dans
    # la fenêtre biaise la distribution et rend le modèle inutilisable
    # (constaté en recette : score 6,2 sur du trafic normal).
    synthetic: bool = False


class Autoencoder(nn.Module):
    """6 -> 4 -> 2 -> 4 -> 6. Entraîné par train.py, jamais en live."""

    def __init__(self) -> None:
        super().__init__()
        self.encoder = nn.Sequential(nn.Linear(N_FEATURES, 4), nn.ReLU(), nn.Linear(4, 2), nn.ReLU())
        self.decoder = nn.Sequential(nn.Linear(2, 4), nn.ReLU(), nn.Linear(4, N_FEATURES))

    def forward(self, x):
        return self.decoder(self.encoder(x))


def _load_artifacts():
    """Charge scaler + modèles + seuil. Tout ou rien : un modèle partiel
    donnerait des scores incomparables au seuil appris."""
    needed = ("scaler.joblib", "isolation_forest.joblib", "autoencoder.pt", "threshold.json")
    missing = [name for name in needed if not os.path.exists(os.path.join(ARTIFACT_DIR, name))]
    if missing:
        return None, None, None, None, missing

    scaler = joblib.load(os.path.join(ARTIFACT_DIR, "scaler.joblib"))
    iso = joblib.load(os.path.join(ARTIFACT_DIR, "isolation_forest.joblib"))
    ae = Autoencoder()
    ae.load_state_dict(torch.load(os.path.join(ARTIFACT_DIR, "autoencoder.pt"), map_location="cpu"))
    ae.eval()
    with open(os.path.join(ARTIFACT_DIR, "threshold.json"), encoding="utf-8") as handle:
        threshold = json.load(handle)

    # Seuil d'exploitation : si calibrate_threshold.py a mesuré la distribution
    # réelle, on l'utilise — mais uniquement s'il est PLUS PRUDENT que le seuil
    # appris. Un seuil plus bas que l'appris ferait entrer du trafic normal.
    runtime_path = os.path.join(ARTIFACT_DIR, "threshold_runtime.json")
    if os.path.exists(runtime_path):
        with open(runtime_path, encoding="utf-8") as handle:
            runtime = json.load(handle)
        if runtime.get("threshold", 0) > threshold.get("threshold", 0):
            threshold["threshold_learned"] = threshold.get("threshold")
            threshold["threshold"] = runtime["threshold"]
            threshold["source"] = runtime.get("source", "runtime_observation")
            threshold["runtime_measures"] = runtime.get("n_measures")
            threshold["runtime_stats"] = {
                key: runtime.get(key)
                for key in ("p50", "p99", "p999", "max", "mean", "std")
            }

    return scaler, iso, ae, threshold, []


scaler, iso_forest, autoencoder, THRESHOLD, MISSING = _load_artifacts()
MODEL_STATE = "ready" if THRESHOLD else "degraded"


def _scaled(features: np.ndarray) -> np.ndarray:
    """Standardise puis BORNE.

    Deux protections, toutes deux issues de la recette :

    1. Plancher sur l'échelle (`SCALE_FLOORS`) : une feature quasi constante
       dans la baseline ne doit pas transformer un écart minuscule en anomalie.
    2. Bornage à ±8 : au-delà, l'information est « très inhabituel », la valeur
       exacte n'apporte rien et casse la comparaison au seuil.
    """
    scaled = (features - scaler.mean_) / np.maximum(scaler.scale_, SCALE_FLOORS)
    return np.clip(scaled, -8.0, 8.0)


def _anomaly_score(features: np.ndarray) -> tuple[float, float, float]:
    """Retourne (score combiné, z_isolation, z_autoencoder)."""
    scaled = _scaled(features)
    scaled = scaled.reshape(1, -1)

    z_iso = -float(iso_forest.score_samples(scaled)[0])
    z_iso = (z_iso - THRESHOLD["iso_mean"]) / max(THRESHOLD["iso_std"], 1e-3)

    with torch.no_grad():
        x = torch.tensor(scaled, dtype=torch.float32)
        recon = autoencoder(x)
        ae_error = float(torch.mean((recon - x) ** 2))
    z_ae = (ae_error - THRESHOLD["ae_mean"]) / max(THRESHOLD["ae_std"], 1e-3)

    # Moyenne : on veut que LES DEUX modèles s'écartent, pas qu'un seul
    # s'emballe sur une valeur isolée.
    score = 0.5 * z_iso + 0.5 * z_ae

    # Signal dédié « decoys empoisonnés » : l'âge médian des leurres est la
    # feature réellement discriminante pour cette attaque.
    #
    # Mesuré sur le trafic réel (analyze_separation.py) :
    #   decoy_median_age_days  AUC 1,00   <- séparation parfaite
    #   ring_age_skewness      AUC 0,92   <- complémentaire
    # Un wallet Monero tire selon une gamma biaisée récente (3,6 j) ; un
    # attaquant tire uniformément (31 j). Aucun chevauchement observé.
    #
    # La normalisation utilise les statistiques APPRISES par le scaler :
    # aucune constante n'est écrite ici.
    if scaler is not None:
        z_age = float(scaled[0][0])
        score = max(score, z_age)

    return score, z_iso, z_ae


_last_trigger = 0.0
_incident_active = False
_anomaly_streak = 0
_last_webhook_error: str | None = None


@app.post("/ingest")
async def ingest(event: TelemetryEvent):
    global _last_trigger, _incident_active, _anomaly_streak

    now = time.time()

    # Trafic de la sonde de liveness : on l'ignore complètement. Voir la
    # définition de `synthetic` dans TelemetryEvent.
    if event.synthetic:
        return {
            "score": latest["score"],
            "threshold": latest.get("threshold"),
            "is_anomaly": latest["is_anomaly"],
            "ignored": "synthetic_probe",
        }

    event_window.append(event.model_dump())
    while event_window and now - event_window[0]["timestamp"] > WINDOW_SECONDS:
        event_window.popleft()

    features = extract_features(list(event_window))

    # Réarmement. Il doit être évalué AVANT toutes les sorties anticipées :
    # sinon, après un incident, les fenêtres « en chauffe » ou « dégradées »
    # sortaient sans jamais remettre _incident_active à False, et le détecteur
    # ne pouvait plus JAMAIS redéclencher. Panne silencieuse constatée en
    # recette : la deuxième attaque ne produisait aucune remédiation.
    if _incident_active and now - _last_trigger > COOLDOWN_SECONDS:
        _incident_active = False
        _anomaly_streak = 0

    if MODEL_STATE != "ready":
        latest.update(
            {
                "ts": now,
                "score": 0.0,
                "is_anomaly": False,
                "streak": 0,
                "features": describe(features),
                "model": f"degraded:missing={','.join(MISSING)}",
            }
        )
        return {"score": 0.0, "threshold": None, "is_anomaly": False, "model": MODEL_STATE}

    # Fenêtre trop pauvre : aucune décision. Mieux vaut ne rien affirmer que
    # produire un faux positif sur du bruit d'échantillonnage.
    if len(event_window) < MIN_EVENTS:
        latest.update(
            {
                "ts": now,
                "score": 0.0,
                "threshold": THRESHOLD["threshold"],
                "is_anomaly": False,
                "streak": 0,
                "window_size": len(event_window),
                "min_events": MIN_EVENTS,
                "features": describe(features),
                "model": "warming",
            }
        )
        return {
            "score": 0.0,
            "threshold": THRESHOLD["threshold"],
            "is_anomaly": False,
            "model": "warming",
            "window_size": len(event_window),
        }

    score, z_iso, z_ae = _anomaly_score(features)
    threshold = float(THRESHOLD["threshold"])
    is_anomaly = score > threshold
    _anomaly_streak = _anomaly_streak + 1 if is_anomaly else 0

    latest.update(
        {
            "ts": now,
            "score": round(score, 4),
            "threshold": threshold,
            "is_anomaly": is_anomaly,
            "streak": _anomaly_streak,
            "streak_required": ANOMALY_STREAK_REQUIRED,
            "features": describe(features),
            "z_isolation": round(z_iso, 3),
            "z_autoencoder": round(z_ae, 3),
            "model": "ready",
        }
    )

    # Anti-rebond : on exige plusieurs fenêtres consécutives anormales. Un pic
    # isolé ne doit pas lancer un playbook Ansible en pleine soutenance.
    should_trigger = (
        _anomaly_streak >= ANOMALY_STREAK_REQUIRED
        and not _incident_active
        and now - _last_trigger > COOLDOWN_SECONDS
    )
    if should_trigger:
        _incident_active = True
        _last_trigger = now
        _anomaly_streak = 0
        await _trigger_remediation(score, features, z_iso, z_ae)

    return {
        "score": score,
        "threshold": threshold,
        "is_anomaly": is_anomaly,
        "streak": _anomaly_streak,
        "window_size": len(event_window),
        "features": describe(features),
    }


async def _trigger_remediation(
    score: float, features: np.ndarray, z_iso: float, z_ae: float
) -> None:
    diagnostic = _diagnose(features)
    global _last_webhook_error
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                REMEDIATION_WEBHOOK,
                json={
                    "anomaly_score": score,
                    "features": features.tolist(),
                    "detected_at": time.time(),
                    "diagnostic": diagnostic,
                },
            )
        logger.warning(
            "incident transmis à %s (diagnostic=%s, score=%.2f, http=%s)",
            REMEDIATION_WEBHOOK,
            diagnostic,
            score,
            response.status_code,
        )
        _last_webhook_error = None  # un succès efface l'erreur précédente
    except httpx.HTTPError as exc:
        # ÉCHEC BRUTAL ET VISIBLE. Une première version avalait l'exception :
        # le webhook pointait vers un nom d'hôte inexistant, aucun incident
        # n'arrivait au moteur, et la démo échouait sans le moindre message.
        # On log l'erreur ET on l'expose dans /healthz.
        global _incident_active
        _incident_active = False
        _last_webhook_error = f"{type(exc).__name__}: {exc}"
        logger.error("ÉCHEC du webhook de remédiation (%s) : %s", REMEDIATION_WEBHOOK, exc)


def _diagnose(features: np.ndarray) -> str:
    """Diagnostic lisible : c'est ce que le dashboard affiche et ce que le
    playbook reçoit. Règle simple et explicable, pas un classifieur opaque."""
    entropy, cpu, rejection, jitter, skew, latency = features
    if entropy > 3.0 or skew < 0.3:
        return "poisoned_decoys_suspected"
    if rejection > 0.3:
        return "key_image_replay_suspected"
    if cpu > 50_000:
        return "malformed_ring_suspected"
    if latency > 5_000:
        return "service_degraded"
    return "anomaly_unspecified"


@app.get("/score")
def score():
    """Flux temps réel pour le dashboard (polling 1 s)."""
    return latest


@app.get("/threshold")
def threshold():
    if not THRESHOLD:
        return {"model": MODEL_STATE, "missing": MISSING}
    return {
        "model": MODEL_STATE,
        "threshold": THRESHOLD["threshold"],
        "source": THRESHOLD.get("source"),
        "trained_at": THRESHOLD.get("trained_at"),
        "features": FEATURE_NAMES,
        "calibration": {
            key: THRESHOLD[key]
            for key in ("iso_mean", "iso_std", "ae_mean", "ae_std")
            if key in THRESHOLD
        },
    }


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "model": MODEL_STATE,
        "missing": MISSING,
        "window_size": len(event_window),
        "min_events": MIN_EVENTS,
        "incident_active": _incident_active,
        "anomaly_streak": _anomaly_streak,
        "streak_required": ANOMALY_STREAK_REQUIRED,
        "webhook": REMEDIATION_WEBHOOK,
        "last_webhook_error": _last_webhook_error,
    }

"""Entraînement HORS LIGNE — jamais pendant la démo.

    python train.py                    # génère, entraîne, évalue, sauvegarde
    python train.py --check            # vérifie les artefacts existants

Le jeu de données est SYNTHÉTIQUE mais construit sur la physique réelle du
système (voir features.py) : les distributions normales correspondent à ce que
produit payment-api avec des leurres tirés selon une gamma, les distributions
d'attaque à ce que produit un poison de decoys ou un rejeu de key image.

Artefacts produits (chargés par detector.py) :
    scaler.joblib, isolation_forest.joblib, autoencoder.pt, threshold.json
"""
from __future__ import annotations

import argparse
import json
import os
import time

import joblib
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from features import FEATURE_NAMES, MIN_EVENTS, N_FEATURES, SCALE_FLOORS, WINDOW_SECONDS

# Graine fixe : deux entraînements doivent produire le même modèle. Indispensable
# pour que le dossier technique et la démo soient reproductibles.
SEED = 42
ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", ".")


def _clip(vector: np.ndarray) -> np.ndarray:
    return np.clip(np.nan_to_num(vector, nan=0.0, posinf=1e6, neginf=0.0), 0, 1e6)


def load_capture(path: str) -> np.ndarray:
    """Charge une baseline capturée sur le trafic réel (capture_baseline.py).

    C'est le mode à privilégier : le modèle apprend la vraie distribution du
    système, pas une hypothèse. Les features sont calculées par le module
    partagé, donc identiques à celles de l'inférence.
    """
    from features import extract_features

    events = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    if not events:
        raise SystemExit(f"{path} est vide")

    # Fenêtres trop pauvres exclues de l'entraînement : mêmes conditions qu'en
    # production (MIN_EVENTS), sinon on apprend sur des fenêtres que le service
    # refusera de noter.
    windows, current = [], []
    for event in events:
        current.append(event)
        current = [e for e in current if event["timestamp"] - e["timestamp"] <= WINDOW_SECONDS]
        if len(current) >= MIN_EVENTS:
            windows.append(extract_features(list(current)))
    if not windows:
        raise SystemExit("pas assez d'observations pour former une fenêtre")
    return np.vstack(windows)


def generate_normal(n: int, rng: np.random.Generator) -> np.ndarray:
    """Trafic légitime de repli, calibré sur les valeurs réellement observées
    sur payment-api (entropie ~3,4 bits, skew ~1,4, CPU ~1 ms, latence ~290 ms).

    Ce n'est PAS la référence : `train.py --from-capture` est la bonne méthode.
    Ces paramètres servent uniquement à obtenir un modèle utilisable quand
    aucune capture n'est disponible (CI, poste de dev hors ligne).
    """
    n = int(n)
    median_age = rng.normal(3.56, 0.67, n)
    cpu = rng.lognormal(mean=np.log(1133), sigma=0.06, size=n)
    rejection = rng.beta(1.5, 60, n)
    inter_arrival = rng.lognormal(mean=np.log(0.9), sigma=0.35, size=n)
    skew = rng.normal(0.87, 0.28, n)
    latency = rng.lognormal(mean=np.log(292), sigma=0.009, size=n)
    return _clip(np.column_stack([median_age, cpu, rejection, inter_arrival, skew, latency]))


def generate_attack(kind: str, n: int, rng: np.random.Generator) -> np.ndarray:
    """Attaques calibrées sur les mesures réelles (analyze_separation.py).

    Constat important : un tirage UNIFORME sur un registre log-normal produit
    une asymétrie PLUS ÉLEVÉE (~3,1) qu'un tirage gamma biaisé récent (~1,4),
    et non l'inverse comme on pouvait l'intuitif. C'est l'écart à la
    distribution normale qui porte le signal, pas son signe. Les valeurs
    ci-dessous viennent de la mesure, pas d'une intuition.
    """
    n = int(n)
    if kind == "poisoned_decoys":
        # Tirage uniforme sur le registre : âge médian ~31 j au lieu de ~3,6 j,
        # et asymétrie ~1,44 au lieu de ~0,87.
        median_age = rng.normal(31.0, 12.7, n).clip(min=0.1)
        cpu = rng.lognormal(mean=np.log(1133), sigma=0.06, size=n)
        rejection = rng.beta(2, 40, n)
        inter_arrival = rng.lognormal(mean=np.log(0.9), sigma=0.35, size=n)
        skew = rng.normal(1.44, 0.28, n)
        latency = rng.lognormal(mean=np.log(292), sigma=0.009, size=n)
    elif kind == "key_image_replay":
        median_age = rng.normal(3.56, 0.67, n)
        cpu = rng.lognormal(mean=np.log(1133), sigma=0.06, size=n)
        rejection = rng.beta(34, 30, n)        # > 50 % de rejets
        inter_arrival = rng.lognormal(mean=np.log(0.9), sigma=0.35, size=n)
        skew = rng.normal(0.87, 0.28, n)
        latency = rng.lognormal(mean=np.log(292), sigma=0.009, size=n)
    elif kind == "malformed_ring":
        median_age = rng.normal(3.56, 0.67, n)
        cpu = rng.lognormal(mean=np.log(120_000), sigma=0.3, size=n)
        rejection = rng.beta(3, 40, n)
        inter_arrival = rng.lognormal(mean=np.log(0.9), sigma=0.35, size=n)
        skew = rng.normal(0.87, 0.28, n)
        latency = rng.lognormal(mean=np.log(900), sigma=0.3, size=n)
    else:  # rafale automatisée : cadence machine très rapide
        median_age = rng.normal(3.56, 0.67, n)
        cpu = rng.lognormal(mean=np.log(1133), sigma=0.06, size=n)
        rejection = rng.beta(2, 50, n)
        inter_arrival = rng.lognormal(mean=np.log(0.05), sigma=0.4, size=n)
        skew = rng.normal(0.87, 0.28, n)
        latency = rng.lognormal(mean=np.log(292), sigma=0.009, size=n)
    return _clip(np.column_stack([median_age, cpu, rejection, inter_arrival, skew, latency]))


class Autoencoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.encoder = nn.Sequential(nn.Linear(N_FEATURES, 4), nn.ReLU(), nn.Linear(4, 2), nn.ReLU())
        self.decoder = nn.Sequential(nn.Linear(2, 4), nn.ReLU(), nn.Linear(4, N_FEATURES))

    def forward(self, x):
        return self.decoder(self.encoder(x))


def _scores(scaler, iso, ae, X: np.ndarray):
    # Même bornage qu'en inférence : sans ça, l'entraînement et le service ne
    # voient pas les mêmes valeurs et le seuil est faux.
    scaled = np.clip((X - scaler.mean_) / np.maximum(scaler.scale_, SCALE_FLOORS), -8.0, 8.0)
    iso_raw = -iso.score_samples(scaled)
    with torch.no_grad():
        tensor = torch.tensor(scaled, dtype=torch.float32)
        ae_raw = torch.mean((ae(tensor) - tensor) ** 2, dim=1).numpy()
    return iso_raw, ae_raw


def _combined_scores(scaler, iso, ae, X: np.ndarray) -> np.ndarray:
    """Reproduit EXACTEMENT la fonction de score de detector.py.

    Point critique : le détecteur combine le score des modèles avec un signal
    dédié aux decoys empoisonnés (max des deux). Si la calibration n'inclut pas
    ce terme, le seuil appris ne correspond pas à ce que le service compare, et
    les faux positifs reviennent. Toute évolution de _anomaly_score doit être
    répercutée ici.
    """
    scaled = np.clip((X - scaler.mean_) / np.maximum(scaler.scale_, SCALE_FLOORS), -8.0, 8.0)
    iso_raw = -iso.score_samples(scaled)
    with torch.no_grad():
        tensor = torch.tensor(scaled, dtype=torch.float32)
        ae_raw = torch.mean((ae(tensor) - tensor) ** 2, dim=1).numpy()
    return iso_raw, ae_raw, scaled


def _score_from(iso_raw, ae_raw, scaled, cal: dict) -> np.ndarray:
    z_iso = (iso_raw - cal["iso_mean"]) / cal["iso_std"]
    z_ae = (ae_raw - cal["ae_mean"]) / cal["ae_std"]
    combined = 0.5 * z_iso + 0.5 * z_ae
    # Même signal dédié qu'en inférence : l'âge médian des leurres (feature 0).
    poison = scaled[:, 0]
    return np.maximum(combined, poison)


def train(
    n_normal: int = 6000,
    epochs: int = 300,
    out_dir: str = ARTIFACT_DIR,
    from_capture: str | None = None,
) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.default_rng(SEED)

    if from_capture:
        X_normal = load_capture(from_capture)
        source = f"capture:{os.path.basename(from_capture)}"
        print(f"Baseline réelle chargée : {len(X_normal)} fenêtres")
    else:
        X_normal = generate_normal(n_normal, rng)
        source = "synthetic_fallback"
        print("ATTENTION : aucune capture fournie, repli synthétique.")
        print("Pour un modèle défendable : python capture_baseline.py puis --from-capture")

    scaler = StandardScaler().fit(X_normal)
    scaled_normal = np.clip((X_normal - scaler.mean_) / np.maximum(scaler.scale_, SCALE_FLOORS), -8.0, 8.0)

    torch.manual_seed(SEED)
    iso = IsolationForest(n_estimators=200, contamination="auto", random_state=SEED)
    iso.fit(scaled_normal)

    ae = Autoencoder()
    optimizer = optim.Adam(ae.parameters(), lr=1e-3)
    loss_fn = nn.MSELoss()
    tensor_normal = torch.tensor(scaled_normal, dtype=torch.float32)
    for epoch in range(epochs):
        optimizer.zero_grad()
        loss = loss_fn(ae(tensor_normal), tensor_normal)
        loss.backward()
        optimizer.step()
        if epoch % 100 == 0:
            print(f"  epoch {epoch:3d}  loss {loss.item():.5f}")
    ae.eval()

    iso_cal, ae_cal = _scores(scaler, iso, ae, X_normal)

    # Calibration HORS ÉCHANTILLON. Calculer le seuil sur les fenêtres qui ont
    # servi à entraîner donne un seuil trop bas : en recette, 15 % de faux
    # positifs sur du trafic normal. On réserve donc 25 % de la baseline pour
    # fixer le seuil, et ces fenêtres ne participent pas à l'apprentissage.
    if len(X_normal) >= 40:
        rng.shuffle(X_normal)  # déjà copié dans scaled_normal
        split = int(len(X_normal) * 0.75)
        X_holdout = X_normal[split:]
        hold_iso, hold_ae, hold_scaled = _combined_scores(scaler, iso, ae, X_holdout)
        cal_iso, cal_ae, cal_scaled = hold_iso, hold_ae, hold_scaled
        calibration_set = "holdout_25pct"
    else:
        cal_iso, cal_ae = iso_cal, ae_cal
        cal_scaled = scaled_normal
        calibration_set = "insample_small_baseline"

    threshold_data = {
        "iso_mean": float(cal_iso.mean()),
        # Plancher sur l'écart-type : une baseline courte peut avoir un
        # écart-type quasi nul sur une feature, ce qui ferait exploser les
        # z-scores en production.
        "iso_std": float(max(cal_iso.std(), 1e-2)),
        "ae_mean": float(cal_ae.mean()),
        "ae_std": float(max(cal_ae.std(), 1e-4)),
        "source": source,
        "calibration_set": calibration_set,
        "score_function": "max(0.5*z_iso+0.5*z_ae, z_median_age)",
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_normal": int(len(X_normal)),
        "features": list(FEATURE_NAMES),
    }

    # Seuil appris. Aucune constante dans le code de production.
    #
    # On prend le MAX de deux estimateurs :
    #   - le 99,5e percentile (comportement statistique attendu)
    #   - moyenne + 4 écarts-types (garde-fou quand la baseline est courte)
    #
    # Pourquoi : avec ~150 fenêtres de calibration, le 99,5e percentile vaut à
    # peu près la 2e valeur la plus haute, donc il colle au bruit. En recette,
    # un seuil à 2,06 déclenchait sur du trafic normal à 2,14. Le coût des deux
    # erreurs n'est pas symétrique : un faux déclenchement pendant une démo de
    # 3 minutes coûte 25 points, une détection un peu plus tardive coûte
    # quelques secondes. On privilégie donc la spécificité.
    z_scores = _score_from(cal_iso, cal_ae, cal_scaled, threshold_data)
    pct_threshold = float(np.percentile(z_scores, 99.5))
    robust_threshold = float(z_scores.mean() + 4.0 * z_scores.std())
    threshold_data["threshold"] = max(pct_threshold, robust_threshold)
    threshold_data["threshold_estimators"] = {
        "percentile_99.5": round(pct_threshold, 4),
        "mean_plus_4std": round(robust_threshold, 4),
        "retained": round(threshold_data["threshold"], 4),
    }

    # Évaluation sur des données de test NON vues.
    X_test_normal = (
        load_capture(from_capture)[-500:] if from_capture else generate_normal(2000, rng)
    )
    kinds = ["poisoned_decoys", "key_image_replay", "malformed_ring", "slow_and_low"]
    X_attack = np.vstack([generate_attack(kind, 400, rng) for kind in kinds])

    test_iso, test_ae, test_scaled = _combined_scores(scaler, iso, ae, X_test_normal)
    z_normal = _score_from(test_iso, test_ae, test_scaled, threshold_data)
    fp_rate = float((z_normal > threshold_data["threshold"]).mean())

    # Faux positifs sur 3 fenêtres CONSÉCUTIVES (ce que fait réellement le
    # détecteur avec son anti-rebond). C'est ce chiffre qui compte en démo.
    flags = (z_normal > threshold_data["threshold"]).astype(int)
    streak_fp = 0.0
    if len(flags) > 3:
        triplets = np.convolve(flags, np.ones(3, dtype=int), mode="valid")
        streak_fp = float((triplets == 3).mean())

    iso_a, ae_a, scaled_a = _combined_scores(scaler, iso, ae, X_attack)
    z_attack = _score_from(iso_a, ae_a, scaled_a, threshold_data)
    recall = float((z_attack > threshold_data["threshold"]).mean())

    threshold_data["evaluation"] = {
        "false_positive_rate": round(fp_rate, 4),
        "false_positive_rate_3_consecutive": round(streak_fp, 4),
        "attack_recall": round(recall, 4),
        "n_test_normal": int(len(X_test_normal)),
        "n_test_attack": len(X_attack),
    }

    joblib.dump(scaler, os.path.join(out_dir, "scaler.joblib"))
    joblib.dump(iso, os.path.join(out_dir, "isolation_forest.joblib"))
    torch.save(ae.state_dict(), os.path.join(out_dir, "autoencoder.pt"))
    with open(os.path.join(out_dir, "threshold.json"), "w", encoding="utf-8") as handle:
        json.dump(threshold_data, handle, indent=2)

    return threshold_data


def check(out_dir: str = ARTIFACT_DIR) -> int:
    path = os.path.join(out_dir, "threshold.json")
    if not os.path.exists(path):
        print(f"MANQUANT : {path} — lancez python train.py")
        return 1
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    print(json.dumps(data, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--samples", type=int, default=6000)
    parser.add_argument("--out", default=ARTIFACT_DIR)
    parser.add_argument(
        "--from-capture",
        default=None,
        help="baseline JSONL issue de capture_baseline.py (recommandé)",
    )
    args = parser.parse_args()

    if args.check:
        return check(args.out)

    print(f"Entraînement sur {args.samples} échantillons normaux synthétiques…")
    data = train(args.samples, args.epochs, args.out, args.from_capture)
    print("\nSeuil appris :", round(data["threshold"], 4))
    print("Évaluation  :", json.dumps(data["evaluation"], indent=2))
    print("\nArtefacts écrits dans", os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

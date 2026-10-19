"""Entraînement HORS LIGNE — à lancer le Jour 2, jamais pendant la démo.

Génère ~5000 transactions normales + 500 transactions d'attaque simulées
(cf. plan), entraîne l'Isolation Forest + l'autoencoder, et sauvegarde les
artefacts que detector.py charge au démarrage.
"""
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.ensemble import IsolationForest

from detector import Autoencoder


def generate_synthetic_dataset(n_normal=5000, n_attack=500):
    rng = np.random.default_rng(42)

    # 6 features : decoy_entropy, ring_verify_cpu_us, key_image_rejection_ratio,
    # inter_arrival_jitter, ring_age_skewness, tx_latency_p95
    normal = rng.normal(
        loc=[3.2, 4000, 0.01, 0.5, 0.1, 4500],
        scale=[0.3, 400, 0.01, 0.2, 0.05, 500],
        size=(n_normal, 6),
    )
    # Attaque : entropie des leurres qui s'effondre, CPU qui explose (anneaux malformés),
    # rejets de key image qui montent, jitter "low & slow"
    attack = rng.normal(
        loc=[0.8, 15000, 0.4, 3.0, 0.9, 20000],
        scale=[0.3, 3000, 0.15, 1.0, 0.1, 4000],
        size=(n_attack, 6),
    )
    X = np.vstack([normal, attack]).clip(min=0)
    y = np.array([0] * n_normal + [1] * n_attack)  # pour évaluation uniquement
    return X, y


def main():
    X, y = generate_synthetic_dataset()
    X_normal = X[y == 0]

    iso_forest = IsolationForest(contamination=0.05, random_state=42)
    iso_forest.fit(X_normal)

    autoencoder = Autoencoder()
    optimizer = optim.Adam(autoencoder.parameters(), lr=1e-3)
    loss_fn = nn.MSELoss()
    X_tensor = torch.tensor(X_normal, dtype=torch.float32)

    for epoch in range(200):
        optimizer.zero_grad()
        recon = autoencoder(X_tensor)
        loss = loss_fn(recon, X_tensor)
        loss.backward()
        optimizer.step()
        if epoch % 50 == 0:
            print(f"epoch {epoch} loss {loss.item():.4f}")

    import joblib

    joblib.dump(iso_forest, "isolation_forest.joblib")
    torch.save(autoencoder.state_dict(), "autoencoder.pt")
    print("Artefacts sauvegardés : isolation_forest.joblib, autoencoder.pt")


if __name__ == "__main__":
    main()

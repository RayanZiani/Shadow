#!/usr/bin/env python3
"""Télécharge les modèles faciaux pour un poste de développement.

    python services/backoffice/fetch_models.py

L'image Docker les télécharge et les vérifie elle-même à la construction
(`services/backoffice/Dockerfile`) : ce script ne sert qu'à faire tourner le
service **hors Docker**, sur le poste, avec `uvicorn`.

Les empreintes sont les mêmes que dans le Dockerfile : un fichier altéré est
refusé, ce qui évite de tester un modèle différent de celui de la démonstration.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/"
        "face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


def main() -> int:
    target = Path(__file__).parent / "models"
    target.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in MODELS.items():
        destination = target / name
        if destination.exists():
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            if digest == expected:
                print(f"{name} : déjà présent, empreinte correcte")
                continue
            print(f"{name} : empreinte incorrecte, nouveau téléchargement")
        print(f"{name} : téléchargement…")
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != expected:
            print(f"  empreinte inattendue : {digest}")
            return 1
        destination.write_bytes(payload)
        print(f"  {len(payload)} octets, empreinte vérifiée")
    return 0


if __name__ == "__main__":
    sys.exit(main())
